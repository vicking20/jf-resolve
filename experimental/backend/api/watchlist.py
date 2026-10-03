"""Watchlist API endpoints"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.session import get_current_user
from ..database import get_db
from ..models.media_item import MediaItem
from ..models.user import User
from ..models.watchlist import WatchlistItem
from ..services.media_resolver import MediaResolver
from .media_unified import get_media_resolver

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


# Schemas
class WatchlistAdd(BaseModel):
    """Add to watchlist"""

    identifier: Optional[str] = None  # Unified identifier (UUID or provider:id)
    media_type: Optional[str] = None

    # Transient item metadata (TMDB)
    tmdb_id: Optional[int] = None
    title: Optional[str] = None
    year: Optional[int] = None
    poster_path: Optional[str] = None
    overview: Optional[str] = None


@router.post("/")
async def add_to_watchlist(
    data: WatchlistAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """
    Add media to watchlist.
    If item is transient, it will be persisted first.
    """
    # 1. Resolve item (Hybrid)
    item_dict = None

    # CASE A: Identifier provided
    if data.identifier:
        item_dict = await resolver.resolve(db, data.identifier, data.media_type)
        if not item_dict:
            raise HTTPException(status_code=404, detail="Media not found")

    # CASE B: No identifier, but TMDB metadata provided (Transient JIT)
    elif data.tmdb_id and data.media_type:
        item_dict = {
            "uuid": None,
            "media_type": data.media_type,
            "title": data.title or "Unknown",
            "year": data.year,
            "tmdb_id": data.tmdb_id,
            "image_paths": {"poster": data.poster_path},
            "metadata": {
                "overview": data.overview,
                "title": data.title,
                "year": data.year,
                # We could fetch more from TMDB here if needed, but this is enough for JIT
            },
            "is_persisted": False,
        }
    else:
        raise HTTPException(
            status_code=400, detail="Either identifier or tmdb_id+media_type required"
        )

    # 2. Persist if it's transient
    media_item = None
    if not item_dict.get("is_persisted"):
        # We need to ensure we have a full enough object for persist()
        # resolver.persist() handles logic to check if it already exists by external ID
        media_item = await resolver.persist(db, item_dict)
    else:
        # Fetch existing MediaItem
        result = await db.execute(
            select(MediaItem).where(MediaItem.uuid == item_dict["uuid"])
        )
        media_item = result.scalar_one_or_none()

    if not media_item:
        raise HTTPException(status_code=500, detail="Failed to persist media item")

    # 3. Add to WatchlistItem table
    # Check if already in watchlist
    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id,
            WatchlistItem.media_uuid == media_item.uuid,
        )
    )
    existing = result.scalar_one_or_none()

    if not existing:
        new_entry = WatchlistItem(user_id=current_user.id, media_uuid=media_item.uuid)
        db.add(new_entry)
        await db.commit()
        return {"message": "Added to watchlist", "media_uuid": media_item.uuid}

    return {"message": "Already in watchlist", "media_uuid": media_item.uuid}


@router.get("/")
async def get_watchlist(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get user's watchlist
    """
    result = await db.execute(
        select(WatchlistItem, MediaItem)
        .join(MediaItem, WatchlistItem.media_uuid == MediaItem.uuid)
        .options(selectinload(MediaItem.external_ids))
        .where(WatchlistItem.user_id == current_user.id)
        .order_by(desc(WatchlistItem.added_at))
    )
    rows = result.all()

    response_items = []
    for wl_item, m_item in rows:
        item_dict = m_item.to_dict()
        item_dict["added_at"] = wl_item.added_at.isoformat()
        response_items.append(item_dict)

    return response_items


@router.get("/check/{identifier}")
async def check_in_watchlist(
    identifier: str,
    media_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """
    Check if media is in user's watchlist
    """
    # We need to resolve the UUID first to check the WatchlistItem table
    item_dict = await resolver.resolve(db, identifier, media_type)
    if not item_dict or not item_dict.get("uuid"):
        return {"in_watchlist": False}

    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id,
            WatchlistItem.media_uuid == item_dict["uuid"],
        )
    )
    existing = result.scalar_one_or_none()

    return {"in_watchlist": existing is not None, "media_uuid": item_dict["uuid"]}


@router.delete("/{identifier}")
async def remove_from_watchlist(
    identifier: str,
    media_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """
    Remove from watchlist
    """
    item_dict = await resolver.resolve(db, identifier, media_type)
    if not item_dict or not item_dict.get("uuid"):
        raise HTTPException(status_code=404, detail="Not in watchlist")

    result = await db.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == current_user.id,
            WatchlistItem.media_uuid == item_dict["uuid"],
        )
    )
    entry = result.scalar_one_or_none()

    if entry:
        await db.delete(entry)
        await db.commit()
        return {"message": "Removed from watchlist"}

    raise HTTPException(status_code=404, detail="Not in watchlist")
