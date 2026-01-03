"""Service for auto-populating library and updating series"""

import asyncio
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.library_item import LibraryItem
from .library_service import LibraryService
from .log_service import log_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


class PopulateService:
    """Handle automatic library population and series updates"""

    def __init__(
        self,
        db: AsyncSession,
        tmdb: TMDBService,
        library: LibraryService,
        settings: SettingsManager,
    ):
        self.db = db
        self.tmdb = tmdb
        self.library = library
        self.settings = settings

    async def run_auto_populate(self) -> Dict:
        """
        Fetch trending/popular content and add to library
        """
        # Read settings
        sources = await self.settings.get("populate_sources", ["popular"])
        limit = await self.settings.get("populate_limit", 5)
        excluded_ids_str = await self.settings.get("populate_excluded_ids", "")
        excluded_ids = [
            int(id_str.strip())
            for id_str in excluded_ids_str.split(",")
            if id_str.strip().isdigit()
        ]

        quality_versions = await self.settings.get(
            "populate_default_qualities", ["1080p"]
        )

        added_count = 0
        total_found = 0
        if isinstance(sources, str):
            sources = [sources]

        log_service.info(
            f"Starting auto-populate from sources: {sources} (Limit: {limit})"
        )

        for source in sources:
            if added_count >= limit:
                break

            items_with_type = []
            try:
                if source == "trending":
                    movie_trending = await self.tmdb.get_trending("movie")
                    tv_trending = await self.tmdb.get_trending("tv")
                    items_with_type = [
                        (item, item.get("media_type"))
                        for item in movie_trending.get("results", [])
                        + tv_trending.get("results", [])
                    ]
                elif source == "popular":
                    movie_pop = await self.tmdb.get_popular("movie")
                    tv_pop = await self.tmdb.get_popular("tv")
                    items_with_type = [
                        (item, "movie") for item in movie_pop.get("results", [])
                    ] + [(item, "tv") for item in tv_pop.get("results", [])]
                elif source == "top_rated":
                    movie_top = await self.tmdb.get_top_rated("movie")
                    tv_top = await self.tmdb.get_top_rated("tv")
                    items_with_type = [
                        (item, "movie") for item in movie_top.get("results", [])
                    ] + [(item, "tv") for item in tv_top.get("results", [])]

                # Process items with their media types
                for item_data, media_type in items_with_type:
                    if added_count >= limit:
                        break

                    tmdb_id = item_data.get("id")

                    if not media_type or media_type not in ["movie", "tv"]:
                        continue

                    if tmdb_id in excluded_ids:
                        continue

                    if await self.library.is_in_library(tmdb_id, media_type):
                        continue
                    try:
                        await self.library.add_to_library(
                            tmdb_id=tmdb_id,
                            media_type=media_type,
                            quality_versions=quality_versions,
                            added_via="auto_populate",
                        )
                        added_count += 1
                        total_found += 1
                        log_service.info(f"Auto-populated {media_type} ID {tmdb_id}")
                    except Exception as e:
                        log_service.error(
                            f"Failed to auto-populate {media_type} {tmdb_id}: {e}"
                        )

            except Exception as e:
                log_service.error(f"Error fetching from source {source}: {e}")

        return {
            "success": True,
            "added_count": added_count,
            "message": f"Successfully added {added_count} items to library",
        }

    async def run_series_update(self) -> Dict:
        """
        Check all series in library for new episodes
        """
        log_service.info("Starting manual series update check for all library items")

        result = await self.db.execute(
            select(LibraryItem).where(LibraryItem.media_type == "tv")
        )
        items = result.scalars().all()

        total_new_episodes = 0
        updated_series_count = 0

        for item in items:
            try:
                refresh_result = await self.library.refresh_item(item.id)
                new_count = refresh_result.get("new_episodes", 0)
                total_new_episodes += new_count
                if new_count > 0:
                    updated_series_count += 1
            except Exception as e:
                log_service.error(f"Failed to update series '{item.title}': {e}")

        return {
            "success": True,
            "updated_series_count": updated_series_count,
            "total_new_episodes": total_new_episodes,
            "message": f"Updated {updated_series_count} series, found {total_new_episodes} new episodes",
        }
