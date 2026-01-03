"""System API routes (health, logs, tasks)"""

from datetime import datetime
from pathlib import Path
from typing import Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user
from ..config import settings as app_settings
from ..database import get_db
from ..models.user import User
from ..services.library_service import LibraryService
from ..services.log_service import log_service
from ..services.populate_service import PopulateService
from ..services.settings_manager import SettingsManager
from ..services.stremio_service import StremioService
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/populate/run")
async def run_auto_populate_manual(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger the auto-populate job"""
    settings = SettingsManager(db)
    await settings.load_cache()

    tmdb_key = await settings.get("tmdb_api_key")
    if not tmdb_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    tmdb = TMDBService(tmdb_key)
    library = LibraryService(db, tmdb, settings)
    populate_service = PopulateService(db, tmdb, library, settings)

    try:
        result = await populate_service.run_auto_populate()
        return result
    except Exception as e:
        log_service.error(f"Manual auto-populate failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await tmdb.close()


@router.post("/series/update")
async def run_series_update_manual(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger the series update check"""
    settings = SettingsManager(db)
    await settings.load_cache()

    tmdb_key = await settings.get("tmdb_api_key")
    if not tmdb_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    tmdb = TMDBService(tmdb_key)
    library = LibraryService(db, tmdb, settings)
    populate_service = PopulateService(db, tmdb, library, settings)

    try:
        result = await populate_service.run_series_update()
        return result
    except Exception as e:
        log_service.error(f"Manual series update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await tmdb.close()


@router.get("/status")
async def system_status():
    """Basic system status check"""
    return {
        "status": "ok",
        "version": "2.0.0",
        "data_dir": str(app_settings.DATA_DIR),
        "logs_dir": str(app_settings.LOGS_DIR),
    }


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Comprehensive health check:
    - TMDB API connectivity
    - Stremio manifest accessibility
    - Paths writable
    """
    settings = SettingsManager(db)
    await settings.load_cache()

    health = {
        "tmdb": {"status": "unknown", "message": ""},
        "stremio": {"status": "unknown", "message": ""},
        "paths": {"status": "unknown", "message": ""},
        "overall": "healthy",
    }

    # Check TMDB API
    tmdb_key = await settings.get("tmdb_api_key")
    if tmdb_key:
        try:
            tmdb = TMDBService(tmdb_key)
            await tmdb.get_trending("movie", "week", 1)
            health["tmdb"] = {"status": "ok", "message": "Connected"}
            await tmdb.close()
        except Exception as e:
            health["tmdb"] = {"status": "error", "message": str(e)}
            health["overall"] = "degraded"
    else:
        health["tmdb"] = {"status": "not_configured", "message": "API key not set"}
        health["overall"] = "degraded"

    # Check Stremio manifest
    manifest_url = await settings.get("stremio_manifest_url")
    if manifest_url:
        try:
            stremio = StremioService(manifest_url)
            # Just check if URL is accessible
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{stremio.manifest_url}/manifest.json", timeout=5.0
                )
                response.raise_for_status()
            health["stremio"] = {"status": "ok", "message": "Manifest accessible"}
            await stremio.close()
        except Exception as e:
            health["stremio"] = {"status": "error", "message": str(e)}
            health["overall"] = "degraded"
    else:
        health["stremio"] = {
            "status": "not_configured",
            "message": "Manifest URL not set",
        }
        health["overall"] = "degraded"

    # Check paths
    movie_path = await settings.get("jellyfin_movie_path")
    tv_path = await settings.get("jellyfin_tv_path")

    path_issues = []
    if movie_path:
        p = Path(movie_path)
        if not p.exists():
            path_issues.append(f"Movie path does not exist: {movie_path}")
        elif not p.is_dir():
            path_issues.append(f"Movie path is not a directory: {movie_path}")
    else:
        path_issues.append("Movie path not configured")

    if tv_path:
        p = Path(tv_path)
        if not p.exists():
            path_issues.append(f"TV path does not exist: {tv_path}")
        elif not p.is_dir():
            path_issues.append(f"TV path is not a directory: {tv_path}")
    else:
        path_issues.append("TV path not configured")

    if path_issues:
        health["paths"] = {"status": "warning", "message": "; ".join(path_issues)}
        if health["overall"] != "degraded":
            health["overall"] = "warning"
    else:
        health["paths"] = {"status": "ok", "message": "All paths configured"}

    return health


@router.get("/logs")
async def get_logs(
    type: str = Query("error", regex="^(error|info|stream)$"),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
):
    """Get recent log entries"""
    try:
        logs = log_service.get_logs(type, limit)
        return {"log_type": type, "lines": logs, "count": len(logs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {str(e)}")


@router.get("/logs/download")
async def download_logs(
    type: str = Query("error", regex="^(error|info|stream)$"),
    current_user: User = Depends(get_current_user),
):
    """Download full log file"""
    try:
        log_file = log_service.get_log_file_path(type)

        if not log_file.exists():
            raise HTTPException(status_code=404, detail="Log file not found")

        return FileResponse(
            path=log_file, filename=f"{type}.log", media_type="text/plain"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download log: {str(e)}")


@router.get("/export")
async def export_library(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Export library as JSON"""
    import json

    from sqlalchemy import select

    from ..models.library_item import LibraryItem

    result = await db.execute(select(LibraryItem))
    items = result.scalars().all()

    export_data = []
    for item in items:
        export_data.append(
            {
                "tmdb_id": item.tmdb_id,
                "imdb_id": item.imdb_id,
                "media_type": item.media_type,
                "title": item.title,
                "year": item.year,
                "quality_versions": json.loads(item.quality_versions)
                if item.quality_versions
                else [],
                "added_via": item.added_via,
            }
        )

    return {
        "version": "2.0.0",
        "exported_at": datetime.utcnow().isoformat(),
        "items": export_data,
        "count": len(export_data),
    }


@router.post("/test-stream-connection")
async def test_stream_connection(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Test connection to the streaming server (Port 8766)"""
    settings = SettingsManager(db)
    await settings.load_cache()

    results = {
        "localhost": {"status": "unknown", "url": "http://127.0.0.1:8766/health"},
        "external": {"status": "unknown", "url": None},
        "overall": "failed",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(results["localhost"]["url"], timeout=3.0)
            if resp.status_code == 200:
                results["localhost"]["status"] = "ok"
                results["overall"] = "ok"
            else:
                results["localhost"]["status"] = f"error_{resp.status_code}"
        except Exception as e:
            results["localhost"]["status"] = "failed"
            results["localhost"]["error"] = str(e)

        external_url = await settings.get("stream_server_url")
        if external_url:
            results["external"]["url"] = f"{external_url.rstrip('/')}/health"
            try:
                resp = await client.get(results["external"]["url"], timeout=3.0)
                if resp.status_code == 200:
                    results["external"]["status"] = "ok"
                    results["overall"] = "ok"
                else:
                    results["external"]["status"] = f"error_{resp.status_code}"
            except Exception as e:
                results["external"]["status"] = "failed"
                results["external"]["error"] = str(e)

    return results


@router.post("/restart")
async def restart_server(current_user: User = Depends(get_current_user)):
    """
    Restart the server process.
    This uses os.execv to replace the current process with a new one.
    """
    import os
    import sys
    import time

    log_service.info("Server restart requested by admin")

    def perform_restart():
        time.sleep(1)  # Wait for response to send
        os.execv(sys.executable, ["python"] + sys.argv)

    import threading

    threading.Thread(target=perform_restart).start()

    return {"message": "Server is restarting... This may take a few seconds."}


@router.post("/import")
async def import_library(
    data: Dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import library from JSON export"""
    # TODO: Implement library import
    raise HTTPException(status_code=501, detail="Import not yet implemented")
