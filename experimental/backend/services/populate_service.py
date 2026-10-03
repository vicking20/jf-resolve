"""Service for updating series and library items"""

from typing import Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .library_service import LibraryService
from .log_service import log_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


class PopulateService:
    """Handle automatic series and media updates"""

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


    async def run_series_update(self) -> Dict:
        """
        Check all series in library for new episodes
        """
        log_service.info("Starting manual series update check for all library items")

        from ..models.media_item import MediaItem, MediaType
        result = await self.db.execute(
            select(MediaItem).where(MediaItem.media_type == MediaType.TV, MediaItem.is_persisted == True)
        )
        series_list = result.scalars().all()

        total_new_episodes = 0
        updated_series_count = 0

        for series in series_list:
            try:
                tmdb_id = series.get_external_id("tmdb")
                if not tmdb_id:
                    continue
                    
                refresh_result = await self.library.refresh_series(int(tmdb_id))
                new_count = refresh_result.get("new_episodes", 0)
                total_new_episodes += new_count
                if new_count > 0:
                    updated_series_count += 1
            except Exception as e:
                log_service.error(f"Failed to update series '{series.title}': {e}")

        return {
            "success": True,
            "updated_series_count": updated_series_count,
            "total_new_episodes": total_new_episodes,
            "message": f"Updated {updated_series_count} series, found {total_new_episodes} new episodes",
        }
