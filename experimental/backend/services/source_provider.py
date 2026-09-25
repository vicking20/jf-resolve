"""Source provider service - Query multiple source addons and aggregate streams"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.media_external_id import MediaExternalId
from ..models.media_item import MediaItem, MediaType
from .log_service import log_service
from .smart_match import SmartMatchService

SCENE_TAGS = re.compile(
    r"[-.\s]\[?(WEB-DL|WEBRip|BluRay|BRRip|DVDRip|DVDScr|CAM|TS|TC|HDCAM|HDTS|VODRip|BDRip|MULTi|COMPLETE|REPACK|INTERNAL|Proper|READNFO|UNRATED|PROPER|REPACK|RERIP|DiRFIX|NFO|SAMPLE|SUBBED|DUBBED|x264|x265|k264|h264|h265|HEVC|AVC|RD\+|RD|Real-Debrid|Premiumize|Debrid|Direct|Torrent|Magnet)\]?",
    re.IGNORECASE,
)

GROUP_TAGS = re.compile(r"-(?P<group>[a-zA-Z0-9]+)$")
YEAR_RE = re.compile(r"[-.\s\(](?P<year>19\d{2}|20\d{2})[\s\.\)]")
RESOLUTION_RE = re.compile(
    r"[-.\s]\[?(?P<res>2160p|1080p|720p|480p|360p|4k|8k)\]?", re.IGNORECASE
)
TAGS_RE = re.compile(r"[-.\s]\[?(?P<tag>HDR10?|DV|Atmos|DTS|DD5\.1)\]?", re.IGNORECASE)


def parse_source_name(name: str, media_title: str) -> Dict:
    """Parse source name to extract key information"""
    data = {"title": media_title, "resolution": None, "tag": None}

    # Extract year
    year_match = YEAR_RE.search(name)
    if year_match:
        data["year"] = year_match.group("year")

    # Extract resolution
    res_match = RESOLUTION_RE.search(name)
    if res_match:
        data["resolution"] = res_match.group("res")

    # Extract HDR/DV tags
    tags = []
    for tag_match in TAGS_RE.finditer(name):
        tags.append(tag_match.group("tag"))
    if tags:
        data["tag"] = " ".join(tags)

    # Clean title
    cleaned_name = name
    cleaned_name = SCENE_TAGS.sub("", cleaned_name)
    cleaned_name = GROUP_TAGS.sub("", cleaned_name)
    cleaned_name = YEAR_RE.sub("", cleaned_name)
    cleaned_name = RESOLUTION_RE.sub("", cleaned_name)
    cleaned_name = TAGS_RE.sub("", cleaned_name)
    cleaned_name = re.sub(r"[-.\s\[\]]+", " ", cleaned_name).strip()
    cleaned_name = re.sub(r"\s+", " ", cleaned_name)

    # Get file extension
    extension = ""
    if "." in cleaned_name:
        extension = "." + cleaned_name.split(".")[-1].lower()
        cleaned_name = cleaned_name.rsplit(".", 1)[0]

    data["title"] = cleaned_name or media_title
    data["extension"] = extension

    return data


def clean_source_name(original_name: str, media_title: str) -> str:
    """Clean and format source name for display"""
    if not original_name:
        return media_title

    # Parse and reconstruct clean name
    data = parse_source_name(original_name, media_title)
    final_name = data["title"]

    # Add year if available
    if data.get("year"):
        final_name += f" ({data['year']})"

    # Add resolution if available
    if data.get("resolution"):
        final_name += f" [{data['resolution']}]"

    # Add tags if available
    if data.get("tag"):
        final_name += f" [{data['tag']}]"

    return f"{final_name}{data['extension']}"


async def get_streams_from_addons(
    db: AsyncSession,
    imdb_id: str,
    media_type: str,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Dict:
    """Get streams from all enabled addons"""
    from ..addons.manager import plugin_manager

    # Convert season/episode to integers for addon compatibility
    season_int = season if season is not None else 0
    episode_int = episode if episode is not None else 0

    return await plugin_manager.get_all_streams(
        imdb_id, media_type, season_int, episode_int
    )


async def get_all_sources_for_media(
    db: AsyncSession,
    tmdb_id: int,
    media_type: str = "movie",
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Optional[Dict]:
    """
    Get all sources for a media item by TMDB ID.

    Returns combined local, strm, and stream sources along with media metadata.
    Used by stream_enhanced.py for best source selection.

    Args:
        db: Database session
        tmdb_id: TMDB ID of item
        media_type: "movie" or "tv"
        season: Season number (for TV episodes)
        episode: Episode number (for TV episodes)

    Returns:
        {
            "media": {"tmdb_id": ..., "title": ..., "media_type": ...},
            "sources": {"local": [...], "strm": [...], "streams": {...}}
        }
    """
    # Get media item from DB
    result = await db.execute(
        select(MediaItem)
        .join(MediaItem.external_ids)
        .where(
            MediaExternalId.provider == "tmdb",
            MediaExternalId.external_id == str(tmdb_id)
        )
    )
    media_item = result.scalar_one_or_none()

    if not media_item:
        return None

    # Get sources
    sources = await get_sources_for_tmdb_item(
        db=db,
        tmdb_id=tmdb_id,
        media_type=media_type,
        title=media_item.title,
        imdb_id=media_item.imdb_id,
        season=season,
        episode=episode,
    )

    return {
        "media": {
            "tmdb_id": tmdb_id,
            "title": media_item.title,
            "media_type": media_type,
            "imdb_id": media_item.imdb_id,
        },
        "sources": sources,
    }


async def get_sources_for_tmdb_item(
    db: AsyncSession,
    tmdb_id: int,
    media_type: str,
    title: str,
    imdb_id: Optional[str] = None,
    season: Optional[int] = None,
    episode: Optional[int] = None,
) -> Dict:
    """
    Unified source fetching for both library and virtual items.

    Updated to work with new Movie/Series models instead of old Media model.
    Uses stored imdb_id to avoid unnecessary TMDB queries.
    Now includes JIT scanning for virtual items.

    Args:
        db: Database session
        tmdb_id: TMDB ID of item
        media_type: "movie" or "tv"
        title: Media title (for formatting stream names)
        imdb_id: IMDB ID (required for stream fetching)
        season: Season number (for TV episodes)
        episode: Episode number (for TV episodes)

    Returns:
        {
            "local": [...],
            "strm": [...],
            "streams": {
                "AddonName": [...],
            }
        }
    """
    sources = {
        "local": [],
        "strm": [],
        "streams": {},
    }

    # Construct title for stream name formatting
    media_title = title
    if media_type == "tv" and season is not None and episode is not None:
        media_title = f"{title} S{str(season).zfill(2)}E{str(episode).zfill(2)}"

    # Check if item exists in library using new models
    result = await db.execute(
        select(MediaItem)
        .join(MediaItem.external_ids)
        .where(
            MediaExternalId.provider == "tmdb",
            MediaExternalId.external_id == str(tmdb_id)
        )
    )
    library_item = result.scalar_one_or_none()

    if library_item:
        # For new models, use stored imdb_id to avoid TMDB queries
        if library_item.imdb_id and not imdb_id:
            imdb_id = library_item.imdb_id
    else:
        # RUN JIT SCANNING for virtual items (not in library)
        # Extract year from title for matching
        year = None
        year_match = re.search(r'\((\d{4})\)', title)
        if year_match:
            year = int(year_match.group(1))
        
        # Find local matches using JIT scanning
        smart_match_service = SmartMatchService()
        local_files = await smart_match_service.find_local_matches(
            db, media_type, title, year
        )
        
        # Add found local files as sources
        for file_path in local_files:
            try:
                # Check if it's a STRM file
                if file_path.endswith('.strm'):
                    # Read STRM content to get actual URL
                    def read_strm():
                        with open(file_path, 'r') as f:
                            return f.read().strip()
                    
                    actual_url = await asyncio.to_thread(read_strm)
                    
                    sources["strm"].append({
                        "id": 0,  # Temporary ID for JIT sources
                        "source_type": "strm",
                        "file_path": file_path,
                        "is_available": True,
                        "resolution": "STRM",
                        "title": f"Local STRM: {os.path.basename(file_path)}",
                    })
                else:
                    # Regular local file
                    sources["local"].append({
                        "id": 0,  # Temporary ID for JIT sources
                        "source_type": "local", 
                        "file_path": file_path,
                        "is_available": True,
                        "resolution": "Local",
                        "title": f"Local: {os.path.basename(file_path)}",
                    })
            except Exception as e:
                # Log error but continue processing other files
                log_service.error(f"Failed to process JIT source {file_path}: {e}")
        
        if local_files:
            log_service.info(f"JIT scanning found {len(local_files)} local files for {title} ({media_type})")

    # Fetch streams from addons if we have an IMDB ID
    if imdb_id:
        raw_streams = await get_streams_from_addons(
            db, imdb_id, media_type, season, episode
        )

        # Post-process streams to format names nicely
        sources["streams"] = {
            addon: [
                {
                    **stream,
                    "title": (
                        parsed := parse_source_name(
                            name=stream.get("raw_title") or stream.get("title", ""),
                            media_title=media_title,
                        )
                    )["title"],
                    "tag": parsed["tag"],
                    "resolution": stream.get("resolution") or parsed["resolution"],
                }
                for stream in streams
            ]
            for addon, streams in raw_streams.items()
            if streams
        }

    return sources