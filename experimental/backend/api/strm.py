"""Stream resolution for STRM files played by Jellyfin.

Jellyfin cannot hold a JF-Resolve session, so these requests authenticate with
the shared token written into every STRM file instead of a cookie.
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..addons.manager import plugin_manager
from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..services.log_service import log_service
from ..services.settings_manager import SettingsManager
from ..models.user import User
from ..services.strm_service import StrmService, get_strm_token
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/strm", tags=["strm"])


def pick_stream(streams_by_addon: dict, index: int = 0) -> Optional[str]:
    """Return the index-th playable URL, keeping each addon's own ranking.

    No quality re-sorting happens here: providers such as AIOStreams already
    rank their results (audio, release group, cache status), and we honor
    that order. Magnets and streams that need custom request headers are
    skipped because Jellyfin follows the redirect without them.
    """
    playable = []
    for streams in streams_by_addon.values():
        for stream in streams:
            url = stream.get("url") or ""
            if not url.startswith(("http://", "https://")) or stream.get("headers"):
                continue
            playable.append(url)
    if not playable:
        return None
    return playable[min(index, len(playable) - 1)]


@router.get("/play/{media_type}/{tmdb_id}")
async def play(
    media_type: str,
    tmdb_id: int,
    token: str = Query(...),
    season: Optional[int] = Query(None, ge=0),
    episode: Optional[int] = Query(None, ge=0),
    index: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    settings = SettingsManager(db)
    if not await settings.get("strm_generation_enabled", False):
        raise HTTPException(status_code=404, detail="STRM output is disabled")
    if not secrets.compare_digest(token, await get_strm_token(settings)):
        raise HTTPException(status_code=403, detail="Invalid STRM token")
    if media_type not in ("movie", "tv"):
        raise HTTPException(status_code=400, detail="media_type must be movie or tv")
    if media_type == "tv" and (season is None or episode is None):
        raise HTTPException(status_code=400, detail="season and episode are required for tv")

    tmdb = TMDBService(await settings.get("tmdb_api_key") or "")
    try:
        imdb_id = await tmdb.get_imdb_id(tmdb_id, media_type)
    finally:
        await tmdb.close()
    if not imdb_id:
        raise HTTPException(status_code=404, detail="No IMDB ID for this title")

    streams = await plugin_manager.get_all_streams(imdb_id, media_type, season, episode)
    url = pick_stream(streams, index)
    label = f"{media_type}:{tmdb_id}" + (f" S{season}E{episode}" if media_type == "tv" else "")
    if not url:
        log_service.error(f"STRM: no playable stream for {label}")
        raise HTTPException(status_code=404, detail="No playable stream found")

    log_service.info(f"STRM: {label} index={index} -> {url[:100]}")
    return RedirectResponse(url, status_code=302)


@router.post("/rewrite-movies")
async def rewrite_movies(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    """Apply the current STRM settings (such as versions per movie) to existing movies."""
    settings = SettingsManager(db)
    if not await settings.get("strm_generation_enabled", False):
        raise HTTPException(status_code=409, detail="STRM output is disabled")
    tmdb = TMDBService(await settings.get("tmdb_api_key") or "")
    try:
        return await StrmService(settings, tmdb).rewrite_movies()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await tmdb.close()
