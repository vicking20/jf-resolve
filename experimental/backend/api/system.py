"""System API routes (health, logs, tasks)"""

from datetime import datetime
from pathlib import Path
from typing import Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings as app_settings
from ..core.dependencies import get_current_admin_user, get_current_user
from ..database import get_db
from ..models.user import User
from ..services.library_service import LibraryService
from ..services.log_service import log_service
from ..services.metadata_service import metadata_service
from ..services.populate_service import PopulateService
from ..services.settings_manager import SettingsManager
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/system", tags=["system"])






@router.post("/tasks/cleanup-cache")
async def cleanup_cache_manual(
    days: int = Query(30, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Manually clean expired cache items, protecting library items.
    """
    from sqlalchemy import select
    from ..models.media_external_id import MediaExternalId

    log_service.info(f"Manual cache cleanup requested (days={days})")
    try:
        # Collect protected IDs (Any TMDB ID in our database should be protected if it's in library/watchlist)
        protected_ids = set()
        
        result = await db.execute(
            select(MediaExternalId.external_id)
            .where(MediaExternalId.provider == "tmdb")
        )
        protected_ids.update([int(tid) for tid in result.scalars().all() if tid.isdigit()])

        count = await metadata_service.clear_expired_cache(
            max_age_days=days, 
            protected_ids=protected_ids
        )
        log_service.info(f"Manual cache cleanup finished. Removed {count} items.")
        return {"message": f"Cache cleanup complete", "removed_count": count}
    except Exception as e:
        log_service.error(f"Manual cache cleanup failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))





@router.post("/tasks/catalogue-sync")
async def run_catalogue_sync_manual(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Manually trigger the media catalogue sync from TMDB"""
    from ..services.catalogue_service import CatalogueService
    
    log_service.info("Manual catalogue sync requested")
    try:
        cat_service = CatalogueService(db)
        await cat_service.initialize_default_catalogues()
        success = await cat_service.sync_all_catalogues()
        if not success:
            return {"message": "Catalogue sync skipped: API key not configured"}
        return {"message": "Catalogue sync complete"}
    except Exception as e:
        log_service.error(f"Manual catalogue sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/resources")
async def get_system_resources(current_user: User = Depends(get_current_admin_user)):
    """Get current system resource usage and JF-Resolve process usage"""
    import psutil
    import os
    
    # Get JF-Resolve process
    current_process = psutil.Process(os.getpid())
    
    # Get JF-Resolve process metrics
    process_cpu = current_process.cpu_percent(interval=1)
    process_memory_info = current_process.memory_info()
    process_memory_mb = process_memory_info.rss / (1024**2)  # Resident Set Size in MB
    process_memory_percent = current_process.memory_percent()
    process_num_threads = current_process.num_threads()
    
    # Get system-wide metrics for comparison
    system_cpu = psutil.cpu_percent(interval=1)
    system_memory = psutil.virtual_memory()
    system_memory_used_gb = system_memory.used / (1024**3)
    system_memory_total_gb = system_memory.total / (1024**3)
    
    # Get disk usage (primary disk)
    disk = psutil.disk_usage('/')
    disk_used_gb = disk.used / (1024**3)
    disk_total_gb = disk.total / (1024**3)
    
    return {
        "process": {
            "cpu_percent": round(process_cpu, 2),
            "memory_mb": round(process_memory_mb, 2),
            "memory_percent": round(process_memory_percent, 2),
            "num_threads": process_num_threads,
            "pid": current_process.pid
        },
        "system": {
            "cpu_percent": system_cpu,
            "memory": {
                "used_gb": round(system_memory_used_gb, 2),
                "total_gb": round(system_memory_total_gb, 2)
            },
            "disk": {
                "used_gb": round(disk_used_gb, 2),
                "total_gb": round(disk_total_gb, 2),
                "percent": disk.percent
            }
        }
    }


@router.get("/status")
async def system_status():
    """Basic system status check"""
    return {"status": "ok", "version": "2.0.0"}


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
            # Normalize URL for health check
            check_url = manifest_url
            if check_url.startswith("stremio://"):
                check_url = check_url.replace("stremio://", "https://", 1)
            check_url = check_url.replace("/manifest.json", "").rstrip("/")

            async with httpx.AsyncClient() as client:
                response = await client.get(f"{check_url}/manifest.json", timeout=5.0)
                response.raise_for_status()
            health["stremio"] = {"status": "ok", "message": "Manifest accessible"}
        except Exception as e:
            health["stremio"] = {"status": "error", "message": str(e)}
            health["overall"] = "degraded"
    else:
        health["stremio"] = {
            "status": "not_configured",
            "message": "Manifest URL not set",
        }
        health["overall"] = "degraded"

    return health


@router.get("/logs")
async def get_logs(
    type: str = Query("error", pattern="^(error|info|stream)$"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_admin_user),
):
    """Get recent log entries"""
    try:
        logs = log_service.get_logs(type, limit, offset)
        return {"log_type": type, "lines": logs, "count": len(logs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {str(e)}")


@router.get("/logs/download")
async def download_logs(
    type: str = Query("error", pattern="^(error|info|stream)$"),
    current_user: User = Depends(get_current_admin_user),
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Export library as JSON"""
    from ..models.media_item import MediaItem, MediaType

    export_data = []

    # Export all persisted items
    result = await db.execute(
        select(MediaItem).where(MediaItem.is_persisted == True)
    )
    items = result.scalars().all()
    
    for item in items:
        # Get TMDB ID if available
        tmdb_id = item.get_external_id("tmdb")
        imdb_id = item.get_external_id("imdb")
        
        export_data.append(
            {
                "uuid": item.uuid,
                "tmdb_id": int(tmdb_id) if tmdb_id and tmdb_id.isdigit() else None,
                "imdb_id": imdb_id,
                "media_type": item.media_type.value,
                "title": item.title,
                "year": item.year,
            }
        )

    return {
        "version": "2.0.0",
        "exported_at": datetime.utcnow().isoformat(),
        "items": export_data,
        "count": len(export_data),
    }


@router.post("/restart")
async def restart_server(current_user: User = Depends(get_current_admin_user)):
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



