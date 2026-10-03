"""TMDB API service"""

import asyncio
from typing import Dict, Optional

import httpx

from .log_service import log_service
from ..utils.content_rating import get_us_movie_certification, get_us_tv_rating


class TMDBService:
    """The Movie Database API integration"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/w500"
        self.client = httpx.AsyncClient(timeout=30.0)
        self.cache = {}  # Simple in-memory cache for API responses

    async def _request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make request to TMDB API with retry logic"""
        if params is None:
            params = {}

        params["api_key"] = self.api_key
        url = f"{self.base_url}/{endpoint}"

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                # Don't retry on 4xx errors (client errors)
                if 400 <= e.response.status_code < 500:
                    log_service.error(
                        f"TMDB API client error ({e.response.status_code}): {endpoint}"
                    )
                    return None
                # Retry on 5xx errors (server errors)
                if attempt < max_retries - 1:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    log_service.warning(
                        f"TMDB API error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    log_service.error(
                        f"TMDB API error after {max_retries} attempts: {e}"
                    )
                    return None
            except httpx.RequestError as e:
                # Network errors - retry
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    log_service.warning(
                        f"TMDB network error, retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    log_service.error(
                        f"TMDB network error after {max_retries} attempts: {e}"
                    )
                    return None
            except Exception as e:
                log_service.error(f"Unexpected TMDB error: {e}", exc_info=True)
                return None

        return None

    async def search_movies(
        self, query: str, page: int = 1, include_adult: bool = False
    ) -> Dict:
        """Search for movies"""
        key = f"search_movies:{query}:{page}:{include_adult}"
        if key in self.cache:
            return self.cache[key]
        result = await self._request(
            "search/movie",
            {"query": query, "page": page, "include_adult": include_adult},
        )
        if result:
            self.cache[key] = result
        return result

    async def search_tv(
        self, query: str, page: int = 1, include_adult: bool = False
    ) -> Dict:
        """Search for TV shows"""
        key = f"search_tv:{query}:{page}:{include_adult}"
        if key in self.cache:
            return self.cache[key]
        result = await self._request(
            "search/tv", {"query": query, "page": page, "include_adult": include_adult}
        )
        if result:
            self.cache[key] = result
        return result

    async def search_multi(
        self, query: str, page: int = 1, include_adult: bool = False
    ) -> Dict:
        """Search for both movies and TV shows"""
        return await self._request(
            "search/multi",
            {"query": query, "page": page, "include_adult": include_adult},
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

    async def get_upcoming_movies(self, page: int = 1) -> Dict:
        """Get future movie releases ordered by popularity"""
        from datetime import date

        return await self._request(
            "discover/movie",
            {
                "primary_release_date.gte": date.today().isoformat(),
                "sort_by": "popularity.desc",
                "include_adult": False,
                "page": page,
            },
        )

    async def get_movie_details(self, tmdb_id: int) -> Dict:
        """Get movie details"""
        key = f"movie_details:{tmdb_id}"
        if key in self.cache:
            return self.cache[key]
        result = await self._request(
            f"movie/{tmdb_id}", {"append_to_response": "external_ids,release_dates,credits"}
        )
        if result:
            self.cache[key] = result
        return result

    async def get_tv_details(self, tmdb_id: int) -> Dict:
        """Get TV show details with all seasons"""
        key = f"tv_details:{tmdb_id}"
        if key in self.cache:
            return self.cache[key]
        result = await self._request(
            f"tv/{tmdb_id}", {"append_to_response": "external_ids,content_ratings,credits"}
        )
        if result:
            self.cache[key] = result
        return result

    async def get_details(self, tmdb_id: int, media_type: str) -> Optional[Dict]:
        """Get details for movie or tv"""
        if media_type == "movie":
            return await self.get_movie_details(tmdb_id)
        elif media_type == "tv":
            return await self.get_tv_details(tmdb_id)
        return None

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

        # Extract IMDb ID from external_ids if present (from details endpoint)
        imdb_id = None
        external_ids = item.get("external_ids", {})
        if external_ids:
            imdb_id = external_ids.get("imdb_id")

        # Extract content rating
        content_rating = None
        if media_type == "movie":
            content_rating = get_us_movie_certification(item)
        elif media_type == "tv":
            content_rating = get_us_tv_rating(item)

        # Extract director and cast from credits
        director = None
        cast = []
        credits_data = item.get("credits", {})
        if credits_data:
            crew = credits_data.get("crew", [])
            for person in crew:
                if person.get("job") == "Director":
                    director = person.get("name")
                    break
            cast_list = credits_data.get("cast", [])
            cast = [actor.get("name") for actor in cast_list[:5] if actor.get("name")]

        # The unified object structure
        # Filter metadata to keep only essential fields to reduce DB bloat
        essential_metadata = {
            "id": item.get("id") or item.get("tmdb_id"),
            "release_date": release_date,
            "overview": item.get("overview") or item.get("overview_raw", ""),
            "vote_average": item.get("vote_average", 0),
            "vote_count": item.get("vote_count", 0),
            "popularity": item.get("popularity", 0),
            "genre_ids": item.get("genre_ids", []),
            "genre_names": [g.get("name") for g in item.get("genres", []) if g.get("name")],
            "content_rating": content_rating,
            "director": director,
            "cast": cast,
            "runtime": item.get("runtime"),
            "number_of_seasons": item.get("number_of_seasons"),
            "number_of_episodes": item.get("number_of_episodes"),
            "status": item.get("status"),
            "tagline": item.get("tagline"),
            "seasons": item.get("seasons", []),
            "collection": item.get("belongs_to_collection"),
        }

        standardized = {
            "uuid": None,
            "tmdb_id": item.get("id") or item.get("tmdb_id"),
            "imdb_id": imdb_id or item.get("imdb_id"),
            "media_type": media_type,
            "title": title or item.get("title"),
            "original_title": original_title or item.get("original_title"),
            "year": year or item.get("year"),
            "image_paths": {
                "poster": item.get("poster_path"),
                "backdrop": item.get("backdrop_path"),
            },
            "poster_path": item.get("poster_path"),
            "backdrop_path": item.get("backdrop_path"),
            "metadata": essential_metadata,
            "is_persisted": False,
            "overview": item.get("overview") or item.get("overview_raw", ""),
            "vote_average": item.get("vote_average", 0),
            "popularity": item.get("popularity", 0),
            # Sync with MediaItem.to_dict() top-level fields
            "content_rating": content_rating,
            "director": director,
            "cast": cast,
            "genre_names": [g.get("name") for g in item.get("genres", []) if g.get("name")],
            "runtime": item.get("runtime"),
            "number_of_seasons": item.get("number_of_seasons"),
            "number_of_episodes": item.get("number_of_episodes"),
        }
        
        return standardized

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
