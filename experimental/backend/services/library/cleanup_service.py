"""Cleanup service - handles removal of files and orphaned media items"""

from typing import Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.media import MediaSourceModel
from ...models.media_item import MediaItem as MediaItemModel
from ...models.media_item import MediaType
from ..log_service import log_service


class CleanupService:
    """
    Handles cleanup of removed files and orphaned media items.

    Used by Active Scanner to maintain library consistency.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def cleanup_removed_files(
        self,
        library_path_id: int,
        current_file_paths: Set[str],
    ) -> int:
        """
        Cleanup files that are no longer present in the filesystem.

        Args:
            library_path_id: The library path being scanned
            current_file_paths: Set of file paths found in current scan

        Returns:
            Number of files removed
        """
        result = await self.db.execute(
            select(MediaSourceModel).where(
                MediaSourceModel.library_path_id == library_path_id
            )
        )
        all_sources = result.scalars().all()

        removed_count = 0
        deleted_media_uuids = []

        for source in all_sources:
            if source.file_path not in current_file_paths:
                log_service.info(f"Removing missing file from DB: {source.file_path}")
                deleted_media_uuids.append(source.media_uuid)
                await self.db.delete(source)
                removed_count += 1

        await self.db.commit()

        if deleted_media_uuids:
            await self._cleanup_orphaned_items(set(deleted_media_uuids))

        return removed_count

    async def cleanup_single_file(self, file_path: str) -> bool:
        """
        Remove a single file from the library (immediate delete).

        Used by file watcher when a file is deleted.

        Returns:
            True if cleanup was performed, False if file not found
        """
        result = await self.db.execute(
            select(MediaSourceModel).where(MediaSourceModel.file_path == file_path)
        )
        source = result.scalar_one_or_none()

        if not source:
            return False

        media_uuid = source.media_uuid
        log_service.info(f"Removing file from library: {file_path}")
        await self.db.delete(source)
        await self.db.commit()

        await self._cleanup_orphaned_items({media_uuid})

        return True

    async def _cleanup_orphaned_items(self, media_uuids: Set[str]):
        """
        Delete media items that have no remaining sources.

        For episodes, also checks if parent series should be removed.
        """
        for media_uuid in media_uuids:
            remaining = await self.db.execute(
                select(MediaSourceModel).where(
                    MediaSourceModel.media_uuid == media_uuid
                )
            )
            if remaining.scalar_one_or_none():
                continue

            item_result = await self.db.execute(
                select(MediaItemModel).where(MediaItemModel.uuid == media_uuid)
            )
            item = item_result.scalar_one_or_none()

            if not item:
                continue

            parent_id = None
            if (
                item.media_type in (MediaType.EPISODE, MediaType.VIDEO)
                and item.parent_id
            ):
                parent_id = item.parent_id

            log_service.info(
                f"Removing orphaned {item.media_type.value}: {item.title} (no sources)"
            )
            await self.db.delete(item)

            if parent_id:
                await self._maybe_cleanup_parent(parent_id)

        await self.db.commit()

    async def _maybe_cleanup_parent(self, parent_uuid: str):
        """
        Delete parent series/video_series if no children remain.
        """
        children_result = await self.db.execute(
            select(MediaItemModel).where(MediaItemModel.parent_id == parent_uuid)
        )
        children = children_result.scalars().all()

        if not children:
            parent_result = await self.db.execute(
                select(MediaItemModel).where(MediaItemModel.uuid == parent_uuid)
            )
            parent = parent_result.scalar_one_or_none()

            if parent:
                log_service.info(
                    f"Removing parent {parent.media_type.value}: {parent.title} (no children)"
                )
                await self.db.delete(parent)
                await self.db.commit()


