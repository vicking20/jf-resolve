"""TMDB API service"""

from typing import Dict, List, Optional

import httpx

from .log_service import log_service


class TMDBService:
    """The Movie Database API integration"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/w500"
        self.client = httpx.AsyncClient(timeout=30.0)

    async def _request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make request to TMDB API"""
        if params is None:
            params = {}

        params["api_key"] = self.api_key

        url = f"{self.base_url}/{endpoint}"

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            log_service.error(f"TMDB API error: {e}")
            raise

    async def search_movies(self, query: str, page: int = 1) -> Dict:
        """Search for movies"""
        return await self._request(
            "search/movie", {"query": query, "page": page, "include_adult": False}
        )

    async def search_tv(self, query: str, page: int = 1) -> Dict:
        """Search for TV shows"""
        return await self._request(
            "search/tv", {"query": query, "page": page, "include_adult": False}
        )

    async def search_multi(self, query: str, page: int = 1) -> Dict:
        """Search for both movies and TV shows"""
        return await self._request(
            "search/multi", {"query": query, "page": page, "include_adult": False}
        )

    async def get_trending(
        self, media_type: str, time_window: str = "week", page: int = 1
    ) -> Dict:
        """Get trending content (media_type: 'movie' or 'tv')"""
        return await self._request(
            f"trending/{media_type}/{time_window}", {"page": page}
        )

    async def get_popular(self, media_type: str, page: int = 1) -> Dict:
        """Get popular content"""
        return await self._request(f"{media_type}/popular", {"page": page})

    async def get_top_rated(self, media_type: str, page: int = 1) -> Dict:
        """Get top rated content"""
        return await self._request(f"{media_type}/top_rated", {"page": page})

    async def get_movie_details(self, tmdb_id: int) -> Dict:
        """Get movie details"""
        return await self._request(
            f"movie/{tmdb_id}", {"append_to_response": "credits,videos"}
        )

    async def get_tv_details(self, tmdb_id: int) -> Dict:
        """Get TV show details with all seasons"""
        return await self._request(
            f"tv/{tmdb_id}", {"append_to_response": "credits,videos"}
        )

    async def get_season_details(self, tmdb_id: int, season_number: int) -> Dict:
        """Get season details with episodes"""
        return await self._request(f"tv/{tmdb_id}/season/{season_number}")

    async def get_external_ids(self, tmdb_id: int, media_type: str) -> Dict:
        """Get external IDs (IMDB, etc.) for a TMDB ID"""
        return await self._request(f"{media_type}/{tmdb_id}/external_ids")

    def is_anime(self, item: Dict) -> bool:
        """
        Detect if item is anime based on:
        - Genre contains "Animation" (genre_id: 16)
        - Origin country contains "JP"
        """
        genre_ids = item.get("genre_ids", [])
        origin_country = item.get("origin_country", [])

        is_animation = 16 in genre_ids
        is_japanese = "JP" in origin_country

        return is_animation and is_japanese

    async def get_imdb_id(self, tmdb_id: int, media_type: str) -> Optional[str]:
        """Get IMDB ID for a TMDB item"""
        try:
            external_ids = await self.get_external_ids(tmdb_id, media_type)
            return external_ids.get("imdb_id")
        except Exception as e:
            log_service.error(f"Failed to get IMDB ID for {media_type}:{tmdb_id}: {e}")
            return None

    def parse_media_item(self, item: Dict, media_type: str = None) -> Dict:
        """Parse TMDB item into standardized format"""
        # Determine media type
        if media_type is None:
            media_type = item.get("media_type", "movie")

        # Handle both movie and TV naming
        if media_type == "movie":
            title = item.get("title", "")
            release_date = item.get("release_date", "")
            original_title = item.get("original_title", "")
        else:
            title = item.get("name", "")
            release_date = item.get("first_air_date", "")
            original_title = item.get("original_name", "")

        # Extract year from release date
        year = None
        if release_date:
            try:
                year = int(release_date.split("-")[0])
            except (ValueError, IndexError):
                pass

        return {
            "tmdb_id": item.get("id"),
            "media_type": media_type,
            "title": title,
            "original_title": original_title,
            "year": year,
            "release_date": release_date,
            "poster_path": item.get("poster_path"),
            "backdrop_path": item.get("backdrop_path"),
            "overview": item.get("overview", ""),
            "vote_average": item.get("vote_average", 0),
            "vote_count": item.get("vote_count", 0),
            "popularity": item.get("popularity", 0),
            "genre_ids": item.get("genre_ids", []),
            "origin_country": item.get("origin_country", []),
        }

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
