"""Library management API routes"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user, get_current_user
from ..database import get_db
from ..models.media_item import MediaItem, MediaType
from ..models.user import User
from .media_unified import get_media_resolver
from ..services.media_resolver import MediaResolver
from ..services.library_service import LibraryService
from ..services.log_service import log_service
from ..services.settings_manager import SettingsManager
from ..services.strm_service import StrmService

router = APIRouter(prefix="/api/library", tags=["library"])


# Schemas
class MediaCreate(BaseModel):
    identifier: Optional[str] = None  # Unified identifier (UUID or tmdb:id)
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    media_type: Optional[str] = None


class MediaListResponse(BaseModel):
    items: List[dict]
    total: int
    page: int
    limit: int


@router.post("/")
@router.post("/items")
@router.post("/add")
async def add_to_library(
    item_data: MediaCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver)
):
    """
    Add item to library (persist transient item)
    """
    identifier = item_data.identifier
    if not identifier:
        if item_data.tmdb_id:
            identifier = f"tmdb:{item_data.tmdb_id}"
        elif item_data.imdb_id:
            identifier = f"imdb:{item_data.imdb_id}"
        else:
            raise HTTPException(status_code=400, detail="Either identifier, tmdb_id, or imdb_id required")

    # 1. Resolve item (Hybrid)
    item_dict = await resolver.resolve(db, identifier, item_data.media_type)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Media not found")

    if item_dict.get("is_persisted"):
        strm = await _write_strm_if_enabled(db, resolver, item_dict, current_user)
        return {"message": "Already in library", "uuid": item_dict["uuid"], "strm": strm}

    # 2. Persist
    try:
        media_item = await resolver.persist(db, item_dict, is_persisted=True)
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add to library: {str(e)}")
    strm = await _write_strm_if_enabled(db, resolver, item_dict, current_user)
    return {"message": "Added to library", "uuid": media_item.uuid, "strm": strm}


async def _write_strm_if_enabled(
    db: AsyncSession, resolver: MediaResolver, item: dict, user: User
) -> Optional[dict]:
    """Mirror an admin's library add into the Jellyfin STRM folders.

    Non-admins go through /api/requests instead, so an admin decides what
    lands in the shared Jellyfin library.
    """
    if not user.is_superuser or item.get("media_type") not in ("movie", "tv") or not item.get("tmdb_id"):
        return None
    strm = StrmService(SettingsManager(db), resolver.tmdb)
    if not await strm.is_enabled():
        return None
    try:
        return await strm.generate(int(item["tmdb_id"]), item["media_type"])
    except Exception as e:
        log_service.error(f"STRM generation failed for {item.get('title')}: {e}")
        return {"error": str(e)}


@router.get("/")
@router.get("/items")
async def list_library_items(
    type: str = Query("all", pattern="^(all|movie|tv)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List library items with pagination (persisted only)
    """
    from sqlalchemy.orm import selectinload
    stmt = select(MediaItem).options(selectinload(MediaItem.external_ids)).where(MediaItem.is_persisted == True)
    
    if type != "all":
        stmt = stmt.where(MediaItem.media_type == MediaType(type))
        
    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Paginate and fetch
    stmt = stmt.order_by(MediaItem.title).offset((page - 1) * limit).limit(limit)
    result = await db.execute(stmt)
    items = result.scalars().all()

    return MediaListResponse(
        items=[item.to_dict() for item in items],
        total=total,
        page=page,
        limit=limit
    )


@router.get("/{identifier}")
async def get_library_item(
    identifier: str,
    media_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver)
):
    """Get specific item from library"""
    item = await resolver.resolve(db, identifier, media_type)
    if not item or not item.get("is_persisted"):
        raise HTTPException(status_code=404, detail="Item not found in library")

    return item


@router.delete("/{identifier}")
async def remove_item_from_library(
    identifier: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Remove item from library (admin only; the library is shared by all users)"""
    settings = SettingsManager(db)
    from ..services.tmdb_service import TMDBService
    api_key = await settings.get("tmdb_api_key")
    tmdb = TMDBService(api_key or "")
    
    library = LibraryService(db, tmdb, settings)
    try:
        item = await MediaResolver(tmdb).resolve(db, identifier)
        await library.remove_from_library(identifier)
        strm = StrmService(settings, tmdb)
        if item and item.get("media_type") in ("movie", "tv") and await strm.is_enabled():
            try:
                await strm.remove(item["media_type"], item["title"], item.get("year"))
            except ValueError as e:
                log_service.error(f"STRM removal skipped: {e}")
        return {"message": "Removed from library"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        await tmdb.close()


@router.post("/{media_uuid}/refresh")
async def refresh_media_metadata(
    media_uuid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refresh media metadata from TMDB"""
    settings = SettingsManager(db)
    from ..services.tmdb_service import TMDBService
    api_key = await settings.get("tmdb_api_key")
    tmdb = TMDBService(api_key or "")
    
    library = LibraryService(db, tmdb, settings)
    try:
        result = await library.refresh_metadata(media_uuid)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        await tmdb.close()


@router.post("/purge")
async def purge_library(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Delete all persisted library items and their sources"""
    from sqlalchemy import delete
    from ..models.media import MediaSourceModel
    from ..services.log_service import log_service

    log_service.info("Purging all library items")

    # Count items before deletion
    count_result = await db.execute(
        select(func.count()).select_from(MediaItem).where(MediaItem.is_persisted == True)
    )
    item_count = count_result.scalar() or 0

    # Delete all media sources
    await db.execute(delete(MediaSourceModel))

    # Delete all persisted media items
    await db.execute(delete(MediaItem).where(MediaItem.is_persisted == True))

    await db.commit()

    log_service.info(f"Purged {item_count} library items")
    return {"message": f"Purged {item_count} items from library", "count": item_count}
