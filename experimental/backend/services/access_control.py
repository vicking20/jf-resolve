"""Per-user content rating enforcement for playback and detail endpoints.

Discover and search only hide restricted titles; these helpers make the same
rule hold when a restricted user opens a title directly (link, library,
catalogue, watchlist) or asks for its sources.
"""

from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.media_item import MediaItem
from ..models.user import User
from ..models.user_profile import ContentRating, UserProfile
from ..utils.content_rating import is_content_allowed


async def get_rating_limit(db: AsyncSession, user: User) -> Optional[ContentRating]:
    """Return the user's max rating, or None when the user is unrestricted."""
    if user.is_superuser:
        return None
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if not profile or not profile.enable_parental_controls:
        return None
    if profile.max_content_rating in (None, ContentRating.ALL):
        return None
    return profile.max_content_rating


async def _rating_for(db: AsyncSession, item: Dict[str, Any]) -> tuple[Optional[str], str]:
    """Content rating and rating scheme ("movie"/"tv") for a resolved item.

    Episodes carry no rating of their own, so they inherit the show's.
    """
    media_type = item.get("media_type")
    if hasattr(media_type, "value"):
        media_type = media_type.value
    rating = item.get("content_rating")
    parent_id = item.get("parent_id")
    if not rating and parent_id:
        parent = await db.get(MediaItem, parent_id)
        if parent:
            rating = (parent.metadata_blob or {}).get("content_rating")
            media_type = parent.media_type.value
    return rating, "movie" if media_type == "movie" else "tv"


async def ensure_item_allowed(db: AsyncSession, user: User, item: Dict[str, Any]) -> None:
    """Raise 403 when a restricted user may not see or play this item.

    Unrated titles are treated as the most restrictive level, matching
    discover/search filtering.
    """
    limit = await get_rating_limit(db, user)
    if limit is None:
        return
    rating, scheme = await _rating_for(db, item)
    if not is_content_allowed(rating, scheme, limit):
        raise HTTPException(status_code=403, detail="This title is restricted for your account")


async def ensure_media_uuid_allowed(db: AsyncSession, user: User, media_uuid: str) -> None:
    """ensure_item_allowed for a persisted item referenced by UUID."""
    if await get_rating_limit(db, user) is None:
        return
    item = await db.get(MediaItem, media_uuid)
    if item is None:
        raise HTTPException(status_code=404, detail="Media not found")
    await ensure_item_allowed(
        db,
        user,
        {
            "media_type": item.media_type,
            "content_rating": (item.metadata_blob or {}).get("content_rating"),
            "parent_id": item.parent_id,
        },
    )
