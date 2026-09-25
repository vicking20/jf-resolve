"""Smart match service for discovery identification"""

import os
import re
from typing import List, Optional, Union

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.library_path import LibraryPath
from ..models.media import MediaSourceModel
from ..models.media_item import MediaItem


class SmartMatchService:
    """Matches TMDB items with local filesystem items proactively"""

    VIDEO_EXTENSIONS = {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".3gp",
        ".ts",
        ".m2ts",
        ".strm",
    }

    def _get_search_patterns(
        self, 
        title: str, 
        year: Optional[int], 
        season: Optional[int] = None, 
        episode: Optional[int] = None
    ) -> List[re.Pattern]:
        """Generate regex patterns for matching title, year, and SxxExx in filenames"""
        # Clean title for simpler matching
        clean_title = re.sub(r"[^\w\s]", "", title).lower()
        # Escaped title for regex, allowing flexible delimiters
        parts = clean_title.split()
        if not parts:
            return []

        # Allow any separators between words
        pattern_str = r"[\.\s\-_]*".join(re.escape(p) for p in parts)

        patterns = []
        
        # If TV show with season/episode
        if season is not None and episode is not None:
            # Common patterns: S01E01, 1x01, S1E1
            s_e_patterns = [
                rf"s0?{season}e0?{episode}",
                rf"{season}x0?{episode}",
                rf"s{season}\.e{episode}",
            ]
            for se_p in s_e_patterns:
                patterns.append(re.compile(rf"{pattern_str}.*?{se_p}", re.I))
        elif year:
            # Pattern: Title ... Year
            patterns.append(re.compile(rf"{pattern_str}.*?{year}", re.I))
        else:
            # Just title (as boundary or direct match)
            patterns.append(re.compile(rf"{pattern_str}", re.I))

        return patterns

    async def find_local_matches(
        self, 
        db: AsyncSession, 
        media_type: str, 
        title: str, 
        year: Optional[int],
        season: Optional[int] = None,
        episode: Optional[int] = None
    ) -> List[str]:
        """
        Find matching local files without creating DB entries (JIT)
        """
        # Get all enabled library paths of same media type
        # Note: In the new schema, we might need a more flexible mapping of media_type to library_type
        result = await db.execute(
            select(LibraryPath).where(
                and_(
                    LibraryPath.enabled == True,
                    LibraryPath.media_type == media_type,
                )
            )
        )
        library_paths = result.scalars().all()

        if not library_paths:
            return []

        patterns = self._get_search_patterns(title, year, season, episode)
        if not patterns:
            return []

        matched_files = []

        for lp in library_paths:
            if not os.path.exists(lp.path):
                continue

            # Walk the library path
            for root, _, files in os.walk(lp.path):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext not in self.VIDEO_EXTENSIONS:
                        continue

                    # Check patterns
                    filename_no_ext = os.path.splitext(file)[0].lower()
                    filename_clean = re.sub(r"[^\w\s]", " ", filename_no_ext)

                    if any(p.search(filename_clean) for p in patterns):
                        file_path = os.path.join(root, file)
                        matched_files.append(file_path)

        return matched_files

    async def scan_for_match(
        self,
        db: AsyncSession,
        media_uuid: str,
        media_type: str,
        title: str,
        year: Optional[int],
    ) -> int:
        """
        Proactively scan library paths for files matching this media item.
        If found, create MediaSourceModel entries.

        Args:
            db: Database session
            media_uuid: UUID of the media item
            media_type: "movie", "tv", etc.
            title: Media title
            year: Release year (optional)

        Returns:
            Number of sources found/linked
        """
        # Get all enabled library paths of same media type
        result = await db.execute(
            select(LibraryPath).where(
                and_(
                    LibraryPath.enabled == True,
                    LibraryPath.media_type == media_type,
                )
            )
        )
        library_paths = result.scalars().all()

        if not library_paths:
            return 0

        patterns = self._get_search_patterns(title, year)
        if not patterns:
            return 0

        found_sources = 0

        for lp in library_paths:
            if not os.path.exists(lp.path):
                continue

            # Walk the library path
            for root, _, files in os.walk(lp.path):
                for file in files:
                    ext = os.path.splitext(file)[1].lower()
                    if ext not in self.VIDEO_EXTENSIONS:
                        continue

                    # Check patterns
                    filename_no_ext = os.path.splitext(file)[0].lower()
                    filename_clean = re.sub(r"[^\w\s]", " ", filename_no_ext)

                    if any(p.search(filename_clean) for p in patterns):
                        file_path = os.path.join(root, file)

                        # Check if this source already exists in DB
                        source_res = await db.execute(
                            select(MediaSourceModel).where(
                                MediaSourceModel.file_path == file_path
                            )
                        )
                        existing_source = source_res.scalar_one_or_none()

                        if existing_source:
                            # Link to current media if it was orphaned or mis-matched
                            if existing_source.media_uuid != media_uuid:
                                existing_source.media_uuid = media_uuid
                                found_sources += 1
                        else:
                            # New source found!
                            new_source = MediaSourceModel(
                                media_uuid=media_uuid,
                                source_type="local",
                                file_path=file_path,
                                file_size=os.path.getsize(file_path),
                                library_path_id=lp.id,
                                is_available=True,
                            )
                            db.add(new_source)
                            found_sources += 1

        if found_sources > 0:
            await db.commit()

        return found_sources

    async def scan_item(self, db: AsyncSession, item: MediaItem) -> int:
        """Scan for local matches for a MediaItem"""
        return await self.scan_for_match(
            db=db,
            media_uuid=item.uuid,
            media_type=item.media_type.value,
            title=item.title,
            year=item.year,
        )


smart_match_service = SmartMatchService()
