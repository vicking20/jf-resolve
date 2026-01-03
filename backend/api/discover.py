"""Discovery API routes (trending, popular content)"""

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

router = APIRouter(prefix="/api/discover", tags=["discover"])


async def get_tmdb_service(db: AsyncSession = Depends(get_db)) -> TMDBService:
    """Get TMDB service instance"""
    settings = SettingsManager(db)
    api_key = await settings.get("tmdb_api_key")

    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    return TMDBService(api_key)


async def check_library_status(
    items: List[Dict], db: AsyncSession, media_type: str = None
) -> List[MediaItem]:
    """Add in_library flag to media items"""
    settings = SettingsManager(db)
    tmdb = await get_tmdb_service(db)
    library = LibraryService(db, tmdb, settings)

    result = []
    for item in items:
        parsed = tmdb.parse_media_item(item, media_type)

        # Check if in library
        in_library = await library.is_in_library(
            parsed["tmdb_id"], parsed["media_type"]
        )
        parsed["in_library"] = in_library

        result.append(MediaItem(**parsed))

    return result


@router.get("/trending/movies", response_model=SearchResult)
async def trending_movies(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get trending movies"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_trending("movie", "week", page)
        items = await check_library_status(data.get("results", []), db, "movie")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/trending/tv", response_model=SearchResult)
async def trending_tv(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get trending TV shows"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_trending("tv", "week", page)
        items = await check_library_status(data.get("results", []), db, "tv")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/popular/movies", response_model=SearchResult)
async def popular_movies(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get popular movies"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_popular("movie", page)
        items = await check_library_status(data.get("results", []), db, "movie")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/popular/tv", response_model=SearchResult)
async def popular_tv(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get popular TV shows"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_popular("tv", page)
        items = await check_library_status(data.get("results", []), db, "tv")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/top-rated/movies", response_model=SearchResult)
async def top_rated_movies(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get top rated movies"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_top_rated("movie", page)
        items = await check_library_status(data.get("results", []), db, "movie")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()


@router.get("/top-rated/tv", response_model=SearchResult)
async def top_rated_tv(
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get top rated TV shows"""
    tmdb = await get_tmdb_service(db)

    try:
        data = await tmdb.get_top_rated("tv", page)
        items = await check_library_status(data.get("results", []), db, "tv")

        return SearchResult(
            results=items,
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 1),
            total_results=data.get("total_results", 0),
        )
    finally:
        await tmdb.close()
