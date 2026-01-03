"""Search API routes"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user
from ..database import get_db
from ..models.user import User
from ..schemas.search import MediaItem, SearchResult
from ..services.library_service import LibraryService
from ..services.settings_manager import SettingsManager
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/search", tags=["search"])


async def get_tmdb_service(db: AsyncSession = Depends(get_db)) -> TMDBService:
    """Get TMDB service instance"""
    settings = SettingsManager(db)
    api_key = await settings.get("tmdb_api_key")

    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    return TMDBService(api_key)


async def check_library_status(
    items: List[Dict], db: AsyncSession, tmdb: TMDBService
) -> List[MediaItem]:
    """Add in_library flag to media items"""
    settings = SettingsManager(db)
    library = LibraryService(db, tmdb, settings)

    result = []
    for item in items:
        # Parse item
        parsed = tmdb.parse_media_item(item)

        # Check if in library
        in_library = await library.is_in_library(
            parsed["tmdb_id"], parsed["media_type"]
        )
        parsed["in_library"] = in_library

        result.append(MediaItem(**parsed))

    return result


@router.get("/multi", response_model=SearchResult)
async def search_multi(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search for both movies and TV shows"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.search_multi(query, page)

        # Filter to only movies and TV
        results = [
            item
            for item in data.get("results", [])
            if item.get("media_type") in ["movie", "tv"]
        ]

        items = await check_library_status(results, db, tmdb)

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=len(items),
        )
    finally:
        await tmdb.close()


@router.get("/movies", response_model=SearchResult)
async def search_movies(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search for movies only"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.search_movies(query, page)
        items = await check_library_status(data.get("results", []), db, tmdb)

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/tv", response_model=SearchResult)
async def search_tv(
    query: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Search for TV shows only"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.search_tv(query, page)

        results = data.get("results", [])
        for item in results:
            item["media_type"] = "tv"

        items = await check_library_status(results, db, tmdb)

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()
