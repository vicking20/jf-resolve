"""Search and discovery schemas"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


class MediaItem(BaseModel):
    """Unified Media item (Transient or Persisted)"""

    uuid: Optional[str] = None
    tmdb_id: Optional[Union[int, str]] = None
    imdb_id: Optional[str] = None
    media_type: str
    title: str
    original_title: Optional[str] = None
    year: Optional[int] = None
    
    image_paths: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}
    
    in_library: bool = False
    is_persisted: bool = False
    is_local: bool = False

    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    overview: Optional[str] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    popularity: Optional[float] = None
    content_rating: Optional[str] = None
    director: Optional[str] = None
    cast: List[str] = []
    genre_names: List[str] = []
    runtime: Optional[int] = None
    number_of_seasons: Optional[int] = None
    number_of_episodes: Optional[int] = None
    collection: Optional[Union[Dict[str, Any], str]] = None


class SearchResult(BaseModel):
    """Search results"""

    results: List[MediaItem]
    page: int
    total_pages: int
    total_results: int
