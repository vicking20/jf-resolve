"""Stream resolution API routes"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.failover_manager import FailoverManager
from ..services.library_service import LibraryService
from ..services.log_service import log_service
from ..services.settings_manager import SettingsManager
from ..services.stremio_service import StremioService
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/resolve/{media_type}/{tmdb_id}")
async def resolve_stream(
    media_type: str,
    tmdb_id: int,
    quality: str = Query("1080p"),
    season: Optional[int] = Query(None),
    episode: Optional[int] = Query(None),
    index: int = Query(0),
    imdb_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve stream URL with failover
    Returns 302 redirect to actual stream URL from Stremio manifest
    """
    log_service.info(
        f"Stream resolve request: {media_type}/{tmdb_id} quality={quality} "
        f"index={index} imdb_id={imdb_id} season={season} episode={episode}"
    )

    if media_type not in ["movie", "tv"]:
        raise HTTPException(status_code=400, detail="Invalid media type")

    if media_type == "tv" and (season is None or episode is None):
        raise HTTPException(
            status_code=400, detail="Season and episode required for TV shows"
        )

    settings = SettingsManager(db)
    await settings.load_cache()

    tmdb = None
    api_key = await settings.get("tmdb_api_key")

    manifest_url = await settings.get("stremio_manifest_url")
    if not manifest_url:
        raise HTTPException(
            status_code=500, detail="Stremio manifest URL not configured"
        )

    stremio = StremioService(manifest_url)

    failover = FailoverManager(db)

    try:
        if media_type == "movie":
            state_key = f"movie:{tmdb_id}"
        else:
            state_key = f"tv:{tmdb_id}:{season}:{episode}"

        grace_seconds = await settings.get("failover_grace_seconds", 45)
        reset_seconds = await settings.get("failover_window_seconds", 120)

        state = await failover.get_state(state_key)

        should_increment, use_index = failover.should_failover(
            state, grace_seconds, reset_seconds
        )

        now = datetime.utcnow()
        if state.first_attempt is None:
            state.first_attempt = now
        state.last_attempt = now

        if should_increment:
            state.current_index = use_index
            state.attempt_count += 1
        else:
            use_index = state.current_index

        await failover.update_state(state)

        if not imdb_id:
            if not api_key:
                raise HTTPException(
                    status_code=500, detail="TMDB API key not configured"
                )
            tmdb = TMDBService(api_key)
            library = LibraryService(db, tmdb, settings)
            imdb_id = await library.get_or_fetch_imdb_id(tmdb_id, media_type)

        if not imdb_id:
            log_service.error(f"No IMDB ID found for {media_type}:{tmdb_id}")
            raise HTTPException(status_code=404, detail="IMDB ID not found")

        if media_type == "movie":
            streams = await stremio.get_movie_streams(imdb_id)
        else:
            streams = await stremio.get_episode_streams(imdb_id, season, episode)

        if not streams:
            log_service.error(
                f"Stremio addon returned zero streams for {state_key} (IMDb: {imdb_id})"
            )
            raise HTTPException(
                status_code=404, detail="No streams available from addon"
            )

        fallback_enabled = await settings.get("quality_fallback_enabled", True)
        fallback_order = await settings.get(
            "quality_fallback_order", ["1080p", "720p", "4k", "480p"]
        )

        target_quality = quality
        if not quality or quality == "auto":
            target_quality = await settings.get("series_preferred_quality", "1080p")

        stream_url = await stremio.select_stream(
            streams, target_quality, use_index, fallback_enabled, fallback_order
        )

        if not stream_url:
            log_service.error(
                f"Stream selection failed for {state_key}. Quality requested: {target_quality}, "
                f"Index: {use_index}, Total streams: {len(streams)}"
            )
            available_qualities = set(stremio.detect_quality(s) for s in streams)
            log_service.error(
                f"Available qualities in addon response: {available_qualities}"
            )
            raise HTTPException(
                status_code=404, detail="No suitable stream quality found"
            )

        log_service.stream(
            f"Resolved {state_key} quality={quality} index={use_index} attempt={state.attempt_count} → {stream_url[:100]}..."
        )

        return RedirectResponse(url=stream_url, status_code=302)

    except HTTPException:
        raise
    except Exception as e:
        log_service.error(f"Stream resolution error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to resolve stream: {str(e)}"
        )
    finally:
        if tmdb:
            await tmdb.close()
        await stremio.close()
