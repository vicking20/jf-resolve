import uuid
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.media_external_id import MediaExternalId
from ..models.media_item import MediaItem, MediaType
from .log_service import log_service
from .tmdb_service import TMDBService
from .metadata_service import metadata_service


class MediaResolver:
    """
    Hybrid Metadata Resolver: Orchestrates local DB and external providers.
    """

    def __init__(self, tmdb_service: TMDBService):
        self.tmdb = tmdb_service

    async def resolve(
        self,
        db: AsyncSession,
        identifier: str,
        media_type: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve a media item by identifier (UUID or provider:id).
        Returns a dictionary representing the media item (Transient or Persisted).
        """
        if self._is_uuid(identifier):
            result = await db.execute(
                select(MediaItem)
                .options(selectinload(MediaItem.external_ids))
                .where(MediaItem.uuid == identifier)
            )
            item = result.scalar_one_or_none()
            if item:
                data = item.to_dict()
                if user_id:
                    from ..models.watchlist import WatchlistItem

                    res = await db.execute(
                        select(WatchlistItem).where(
                            WatchlistItem.user_id == user_id,
                            WatchlistItem.media_uuid == item.uuid,
                        )
                    )
                    data["in_watchlist"] = res.scalar_one_or_none() is not None
                return data
            return None

        provider, ext_id = self._parse_identifier(identifier)
        if provider and ext_id:
            result = await db.execute(
                select(MediaExternalId).where(
                    MediaExternalId.provider == provider,
                    MediaExternalId.external_id == ext_id,
                )
            )
            mapping = result.scalar_one_or_none()
            if mapping:
                result = await db.execute(
                    select(MediaItem)
                    .options(selectinload(MediaItem.external_ids))
                    .where(MediaItem.uuid == mapping.media_uuid)
                )
                item = result.scalar_one_or_none()
                if item:
                    data = item.to_dict()
                    if user_id:
                        from ..models.watchlist import WatchlistItem

                        res = await db.execute(
                            select(WatchlistItem).where(
                                WatchlistItem.user_id == user_id,
                                WatchlistItem.media_uuid == item.uuid,
                            )
                        )
                        data["in_watchlist"] = res.scalar_one_or_none() is not None
                    return data

            item_dict = None
            if provider == "tmdb":
                item_dict = await self._fetch_transient_tmdb(ext_id, media_type)
            elif provider == "imdb":
                pass

            if item_dict and user_id:
                if provider == "tmdb":
                    from ..models.watchlist import WatchlistItem

                    result = await db.execute(
                        select(WatchlistItem)
                        .join(
                            MediaExternalId,
                            WatchlistItem.media_uuid == MediaExternalId.media_uuid,
                        )
                        .where(
                            WatchlistItem.user_id == user_id,
                            MediaExternalId.provider == "tmdb",
                            MediaExternalId.external_id == ext_id,
                        )
                    )
                    item_dict["in_watchlist"] = result.scalar_one_or_none() is not None

            return item_dict

        return None

    async def persist(
        self,
        db: AsyncSession,
        transient_item: Dict[str, Any],
        is_persisted: bool = False,
    ) -> MediaItem:
        """
        Convert a transient media item (dict) into a persisted database record.
        """
        tmdb_id = transient_item.get("tmdb_id")
        item = None
        
        if tmdb_id:
            result = await db.execute(
                select(MediaExternalId).where(
                    MediaExternalId.provider == "tmdb",
                    MediaExternalId.external_id == str(tmdb_id),
                )
            )
            mapping = result.scalar_one_or_none()
            if mapping:
                result = await db.execute(
                    select(MediaItem).where(MediaItem.uuid == mapping.media_uuid)
                )
                item = result.scalar_one_or_none()
                
        if not item and transient_item.get("uuid"):
            result = await db.execute(
                select(MediaItem).where(MediaItem.uuid == transient_item["uuid"])
            )
            item = result.scalar_one_or_none()

        if item:
            needs_metadata_trigger = False
            if is_persisted and not item.is_persisted:
                item.is_persisted = True
                needs_metadata_trigger = True

            new_metadata = transient_item.get("metadata") or {}
            if new_metadata:
                existing_meta = item.metadata_blob or {}
                item.metadata_blob = {**existing_meta, **new_metadata}

            new_images = transient_item.get("image_paths") or {}
            if new_images:
                existing_images = item.image_paths or {}
                item.image_paths = {**existing_images, **new_images}

            if needs_metadata_trigger and tmdb_id:
                await metadata_service.save_metadata(
                    media_type=item.media_type.value,
                    item_id=str(tmdb_id),
                    tmdb_data=item.metadata_blob,
                    download_images=True
                )

            return item

        new_item = MediaItem(
            uuid=transient_item.get("uuid") or str(uuid.uuid4()),
            media_type=transient_item["media_type"]
            if isinstance(transient_item["media_type"], MediaType)
            else MediaType(transient_item["media_type"]),
            title=transient_item["title"],
            original_title=transient_item.get("original_title"),
            year=transient_item.get("year"),
            image_paths=transient_item.get("image_paths", {}),
            metadata_blob=transient_item.get("metadata", {}),
            is_persisted=is_persisted,
        )
        db.add(new_item)

        if tmdb_id:
            stmt = select(MediaExternalId).where(
                MediaExternalId.provider == "tmdb",
                MediaExternalId.external_id == str(tmdb_id)
            )
            existing = await db.execute(stmt)
            if not existing.scalar_one_or_none():
                db.add(
                    MediaExternalId(
                        media_uuid=new_item.uuid, provider="tmdb", external_id=str(tmdb_id)
                    )
                )

        imdb_id = transient_item.get("imdb_id")
        if imdb_id:
            stmt = select(MediaExternalId).where(
                MediaExternalId.provider == "imdb",
                MediaExternalId.external_id == imdb_id
            )
            existing = await db.execute(stmt)
            if not existing.scalar_one_or_none():
                db.add(
                    MediaExternalId(
                        media_uuid=new_item.uuid, provider="imdb", external_id=imdb_id
                    )
                )

        await db.flush()

        if is_persisted and tmdb_id:
            metadata_to_save = transient_item.get("metadata") or transient_item
            await metadata_service.save_metadata(
                media_type=new_item.media_type.value,
                item_id=str(tmdb_id),
                tmdb_data=metadata_to_save,
                download_images=True
            )

        return new_item

    def _is_uuid(self, val: str) -> bool:
        try:
            uuid.UUID(val)
            return True
        except ValueError:
            return False

    def _parse_identifier(self, identifier: str) -> tuple[Optional[str], Optional[str]]:
        if ":" in identifier:
            parts = identifier.split(":", 1)
            return parts[0].lower(), parts[1]
        return None, None

    async def _fetch_transient_tmdb(
        self, tmdb_id: str, media_type: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Fetch from TMDB and wrap in transient dictionary"""
        try:
            tid = int(tmdb_id)
            if not media_type:
                return None

            data = await self.tmdb.get_details(tid, media_type)
            if not data:
                return None

            item_dict = self.tmdb.parse_media_item(data, media_type)
            item_dict["uuid"] = None
            
            item_dict["overview"] = item_dict["metadata"].get("overview")
            item_dict["vote_average"] = item_dict["metadata"].get("vote_average")
            item_dict["runtime"] = item_dict["metadata"].get("runtime")
            
            return item_dict
        except Exception as e:
            log_service.error(f"Failed to fetch transient TMDB item {tmdb_id}: {e}")
            return None
