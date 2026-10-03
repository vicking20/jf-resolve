"""Service for managing and syncing media catalogues (Trending, Popular, etc.)"""

import asyncio
from typing import Dict

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.catalogue import Catalogue, CatalogueItem
from ..models.media_external_id import MediaExternalId
from ..models.media_item import MediaItem, MediaType
from .log_service import log_service
from .metadata_service import metadata_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


class CatalogueService:
    """Handles syncing and management of media catalogues in the database"""

    DEFAULT_CATALOGUES = [
        {
            "id_slug": "tmdb_upcoming_movies",
            "name": "Upcoming Movies",
            "type": "movie",
            "source": "upcoming",
        },
        {
            "id_slug": "tmdb_trending_movies",
            "name": "Trending Movies",
            "type": "movie",
            "source": "trending",
        },
        {
            "id_slug": "tmdb_trending_tv",
            "name": "Trending TV Shows",
            "type": "tv",
            "source": "trending",
        },
        {
            "id_slug": "tmdb_popular_movies",
            "name": "Popular Movies",
            "type": "movie",
            "source": "popular",
        },
        {
            "id_slug": "tmdb_popular_tv",
            "name": "Popular TV Shows",
            "type": "tv",
            "source": "popular",
        },
        {
            "id_slug": "tmdb_top_rated_movies",
            "name": "Top Rated Movies",
            "type": "movie",
            "source": "top_rated",
        },
        {
            "id_slug": "tmdb_top_rated_tv",
            "name": "Top Rated TV Shows",
            "type": "tv",
            "source": "top_rated",
        },
    ]

    def __init__(self, db: AsyncSession):
        self.db = db

    async def initialize_default_catalogues(self):
        """Seed the database with default catalogues if they don't exist"""
        for cat_def in self.DEFAULT_CATALOGUES:
            result = await self.db.execute(
                select(Catalogue).where(Catalogue.slug == cat_def["id_slug"])
            )
            if not result.scalar_one_or_none():
                catalogue = Catalogue(
                    name=cat_def["name"],
                    slug=cat_def["id_slug"],
                    # description=f"Auto-synced {cat_def['source']} {cat_def['type']}s from TMDB",
                    description=f"Synced {cat_def['name']}.",
                )
                self.db.add(catalogue)

        await self.db.commit()

    async def sync_all_catalogues(self):
        """Sync items for all system catalogues from their sources"""
        settings_mgr = SettingsManager(self.db)
        api_key = await settings_mgr.get("tmdb_api_key")

        if not api_key:
            log_service.info("Catalogue sync skipped: TMDB API key not configured")
            return False

        tmdb = TMDBService(api_key)
        try:
            for cat_def in self.DEFAULT_CATALOGUES:
                await self.sync_catalogue_items(cat_def, tmdb)
            return True
        finally:
            await tmdb.close()

    async def sync_catalogue_items(self, cat_def: Dict, tmdb: TMDBService):
        """Fetch and populate items for a specific catalogue"""
        log_service.info(f"Syncing catalogue: {cat_def['name']}")

        # 1. Fetch from TMDB
        results = []
        try:
            if cat_def["source"] == "trending":
                data = await tmdb.get_trending(cat_def["type"], "week", 1)
                results = data.get("results", [])
            elif cat_def["source"] == "popular":
                data = await tmdb.get_popular(cat_def["type"], 1)
                results = data.get("results", [])
            elif cat_def["source"] == "top_rated":
                data = await tmdb.get_top_rated(cat_def["type"], 1)
                results = data.get("results", [])
            elif cat_def["source"] == "upcoming":
                data = await tmdb.get_upcoming_movies(1)
                results = data.get("results", [])
        except Exception as e:
            log_service.error(
                f"Failed to fetch TMDB data for {cat_def['id_slug']}: {e}"
            )
            return

        # 2. Get catalogue record
        cat_result = await self.db.execute(
            select(Catalogue).where(Catalogue.slug == cat_def["id_slug"])
        )
        catalogue = cat_result.scalar_one_or_none()
        if not catalogue:
            return

        # 3. Process items
        # We'll replace the entire catalogue content for these system ones
        # and keep the ones that are valid.

        # Clear existing items first for a clean sync
        await self.db.execute(
            delete(CatalogueItem).where(CatalogueItem.catalogue_id == catalogue.id)
        )

        position = 0
        for item_data in results[:10]:  # Keep top 10
            try:
                if not item_data:
                    continue
                tmdb_id = item_data.get("id")
                if not tmdb_id:
                    continue

                # Fetch full details for ratings and external IDs
                details = await tmdb.get_details(tmdb_id, cat_def["type"])
                if not details:
                    continue

                # Parse into our standard format
                parsed = tmdb.parse_media_item(details, cat_def["type"])

                # Check if item exists in MediaItem (via external ID)
                # Track if this is a new item for image caching decision
                tmdb_id_str = str(tmdb_id)
                stmt = select(MediaExternalId).where(
                    MediaExternalId.provider == "tmdb",
                    MediaExternalId.external_id == tmdb_id_str,
                )
                res = await self.db.execute(stmt)
                ext_id_record = res.scalar_one_or_none()
                is_new_item = ext_id_record is None

                media_item = await self._get_or_create_media_shell(
                    tmdb_id, parsed, cat_def["type"]
                )

                # Add to catalogue
                cat_item = CatalogueItem(
                    catalogue_id=catalogue.id,
                    media_uuid=media_item.uuid,
                    position=position,
                )
                self.db.add(cat_item)
                position += 1

                # Trigger background image caching for new items only
                # Existing items likely have cached images already
                if is_new_item:
                    import asyncio
                    
                    # Use unified image_paths structure
                    images = parsed.get("image_paths", {})
                    poster = images.get("poster")
                    if poster:
                        asyncio.create_task(
                            metadata_service.cache_image(
                                cat_def["type"], tmdb_id, "poster", poster
                            )
                        )

                    backdrop = images.get("backdrop")
                    if backdrop:
                        asyncio.create_task(
                            metadata_service.cache_image(
                                cat_def["type"], tmdb_id, "backdrop", backdrop
                            )
                        )

            except Exception as e:
                log_service.error(
                    f"Error processing catalogue item {item_data.get('title')}: {e}"
                )

        await self.db.commit()
        log_service.info(f"Catalogue {cat_def['name']} synced with {position} items")

    async def _get_or_create_media_shell(
        self, tmdb_id: int, parsed_data: Dict, media_type: str
    ) -> MediaItem:
        """Get existing MediaItem or create a non-persisted shell"""
        tmdb_id_str = str(tmdb_id)

        # Check external IDs
        stmt = select(MediaExternalId).where(
            MediaExternalId.provider == "tmdb",
            MediaExternalId.external_id == tmdb_id_str,
        )
        res = await self.db.execute(stmt)
        ext_id_record = res.scalar_one_or_none()

        if ext_id_record:
            # Item exists, fetch the MediaItem
            stmt = select(MediaItem).where(MediaItem.uuid == ext_id_record.media_uuid)
            item_res = await self.db.execute(stmt)
            item = item_res.scalar_one()

            # Update metadata blob/image paths for both movies and TV to ensure we have overview and ratings
            new_images = parsed_data.get("image_paths", {})
            if new_images:
                existing_images = item.image_paths or {}
                item.image_paths = {**existing_images, **new_images}
                
            # Update with full parsed data to ensure overview, ratings, and genres are present
            new_metadata = parsed_data.get("metadata", {})
            if new_metadata:
                existing_meta = item.metadata_blob or {}
                item.metadata_blob = {**existing_meta, **new_metadata}

            return item

        # Create new shell
        m_type = MediaType.MOVIE if media_type == "movie" else MediaType.TV

        # Use unified image_paths from parsed data
        image_paths = parsed_data.get("image_paths", {})
        
        # Use nested metadata from unified structure
        metadata_blob = parsed_data.get("metadata", {})

        new_item = MediaItem(
            media_type=m_type,
            title=parsed_data["title"],
            original_title=parsed_data.get("original_title"),
            year=parsed_data.get("year"),
            image_paths=image_paths,
            metadata_blob=metadata_blob,  # Store full metadata blob
            is_persisted=False,  # Shell item
        )
        self.db.add(new_item)
        await self.db.flush()  # Get UUID

        # Add external ID
        new_ext_id = MediaExternalId(
            media_uuid=new_item.uuid, provider="tmdb", external_id=tmdb_id_str
        )
        self.db.add(new_ext_id)

        return new_item
