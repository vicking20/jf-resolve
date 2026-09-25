"""Modular Plugin Management API"""

import logging
from typing import Any, Dict, List, Optional

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from packaging.version import Version
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import HTMLResponse

from ..addons.manager import plugin_manager
from ..addons.manager import plugin_manager
from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..services.settings_manager import SettingsManager


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/addons", tags=["addons"])


@router.get("/")
async def list_plugins(user=Depends(get_current_admin_user)):
    """List all installed plugins with their manifests"""
    plugins = []
    for plugin_id, plugin in plugin_manager.plugins.items():
        plugins.append(
            {
                "id": plugin_id,
                "manifest": plugin.manifest,
                "has_ui": plugin.has_ui,
                "enabled": True,  # For now, if it's in manager.plugins, it's loaded
            }
        )
    return plugins


@router.get("/marketplace")
async def get_marketplace(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_admin_user),
):
    """Get marketplace plugins from all configured repositories"""
    settings_mgr = SettingsManager(db)
    repositories = await settings_mgr.get("plugin_repositories", [])
    if not repositories:
        return {"repositories": []}

    # Simple in-memory cache (15 minutes)
    cache_key = "marketplace_cache"
    cache_time_key = "marketplace_cache_time"
    cache_duration = timedelta(minutes=15)

    # Check cache
    now = datetime.now()
    cache_time = await settings_mgr.get(cache_time_key)
    if cache_time and (now - datetime.fromisoformat(cache_time)) < cache_duration:
        cached_data = await settings_mgr.get(cache_key)
        if cached_data:
            return cached_data

    # Official repository URL (hardcoded for identification)
    official_url = "https://raw.githubusercontent.com/vicking20/jf-resolve-plugin-repo/main/master.json"

    async def fetch_repository(url: str) -> Optional[Dict]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logging.warning(f"Failed to fetch repository {url}: {e}")
            return None

    # Fetch all repositories concurrently
    tasks = [fetch_repository(url) for url in repositories]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    repositories_data = []
    for url, result in zip(repositories, results):
        if isinstance(result, Exception) or result is None:
            continue

        manifest = result
        registry_name = manifest.get("registry_name", "Unknown Registry")
        plugins_list = manifest.get("plugins", [])

        # Enrich plugins with install status
        for plugin in plugins_list:
            plugin_id = plugin["id"]
            plugin["installed_version"] = None
            plugin["update_available"] = False

            if plugin_id in plugin_manager.plugins:
                installed_plugin = plugin_manager.plugins[plugin_id]
                installed_manifest = installed_plugin.manifest
                installed_version = installed_manifest.get("version", "0.0.0")
                plugin["installed_version"] = installed_version

                try:
                    installed_ver = Version(installed_version)
                    remote_ver = Version(plugin.get("version", "0.0.0"))
                    plugin["update_available"] = remote_ver > installed_ver
                except Exception:
                    pass  # Version comparison failed, assume no update

        repositories_data.append(
            {
                "name": registry_name,
                "url": url,
                "is_official": url == official_url,
                "plugins": plugins_list,
            }
        )

    response_data = {"repositories": repositories_data}

    # Cache the result
    await settings_mgr.set(cache_key, response_data)
    await settings_mgr.set(cache_time_key, now.isoformat())

    return response_data


@router.get("/{plugin_id}")
async def get_plugin_details(plugin_id: str, user=Depends(get_current_admin_user)):
    """Get details for a specific plugin"""
    plugin = plugin_manager.plugins.get(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    return {
        "id": plugin_id,
        "manifest": plugin.manifest,
        "has_ui": plugin.has_ui,
        "config": plugin.config,
    }


@router.get("/{plugin_id}/ui", response_class=HTMLResponse)
async def get_plugin_ui(plugin_id: str, user=Depends(get_current_admin_user)):
    """Get the configuration UI (HTML) for a plugin"""
    ui_html = plugin_manager.get_plugin_ui(plugin_id)
    if not ui_html:
        raise HTTPException(status_code=404, detail="UI not found for this plugin")
    return ui_html


@router.get("/{plugin_id}/config")
async def get_plugin_config(
    plugin_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_admin_user),
):
    """Get the current configuration for a plugin"""
    config = plugin_manager.get_plugin_config(plugin_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    if plugin_id == "com.vicking20.tmdb-core":
        config = {**config, "api_key": await SettingsManager(db).get("tmdb_api_key", "")}
    return config


@router.put("/{plugin_id}/config")
@router.post("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    config: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_admin_user),
):
    """Update plugin configuration"""
    try:
        new_config = await plugin_manager.save_plugin_config(plugin_id, config)
        if plugin_id == "com.vicking20.tmdb-core":
            await SettingsManager(db).set("tmdb_api_key", config.get("api_key", ""))
        # Re-trigger plugin on_load to apply changes
        plugin = plugin_manager.plugins.get(plugin_id)
        if plugin:
            await plugin.on_load()
        return new_config
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update config for {plugin_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save configuration")


@router.delete("/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    delete_data: bool = False,
    user=Depends(get_current_admin_user),
):
    """Uninstall a plugin"""
    await plugin_manager.delete_plugin(plugin_id, delete_data)
    return {"status": "success", "message": f"Plugin {plugin_id} uninstalled"}
