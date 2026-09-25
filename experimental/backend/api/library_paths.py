"""Library path management API routes"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..models.library_path import LibraryPath
from ..models.user import User
from ..services.log_service import log_service

router = APIRouter(prefix="/api/library-paths", tags=["library-paths"])


# Schemas
class LibraryPathCreate(BaseModel):
    """Create library path request"""

    name: str
    path: str
    media_type: str  # "movie", "tv", or "video"
    scan_interval: int = 0  # Minutes


class LibraryPathUpdate(BaseModel):
    """Update library path request"""

    name: str = None
    path: str = None
    enabled: bool = None
    scan_interval: int = None


class LibraryPathResponse(BaseModel):
    """Library path response"""

    id: int
    name: str
    path: str
    media_type: str
    enabled: bool
    scan_interval: int
    last_scanned: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


@router.get("/")
async def list_library_paths(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Get all library paths

    """

    result = await db.execute(
        select(LibraryPath).order_by(LibraryPath.created_at.desc())
    )
    paths = result.scalars().all()

    return [path.to_dict() for path in paths]


@router.post("/")
async def create_library_path(
    data: LibraryPathCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Create a new library path

    """

    # Validate media type
    if data.media_type not in ["movie", "tv", "video"]:
        raise HTTPException(
            status_code=400, detail="media_type must be 'movie', 'tv', or 'video'"
        )

    # Check if path already exists
    result = await db.execute(select(LibraryPath).where(LibraryPath.path == data.path))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Library path already exists")

    # Create library path
    from ..models.media import MediaType

    library_path = LibraryPath(
        name=data.name,
        path=data.path,
        media_type=MediaType(data.media_type),
        scan_interval=data.scan_interval,
        created_by_user_id=current_user.id,
    )

    db.add(library_path)
    await db.commit()
    await db.refresh(library_path)

    # Trigger initial scan if appropriate
    # Import here to avoid circular dependency
    import asyncio
    from ..services.library.scanner_coordinator import create_scanner_coordinator
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService
    
    try:
        settings = SettingsManager(db)
        tmdb_key = await settings.get("tmdb_api_key")
        tmdb = TMDBService(tmdb_key) if tmdb_key else None
        
        coordinator = await create_scanner_coordinator(db, tmdb)
        mode = await coordinator.mode_manager.get_effective_mode(library_path)
        
        # Trigger scan for:
        # 1. Active mode (all media types)
        # 2. Video/video_series (always scanned regardless of mode)
        should_scan = (
            mode == "active" or 
            library_path.media_type.value in ["video", "video_series"]
        )
        
        if should_scan:
            log_service.info(f"Triggering initial scan for new library path: {library_path.path}")
            # Run scan in background
            asyncio.create_task(coordinator.run_scan(library_path, force_rescan=True))
        
        if tmdb:
            await tmdb.close()
    except Exception as e:
        log_service.error(f"Failed to trigger initial scan: {e}")

    return library_path.to_dict()


@router.get("/{path_id}")
async def get_library_path(
    path_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Get specific library path

    """

    result = await db.execute(select(LibraryPath).where(LibraryPath.id == path_id))
    path = result.scalar_one_or_none()

    if not path:
        raise HTTPException(status_code=404, detail="Library path not found")

    return path.to_dict()


@router.put("/{path_id}")
async def update_library_path(
    path_id: int,
    data: LibraryPathUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Update library path

    """

    result = await db.execute(select(LibraryPath).where(LibraryPath.id == path_id))
    path = result.scalar_one_or_none()

    if not path:
        raise HTTPException(status_code=404, detail="Library path not found")

    # Update fields
    if data.name is not None:
        path.name = data.name
    if data.path is not None:
        path.path = data.path
    if data.enabled is not None:
        path.enabled = data.enabled
    if data.scan_interval is not None:
        path.scan_interval = data.scan_interval

    await db.commit()
    await db.refresh(path)

    return path.to_dict()


@router.delete("/{path_id}")
async def delete_library_path(
    path_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """
    Delete library path and associated media sources
    """
    from sqlalchemy import delete

    from ..models.media import LibraryScanJob, MediaSourceModel
    from ..services.library.cleanup_service import CleanupService
    from ..services.log_service import log_service

    log_service.info(f"Deleting library path {path_id}")

    result = await db.execute(select(LibraryPath).where(LibraryPath.id == path_id))
    path = result.scalar_one_or_none()

    if not path:
        raise HTTPException(status_code=404, detail="Library path not found")

    # Get all media UUIDs associated with this library path before deletion
    sources_result = await db.execute(
        select(MediaSourceModel.media_uuid).where(
            MediaSourceModel.library_path_id == path_id
        )
    )
    media_uuids = set(sources_result.scalars().all())

    # Delete dependent records first to avoid FK constraints
    await db.execute(
        delete(LibraryScanJob).where(LibraryScanJob.library_path_id == path_id)
    )

    # Delete media sources associated with this path
    await db.execute(
        delete(MediaSourceModel).where(MediaSourceModel.library_path_id == path_id)
    )

    # Delete the path itself
    await db.delete(path)
    await db.commit()

    # Cleanup orphaned media items (items with no remaining sources)
    if media_uuids:
        cleanup_service = CleanupService(db)
        await cleanup_service._cleanup_orphaned_items(media_uuids)

    log_service.info(f"Library path {path_id} deleted successfully")
    return {"message": "Library path deleted successfully"}
