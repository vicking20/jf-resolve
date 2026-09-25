"""Search API routes"""

from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user
from ..core.dependencies import get_user_content_filter
from ..database import get_db
from ..models.user import User
from ..models.user_profile import ContentRating
from ..schemas.search import MediaItem, SearchResult
from ..services.library_service import LibraryService
from ..services.settings_manager import SettingsManager
from ..services.tmdb_service import TMDBService
from ..utils.content_rating import (
    get_us_movie_certification,
    get_us_tv_rating,
    is_content_allowed,
)

router = APIRouter(prefix="/api/search", tags=["search"])


async def get_tmdb_service(db: AsyncSession = Depends(get_db)) -> TMDBService:
    """Get TMDB service instance"""
    settings = SettingsManager(db)
    api_key = await settings.get("tmdb_api_key")

    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    return TMDBService(api_key)


async def check_library_status(
    items: List[Dict],
    db: AsyncSession,
    tmdb: TMDBService,
    content_filter: Optional[Tuple[ContentRating, bool, Optional[List[str]]]] = None,
) -> List[MediaItem]:
    """Add in_library flag to media items and filter by content rating"""
    # Convert TMDB results to parsed format
    parsed_items = []
    for item in items:
        parsed = tmdb.parse_media_item(item)
        parsed_items.append(parsed)

    # 1. Bulk check library status via external IDs
    tmdb_ids = [str(p["tmdb_id"]) for p in parsed_items if p.get("tmdb_id")]

    from ..models.media_external_id import MediaExternalId

    result = await db.execute(
        select(MediaExternalId.external_id, MediaExternalId.media_uuid).where(
            MediaExternalId.provider == "tmdb",
            MediaExternalId.external_id.in_(tmdb_ids),
        )
    )
    mapping = {ext_id: uuid for ext_id, uuid in result.all()}

    final_results = []
    for parsed in parsed_items:
        # Check if in library
        tmdb_id_str = str(parsed.get("tmdb_id"))
        media_uuid = mapping.get(tmdb_id_str)

        parsed["in_library"] = media_uuid is not None
        if media_uuid:
            parsed["uuid"] = media_uuid  # Return UUID for frontend consistency

        # 2. Parental Control filtering (similar to discover.py)
        if content_filter and content_filter[1]:
            max_rating, enabled, _ = content_filter
            try:
                if parsed["media_type"] == "movie":
                    details = await tmdb.get_movie_details(parsed["tmdb_id"])
                    content_rating = get_us_movie_certification(details)
                else:  # tv
                    details = await tmdb.get_tv_details(parsed["tmdb_id"])
                    content_rating = get_us_tv_rating(details)

                parsed["content_rating"] = content_rating
                if not is_content_allowed(
                    content_rating, parsed["media_type"], max_rating
                ):
                    continue
            except Exception:
                continue

        final_results.append(MediaItem(**parsed))

    return final_results


@router.get("/")
async def search_multi(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    content_filter: Tuple = Depends(get_user_content_filter),
):
    """Search for both movies and TV shows (local + TMDB)"""
    tmdb = await get_tmdb_service(db)

    try:
        # Always search TMDB (adult content filtered via ratings)
        data = await tmdb.search_multi(query, page, include_adult=False)
        results = [
            item
            for item in data.get("results", [])
            if item.get("media_type") in ["movie", "tv"]
        ]

        # Search local media (Unified MediaItem table)
        local_results = []
        from sqlalchemy import or_, select

        from ..models.media_item import MediaItem as MediaItemModel
        from ..models.user_profile import ContentRating

        search_term = f"%{query}%"
        max_rating, enabled, _ = content_filter

        # Search persisted media items
        from sqlalchemy import and_
        from sqlalchemy.orm import selectinload

        media_stmt = (
            select(MediaItemModel)
            .options(selectinload(MediaItemModel.external_ids))
            .where(
                or_(
                    MediaItemModel.title.ilike(search_term),
                    MediaItemModel.original_title.ilike(search_term),
                )
            )
            .limit(20)
        )
        media_result = await db.execute(media_stmt)
        local_media_items = media_result.scalars().all()

        for item in local_media_items:
            data = item.to_dict()
            # Flag item's library status based on persistence
            data["in_library"] = data.get("is_persisted", False)
            # Set is_local for user content types
            data["is_local"] = data.get("media_type") in ("video", "video_series")
            local_results.append(MediaItem(**data))

        # Merge results: local first, then TMDB (avoiding duplicates)
        tmdb_items = await check_library_status(results, db, tmdb, content_filter)

        # Remove TMDB items that are already in local results (check UUID via mapping)
        # For simplicity, we can also check TMDB ID if we fetch it for local items
        # But since we use UUID for local, we'll check the in_library flag
        unique_tmdb_items = [item for item in tmdb_items if not item.in_library]

        final_results = local_results + unique_tmdb_items

        return SearchResult(
            results=final_results,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=len(final_results),
        )
    finally:
        await tmdb.close()
