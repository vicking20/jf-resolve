"""Active scanner - library scanning with full persistence"""

import asyncio
import os
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ...database import AsyncSessionLocal

from ...models.file_manifest import FileManifest
from ...models.library_path import LibraryPath
from ...models.media import LibraryScanJob, MediaSourceModel
from ...models.media import MediaType as MediaTypeEnum
from ...models.media_item import MediaItem as MediaItemModel
from ..log_service import log_service
from ..media_resolver import MediaResolver
from ..tmdb_service import TMDBService
from .base_scanner import BaseScanner
from .batch_processor import BatchProcessor
from .cleanup_service import CleanupService


class ActiveScanner:
    """
    Active scanner for library management.

    Features:
    - Full TMDB matching for movies/TV
    - Persistence to database
    - File change detection
    - Automatic cleanup of removed files
    - Batch processing with rate limiting
    """

    def __init__(
        self,
        db: AsyncSession,
        tmdb: Optional[TMDBService] = None,
        batch_processor: Optional[BatchProcessor] = None,
    ):
        self.db = db
        self.tmdb = tmdb
        self.base = BaseScanner(tmdb)
        self.cleanup = CleanupService(db)
        self.batch_processor = batch_processor or BatchProcessor()
        self.resolver = MediaResolver(tmdb)

    async def full_scan(
        self,
        library_path: LibraryPath,
        job: Optional[LibraryScanJob] = None,
        force_rescan: bool = False,
    ) -> Dict:
        """
        Perform full library scan with TMDB matching and persistence.

        Args:
            library_path: The library path to scan
            job: Optional scan job for progress tracking
            force_rescan: If True, rescan and update existing items

        Returns:
            Dict with scan statistics
        """
        log_service.info(f"Starting active scan of {library_path.path}")

        # Check if path exists
        if not os.path.exists(library_path.path):
            raise FileNotFoundError(f"Library path not found: {library_path.path}")

        # Find all video files
        video_files = await self.base.find_video_files_async(library_path.path)
        log_service.info(f"Found {len(video_files)} video files in {library_path.path}")

        if job:
            job.total_files = len(video_files)
            await self.db.commit()

        # Extract metadata for all files
        metadata_list = []
        for file_path in video_files:
            filename = os.path.basename(file_path)
            metadata = self.base.extract_metadata_from_filename(
                filename, library_path.media_type
            )
            metadata_list.append((file_path, metadata))

        # Prefetch TMDB results if needed for movies/TV
        if library_path.media_type in ["movie", "tv"] and self.tmdb:
            await self._prefetch_tmdb_results(metadata_list, library_path.media_type)

        # Process files in batches with rate limiting
        new_count = 0
        updated_count = 0

        async def process_file(file_path: str, batch_proc: BatchProcessor):
            """Process a single file with its own DB session"""
            metadata = next((m for fp, m in metadata_list if fp == file_path), None)
            if not metadata:
                return None

            try:
                async with AsyncSessionLocal() as db:
                    # Check if file already exists
                    result = await db.execute(
                        select(MediaSourceModel).where(MediaSourceModel.file_path == file_path)
                    )
                    existing_source = result.scalar_one_or_none()

                    if existing_source and not force_rescan:
                        # Update verification timestamp
                        existing_source.last_verified = datetime.now(timezone.utc)
                        existing_source.is_available = True
                        await db.commit()
                        return {"updated": True}
                    
                    # New file or force rescan - process it
                    created = await self._process_video_file(
                        db=db,
                        file_path=file_path,
                        library_path=library_path,
                        existing_source=existing_source,
                        batch_proc=batch_proc,
                    )
                    # Commit changes in this isolated session
                    await db.commit()
                    
                    if created:
                        return {"created": True}
                    return None
            except Exception as e:
                log_service.error(f"Error processing file {file_path}: {e}")
                return e

        async def report_progress(processed, total, new, updated):
            """Report progress to job"""
            # Use main session for job updates effectively lock it briefly? 
            # Or just fire and forget if we trust concurrency?
            # Better to use a separate short-lived session or just keep updating the passed job 
            # if attached to self.db, but self.db is not thread-safe for gathered tasks.
            # However, report_progress is called sequentially by BatchProcessor usually?
            # BatchProcessor awaits gather(), simplifies things.
            # Wait, BatchProcessor calls report_progress AFTER `await asyncio.gather`.
            # So main thread is free. We can use self.db safely here if not in parallel.
            try:
                if job:
                    job.processed_files = processed
                    job.matched_files = new
                    job.new_media_added = new
                    job.existing_media_updated = updated
                    await self.db.commit()
            except Exception as e:
                log_service.warning(f"Failed to update progress: {e}")

        # Process all files
        result = await self.batch_processor.process_files(
            files=video_files,
            processor_func=process_file,
            commit_func=None,
            progress_callback=report_progress,
        )

        new_count = result["new_items"]
        updated_count = result["updated_items"]

        # Update last scanned timestamp
        library_path.last_scanned = datetime.now(timezone.utc)
        await self.db.commit()

        # Cleanup removed files
        current_file_paths = set(video_files)
        removed_count = await self.cleanup.cleanup_removed_files(
            library_path.id, current_file_paths
        )

        log_service.info(
            f"Active scan complete: {new_count} new, {updated_count} updated, {removed_count} removed"
        )

        return {
            "new": new_count,
            "updated": updated_count,
            "removed": removed_count,
            "total": len(video_files),
        }

    async def incremental_scan(
        self,
        library_path: LibraryPath,
        job: Optional[LibraryScanJob] = None,
    ) -> Dict:
        """
        Perform incremental scan - only processes changed files.

        Uses FileManifest table to detect:
        - New files (not in manifest)
        - Removed files (in manifest but not on disk)
        - Modified files (different size or mtime)

        Much faster than full scan for large libraries.

        Args:
            library_path: The library path to scan
            job: Optional scan job for progress tracking

        Returns:
            Dict with scan statistics
        """
        log_service.info(f"Starting incremental scan of {library_path.path}")

        if not os.path.exists(library_path.path):
            raise FileNotFoundError(f"Library path not found: {library_path.path}")

        # Get current files from filesystem
        current_files = await self.base.find_video_files_async(library_path.path)
        current_file_info = {}

        for file_path in current_files:
            try:
                stat = os.stat(file_path)
                current_file_info[file_path] = {
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                }
            except OSError:
                pass

        # Get previous manifest from DB
        result = await self.db.execute(
            select(FileManifest).where(FileManifest.library_path_id == library_path.id)
        )
        manifest_entries = result.scalars().all()
        manifest_dict = {entry.file_path: entry for entry in manifest_entries}

        # Calculate delta
        current_paths = set(current_file_info.keys())
        manifest_paths = set(manifest_dict.keys())

        added_files = current_paths - manifest_paths
        removed_files = manifest_paths - current_paths
        potentially_modified = current_paths & manifest_paths

        # Check for actually modified files
        modified_files = []
        for file_path in potentially_modified:
            manifest_entry = manifest_dict[file_path]
            current_info = current_file_info[file_path]

            # Check if size or mtime changed
            if (
                manifest_entry.file_size != current_info["size"]
                or manifest_entry.modified_time != current_info["mtime"]
            ):
                modified_files.append(file_path)

        log_service.info(
            f"Incremental scan delta: {len(added_files)} added, "
            f"{len(removed_files)} removed, {len(modified_files)} modified"
        )

        if job:
            job.total_files = len(added_files) + len(modified_files)
            await self.db.commit()

        # Process added files
        new_count = 0
        for file_path in added_files:
            try:
                created = await self._process_video_file(
                    db=self.db,
                    file_path=file_path,
                    library_path=library_path,
                    existing_source=None,
                    batch_proc=self.batch_processor,
                )
                if created:
                    new_count += 1

                # Add to manifest
                info = current_file_info[file_path]
                manifest_entry = FileManifest(
                    library_path_id=library_path.id,
                    file_path=file_path,
                    file_size=info["size"],
                    modified_time=info["mtime"],
                    last_seen=datetime.now(timezone.utc),
                )
                self.db.add(manifest_entry)

                if job:
                    job.processed_files += 1
                    job.new_media_added = new_count
                    await self.db.commit()

            except Exception as e:
                log_service.error(f"Error processing added file {file_path}: {e}")
                if job:
                    job.failed_files += 1

        # Process modified files
        updated_count = 0
        for file_path in modified_files:
            try:
                # Re-process the file
                result = await self.db.execute(
                    select(MediaSourceModel).where(
                        MediaSourceModel.file_path == file_path
                    )
                )
                existing_source = result.scalar_one_or_none()

                created = await self._process_video_file(
                    db=self.db,
                    file_path=file_path,
                    library_path=library_path,
                    existing_source=existing_source,
                    batch_proc=self.batch_processor,
                )
                if not created:
                    updated_count += 1

                # Update manifest
                manifest_entry = manifest_dict[file_path]
                info = current_file_info[file_path]
                manifest_entry.file_size = info["size"]
                manifest_entry.modified_time = info["mtime"]
                manifest_entry.last_seen = datetime.now(timezone.utc)

                if job:
                    job.processed_files += 1
                    job.existing_media_updated = updated_count
                    await self.db.commit()

            except Exception as e:
                log_service.error(f"Error processing modified file {file_path}: {e}")
                if job:
                    job.failed_files += 1

        # Process removed files
        removed_count = 0
        for file_path in removed_files:
            try:
                # Delete from manifest
                manifest_entry = manifest_dict[file_path]
                await self.db.delete(manifest_entry)

                # Cleanup media
                await self.cleanup.cleanup_single_file(file_path)
                removed_count += 1

            except Exception as e:
                log_service.error(f"Error removing file {file_path}: {e}")

        # Update manifest for unchanged files (last_seen timestamp)
        unchanged_files = potentially_modified - set(modified_files)
        for file_path in unchanged_files:
            manifest_entry = manifest_dict[file_path]
            manifest_entry.last_seen = datetime.now(timezone.utc)

        # Update library path timestamp
        library_path.last_scanned = datetime.now(timezone.utc)
        await self.db.commit()

        log_service.info(
            f"Incremental scan complete: {new_count} new, {updated_count} updated, {removed_count} removed"
        )

        return {
            "new": new_count,
            "updated": updated_count,
            "removed": removed_count,
            "total": len(current_files),
            "scan_type": "incremental",
        }

    async def process_single_file(
        self, file_path: str, library_path: LibraryPath
    ) -> bool:
        """
        Process a single new file (used by file watcher).

        Returns:
            True if file was added successfully
        """
        log_service.info(f"Processing new file: {file_path}")

        try:
            created = await self._process_video_file(
                db=self.db,
                file_path=file_path,
                library_path=library_path,
                existing_source=None,
                batch_proc=self.batch_processor,
            )
            await self.db.commit()
            return created
        except Exception as e:
            log_service.error(f"Error processing file {file_path}: {e}")
            return False

    async def _process_video_file(
        self,
        db: AsyncSession,
        file_path: str,
        library_path: LibraryPath,
        existing_source: Optional[MediaSourceModel] = None,
        batch_proc: Optional[BatchProcessor] = None,
    ) -> bool:
        """
        Process a single video file.

        1. Extract metadata from filename
        2. Try to match with TMDB (for movies/TV)
        3. Create MediaItem (persisted)
        4. Create/update MediaSource entry

        Returns:
            True if a new entry was created, False otherwise
        """
        from ..download_library_service import read_identity, register_local_file
        identity = read_identity(file_path)
        if identity and await db.get(MediaItemModel, identity['media_uuid']):
            await register_local_file(db, file_path, identity, library_path_id=library_path.id)
            return True
        filename = os.path.basename(file_path)

        # Extract metadata from filename
        metadata = self.base.extract_metadata_from_filename(
            filename, library_path.media_type
        )

        # Get file info
        file_info = await self.base.get_file_info(file_path)
        if metadata.get("quality"):
            file_info["resolution"] = metadata["quality"]

        # Try TMDB matching if needed
        tmdb_data = None
        if library_path.media_type in ["movie", "tv"] and self.tmdb:
            # Use batch processor for rate limiting if available
            if batch_proc:
                tmdb_data = await batch_proc.with_tmdb_limit(
                    self.base.match_with_tmdb(
                        title=metadata["title"],
                        year=metadata.get("year"),
                        media_type=library_path.media_type,
                        season=metadata.get("season"),
                        episode=metadata.get("episode"),
                    )
                )
            else:
                tmdb_data = await self.base.match_with_tmdb(
                    title=metadata["title"],
                    year=metadata.get("year"),
                    media_type=library_path.media_type,
                    season=metadata.get("season"),
                    episode=metadata.get("episode"),
                )

        # Create or update MediaItem
        media_item = None

        if tmdb_data:
            # Matched with TMDB - create persisted item
            if (
                library_path.media_type == "tv"
                and metadata.get("season")
                and metadata.get("episode")
            ):
                # TV episode - create series and episode
                media_item = await self._create_tv_episode(
                    db=db,
                    series_data=tmdb_data,
                    season_number=metadata["season"],
                    episode_number=metadata["episode"],
                    file_path=file_path,
                )
            else:
                # Movie or TV series - persist to library
                media_item = await self.resolver.persist(db, tmdb_data, is_persisted=True)

        else:
            # No TMDB match - create as user content
            media_item = await self._create_user_media(
                db, metadata, file_path, library_path
            )

        if not media_item:
            return False

        # Create or update MediaSource
        source_type = self.base.get_source_type(file_path)

        source = existing_source
        if not source:
            result = await db.execute(
                select(MediaSourceModel).where(MediaSourceModel.file_path == file_path)
            )
            source = result.scalar_one_or_none()

        if source:
            # Update existing source
            source.media_uuid = media_item.uuid
            source.file_size = file_info.get("size")
            source.resolution = file_info.get("resolution")
            source.last_verified = datetime.now(timezone.utc)
            source.is_available = True
        else:
            # Create new source
            source = MediaSourceModel(
                media_uuid=media_item.uuid,
                source_type=source_type,
                file_path=file_path,
                library_path_id=library_path.id,
                file_size=file_info.get("size"),
                resolution=file_info.get("resolution"),
                is_available=True,
                last_verified=datetime.now(timezone.utc),
                priority=0,
            )
            db.add(source)

        return True

    async def _create_tv_episode(
        self,
        db: AsyncSession,
        series_data: Dict,
        season_number: int,
        episode_number: int,
        file_path: str,
    ) -> MediaItemModel:
        """
        Create or get TV episode entry with proper parent relationship.

        1. Persist the series (if not exists)
        2. Create episode entry with parent_id pointing to series
        """
        # First, persist the series
        series_item = await self.resolver.persist(db, series_data)

        # Check if episode already exists
        result = await db.execute(
            select(MediaItemModel).where(
                MediaItemModel.parent_id == series_item.uuid,
                MediaItemModel.season_number == season_number,
                MediaItemModel.episode_number == episode_number,
            )
        )
        existing_episode = result.scalar_one_or_none()

        if existing_episode:
            return existing_episode

        # Create episode entry
        episode_uuid = str(uuid_lib.uuid4())
        episode_title = f"S{season_number:02d}E{episode_number:02d}"

        # Try to get episode title from series metadata if available
        if series_data.get("metadata"):
            seasons = series_data["metadata"].get("seasons", [])
            for season in seasons:
                if season.get("season_number") == season_number:
                    episodes = season.get("episodes", [])
                    for ep in episodes:
                        if ep.get("episode_number") == episode_number:
                            episode_title = ep.get("name", episode_title)
                            break
                    break

        episode_item = MediaItemModel(
            uuid=episode_uuid,
            media_type=MediaTypeEnum.EPISODE,
            title=episode_title,
            parent_id=series_item.uuid,
            season_number=season_number,
            episode_number=episode_number,
            is_persisted=True,
        )

        db.add(episode_item)
        await db.flush()
        return episode_item

    async def _create_user_media(
        self,
        db: AsyncSession,
        metadata: Dict,
        file_path: str,
        library_path: LibraryPath,
    ) -> MediaItemModel:
        """
        Create MediaItem for user content (no TMDB match).

        Handles:
        - Single videos (video type)
        - Video series with folder structure (video_series type)
        - Local images (poster.jpg, backdrop.jpg in same folder)
        """
        # Check if already exists via source
        result = await db.execute(
            select(MediaItemModel)
            .join(MediaSourceModel, MediaSourceModel.media_uuid == MediaItemModel.uuid)
            .where(MediaSourceModel.file_path == file_path)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        # Determine media type and check for series structure
        media_type = library_path.media_type
        parent_item = None
        season_number = metadata.get("season")
        episode_number = metadata.get("episode")

        # Check for video series structure
        if media_type in ["video", "video_series"]:
            (
                is_series,
                series_name,
                detected_season,
            ) = self.base.detect_video_series_structure(file_path, library_path.path)

            if is_series:
                # Get or create video series
                result = await db.execute(
                    select(MediaItemModel).where(
                        MediaItemModel.title == series_name,
                        MediaItemModel.media_type == MediaTypeEnum.VIDEO_SERIES,
                    )
                )
                parent_item = result.scalar_one_or_none()

                if not parent_item:
                    # Create the series item
                    series_uuid = str(uuid_lib.uuid4())

                    # Look for series-level images
                    series_dir = os.path.join(library_path.path, series_name)
                    series_images = self.base.find_local_images(series_dir)

                    parent_item = MediaItemModel(
                        uuid=series_uuid,
                        media_type=MediaTypeEnum.VIDEO_SERIES,
                        title=series_name,
                        image_paths=series_images if series_images else None,
                        is_persisted=True,
                    )
                    db.add(parent_item)
                    await db.flush()

                # This file is part of the series
                media_type = MediaTypeEnum.VIDEO
                if detected_season:
                    season_number = detected_season

        # Look for local images in the file's directory
        file_dir = os.path.dirname(file_path)
        image_paths = self.base.find_local_images(file_dir)

        # Create new MediaItem
        media_uuid = str(uuid_lib.uuid4())
        item = MediaItemModel(
            uuid=media_uuid,
            media_type=(
                media_type
                if isinstance(media_type, MediaTypeEnum)
                else MediaTypeEnum(media_type)
            ),
            title=metadata.get("title", "Unknown"),
            year=metadata.get("year"),
            parent_id=parent_item.uuid if parent_item else None,
            season_number=season_number,
            episode_number=episode_number,
            image_paths=image_paths if image_paths else None,
            is_persisted=True,
        )

        db.add(item)
        try:
            await db.flush()
        except Exception as e:
            # Skip this item if flush fails (likely due to concurrent access)
            log_service.warning(f"Flush failed for {file_path}, skipping: {e}")
            return None
        return item

    async def _prefetch_tmdb_results(self, metadata_list, media_type: str):
        """
        Prefetch TMDB results in parallel for better performance.

        Uses the TMDB service's caching mechanism.
        """
        if not self.tmdb:
            return

        # Collect unique search queries
        search_queries = set()
        for file_path, metadata in metadata_list:
            if metadata and metadata.get("title"):
                search_queries.add(
                    (metadata["title"], metadata.get("year"), media_type)
                )

        if not search_queries:
            return

        # Prefetch in parallel
        search_tasks = []
        for title, year, mt in search_queries:
            if mt == "movie":
                task = self.batch_processor.with_tmdb_limit(
                    self.tmdb.search_movies(title, 1, False)
                )
            else:
                task = self.batch_processor.with_tmdb_limit(
                    self.tmdb.search_tv(title, 1, False)
                )
            search_tasks.append(task)

        await asyncio.gather(*search_tasks, return_exceptions=True)
