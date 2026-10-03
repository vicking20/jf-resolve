"""JIT (Just-In-Time) scanner for lazy mode - on-demand scanning without DB persistence"""

import asyncio
import os
from typing import Dict, List, Optional

from ...models.library_path import LibraryPath
from ..tmdb_service import TMDBService
from .base_scanner import BaseScanner


class JITScanner:
    """
    Just-In-Time scanner for lazy mode.

    Scans directories on-demand and returns transient results.
    Does NOT persist to database for movies/TV.

    Features:
    - Fast on-demand scanning
    - TMDB enrichment with caching
    - Pagination support
    - No database writes
    """

    def __init__(self, tmdb: Optional[TMDBService] = None):
        self.tmdb = tmdb
        self.base = BaseScanner(tmdb)
        self._cache = {}
        self._cache_timestamps = {}


    async def search_jit(
        self,
        library_path: LibraryPath,
        query: str,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Search within a JIT-scanned library.

        Args:
            library_path: The path to search
            query: Search query
            limit: Maximum results

        Returns:
            List of matching items
        """
        cache_key = f"files_{library_path.id}"
        video_files = self._cache.get(cache_key)

        if not video_files:
            video_files = await self.base.find_video_files_async(library_path.path)
            self._cache[cache_key] = video_files

        query_lower = query.lower()
        matches = []

        for file_path in video_files:
            filename = os.path.basename(file_path)
            metadata = self.base.extract_metadata_from_filename(
                filename, library_path.media_type
            )

            title = metadata.get("title", "").lower()
            if query_lower in title:
                metadata["file_path"] = file_path
                matches.append(metadata)

                if len(matches) >= limit:
                    break

        enriched = await self._enrich_with_tmdb(matches, library_path.media_type)

        return enriched

    async def _enrich_with_tmdb(self, items: List[Dict], media_type: str) -> List[Dict]:
        """
        Enrich items with TMDB metadata (cached, parallel).

        Args:
            items: List of metadata dicts
            media_type: "movie" or "tv"

        Returns:
            Enriched items with TMDB poster/backdrop/year/etc
        """
        if not self.tmdb:
            return items

        enriched = []

        async def enrich_one(item: Dict):
            title = item.get("title")
            year = item.get("year")

            if not title:
                return item

            tmdb_data = await self.base.match_with_tmdb(
                title=title, year=year, media_type=media_type
            )

            if tmdb_data:
                meta = tmdb_data.get("metadata", {})
                images = tmdb_data.get("image_paths", {})
                
                enriched_item = {
                    **item,
                    "tmdb_id": tmdb_data.get("tmdb_id"),
                    "title": tmdb_data.get("title") or item.get("title"),
                    "year": tmdb_data.get("year") or item.get("year"),
                    "overview": meta.get("overview"),
                    "poster": images.get("poster"),
                    "backdrop": images.get("backdrop"),
                    "rating": meta.get("vote_average"),
                    "genres": meta.get("genre_names"),
                    "runtime": meta.get("runtime"),
                }
                return enriched_item

            return item

        semaphore = asyncio.Semaphore(10)

        async def enrich_with_limit(item):
            async with semaphore:
                return await enrich_one(item)

        tasks = [enrich_with_limit(item) for item in items]
        enriched = await asyncio.gather(*tasks)

        return enriched

    def clear_cache(self, library_path_id: Optional[int] = None):
        """
        Clear the JIT scanner cache.

        Args:
            library_path_id: If provided, clear only this path's cache
        """
        if library_path_id:
            cache_key = f"files_{library_path_id}"
            self._cache.pop(cache_key, None)
            self._cache_timestamps.pop(cache_key, None)
        else:
            self._cache.clear()
            self._cache_timestamps.clear()

    def get_cache_stats(self) -> Dict:
        """Get cache statistics"""
        return {
            "cached_paths": len(self._cache),
            "total_cached_files": sum(
                len(files) for files in self._cache.values() if isinstance(files, list)
            ),
        }
