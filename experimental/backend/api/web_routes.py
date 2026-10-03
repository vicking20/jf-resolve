"""Web routes for HTML pages"""

import logging
import re
import shutil
from pathlib import Path
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy.ext.asyncio import AsyncSession

from ..addons.manager import plugin_manager
from ..core.config import settings
from ..core.dependencies import get_admin_user_page, get_current_admin_user
from ..core.session import get_current_user, get_current_user_optional
from ..database import AsyncSessionLocal, get_db
from ..models.user import User
from ..services.access_control import ensure_item_allowed
from ..services.auth_service import AuthService
from ..services.media_resolver import MediaResolver
from ..services.settings_manager import SettingsManager
from ..services.source_orchestrator import source_orchestrator
from ..services.tmdb_service import TMDBService

router = APIRouter(include_in_schema=False)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))

PLUGIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def validate_plugin_id(plugin_id: str) -> str:
    """Plugin ids become directory names under plugins/, so they must be plain names."""
    if not PLUGIN_ID.fullmatch(plugin_id) or ".." in plugin_id:
        raise HTTPException(status_code=400, detail="Invalid plugin id")
    return plugin_id


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request, user: User | None = Depends(get_current_user_optional)
):
    """Homepage - redirects to home or setup"""
    if not user:
        async with AsyncSessionLocal() as db:
            auth = AuthService(db)
            if not await auth.has_users():
                return RedirectResponse(url="/setup")
        return RedirectResponse(url="/login")

    return templates.TemplateResponse(request=request, name="home.html", context={"request": request, "user": user})


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request})


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """First-time setup wizard"""
    # Check if setup flag file exists
    if settings.SETUP_FLAG_FILE.exists():
        return RedirectResponse(url="/login")

    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        if await auth.has_users():
            return RedirectResponse(url="/login")

    return templates.TemplateResponse(request=request, name="setup.html", context={"request": request})


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, user: User = Depends(get_current_user)):
    """Search page"""
    return templates.TemplateResponse(request=request, name="search.html", context={"request": request, "user": user})


@router.get("/library", response_class=HTMLResponse)
async def library_page(request: Request, user: User = Depends(get_current_user)):
    """Library management page"""
    return templates.TemplateResponse(
        request=request, name="library.html", context={"request": request, "user": user}
    )


@router.get("/discover", response_class=HTMLResponse)
async def discover_page(request: Request, user: User = Depends(get_current_user)):
    """Discover page"""
    return templates.TemplateResponse(
        request=request, name="discover.html", context={"request": request, "user": user}
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: User = Depends(get_admin_user_page)):
    """Settings page"""
    return templates.TemplateResponse(
        request=request, name="settings.html", context={"request": request, "user": user}
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, user: User = Depends(get_admin_user_page)):
    """Logs viewer page"""
    return templates.TemplateResponse(request=request, name="logs.html", context={"request": request, "user": user})


@router.get("/movie/{item_id}", response_class=HTMLResponse)
@router.get("/series/{item_id}", response_class=HTMLResponse)
@router.get("/tv/{item_id}", response_class=HTMLResponse)
@router.get("/media/{media_type}/{item_id}", response_class=HTMLResponse)
async def unified_media_detail_page(
    request: Request,
    item_id: str,
    media_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Unified media detail page handling TMDB IDs, UUIDs, and provider:id"""
    settings_mgmt = SettingsManager(db)
    api_key = await settings_mgmt.get("tmdb_api_key")
    tmdb = TMDBService(api_key) if api_key else None

    # We create resolver with TMDB service (or dummy if not configured)
    resolver = MediaResolver(tmdb) if tmdb else MediaResolver(TMDBService(""))

    # Determine media type if not provided (from URL path)
    if not media_type:
        path = request.url.path
        if "/movie" in path:
            media_type = "movie"
        elif "/series" in path or "/tv" in path:
            media_type = "tv"
        else:
            media_type = "movie"  # Default

    try:
        # 1. Resolve media item (Hybrid: DB + TMDB)
        # item_id can be UUID, tmdb:123, or just 123 (backwards compat)
        identifier = item_id
        if item_id.isdigit():
            identifier = f"tmdb:{item_id}"

        item = await resolver.resolve(
            db,
            identifier,
            media_type,
            user_id=int(user.id) if user and hasattr(user, "id") else None,
        )
        if not item:
            return templates.TemplateResponse(
                request=request, name="404.html", context={"request": request}, status_code=404
            )
        try:
            await ensure_item_allowed(db, user, item)
        except HTTPException:
            # Same page as a missing title, so restricted titles aren't revealed
            return templates.TemplateResponse(
                request=request, name="404.html", context={"request": request}, status_code=403
            )

        # item is already a dictionary from MediaResolver
        item_data = item

        # Extract key metadata fields to top level for consistency
        metadata = item_data.get("metadata", {})
        if metadata:
            # Ensure key fields are available at top level
            if not item_data.get("overview") and metadata.get("overview"):
                item_data["overview"] = metadata.get("overview")
            if not item_data.get("vote_average") and metadata.get("vote_average"):
                item_data["vote_average"] = metadata.get("vote_average")
            if not item_data.get("genre_ids") and metadata.get("genres"):
                item_data["genre_ids"] = [g["id"] for g in metadata.get("genres", [])]
            if not item_data.get("runtime") and metadata.get("runtime"):
                item_data["runtime"] = metadata.get("runtime")

        # 2. Get sources via SourceOrchestrator
        sources_dict = await source_orchestrator.get_sources(
            db,
            media_uuid=item.get("uuid") or "",
            tmdb_id=item.get("tmdb_id") or 0,
            imdb_id=item.get("imdb_id") or "",
            media_type=item.get("media_type") or "",
            title=item.get("title") or "",
            year=item.get("year") or 0,
        )

        # Inject additional metadata for UI if it's a TV show
        if item.get("media_type") == "tv" and tmdb:
            tmdb_id = item.get("tmdb_id")
            if tmdb_id:
                details = await tmdb.get_tv_details(int(tmdb_id))
                seasons = []
                for s in details.get("seasons", []):
                    try:
                        season_data = await tmdb.get_season_details(
                            int(tmdb_id), s["season_number"]
                        )
                        seasons.append(
                            {
                                "season_number": s["season_number"],
                                "name": s["name"],
                                "episode_count": len(season_data.get("episodes", [])),
                                "episodes": [
                                    {
                                        "episode_number": ep["episode_number"],
                                        "name": ep["name"],
                                        "overview": ep["overview"],
                                        "still_path": ep.get("still_path"),
                                    }
                                    for ep in season_data.get("episodes", [])
                                ],
                            }
                        )
                    except:
                        continue
                item_data["seasons"] = seasons

        # Image URLs - Unified format (use UUID if available, otherwise TMDB proxy)
        if item.get("uuid"):
            item_data["poster_url"] = f"/api/media/images/poster/{item.get('uuid')}"
            item_data["backdrop_url"] = f"/api/media/images/backdrop/{item.get('uuid')}"
        else:
            # For transient TMDB items, use TMDB proxy directly
            poster_path = item.get("image_paths", {}).get("poster")
            backdrop_path = item.get("image_paths", {}).get("backdrop")
            if poster_path:
                item_data["poster_url"] = (
                    f"https://image.tmdb.org/t/p/w500{poster_path}"
                )
            if backdrop_path:
                item_data["backdrop_url"] = (
                    f"https://image.tmdb.org/t/p/original{backdrop_path}"
                )

        return templates.TemplateResponse(
            request=request, name="media_detail.html",
            context={
                "request": request,
                "media": item_data,
                "sources": sources_dict.get("local", [])
                + sources_dict.get("jit_local", [])
                + sources_dict.get("strm", []),
                "addon_streams": sources_dict.get("streams", {}),
                "user": user,
                "is_virtual": not item.get("is_persisted", False),
            },
        )
    except Exception as e:
        logger.error(f"Error in detail page: {e}", exc_info=True)
        return templates.TemplateResponse(
            request=request, name="404.html", context={"request": request}, status_code=404
        )
    finally:
        if tmdb:
            await tmdb.close()


@router.get("/addons/{plugin_id}/configure", response_class=HTMLResponse)
async def plugin_configure_page(
    request: Request,
    plugin_id: str,
    user: User = Depends(get_admin_user_page),
):
    """Plugin configuration page"""
    plugin = plugin_manager.plugins.get(plugin_id)
    if not plugin:
        return templates.TemplateResponse(
            request=request, name="404.html", context={"request": request}, status_code=404
        )

    return templates.TemplateResponse(
        request=request, name="plugin_configure.html", context={"request": request, "plugin": plugin, "user": user}
    )





@router.post("/api/addons/install")
async def install_plugin(
    plugin_id: str = Query(...),
    repository_url: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Install a plugin from the marketplace"""
    from ..addons.manager import plugin_manager
    from ..services.settings_manager import SettingsManager

    validate_plugin_id(plugin_id)

    if plugin_id in plugin_manager.plugins:
        raise HTTPException(status_code=400, detail="Plugin already installed")

    # Fetch manifest from repository
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(repository_url)
            response.raise_for_status()
            manifest = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch repository: {e}")

    # Find the plugin
    plugins = manifest.get("plugins", [])
    plugin_data = next((p for p in plugins if p["id"] == plugin_id), None)
    if not plugin_data:
        raise HTTPException(status_code=404, detail="Plugin not found in repository")

    # Create plugin directory
    plugin_dir = Path(settings.BASE_DIR) / "plugins" / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = Path(settings.BASE_DIR) / "data" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Download files
        files = plugin_data.get("files", {})
        downloaded_files = {}

        for file_type, url in files.items():
            if not url:
                continue
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    content = response.content

                # Save to temp
                temp_file = temp_dir / f"{plugin_id}_{file_type}"
                with open(temp_file, "wb") as f:
                    f.write(content)
                downloaded_files[file_type] = temp_file
            except Exception as e:
                logging.error(f"Failed to download {file_type}: {e}")
                raise HTTPException(
                    status_code=400, detail=f"Failed to download {file_type}"
                )

        # Move files to plugin directory
        manifest_dest = plugin_dir / "manifest.json"
        plugin_dest = plugin_dir / "plugin.py"
        config_dest = plugin_dir / "config.html"

        if "manifest" in downloaded_files:
            downloaded_files["manifest"].rename(manifest_dest)
        if "plugin" in downloaded_files:
            downloaded_files["plugin"].rename(plugin_dest)
        if "config" in downloaded_files:
            downloaded_files["config"].rename(config_dest)

        # Handle dependencies
        depends_on = plugin_data.get("depends_on", [])
        if depends_on:
            import subprocess

            libs_dir = plugin_dir / "libs"
            libs_dir.mkdir(exist_ok=True)
            try:
                subprocess.check_call(
                    ["pip3", "install", "--target", str(libs_dir), "--quiet"]
                    + depends_on
                )
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to install dependencies: {e}")
                # Continue anyway, plugin might work without

        # Load the plugin
        await plugin_manager._load_plugin(plugin_dir, manifest_dest)

        return {"message": "Plugin installed successfully", "plugin_id": plugin_id}

    except Exception as e:
        # Cleanup on failure

        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        raise HTTPException(status_code=500, detail=f"Installation failed: {e}")
    finally:
        # Cleanup temp files
        for temp_file in downloaded_files.values():
            if temp_file.exists():
                temp_file.unlink()


@router.get("/api/addons/{plugin_id}/check-update")
async def check_plugin_update(
    plugin_id: str,
    repository_url: str = Query(...),
    current_user: User = Depends(get_current_admin_user),
):
    """Check if an update is available for a plugin"""
    from packaging.version import Version

    from ..addons.manager import plugin_manager

    if plugin_id not in plugin_manager.plugins:
        raise HTTPException(status_code=404, detail="Plugin not installed")

    installed_plugin = plugin_manager.plugins[plugin_id]
    installed_version = installed_plugin.manifest.get("version", "0.0.0")

    # Fetch remote manifest
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(repository_url)
            response.raise_for_status()
            manifest = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch repository: {e}")

    # Find the plugin
    plugins = manifest.get("plugins", [])
    plugin_data = next((p for p in plugins if p["id"] == plugin_id), None)
    if not plugin_data:
        raise HTTPException(status_code=404, detail="Plugin not found in repository")

    remote_version = plugin_data.get("version", "0.0.0")

    try:
        installed_ver = Version(installed_version)
        remote_ver = Version(remote_version)
        update_available = remote_ver > installed_ver
        major_change = remote_ver.major > installed_ver.major
    except Exception:
        update_available = False
        major_change = False

    return {
        "plugin_id": plugin_id,
        "installed_version": installed_version,
        "remote_version": remote_version,
        "update_available": update_available,
        "breaking_change": major_change,
    }


@router.post("/api/addons/update/{plugin_id}")
async def update_plugin(
    plugin_id: str,
    repository_url: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Update an installed plugin"""
    from ..addons.manager import plugin_manager

    if plugin_id not in plugin_manager.plugins:
        raise HTTPException(status_code=404, detail="Plugin not installed")

    # Fetch remote manifest
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(repository_url)
            response.raise_for_status()
            manifest = response.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch repository: {e}")

    # Find the plugin
    plugins = manifest.get("plugins", [])
    plugin_data = next((p for p in plugins if p["id"] == plugin_id), None)
    if not plugin_data:
        raise HTTPException(status_code=404, detail="Plugin not found in repository")

    # Check if update needed
    installed_version = plugin_manager.plugins[plugin_id].manifest.get(
        "version", "0.0.0"
    )
    remote_version = plugin_data.get("version", "0.0.0")

    try:
        from packaging.version import Version

        if Version(remote_version) <= Version(installed_version):
            return {"message": "Plugin is already up to date"}
    except Exception:
        pass  # Continue with update

    # Backup config if breaking change
    plugin_dir = Path(settings.BASE_DIR) / "plugins" / plugin_id
    config_file = plugin_dir / "config.json"
    config_backup = None

    try:
        installed_ver = Version(installed_version)
        remote_ver = Version(remote_version)
        if remote_ver.major > installed_ver.major and config_file.exists():
            # Breaking change - backup config

            config_backup = plugin_dir / "config.json.backup"
            shutil.copy2(config_file, config_backup)
    except Exception:
        pass

    # Download and update files (similar to install)
    temp_dir = Path(settings.BASE_DIR) / "data" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    files = plugin_data.get("files", {})
    downloaded_files = {}

    try:
        for file_type, url in files.items():
            if not url:
                continue
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    content = response.content

                temp_file = temp_dir / f"{plugin_id}_{file_type}_update"
                with open(temp_file, "wb") as f:
                    f.write(content)
                downloaded_files[file_type] = (
                    temp_file,
                    plugin_dir / f"{file_type}.json"
                    if file_type == "manifest"
                    else plugin_dir / f"{file_type}.py"
                    if file_type == "plugin"
                    else plugin_dir / f"{file_type}.html",
                )
            except Exception as e:
                logging.error(f"Failed to download {file_type}: {e}")
                raise HTTPException(
                    status_code=400, detail=f"Failed to download {file_type}"
                )

        # Update files
        for file_type, (temp_file, dest_file) in downloaded_files.items():
            temp_file.rename(dest_file)

        # Handle dependencies (reinstall if changed)
        depends_on = plugin_data.get("depends_on", [])
        if depends_on:
            import subprocess

            libs_dir = plugin_dir / "libs"
            libs_dir.mkdir(exist_ok=True)
            try:
                subprocess.check_call(
                    [
                        "pip3",
                        "install",
                        "--target",
                        str(libs_dir),
                        "--quiet",
                        "--upgrade",
                    ]
                    + depends_on
                )
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to update dependencies: {e}")

        # Reload the plugin
        await plugin_manager.unload_plugin(plugin_id)
        manifest_path = plugin_dir / "manifest.json"
        await plugin_manager._load_plugin(plugin_dir, manifest_path)

        # Clean up backup if successful
        if config_backup and config_backup.exists():
            config_backup.unlink()

        return {
            "message": "Plugin updated successfully",
            "plugin_id": plugin_id,
            "old_version": installed_version,
            "new_version": remote_version,
        }

    except Exception as e:
        # Restore backup on failure
        if config_backup and config_backup.exists():
            shutil.copy2(config_backup, config_file)
            config_backup.unlink()
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")
    finally:
        # Cleanup temp files
        for temp_file, _ in downloaded_files.values():
            if temp_file.exists():
                temp_file.unlink()


@router.get("/api/addons/repositories")
async def get_plugin_repositories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Get configured plugin repositories"""
    from ..services.settings_manager import SettingsManager

    settings_mgr = SettingsManager(db)
    repositories = await settings_mgr.get("plugin_repositories", [])
    return {"repositories": repositories}


@router.post("/api/addons/repositories")
async def update_plugin_repositories(
    repositories: List[str],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Update plugin repositories list"""
    from ..services.settings_manager import SettingsManager

    settings_mgr = SettingsManager(db)
    repositories = list(dict.fromkeys(url.strip() for url in repositories))
    existing = await settings_mgr.get("plugin_repositories", [])
    for url in repositories:
        if not url.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail="Repository URLs must use HTTP or HTTPS")
        if url in existing:
            continue
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                manifest = response.json()
            if not isinstance(manifest, dict) or not isinstance(manifest.get("plugins"), list):
                raise ValueError("missing plugins array")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid repository: {exc}") from exc
    await settings_mgr.set("plugin_repositories", repositories)
    await settings_mgr.set("marketplace_cache", {})
    await settings_mgr.set("marketplace_cache_time", "")
    return {"message": "Repositories updated"}


@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_page(request: Request, user: User = Depends(get_admin_user_page)):
    """Plugin marketplace page - browse and install plugins"""
    return templates.TemplateResponse(
        request=request, name="marketplace.html", context={"request": request, "user": user}
    )


@router.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request, user: User = Depends(get_admin_user_page)):
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request=request, name="downloads.html", context={"user": user})
