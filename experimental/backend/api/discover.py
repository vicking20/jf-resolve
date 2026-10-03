from typing import Tuple

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_user_content_filter, get_tmdb_service
from ..core.database import get_db
from ..models.user import User
from ..api.auth import get_current_user
from ..services.discover_service import DiscoverService
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/discover", tags=["discover"])

@router.get("/{category}")
async def get_discover_content(
    category: str,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tmdb: TMDBService = Depends(get_tmdb_service),
    content_filter: Tuple = Depends(get_user_content_filter),
):
    """
    Get discover content for a category (e.g. popular_movies, trending_tv).
    Item are cached in DB and filtered by parental controls.
    """
    valid_categories = ["popular_movies", "popular_tv", "trending_movies", "trending_tv"]
    if category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of {valid_categories}")

    max_rating, enabled, _ = content_filter
    
    service = DiscoverService(db, tmdb)
    
    results = await service.get_discover_results(
        category=category,
        page=page,
        max_rating=max_rating,
        parental_controls=enabled
    )
    
    return {
        "items": results,
        "page": page,
        "has_more": len(results) > 0 # Simple check, ideally would check total pages from TMDB but this suffices for infinite scroll
    }
