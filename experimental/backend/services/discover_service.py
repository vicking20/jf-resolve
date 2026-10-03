import logging
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.discover import DiscoverItem
from ..models.media_item import MediaItem, MediaType
from ..models.media_external_id import MediaExternalId
from ..models.user_profile import ContentRating
from .tmdb_service import TMDBService
from ..utils.content_rating import is_content_allowed

logger = logging.getLogger(__name__)

class DiscoverService:
    """Service for managing discovery categories and caching TMDB items"""

    def __init__(self, db: AsyncSession, tmdb: TMDBService):
        self.db = db
        self.tmdb = tmdb

    async def get_discover_results(
        self, 
        category: str, 
        page: int = 1,
        max_rating: ContentRating = ContentRating.ALL,
        parental_controls: bool = False
    ) -> List[dict]:
        """
        Get discover results for a category. 
        Uses cache if valid, otherwise refreshes from TMDB.
        """
        cache_cutoff = datetime.now() - timedelta(hours=24)
        
        check_stmt = select(DiscoverItem).where(
            and_(
                DiscoverItem.category == category,
                DiscoverItem.tmdb_page == page,
                DiscoverItem.cached_at > cache_cutoff
            )
        ).limit(1)
        
        cache_exists = (await self.db.execute(check_stmt)).scalar_one_or_none()
        
        if not cache_exists:
            logger.info(f"Cache miss or expired for discover category '{category}' page {page}. Syncing...")
            await self._sync_category(category, page)

        items_stmt = (
            select(MediaItem)
            .join(DiscoverItem, DiscoverItem.media_uuid == MediaItem.uuid)
            .options(selectinload(MediaItem.external_ids))
            .where(
                and_(
                    DiscoverItem.category == category,
                    DiscoverItem.tmdb_page == page
                )
            )
            .order_by(DiscoverItem.position)
        )
        
        result = await self.db.execute(items_stmt)
        items = result.scalars().all()
        
        filtered_items = []
        for item in items:
            data = item.to_dict()
            
            if parental_controls:
                content_rating = data.get("content_rating")
                if not is_content_allowed(content_rating, data["media_type"], max_rating):
                    continue
            
            filtered_items.append(data)
            
        return filtered_items

    async def _sync_category(self, category: str, page: int):
        """Fetch from TMDB and save to MediaItem/DiscoverItem"""
        try:
            results = []
            if category == "popular_movies":
                data = await self.tmdb.get_popular("movie", page=page)
                results = data.get("results", [])
                media_type = "movie"
            elif category == "popular_tv":
                data = await self.tmdb.get_popular("tv", page=page)
                results = data.get("results", [])
                media_type = "tv"
            elif category == "trending_movies":
                data = await self.tmdb.get_trending("movie", page=page)
                results = data.get("results", [])
                media_type = "movie"
            elif category == "trending_tv":
                data = await self.tmdb.get_trending("tv", page=page)
                results = data.get("results", [])
                media_type = "tv"
            else:
                logger.error(f"Unknown discover category: {category}")
                return

            await self.db.execute(
                delete(DiscoverItem).where(
                    and_(
                        DiscoverItem.category == category,
                        DiscoverItem.tmdb_page == page
                    )
                )
            )

            for idx, item_data in enumerate(results):
                tmdb_id = item_data.get("id")
                if not tmdb_id:
                    continue

                existing_stmt = select(MediaExternalId.media_uuid).where(
                    and_(
                        MediaExternalId.provider == "tmdb",
                        MediaExternalId.external_id == str(tmdb_id)
                    )
                )
                media_uuid = (await self.db.execute(existing_stmt)).scalar_one_or_none()

                if not media_uuid:
                    parsed = self.tmdb.parse_media_item(item_data, media_type=media_type)
                    
                    if not parsed.get("content_rating"):
                        try:
                            details = await self.tmdb.get_details(tmdb_id, media_type)
                            if details:
                                from ..utils.content_rating import get_us_movie_certification, get_us_tv_rating
                                if media_type == "movie":
                                    parsed["content_rating"] = get_us_movie_certification(details)
                                else:
                                    parsed["content_rating"] = get_us_tv_rating(details)
                                parsed["metadata"]["content_rating"] = parsed["content_rating"]
                        except Exception as e:
                            logger.warning(f"Could not fetch details for {media_type}:{tmdb_id}: {e}")

                    new_media = MediaItem(
                        title=parsed["title"],
                        media_type=MediaType(media_type),
                        original_title=parsed["original_title"],
                        year=parsed["year"],
                        image_paths=parsed["image_paths"],
                        metadata_blob=parsed["metadata"],
                        is_persisted=False
                    )
                    self.db.add(new_media)
                    await self.db.flush()
                    media_uuid = new_media.uuid
                    
                    self.db.add(MediaExternalId(
                        media_uuid=media_uuid,
                        provider="tmdb",
                        external_id=str(tmdb_id)
                    ))
                
                discover_link = DiscoverItem(
                    media_uuid=media_uuid,
                    category=category,
                    position=idx,
                    tmdb_page=page
                )
                self.db.add(discover_link)

            await self.db.commit()
            logger.info(f"Synced {len(results)} items for category '{category}' page {page}")

        except Exception as e:
            logger.error(f"Failed to sync discover category '{category}': {e}", exc_info=True)
            await self.db.rollback()
