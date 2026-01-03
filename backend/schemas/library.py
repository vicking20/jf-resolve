"""Library schemas"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class LibraryItemCreate(BaseModel):
    """Schema for adding item to library"""

    tmdb_id: int
    media_type: str = Field(..., pattern="^(movie|tv)$")
    quality_versions: List[str] = ["1080p"]
    added_via: str = Field("discover", pattern="^(discover|search)$")


class LibraryItemResponse(BaseModel):
    """Library item response"""

    id: int
    tmdb_id: int
    imdb_id: Optional[str] = None
    media_type: str
    title: str
    year: Optional[int] = None
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    overview: Optional[str] = None
    total_seasons: Optional[int] = None
    total_episodes: Optional[int] = None
    folder_path: str
    quality_versions: Optional[str] = None
    added_via: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LibraryItemList(BaseModel):
    """Paginated library items"""

    items: List[LibraryItemResponse]
    total: int
    page: int
    limit: int
