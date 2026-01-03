"""Search and discovery schemas"""

from typing import List, Optional

from pydantic import BaseModel


class MediaItem(BaseModel):
    """Media item from TMDB"""

    tmdb_id: int
    imdb_id: Optional[str] = None
    media_type: str  # 'movie' or 'tv'
    title: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    release_date: Optional[str] = None
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    overview: Optional[str] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    popularity: Optional[float] = None
    genre_ids: List[int] = []
    origin_country: List[str] = []
    in_library: bool = False


class SearchResult(BaseModel):
    """Search results"""

    results: List[MediaItem]
    page: int
    total_pages: int
    total_results: int
