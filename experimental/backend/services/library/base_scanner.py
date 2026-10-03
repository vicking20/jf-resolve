"""Base scanner utilities - shared by both Active and JIT scanners"""

import asyncio
import os
import re
from typing import Dict, List, Optional, Tuple

from ..tmdb_service import TMDBService

# Supported video extensions
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

# Common patterns for extracting metadata from filenames
# Supports both strict "Title - (Year)" and flexible "Title.2023" formats
MOVIE_PATTERNS = [
    r"^(?P<title>.+?)\s+-\s+\((?P<year>\d{4})\)",  # Strict: Movie Name - (2023).mkv
    r"^(?P<title>.+?)[.\s]+\((?P<year>\d{4})\)",  # Flexible: Movie Name (2023).mkv
    r"^(?P<title>.+?)[.\s]+(?P<year>\d{4})",  # Scene: Movie.Name.2023.1080p.mkv
    r"^(?P<title>.+)$",  # Fallback: Just the title
]

TV_PATTERNS = [
    r"^(?P<title>.+?)\s+-\s+S(?P<season>\d+)E(?P<episode>\d+)",  # Strict: Show Name - S01E05.mkv
    r"^(?P<title>.+?)[.\s]+S(?P<season>\d+)E(?P<episode>\d+)",  # Flexible: Show.Name.S01E05.mkv
    r"^(?P<title>.+?)[.\s]+(?P<season>\d+)x(?P<episode>\d+)",  # Flexible: Show.Name.1x05.mkv
    r"^(?P<title>.+)$",  # Fallback: Just the title
]

# Common quality/resolution patterns
QUALITY_RE = re.compile(
    r"[-.\s]\[?(?P<quality>2160p|1080p|720p|480p|360p|4k|8k)\]?", re.IGNORECASE
)
TAGS_RE = re.compile(
    r"[-.\s]\[?(?P<tag>HDR|HDR10|DV|Atmos|DTS|DD5\.1)\]?", re.IGNORECASE
)


class BaseScanner:
    """
    Shared scanning utilities used by both Active and JIT scanners.

    Provides:
    - File discovery
    - Metadata extraction from filenames
    - TMDB matching
    - Local image discovery
    - File info extraction
    """

    def __init__(self, tmdb: Optional[TMDBService] = None):
        self.tmdb = tmdb

    def find_video_files(self, root_path: str) -> List[str]:
        """Find all video files in directory recursively"""
        video_files = []

        for root, dirs, files in os.walk(root_path):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    video_files.append(os.path.join(root, file))

        return video_files

    async def find_video_files_async(self, root_path: str) -> List[str]:
        """Async version - runs in executor to avoid blocking"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.find_video_files, root_path)

    def extract_metadata_from_filename(self, filename: str, media_type: str) -> Dict:
        """
        Extract metadata from filename using regex patterns

        Returns dict with: title, year, season, episode, quality, tags
        """
        metadata = {}

        # Clean filename (remove extension)
        name = os.path.splitext(filename)[0]

        # Replace underscores with spaces (common in filenames)
        name = name.replace("_", " ")

        if media_type == "tv":
            # Try TV patterns
            for pattern in TV_PATTERNS:
                match = re.match(pattern, name, re.IGNORECASE)
                if match:
                    metadata = match.groupdict()
                    metadata["title"] = metadata["title"].replace(".", " ").strip()
                    if "season" in metadata and metadata["season"]:
                        metadata["season"] = int(metadata["season"])
                    if "episode" in metadata and metadata["episode"]:
                        metadata["episode"] = int(metadata["episode"])
                    metadata["media_type"] = "tv"
                    return metadata

            # Fallback
            # Fallback
            metadata["title"] = name.replace(".", " ").strip()
            metadata["media_type"] = "tv"
            return metadata

        elif media_type == "movie":
            # Extract Quality and Tags first
            quality_match = QUALITY_RE.search(name)
            if quality_match:
                metadata["quality"] = quality_match.group("quality").lower()
                name = name[: quality_match.start()] + name[quality_match.end() :]

            tag_match = TAGS_RE.search(name)
            if tag_match:
                metadata["tags"] = tag_match.group("tag")
                name = name[: tag_match.start()] + name[tag_match.end() :]

            # Try movie patterns
            for pattern in MOVIE_PATTERNS:
                match = re.match(pattern, name.strip(), re.IGNORECASE)
                if match:
                    metadata.update(match.groupdict())
                    metadata["title"] = metadata["title"].replace(".", " ").strip()
                    if "year" in metadata and metadata["year"]:
                        metadata["year"] = int(metadata["year"])
                    metadata["media_type"] = "movie"
                    return metadata

            # Fallback
            metadata["title"] = name.replace(".", " ").strip()
            metadata["media_type"] = "movie"
            return metadata

        # User / Home Video Fallback
        if media_type == "video" or media_type == "video_series":
            metadata["title"] = name.replace(".", " ").strip()
            metadata["media_type"] = media_type
            return metadata

        # Unknown type - return basic metadata
        metadata["title"] = name.replace(".", " ").strip()
        return metadata

    async def match_with_tmdb(
        self,
        title: str,
        year: Optional[int],
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Try to match content with TMDB

        Returns TMDB data dict or None if no match
        """
        if not self.tmdb:
            return None

        try:
            if media_type == "movie":
                results = await self.tmdb.search_movies(title)
            else:
                results = await self.tmdb.search_tv(title)

            if not results or not results.get("results"):
                return None

            # Filter and rank results
            matches = []
            for res in results["results"]:
                res_title = res.get("title") or res.get("name")
                res_date = res.get("release_date") or res.get("first_air_date")
                res_year = int(res_date.split("-")[0]) if res_date else None

                # 1. Exact Title Match
                title_match = res_title and res_title.lower() == title.lower()

                # 2. Year Match logic
                year_match = False
                if year and res_year:
                    # Allow +/- 1 year difference (TMDB dates vs file dates can vary)
                    if abs(res_year - year) <= 1:
                        year_match = True
                elif not year:
                    # If no year in filename, we don't penalize based on year
                    year_match = True

                if title_match and year_match:
                    # Strongest match - fetch full details
                    tmdb_id = res.get("id")
                    if tmdb_id:
                        details = await self.tmdb.get_details(tmdb_id, media_type)
                        if details:
                            return self.tmdb.parse_media_item(details, media_type)
                    return res

                if title_match or (year and year_match):
                    matches.append(res)

            # If we have a year and no title+year match, we should be careful
            if year and not any(
                int(
                    (r.get("release_date") or r.get("first_air_date") or "0").split(
                        "-"
                    )[0]
                )
                == year
                for r in matches
            ):
                return None

            # Fallback to the first match if it exists - fetch full details
            if matches:
                tmdb_id = matches[0].get("id")
                if tmdb_id:
                    details = await self.tmdb.get_details(tmdb_id, media_type)
                    if details:
                        return self.tmdb.parse_media_item(details, media_type)
                return matches[0]

            return None

        except Exception:
            return None

    async def get_file_info(self, file_path: str) -> Dict:
        """
        Get file information (just file size)

        Resolution, codec, etc. comes from stream sources, not local files
        """
        try:
            return {
                "size": os.path.getsize(file_path),
            }
        except OSError:
            return {}


    def find_local_images(self, directory: str) -> Dict[str, str]:
        """
        Find local poster/backdrop images in a directory

        Looks for:
        - poster.jpg, poster.png, folder.jpg
        - backdrop.jpg, backdrop.png, fanart.jpg
        """
        images = {}

        if not os.path.isdir(directory):
            return images

        poster_names = [
            "poster.jpg",
            "poster.png",
            "folder.jpg",
            "cover.jpg",
            "cover.png",
        ]
        backdrop_names = [
            "backdrop.jpg",
            "backdrop.png",
            "fanart.jpg",
            "fanart.png",
            "background.jpg",
        ]

        try:
            files_lower = {f.lower(): f for f in os.listdir(directory)}

            for name in poster_names:
                if name in files_lower:
                    images["poster"] = os.path.join(directory, files_lower[name])
                    break

            for name in backdrop_names:
                if name in files_lower:
                    images["backdrop"] = os.path.join(directory, files_lower[name])
                    break
        except OSError:
            pass

        return images

    def detect_video_series_structure(
        self, file_path: str, library_root: str
    ) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Detect if a video file is part of a series based on folder structure.

        Returns: (is_series, series_name, season_number)

        Examples:
        /Videos/vacation_2024.mp4 -> (False, None, None)
        /Videos/Family Events/Birthday/clip1.mp4 -> (True, "Family Events", None)
        /Videos/Family Events/Season 1/clip1.mp4 -> (True, "Family Events", 1)
        """
        relative_path = os.path.relpath(file_path, library_root)
        path_parts = relative_path.split(os.sep)

        if len(path_parts) == 1:
            # File directly in library root = standalone video
            return False, None, None

        # File in subfolder = part of series
        series_name = path_parts[0]

        # Detect season from folder structure
        season_number = None
        if len(path_parts) >= 3:
            season_folder = path_parts[1]
            # Try to extract number from "Season 1", "S01", "Part 2", etc.
            match = re.search(r"(\d+)", season_folder)
            if match:
                season_number = int(match.group(1))

        return True, series_name, season_number

    def get_source_type(self, file_path: str) -> str:
        """Determine source type from file extension"""
        ext = os.path.splitext(file_path)[1].lower()
        return "strm" if ext == ".strm" else "local"
