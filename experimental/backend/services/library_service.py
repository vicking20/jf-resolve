"""Library management service"""

from typing import Dict, Optional, Union

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.media_item import MediaItem, MediaType
from ..models.media_external_id import MediaExternalId
from .log_service import log_service
from .metadata_service import metadata_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


class LibraryService:
    """Manage library items (Unified MediaItems)"""

    def __init__(self, db: AsyncSession, tmdb: TMDBService, settings: SettingsManager):
        self.db = db
        self.tmdb = tmdb
        self.settings = settings

    async def is_in_library(self, tmdb_id: int, media_type: str) -> Optional[str]:
        """Check if item is already in library and return media_uuid if exists"""
        result = await self.db.execute(
            select(MediaExternalId.media_uuid)
            .where(
                MediaExternalId.provider == "tmdb",
                MediaExternalId.external_id == str(tmdb_id)
            )
        )
        mapping = result.scalar_one_or_none()
        if not mapping:
            return None
            
        # Verify it's persisted
        result = await self.db.execute(
            select(MediaItem.uuid).where(
                MediaItem.uuid == mapping,
                MediaItem.is_persisted == True
            )
        )
        return result.scalar_one_or_none()

    async def get_or_fetch_imdb_id(
        self, tmdb_id: int, media_type: str
    ) -> Optional[str]:
        """
        Get IMDB ID from MediaExternalId or fetch from TMDB
        """
        result = await self.db.execute(
            select(MediaExternalId.external_id)
            .join(MediaExternalId, MediaExternalId.media_uuid == MediaExternalId.media_uuid) # This is dummy join for now
            .where(
                MediaExternalId.provider == "imdb",
                MediaExternalId.media_uuid.in_(
                    select(MediaExternalId.media_uuid).where(
                        MediaExternalId.provider == "tmdb",
                        MediaExternalId.external_id == str(tmdb_id)
                    )
                )
            )
        )
        imdb_id = result.scalar_one_or_none()
        if imdb_id:
            return imdb_id

        details = await self.tmdb.get_details(tmdb_id, media_type)
        imdb_id = details.get("external_ids", {}).get("imdb_id") if details else None
        
        # If we have a MediaItem for this TMDB ID, save the IMDB ID
        tmdb_mapping_result = await self.db.execute(
            select(MediaExternalId.media_uuid).where(
                MediaExternalId.provider == "tmdb",
                MediaExternalId.external_id == str(tmdb_id)
            )
        )
        media_uuid = tmdb_mapping_result.scalar_one_or_none()
        
        if media_uuid and imdb_id:
            # Check if this provider/id pair already exists
            stmt = select(MediaExternalId).where(
                MediaExternalId.provider == "imdb",
                MediaExternalId.external_id == imdb_id
            )
            existing = await self.db.execute(stmt)
            if not existing.scalar_one_or_none():
                self.db.add(MediaExternalId(
                    media_uuid=media_uuid,
                    provider="imdb",
                    external_id=imdb_id
                ))
                await self.db.commit()

        return imdb_id

    async def remove_from_library(self, identifier: str, media_type: Optional[str] = None):
        """Remove item from library by UUID or provider:id"""
        media_uuid = None
        if ":" in identifier:
            provider, ext_id = identifier.split(":", 1)
            result = await self.db.execute(
                select(MediaExternalId.media_uuid).where(
                    MediaExternalId.provider == provider,
                    MediaExternalId.external_id == ext_id
                )
            )
            media_uuid = result.scalar_one_or_none()
        else:
            media_uuid = identifier

        if not media_uuid:
            raise ValueError(f"Library item {identifier} not found")

        result = await self.db.execute(
            select(MediaItem).where(MediaItem.uuid == media_uuid)
        )
        item = result.scalar_one_or_none()

        if not item:
            raise ValueError(f"Library item {media_uuid} not found")

        title = item.title
        # We don't necessarily delete the MediaItem record if it has playback sessions 
        # but we mark it as NOT persisted.
        # Actually, for a clean "Remove from Library" we might want to delete it 
        # unless it's in the watchlist.
        
        item.is_persisted = False
        await self.db.commit()

        log_service.info(f"Removed from library: {title} ({media_uuid})")

    async def refresh_metadata(self, media_uuid: str) -> Dict:
        """Refresh metadata from TMDB"""
        result = await self.db.execute(select(MediaItem).where(MediaItem.uuid == media_uuid))
        item = result.scalar_one_or_none()
        if not item:
            raise ValueError(f"Media item {media_uuid} not found")

        # Get TMDB ID
        result = await self.db.execute(
            select(MediaExternalId.external_id).where(
                MediaExternalId.media_uuid == media_uuid,
                MediaExternalId.provider == "tmdb"
            )
        )
        tmdb_id = result.scalar_one_or_none()
        if not tmdb_id:
            return {"message": "No TMDB ID for this item"}

        details = await self.tmdb.get_details(int(tmdb_id), item.media_type.value)
        if not details:
            return {"message": "Could not fetch details from TMDB"}

        # Update fields
        item.title = details.get("title") or details.get("name")
        item.year = int(details.get("release_date", details.get("first_air_date", ""))[:4] or 0)
        item.image_paths = {
            "poster": details.get("poster_path"),
            "backdrop": details.get("backdrop_path")
        }
        item.metadata_blob = details

        await self.db.commit()
        await metadata_service.save_metadata(item.media_type.value, int(tmdb_id), details)

        return {"message": "Metadata refreshed"}
