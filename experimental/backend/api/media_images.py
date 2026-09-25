"""Media images proxy API"""

import os
import re
from pathlib import Path
from typing import Optional

from ..config import settings as app_settings

import aiofiles
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.session import get_current_user
from ..database import get_db
from ..models.media import MediaType as MediaTypeEnum
from ..models.media_item import MediaItem
from ..services.metadata_service import MetadataService
from ..services.settings_manager import SettingsManager
from ..services.tmdb_service import TMDBService

router = APIRouter(
    prefix="/api/media/images",
    tags=["media_images"],
    dependencies=[Depends(get_current_user)],
)

MEDIA_TYPES = {"movie", "tv", "series", "video", "video_series"}
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
TMDB_IMAGE_PATH = re.compile(r"/[A-Za-z0-9_-]+\.(jpg|jpeg|png|webp|svg)")


def _validate_request(media_type: str, item_id, path: Optional[str] = None) -> None:
    """Reject values that would be used to build cache paths or TMDB URLs unsafely."""
    if media_type not in MEDIA_TYPES or not SAFE_ID.fullmatch(str(item_id)):
        raise HTTPException(status_code=400, detail="Invalid media identifier")
    # User-content handlers ignore ``path``; only TMDB paths build download URLs.
    if media_type not in ("video", "video_series") and path is not None and not TMDB_IMAGE_PATH.fullmatch(path):
        raise HTTPException(status_code=400, detail="Invalid image path")


async def _get_local_file_path(
    media_type: str, item_id: str | int, image_type: str
) -> Optional[Path]:
    """Get path to local cached image if it exists"""
    # Initialize metadata service (paths are standard)
    metadata = MetadataService()
    media_dir = metadata.get_media_dir(media_type, item_id)

    # Common extensions
    extensions = [".jpg", ".jpeg", ".png", ".webp"]

    # Check for direct file (e.g. poster.jpg)
    for ext in extensions:
        file_path = media_dir / f"{image_type}{ext}"
        if file_path.exists():
            return file_path

    return None


@router.get("/poster/{media_uuid}")
async def get_poster_unified(
    media_uuid: str,
    db: AsyncSession = Depends(get_db),
):
    """Get poster image by media UUID"""
    return await _handle_image_unified(db, media_uuid, "poster")


@router.get("/backdrop/{media_uuid}")
async def get_backdrop_unified(
    media_uuid: str,
    db: AsyncSession = Depends(get_db),
):
    """Get backdrop image by media UUID"""
    return await _handle_image_unified(db, media_uuid, "backdrop")


async def _handle_image_unified(
    db: AsyncSession,
    media_uuid: str,
    image_type: str,
):
    """Unified handler for posters/backdrops by UUID"""
    from fastapi.responses import RedirectResponse

    # 1. Resolve MediaItem
    result = await db.execute(
        select(MediaItem)
        .where(MediaItem.uuid == media_uuid)
        .options(selectinload(MediaItem.external_ids))
    )
    item = result.scalar_one_or_none()
    if not item:
        # Fallback to default if not found
        default_path = (Path(app_settings.STATIC_DIR) / "images" / "default_poster.png").resolve()
        return FileResponse(str(default_path))

    # 2. Get the image path from the image_paths JSON blob
    path_val = None
    if item.image_paths:
        path_val = item.image_paths.get(image_type)

    if path_val:
        # If it's a full URL, redirect
        if path_val.startswith("http"):
            return RedirectResponse(path_val)

        # If it's a local file path
        p = Path(path_val)
        if p.exists():
            return FileResponse(p)

    # 3. Check for metadata directory cache
    tmdb_id_for_cache = item.get_external_id("tmdb")
    if tmdb_id_for_cache:
        local_path = await _get_local_file_path(
            item.media_type, tmdb_id_for_cache, image_type
        )
        if local_path and local_path.exists():
            return FileResponse(local_path)

    # 4. Handle TMDB content proxy/cache
    tmdb_id = item.get_external_id("tmdb")
    if tmdb_id and path_val:
        # We need the path_val from metadata_blob if not in top-level image_paths
        # But usually TMDB path starts with /
        if path_val.startswith("/"):
            return await _proxy_tmdb_image(
                db, item.media_type, tmdb_id, image_type, path_val
            )

    # 5. Last resort: Default image
    default_path = (Path(app_settings.STATIC_DIR) / "images" / "default_poster.png").resolve()
    return FileResponse(str(default_path))


@router.get("/{media_type}/{item_id}/poster")
async def get_poster_legacy(
    media_type: str,
    item_id: str,
    path: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Legacy poster endpoint (remains for compatibility)"""
    _validate_request(media_type, item_id, path)
    if media_type in ("video", "video_series"):
        return await _handle_user_content_image(db, media_type, item_id, "poster", path)

    if not path:
        default_path = (Path(app_settings.STATIC_DIR) / "images" / "default_poster.png").resolve()
        return FileResponse(str(default_path))

    return await _proxy_tmdb_image(db, media_type, item_id, "poster", path)


@router.get("/{media_type}/{item_id}/backdrop")
async def get_backdrop_legacy(
    media_type: str,
    item_id: str,
    path: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Legacy backdrop endpoint (remains for compatibility)"""
    _validate_request(media_type, item_id, path)
    if media_type in ("video", "video_series"):
        return await _handle_user_content_image(
            db, media_type, item_id, "backdrop", path
        )

    if not path:
        return Response(status_code=404)

    return await _proxy_tmdb_image(db, media_type, item_id, "backdrop", path)


async def _handle_user_content_image(
    db: AsyncSession,
    media_type: str,
    item_id: str,
    image_type: str,
    path_param: str = None,
):
    """Handle images for user content (Legacy support)"""
    from fastapi.responses import RedirectResponse

    # Try to find MediaItem first since most things should be migrated
    result = await db.execute(select(MediaItem).where(MediaItem.uuid == item_id))
    item = result.scalar_one_or_none()

    if not item:
        default_path = (Path(app_settings.STATIC_DIR) / "images" / "default_poster.png").resolve()
        return FileResponse(str(default_path))

    path_val = item.poster_path if image_type == "poster" else None
    if not path_val and item.image_paths:
        path_val = item.image_paths.get(image_type)

    # Only paths stored on the media item may be served from disk; the query
    # parameter is client-controlled and is ignored here.
    if path_val:
        if path_val.startswith("http"):
            return RedirectResponse(path_val)
        p = Path(path_val)
        if p.exists():
            return FileResponse(p)

    metadata_poster = await _get_local_file_path(media_type, item_id, image_type)
    if metadata_poster and metadata_poster.exists():
        return FileResponse(metadata_poster)

    default_path = (Path(app_settings.STATIC_DIR) / "images" / "default_poster.png").resolve()
    return FileResponse(str(default_path))


async def _proxy_tmdb_image(
    db: AsyncSession, media_type: str, item_id: str, image_type: str, path: str
):
    """Proxy image from TMDB and cache it in background"""
    metadata = MetadataService()
    try:
        cached_path = await metadata.cache_image(media_type, item_id, image_type, path)
        if cached_path and cached_path.exists():
            return FileResponse(cached_path)
    except Exception as e:
        print(f"Failed to cache image: {e}")

    tmdb_url = f"https://image.tmdb.org/t/p/original{path}"
    if image_type == "poster":
        tmdb_url = f"https://image.tmdb.org/t/p/w500{path}"

    async def iterfile():
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", tmdb_url) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(iterfile(), media_type="image/jpeg")


@router.get("/{media_type}/{tmdb_id}/{image_type}")
async def get_media_image(
    media_type: str,
    tmdb_id: int,
    image_type: str,
    path: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Serve cached media images with JIT download capability and library protection"""
    _validate_request(media_type, tmdb_id, path)
    if image_type not in ("poster", "backdrop"):
        raise HTTPException(status_code=400, detail="Invalid image type")
    from ..models.media_external_id import MediaExternalId

    # 1. Try to return cached image first
    cached_path = await _get_local_file_path(media_type, tmdb_id, image_type)
    if cached_path and cached_path.exists():
        return FileResponse(cached_path)

    # 2. If not cached and path provided, download JIT
    if path:
        # Collect protected IDs to ensure we don't evict library items during JIT
        protected_ids = set()
        result = await db.execute(
            select(MediaExternalId.external_id).where(
                MediaExternalId.provider == "tmdb"
            )
        )
        protected_ids.update(
            [int(tid) for tid in result.scalars().all() if tid.isdigit()]
        )

        metadata = MetadataService()
        cached_path = await metadata.cache_image(
            media_type, tmdb_id, image_type, path, protected_ids=protected_ids
        )
        if cached_path and cached_path.exists():
            return FileResponse(cached_path)

    raise HTTPException(status_code=404, detail="Image not found")
