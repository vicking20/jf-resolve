"""Metadata management service - NFO files and image caching"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Union

import httpx

logger = logging.getLogger(__name__)


class MetadataService:
    """Manage metadata caching, NFO files, and image downloads"""

    def __init__(self, metadata_dir: Path = None):
        if metadata_dir is None:
            metadata_dir = Path("./data/metadata")

        self.metadata_dir = metadata_dir
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        (self.metadata_dir / "movies").mkdir(exist_ok=True)
        (self.metadata_dir / "tv").mkdir(exist_ok=True)

    def get_media_dir(self, media_type: str, item_id: Union[int, str]) -> Path:
        """
        Get directory for media metadata
        item_id can be TMDB ID (int/str) or Cache Key (str)
        """
        if media_type == "movie":
            type_dir = "movies"
        elif media_type in ["series", "tv"]:
            type_dir = "tv"
        else:
            type_dir = "videos"

        dir_name = str(item_id)

        media_dir = self.metadata_dir / type_dir / dir_name
        media_dir.mkdir(parents=True, exist_ok=True)
        return media_dir

    async def save_metadata(
        self,
        media_type: str,
        item_id: Union[int, str],
        tmdb_data: dict,
        download_images: bool = True,
    ):
        """Save metadata (images only, NFO removed) for a media item"""
        try:
            media_dir = self.get_media_dir(media_type, item_id)

            if download_images:
                poster_path = tmdb_data.get("poster_path")
                if poster_path:
                    await self.cache_image(media_type, item_id, "poster", poster_path)
                
                backdrop_path = tmdb_data.get("backdrop_path")
                if backdrop_path:
                    await self.cache_image(media_type, item_id, "backdrop", backdrop_path)

            logger.info(f"Updated cached images for {media_type}:{item_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update images for {media_type}:{item_id}: {e}")
            return False


    def is_cache_valid(self, file_path: Path, max_age_days: int = 30) -> bool:
        """Check if cached file is still valid based on age"""
        try:
            import time

            file_age_seconds = time.time() - file_path.stat().st_mtime
            file_age_days = file_age_seconds / (24 * 3600)
            return max_age_days != 0 and file_age_days < max_age_days
        except Exception:
            return False


    async def clear_expired_cache(self, max_age_days: int = 30, protected_ids: Optional[set] = None):
        """Clear expired cache entries, avoiding protected IDs (library items)"""
        cleared_count = 0
        protected_ids = protected_ids or set()
        try:
            for type_dir in ["movies", "series"]:
                type_path = self.metadata_dir / type_dir
                if not type_path.exists():
                    continue

                for media_dir in type_path.iterdir():
                    if not media_dir.is_dir():
                        continue

                    try:
                        dir_id = int(media_dir.name)
                        if dir_id in protected_ids:
                            continue
                    except ValueError:
                        pass

                    should_remove = False
                    poster_path = media_dir / "poster.jpg"

                    if not poster_path.exists():
                        should_remove = True
                    elif not self.is_cache_valid(poster_path, max_age_days):
                        should_remove = True

                    if should_remove:
                        import shutil

                        shutil.rmtree(media_dir)
                        cleared_count += 1
                        logger.info(f"Cleared expired/broken cache: {media_dir}")

            logger.info(f"Cleared {cleared_count} expired cache entries")
            return cleared_count

        except Exception as e:
            logger.error(f"Failed to clear expired cache: {e}")
            return 0

    async def cache_image(
        self, media_type: str, tmdb_id: int, image_type: str, tmdb_path: str, protected_ids: Optional[set] = None
    ) -> Optional[Path]:
        """
        Download and cache a specific image from TMDB

        Args:
            media_type: 'movie', 'tv'
            tmdb_id: The TMDB ID
            image_type: 'poster' or 'backdrop'
            tmdb_path: The path suffix from TMDB (e.g. /abc.jpg)
        """
        if not tmdb_path:
            return None

        import os

        try:
            target_dir = self.get_media_dir(media_type, tmdb_id)
            filename = f"{image_type}.jpg"
            size = "w500" if image_type == "poster" else "original"

            target_path = target_dir / filename

            if target_path.exists() and target_path.stat().st_size > 0:
                return target_path

            url = f"https://image.tmdb.org/t/p/{size}{tmdb_path}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.warning(
                        f"Failed to download image {url}: {response.status_code}"
                    )
                    return None

                tmp_path = target_path.with_suffix(".tmp")
                with open(tmp_path, "wb") as f:
                    f.write(response.content)

                os.replace(tmp_path, target_path)
                logger.info(f"Cached {image_type} for {media_type}:{tmdb_id}")

                import random

                if random.random() < 0.05:
                    asyncio.create_task(self.enforce_cache_size_limit(protected_ids=protected_ids))

                return target_path

        except Exception as e:
            logger.error(f"Error caching image {media_type}:{tmdb_id}: {e}")
            return None

    def get_cache_size_mb(self) -> float:
        """Get total size of metadata cache in MB"""
        total_bytes = 0
        for root, _, files in os.walk(self.metadata_dir):
            for f in files:
                try:
                    fp = Path(root) / f
                    total_bytes += fp.stat().st_size
                except:
                    pass
        return total_bytes / (1024 * 1024)

    async def enforce_cache_size_limit(self, max_size_mb: int = 500, protected_ids: Optional[set] = None):
        """
        Enforce cache size limit by evicting oldest items, avoiding protected IDs.
        """
        import time
        import shutil

        protected_ids = protected_ids or set()
        try:
            current_size = self.get_cache_size_mb()
            if current_size <= max_size_mb:
                return

            logger.info(
                f"Cache size ({current_size:.2f} MB) exceeds limit ({max_size_mb} MB). Evicting..."
            )

            media_dirs = []

            for type_dir in ["movies", "series"]:
                type_path = self.metadata_dir / type_dir
                if not type_path.exists():
                    continue

                for m_dir in type_path.iterdir():
                    if m_dir.is_dir():
                        try:
                            dir_id = int(m_dir.name)
                            if dir_id in protected_ids:
                                continue
                        except ValueError:
                            pass

                        try:
                            mtime = m_dir.stat().st_mtime
                            poster_path = m_dir / "poster.jpg"
                            if poster_path.exists():
                                mtime = poster_path.stat().st_mtime

                            media_dirs.append((mtime, m_dir))
                        except:
                            pass

            media_dirs.sort(key=lambda x: x[0])

            target_size = max_size_mb * 0.8
            deleted_count = 0

            for _, m_dir in media_dirs:
                if current_size <= target_size:
                    break

                dir_size = 0
                for f in m_dir.rglob("*"):
                    if f.is_file():
                        dir_size += f.stat().st_size

                dir_size_mb = dir_size / (1024 * 1024)

                shutil.rmtree(m_dir)
                current_size -= dir_size_mb
                deleted_count += 1

            logger.info(
                f"Eviction complete. Removed {deleted_count} items. New size: {current_size:.2f} MB"
            )

        except Exception as e:
            logger.error(f"Error during cache eviction: {e}")


metadata_service = MetadataService()
