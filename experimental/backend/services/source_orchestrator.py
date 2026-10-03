import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..addons.manager import plugin_manager
from ..models.library_path import LibraryPath
from ..models.media import MediaSourceModel
from ..models.media_item import MediaItem
from .log_service import log_service
from .settings_manager import SettingsManager
from .smart_match import smart_match_service
from .torrent_service import is_torrent

if TYPE_CHECKING:
    from .media_resolver import MediaResolver


class SourceOrchestrator:
    """
    Orchestrates source resolution from multiple layers:
    1. Database (Resolved/Persisted sources)
    2. Filesystem (JIT Scanning)
    3. Addons (External Stream Providers)
    """

    SCENE_TAGS = re.compile(
        r"[-.\s]\[?(WEB-DL|WEBRip|BluRay|BRRip|DVDRip|DVDScr|CAM|TS|TC|HDCAM|HDTS|VODRip|BDRip|MULTi|COMPLETE|REPACK|INTERNAL|Proper|READNFO|UNRATED|PROPER|REPACK|RERIP|DiRFIX|NFO|SAMPLE|SUBBED|DUBBED|x264|x265|k264|h264|h265|HEVC|AVC|RD\+|RD|Real-Debrid|Premiumize|Debrid|Direct|Torrent|Magnet)\]?",
        re.IGNORECASE,
    )
    YEAR_RE = re.compile(r"[-.\s\(](?P<year>19\d{2}|20\d{2})[\s\.\)]")
    RESOLUTION_RE = re.compile(
        r"[-.\s]\[?(?P<res>2160p|1080p|720p|480p|360p|4k|8k)\]?", re.IGNORECASE
    )
    TAGS_RE = re.compile(
        r"[-.\s]\[?(?P<tag>HDR10?|DV|Atmos|DTS|DD5\.1)\]?", re.IGNORECASE
    )

    def parse_source_name(self, name: str, media_title: str) -> Dict[str, Any]:
        """Parse source name to extract resolution, year, tags"""
        data = {"title": media_title, "resolution": "Unknown", "tag": None}

        # Extract resolution
        res_match = self.RESOLUTION_RE.search(name)
        if res_match:
            data["resolution"] = res_match.group("res")

        # Extract HDR/DV tags
        tags = []
        for tag_match in self.TAGS_RE.finditer(name):
            tags.append(tag_match.group("tag"))
        if tags:
            data["tag"] = " ".join(tags)

        return data

    async def get_sources(
        self,
        db: AsyncSession,
        media_uuid: Optional[str] = None,
        tmdb_id: Optional[int] = None,
        imdb_id: Optional[str] = None,
        media_type: str = "movie",
        title: str = "",
        year: Optional[int] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        transient_item: Optional[Dict[str, Any]] = None,
        resolver: Optional["MediaResolver"] = None,
    ) -> Dict[str, Any]:
        """
        Get all sources for a media item.
        Prioritizes: Local (DB) > Local (JIT with auto-persist) > Addon Streams

        JIT Auto-Persist: When JIT scanner finds matching files for a transient
        item (no uuid), the item is persisted to DB along with its sources.
        This enables lazy library building - only items with local files get persisted.

        Args:
            transient_item: If provided, this is a transient TMDB item that should
                           be persisted only if JIT finds matching files.
            resolver: MediaResolver instance needed to persist transient items.
        """
        sources = {
            "local": [],  # DB persisted + JIT found
            "streams": {},  # Addon provided
        }

        # 1. Fetch from Database (if UUID exists)
        if media_uuid:
            # If we're looking for a specific episode of a series, find its UUID
            lookup_uuid = media_uuid
            if season is not None and episode is not None and media_type in ("tv", "video_series"):
                ep_result = await db.execute(
                    select(MediaItem.uuid).where(
                        MediaItem.parent_id == media_uuid,
                        MediaItem.season_number == season,
                        MediaItem.episode_number == episode
                    )
                )
                lookup_uuid = ep_result.scalar_one_or_none()

            if lookup_uuid:
                result = await db.execute(
                    select(MediaSourceModel).where(
                        MediaSourceModel.media_uuid == lookup_uuid
                    )
                )
                db_sources = result.scalars().all()
                for s in db_sources:
                    sources["local"].append(
                        {
                            "id": s.id,
                            "source_type": s.source_type,
                            "file_path": s.file_path,
                            "stream_url": s.stream_url,
                            "stream_title": s.stream_title
                            or f"Local: {os.path.basename(s.file_path or '')}",
                            "resolution": s.resolution or "Unknown",
                            "is_persisted": True,
                        }
                    )

        # 2. JIT Filesystem Scanning (only if no DB sources found)
        # Skip scan entirely if we already have local sources - saves IO
        if not sources["local"] and title:
            jit_files = await smart_match_service.find_local_matches(
                db, media_type, title, year, season, episode
            )

            if jit_files:
                # If this is a transient item and JIT found files, persist it now
                if transient_item and resolver and not media_uuid:
                    persisted_item = await resolver.persist(
                        db, transient_item, is_persisted=False
                    )
                    media_uuid = persisted_item.uuid
                    await db.commit()
                    log_service.info(
                        f"JIT auto-persisted transient item '{title}' as {media_uuid}"
                    )

                # Auto-persist JIT matches to DB if we have a media_uuid
                if media_uuid:
                    await self._persist_jit_sources(
                        db, media_uuid, jit_files, media_type
                    )

                for file_path in jit_files:
                    filename = os.path.basename(file_path)
                    parsed = self.parse_source_name(filename, title)

                    sources["local"].append(
                        {
                            "id": None,  # Will have ID after refresh, but not critical for playback
                            "source_type": "local",
                            "file_path": file_path,
                            "stream_title": f"Local: {filename}",
                            "resolution": parsed["resolution"],
                            "tag": parsed["tag"],
                            "is_persisted": media_uuid is not None,
                        }
                    )

        # 3. Fetch from Addons (requires IMDb ID)
        if imdb_id:
            addon_streams = await plugin_manager.get_all_streams(
                imdb_id, media_type, season or 0, episode or 0
            )

            torrents_enabled = await SettingsManager(db).get("torrent_playback_enabled", True)

            # Post-process addon streams with our parsing
            for addon, streams in addon_streams.items():
                processed_streams = []
                for s in streams:
                    if not torrents_enabled and is_torrent(s.get("url") or ""):
                        continue
                    parsed = self.parse_source_name(s.get("title", ""), title)
                    processed_streams.append(
                        {
                            **s,
                            "resolution": s.get("resolution") or parsed["resolution"],
                            "tag": parsed.get("tag"),
                        }
                    )
                sources["streams"][addon] = processed_streams

        return sources

    async def _persist_jit_sources(
        self,
        db: AsyncSession,
        media_uuid: str,
        file_paths: List[str],
        media_type: str,
    ) -> None:
        """
        Persist JIT-discovered files to media_sources table.
        This ensures future requests skip the filesystem scan.
        """
        try:
            # Get library paths to find matching library_path_id
            result = await db.execute(
                select(LibraryPath).where(LibraryPath.media_type == media_type)
            )
            library_paths = {lp.path: lp.id for lp in result.scalars().all()}

            persisted_count = 0
            for file_path in file_paths:
                # Check if already exists
                existing = await db.execute(
                    select(MediaSourceModel).where(
                        MediaSourceModel.file_path == file_path
                    )
                )
                if existing.scalar_one_or_none():
                    continue  # Already persisted

                # Find matching library path
                library_path_id = None
                for lp_path, lp_id in library_paths.items():
                    if file_path.startswith(lp_path):
                        library_path_id = lp_id
                        break

                # Determine source type
                ext = os.path.splitext(file_path)[1].lower()
                source_type = "strm" if ext == ".strm" else "local"

                # Get file size
                file_size = None
                try:
                    file_size = os.path.getsize(file_path)
                except OSError:
                    pass

                # Parse resolution from filename
                filename = os.path.basename(file_path)
                parsed = self.parse_source_name(filename, "")

                # Create source entry
                new_source = MediaSourceModel(
                    media_uuid=media_uuid,
                    library_path_id=library_path_id,
                    source_type=source_type,
                    file_path=file_path,
                    file_size=file_size,
                    resolution=parsed["resolution"]
                    if parsed["resolution"] != "Unknown"
                    else None,
                    is_available=True,
                    priority=0,  # Local sources have highest priority
                )
                db.add(new_source)
                persisted_count += 1

            if persisted_count > 0:
                await db.commit()
                log_service.info(
                    f"JIT auto-persisted {persisted_count} source(s) for media {media_uuid}"
                )

        except Exception as e:
            log_service.error(f"Failed to persist JIT sources: {e}")
            # Don't fail the request, just log the error


source_orchestrator = SourceOrchestrator()
