"""Library management service"""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.library_item import LibraryItem
from .log_service import log_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


class LibraryService:
    """Manage library items and STRM files"""

    def __init__(self, db: AsyncSession, tmdb: TMDBService, settings: SettingsManager):
        self.db = db
        self.tmdb = tmdb
        self.settings = settings

    async def _get_stream_server_url(self) -> str:
        """
        Get the stream server URL for STRM file generation.
        """
        stream_url = await self.settings.get("stream_server_url")
        if stream_url:
            return stream_url.rstrip("/")
        resolve_url = await self.settings.get("jfresolve_server_url")
        if resolve_url:
            parsed = urlparse(resolve_url)
            scheme = parsed.scheme or "http"
            hostname = parsed.hostname or "localhost"
            return f"{scheme}://{hostname}:8766"
        return "http://127.0.0.1:8766"

    async def is_in_library(self, tmdb_id: int, media_type: str) -> bool:
        """Check if item is already in library"""
        result = await self.db.execute(
            select(LibraryItem).where(
                LibraryItem.tmdb_id == tmdb_id, LibraryItem.media_type == media_type
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_or_fetch_imdb_id(
        self, tmdb_id: int, media_type: str
    ) -> Optional[str]:
        """
        Get IMDB ID from cache (library_items) or fetch from TMDB
        """
        result = await self.db.execute(
            select(LibraryItem).where(
                LibraryItem.tmdb_id == tmdb_id, LibraryItem.media_type == media_type
            )
        )
        item = result.scalar_one_or_none()

        if item and item.imdb_id:
            return item.imdb_id
        imdb_id = await self.tmdb.get_imdb_id(tmdb_id, media_type)
        if item and imdb_id:
            item.imdb_id = imdb_id
            await self.db.commit()

        return imdb_id

    async def add_to_library(
        self,
        tmdb_id: int,
        media_type: str,
        quality_versions: List[str],
        user_id: int = 1,
        added_via: str = "search",
    ) -> LibraryItem:
        """
        Add item to library and create STRM files
        """
        if await self.is_in_library(tmdb_id, media_type):
            raise ValueError(f"{media_type}:{tmdb_id} already in library")
        if media_type == "movie":
            details = await self.tmdb.get_movie_details(tmdb_id)
            title = details.get("title", "Unknown")
            year = None
            if details.get("release_date"):
                try:
                    year = int(details["release_date"].split("-")[0])
                except (ValueError, IndexError):
                    pass

            total_seasons = None
            total_episodes = None
        else:  # TV show
            details = await self.tmdb.get_tv_details(tmdb_id)
            title = details.get("name", "Unknown")
            year = None
            if details.get("first_air_date"):
                try:
                    year = int(details["first_air_date"].split("-")[0])
                except (ValueError, IndexError):
                    pass

            total_seasons = details.get("number_of_seasons", 0)
            total_episodes = details.get("number_of_episodes", 0)

        # Get IMDB ID
        imdb_id = await self.tmdb.get_imdb_id(tmdb_id, media_type)

        if not imdb_id:
            log_service.error(f"No IMDB ID found for {media_type}:{tmdb_id}")
            raise ValueError("IMDB ID not found - cannot create STRM files")

        is_anime = self.tmdb.is_anime(details)
        folder_path = await self._get_folder_path(media_type, is_anime, added_via)

        folder_name = self._get_folder_name(title, year)
        full_path = Path(folder_path) / folder_name

        item = LibraryItem(
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            media_type=media_type,
            title=title,
            year=year,
            poster_path=details.get("poster_path"),
            backdrop_path=details.get("backdrop_path"),
            overview=details.get("overview"),
            total_seasons=total_seasons,
            total_episodes=total_episodes,
            folder_path=str(full_path),
            quality_versions=json.dumps(quality_versions),
            added_by_user_id=user_id,
            added_via=added_via,
        )

        self.db.add(item)

        await self._create_strm_files(item, details, quality_versions)
        await self.db.commit()
        await self.db.refresh(item)
        await self._trigger_jellyfin_scan()

        log_service.info(f"Added to library: {media_type}:{tmdb_id} - {title}")

        return item

    async def _get_folder_path(
        self, media_type: str, is_anime: bool, added_via: str
    ) -> str:
        """Determine base folder path for item"""
        use_search_paths = await self.settings.get("use_separate_search_paths", False)
        if added_via == "search" and use_search_paths:
            if media_type == "movie":
                search_path = await self.settings.get("search_movie_path", "")
                if search_path:
                    if (
                        is_anime
                        and await self.settings.get("use_separate_anime_paths")
                        and await self.settings.get("use_separate_anime_search_paths")
                    ):
                        anime_search_path = await self.settings.get(
                            "anime_search_movie_path", ""
                        )
                        if anime_search_path:
                            return anime_search_path
                    return search_path
            else:  # TV
                search_path = await self.settings.get("search_tv_path", "")
                if search_path:
                    if (
                        is_anime
                        and await self.settings.get("use_separate_anime_paths")
                        and await self.settings.get("use_separate_anime_search_paths")
                    ):
                        anime_search_path = await self.settings.get(
                            "anime_search_tv_path", ""
                        )
                        if anime_search_path:
                            return anime_search_path
                    return search_path
        if is_anime and await self.settings.get("use_separate_anime_paths"):
            if media_type == "movie":
                anime_movie_path = await self.settings.get("anime_movie_path")
                if anime_movie_path:
                    return anime_movie_path
            else:  # TV
                anime_tv_path = await self.settings.get("anime_tv_path")
                if anime_tv_path:
                    return anime_tv_path
        if media_type == "movie":
            return await self.settings.get("jellyfin_movie_path", "/movies")
        else:
            return await self.settings.get("jellyfin_tv_path", "/tv")

    @staticmethod
    def _get_folder_name(title: str, year: Optional[int] = None) -> str:
        """
        Generate folder name
        """
        clean_title = LibraryService._sanitize_filename(title)

        if year:
            return f"{clean_title} ({year})"
        return f"{clean_title}"

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Remove invalid filesystem characters"""
        name = name.replace(":", "")
        invalid_chars = '<>"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, "")
        return name.strip()

    async def _create_strm_files(
        self, item: LibraryItem, details: Dict, quality_versions: List[str]
    ):
        """Create STRM files based on media type"""
        if item.media_type == "movie":
            await self._create_movie_strms(item, quality_versions)
        else:
            await self._create_tv_strms(item, details, quality_versions)

    async def _create_movie_strms(self, item: LibraryItem, qualities: List[str]):
        """
        Create movie STRM files
        Format: Movie Title (Year) - [quality].strm
        Also creates a .jfresolve marker file to identify JF-Resolve managed folders
        """
        folder_path = Path(item.folder_path)
        await asyncio.to_thread(folder_path.mkdir, parents=True, exist_ok=True)
        server_url = await self._get_stream_server_url()

        for quality in qualities:
            clean_title = self._sanitize_filename(item.title)
            if quality == "unknown":
                filename = f"{clean_title} ({item.year}).strm"
            else:
                filename = f"{clean_title} ({item.year}) - [{quality}].strm"

            strm_path = folder_path / filename

            base_url = f"{server_url}/api/stream/resolve/movie/{item.tmdb_id}?quality={quality}&index=0"
            stream_url = (
                f"{base_url}&imdb_id={item.imdb_id}" if item.imdb_id else base_url
            )

            await asyncio.to_thread(strm_path.write_text, stream_url)
            await asyncio.to_thread(strm_path.chmod, 0o644)

        marker_path = folder_path / ".jfresolve"
        await asyncio.to_thread(marker_path.write_text, "")

        metadata = {
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "media_type": "movie",
            "title": item.title,
            "year": item.year,
            "quality_versions": qualities,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

        metadata_path = folder_path / ".metadata.json"
        await asyncio.to_thread(
            metadata_path.write_text, json.dumps(metadata, indent=2)
        )

        log_service.info(
            f"Created STRM files for movie: {item.title} with qualities: {qualities}"
        )

    async def _create_tv_strms(
        self, item: LibraryItem, details: Dict, qualities: List[str]
    ):
        """
        Create TV show STRM files for all seasons and episodes
        Format: Show Name (Year) - S01E01 - Episode Title - [quality].strm
        Also creates a .jfresolve marker file to identify JF-Resolve managed folders
        """
        folder_path = Path(item.folder_path)
        await asyncio.to_thread(folder_path.mkdir, parents=True, exist_ok=True)

        server_url = await self._get_stream_server_url()

        num_seasons = details.get("number_of_seasons", 0)

        for season_num in range(1, num_seasons + 1):
            season_details = await self.tmdb.get_season_details(
                item.tmdb_id, season_num
            )
            episodes = season_details.get("episodes", [])

            season_folder = folder_path / f"Season {season_num:02d}"
            await asyncio.to_thread(season_folder.mkdir, parents=True, exist_ok=True)

            for episode in episodes:
                episode_num = episode.get("episode_number", 0)
                episode_title = episode.get("name", f"Episode {episode_num}")

                # Create single STRM file with 'auto' quality
                clean_title = self._sanitize_filename(item.title)
                filename = f"{clean_title} ({item.year}) - S{season_num:02d}E{episode_num:02d} - {self._sanitize_filename(episode_title)}.strm"
                strm_path = season_folder / filename

                base_url = f"{server_url}/api/stream/resolve/tv/{item.tmdb_id}?season={season_num}&episode={episode_num}&quality=auto&index=0"
                stream_url = (
                    f"{base_url}&imdb_id={item.imdb_id}" if item.imdb_id else base_url
                )

                await asyncio.to_thread(strm_path.write_text, stream_url)
                await asyncio.to_thread(strm_path.chmod, 0o644)

        # Create JF-Resolve marker file in the root folder
        marker_path = folder_path / ".jfresolve"
        await asyncio.to_thread(marker_path.write_text, "")

        # Update last checked season/episode
        item.last_season_checked = num_seasons
        if num_seasons > 0:
            last_season = await self.tmdb.get_season_details(item.tmdb_id, num_seasons)
            item.last_episode_checked = len(last_season.get("episodes", []))

        # Create metadata JSON
        metadata = {
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "media_type": "tv",
            "title": item.title,
            "year": item.year,
            "total_seasons": num_seasons,
            "quality_versions": qualities,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

        metadata_path = folder_path / ".metadata.json"
        await asyncio.to_thread(
            metadata_path.write_text, json.dumps(metadata, indent=2)
        )

        log_service.info(
            f"Created STRM files for TV show: {item.title} ({num_seasons} seasons)"
        )

    async def remove_from_library(self, item_id: int):
        """Remove item from library and delete STRM files"""
        result = await self.db.execute(
            select(LibraryItem).where(LibraryItem.id == item_id)
        )
        item = result.scalar_one_or_none()

        if not item:
            raise ValueError(f"Library item {item_id} not found")

        # Delete folder (verify .jfresolve marker exists for safety)
        folder_path = Path(item.folder_path)

        # Check if folder contains .jfresolve marker file to verify it's safe to delete
        marker_path = folder_path / ".jfresolve"
        if not marker_path.exists():
            raise ValueError(
                "Folder does not contain .jfresolve marker file, refusing to delete for safety"
            )

        if folder_path.exists():
            shutil.rmtree(folder_path)
            log_service.info(f"Deleted folder: {folder_path}")

        # Delete from database
        await self.db.delete(item)
        await self.db.commit()

        # Trigger Jellyfin scan
        await self._trigger_jellyfin_scan()

        log_service.info(
            f"Removed from library: {item.media_type}:{item.tmdb_id} - {item.title}"
        )

    async def purge_all_jfr_items(self) -> Dict:
        """Delete all items with .jfresolve marker file from library"""
        result = await self.db.execute(select(LibraryItem))
        items = result.scalars().all()

        deleted_count = 0
        for item in items:
            folder_path = Path(item.folder_path)

            # Check if folder contains .jfresolve marker file
            marker_path = folder_path / ".jfresolve"
            has_marker = marker_path.exists() if folder_path.exists() else False

            # Only delete if folder has .jfresolve marker
            if has_marker:
                shutil.rmtree(folder_path)
                deleted_count += 1

        # Clear database
        for item in items:
            await self.db.delete(item)

        await self.db.commit()

        # Trigger Jellyfin scan
        await self._trigger_jellyfin_scan()

        log_service.info(f"Purged {deleted_count} items from library")

        return {
            "deleted_count": deleted_count,
            "message": f"Successfully deleted {deleted_count} items",
        }

    async def refresh_item(self, item_id: int) -> Dict:
        """
        Refresh metadata and check for new episodes (TV shows)
        """
        result = await self.db.execute(
            select(LibraryItem).where(LibraryItem.id == item_id)
        )
        item = result.scalar_one_or_none()

        if not item:
            raise ValueError(f"Library item {item_id} not found")

        if item.media_type == "movie":
            # For movies, just update metadata
            details = await self.tmdb.get_movie_details(item.tmdb_id)
            item.poster_path = details.get("poster_path")
            item.backdrop_path = details.get("backdrop_path")
            item.overview = details.get("overview")
            await self.db.commit()

            return {"new_episodes": 0, "message": "Metadata updated"}

        else:  # TV show
            # Fetch latest details
            details = await self.tmdb.get_tv_details(item.tmdb_id)
            current_seasons = details.get("number_of_seasons", 0)

            # Check for new seasons/episodes
            new_episodes = 0
            qualities = (
                json.loads(item.quality_versions)
                if item.quality_versions
                else ["1080p"]
            )
            # Get Stream Server URL - intelligently derived from Jellyfin URL
            server_url = await self._get_stream_server_url()

            for season_num in range(item.last_season_checked + 1, current_seasons + 1):
                season_details = await self.tmdb.get_season_details(
                    item.tmdb_id, season_num
                )
                episodes = season_details.get("episodes", [])

                # Create season folder
                folder_path = Path(item.folder_path)
                season_folder = folder_path / f"Season {season_num:02d}"
                await asyncio.to_thread(
                    season_folder.mkdir, parents=True, exist_ok=True
                )

                for episode in episodes:
                    episode_num = episode.get("episode_number", 0)
                    episode_title = episode.get("name", f"Episode {episode_num}")

                    # Create single STRM file with 'auto' quality
                    clean_title = self._sanitize_filename(item.title)
                    filename = f"{clean_title} ({item.year}) - S{season_num:02d}E{episode_num:02d} - {self._sanitize_filename(episode_title)}.strm"
                    strm_path = season_folder / filename

                    # Only create if doesn't exist
                    if not await asyncio.to_thread(strm_path.exists):
                        stream_url = f"{server_url}/api/stream/resolve/tv/{item.tmdb_id}?season={season_num}&episode={episode_num}&quality=auto&index=0"
                        await asyncio.to_thread(strm_path.write_text, stream_url)
                        await asyncio.to_thread(strm_path.chmod, 0o644)
                        new_episodes += 1

            # Update metadata
            item.total_seasons = current_seasons
            item.total_episodes = details.get("number_of_episodes", 0)
            item.last_season_checked = current_seasons
            if current_seasons > 0:
                last_season = await self.tmdb.get_season_details(
                    item.tmdb_id, current_seasons
                )
                item.last_episode_checked = len(last_season.get("episodes", []))

            await self.db.commit()

            if new_episodes > 0:
                await self._trigger_jellyfin_scan()
                log_service.info(f"Added {new_episodes} new episodes for {item.title}")

            return {
                "new_episodes": new_episodes,
                "message": f"Added {new_episodes} new episodes",
            }

    async def _trigger_jellyfin_scan(self, specific_path: str = None):
        """
        Trigger Jellyfin library scan if enabled

        Args:
            specific_path: Optional path to scan (faster than full library scan)
        """
        if not await self.settings.get("trigger_jellyfin_scan", False):
            return

        jellyfin_url = await self.settings.get("jellyfin_server_url")
        api_key = await self.settings.get("jellyfin_api_key")

        if not jellyfin_url or not api_key:
            return

        try:
            async with httpx.AsyncClient() as client:
                if specific_path:
                    # Targeted scan - only scan specific path (much faster)
                    response = await client.post(
                        f"{jellyfin_url}/Library/Media/Updated",
                        headers={"X-Emby-Token": api_key},
                        json={
                            "Updates": [
                                {"Path": specific_path, "UpdateType": "Created"}
                            ]
                        },
                    )
                else:
                    # Full library refresh (slower)
                    response = await client.post(
                        f"{jellyfin_url}/Library/Refresh",
                        headers={"X-Emby-Token": api_key},
                    )

                response.raise_for_status()
                log_service.info(
                    f"Triggered Jellyfin scan{' for ' + specific_path if specific_path else ''}"
                )
        except Exception as e:
            log_service.error(f"Failed to trigger Jellyfin scan: {e}")
