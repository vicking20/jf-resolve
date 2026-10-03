"""Media schemas for API requests/responses"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class MediaResponse(BaseModel):
    """Media response"""

    id: int
    title: str
    original_title: Optional[str] = None
    media_type: str
    year: Optional[int] = None
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    tmdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_title: Optional[str] = None
    total_seasons: Optional[int] = None
    total_episodes: Optional[int] = None
    last_season_checked: Optional[int] = 0
    last_episode_checked: Optional[int] = 0
    is_user_content: bool
    folder_path: Optional[str] = None
    quality_versions: Optional[str] = None
    vote_average: Optional[float] = None
    content_rating: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class MediaCreate(BaseModel):
    """Create media request"""

    tmdb_id: int
    media_type: str  # "movie" or "tv"


class MediaList(BaseModel):
    """Paginated media list"""

    items: List[MediaResponse]
    total: int
    page: int
    limit: int
