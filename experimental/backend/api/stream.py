"""Enhanced streaming API with transcoding support"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.session import get_current_user
from ..database import get_db
from ..models.media import MediaSourceModel
from ..models.user import User
from ..services.access_control import ensure_item_allowed, ensure_media_uuid_allowed
from ..services.ffprobe_service import FFprobeService
from ..services.log_service import log_service
from ..services.media_resolver import MediaResolver
from ..services.settings_manager import SettingsManager
from ..services.source_orchestrator import source_orchestrator
from ..services.stream_service import stream_service
from ..services.transcode_manager import transcode_manager

router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.get("/source/{source_id}")
async def stream_by_source(
    source_id: int,
    request: Request,
    audio_track: Optional[int] = Query(None),
    force_transcode: bool = Query(False),
    auto_skip_invalid: bool = Query(True),
    range_header: Optional[str] = Header(None, alias="Range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream media from specific source with automatic transcoding

    - Detects client capabilities from User-Agent
    - Probes stream for codec information
    - Transcodes only if needed
    - Supports multiple audio tracks
    - Auto-skips invalid streams (< 2 min) for TMDB content only
    """
    from sqlalchemy import select

    result = await db.execute(
        select(MediaSourceModel).where(MediaSourceModel.id == source_id)
    )
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await ensure_media_uuid_allowed(db, current_user, source.media_uuid)

    settings = SettingsManager(db)
    await settings.load_cache()

    transcode_settings = {
        "transcode_enabled": await settings.get("transcode_enabled", True),
        "hardware_acceleration": await settings.get("hardware_acceleration", "none"),
        "transcode_preset": await settings.get("transcode_preset", "veryfast"),
        "hdr_tone_mapping": await settings.get("hdr_tone_mapping", True),
        "target_video_codec": await settings.get("target_video_codec", "h264"),
        "target_audio_codec": await settings.get("target_audio_codec", "aac"),
        "hls_segment_duration": await settings.get("hls_segment_duration", 4),
    }

    user_agent = request.headers.get("user-agent", "")

    log_service.info(
        f"Stream request: source_id={source_id}, "
        f"audio_track={audio_track}, force_transcode={force_transcode}"
    )

    return await stream_service.get_stream_response(
        source_path=source.file_path,
        range_header=range_header,
        user_agent=user_agent,
        transcode_settings=transcode_settings,
        force_transcode=force_transcode,
        audio_track=audio_track,
    )


@router.get("/media/{identifier}")
async def stream_best_source(
    identifier: str,
    request: Request,
    media_type: Optional[str] = Query(None, description="movie or tv"),
    season: Optional[int] = Query(None, description="Season number for TV"),
    episode: Optional[int] = Query(None, description="Episode number for TV"),
    source_index: int = Query(0),
    audio_track: Optional[int] = Query(None),
    force_transcode: bool = Query(False),
    auto_skip_invalid: bool = Query(True),
    range_header: Optional[str] = Header(None, alias="Range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream media from best available source with validation and failover.
    Uses unified identifier and SourceOrchestrator.
    """
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService

    settings = SettingsManager(db)
    api_key = await settings.get("tmdb_api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB API key not configured")

    resolver = MediaResolver(TMDBService(api_key))
    item = await resolver.resolve(db, identifier, media_type)
    if not item:
        raise HTTPException(status_code=404, detail="Media not found")
    await ensure_item_allowed(db, current_user, item)

    sources_data = await source_orchestrator.get_sources(
        db=db,
        media_uuid=item.get("uuid"),
        tmdb_id=item.get("tmdb_id"),
        imdb_id=item.get("imdb_id"),
        media_type=item["media_type"],
        title=item["title"],
        year=item.get("year"),
        season=season,
        episode=episode,
    )

    all_sources = []

    for source in sources_data["local"]:
        all_sources.append({"type": "local", "data": source, "priority": 0})

    for addon_name, streams in sources_data["streams"].items():
        for stream in streams:
            all_sources.append(
                {"type": "stream", "data": stream, "priority": 1, "addon": addon_name}
            )

    if not all_sources:
        raise HTTPException(status_code=404, detail="No sources available")

    all_sources.sort(key=lambda x: x["priority"])

    await settings.load_cache()
    transcode_settings = {
        "transcode_enabled": await settings.get("transcode_enabled", True),
        "hardware_acceleration": await settings.get("hardware_acceleration", "none"),
        "transcode_preset": await settings.get("transcode_preset", "veryfast"),
        "hdr_tone_mapping": await settings.get("hdr_tone_mapping", True),
        "target_video_codec": await settings.get("target_video_codec", "h264"),
        "target_audio_codec": await settings.get("target_audio_codec", "aac"),
        "hls_segment_duration": await settings.get("hls_segment_duration", 4),
    }

    min_duration = await settings.get("min_valid_duration_seconds", 120)
    user_agent = request.headers.get("user-agent", "")

    for idx, source_entry in enumerate(all_sources[source_index:], start=source_index):
        source_data = source_entry["data"]
        source_path = source_data.get("file_path") or source_data.get("url")

        if not source_path:
            continue

        log_service.info(
            f"Trying source {idx}: {source_entry['type']} - {source_path[:100]}"
        )

        if auto_skip_invalid:
            stream_url = await stream_service._resolve_source(source_path)
            validation = await FFprobeService.validate_stream(
                stream_url, item["media_type"], min_duration
            )
            if not validation["valid"]:
                log_service.info(
                    f"Skipping invalid source {idx}: {validation['reason']}"
                )
                continue

        try:
            if not item.get("is_persisted"):
                await resolver.persist(db, item)
                await db.commit()

            return await stream_service.get_stream_response(
                source_path=source_path,
                range_header=range_header,
                user_agent=user_agent,
                transcode_settings=transcode_settings,
                force_transcode=force_transcode,
                audio_track=audio_track,
            )
        except Exception as e:
            log_service.error(f"Source {idx} failed: {e}")
            continue

    raise HTTPException(status_code=404, detail="All sources failed")


@router.get("/source/{source_id}/info")
async def get_source_info(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get detailed information about a stream source

    Returns codec, audio tracks, resolution, etc.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(MediaSourceModel).where(MediaSourceModel.id == source_id)
    )
    source = result.scalar_one_or_none()

    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await ensure_media_uuid_allowed(db, current_user, source.media_uuid)

    stream_url = await stream_service._resolve_source(source.file_path)
    probe_info = await FFprobeService.probe_stream(stream_url)

    if not probe_info:
        raise HTTPException(status_code=500, detail="Failed to probe stream")

    return {
        "source_id": source_id,
        "source_type": source.source_type,
        "file_path": source.file_path,
        "probe_info": probe_info,
    }


@router.get("/info/source/{source_id}")
async def get_source_info_alias(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Alias for get_source_info to match frontend expectations"""
    return await get_source_info(source_id, db, current_user)


@router.get("/probe")
async def probe_remote_url(
    url: str = Query(..., description="Remote URL to probe"),
    current_user: User = Depends(get_current_user),
):
    """
    Probe a remote URL to get codec and audio track information

    Used by frontend to fetch audio tracks for remote/proxied streams
    """
    from ..core.url_guard import validate_source_url

    url = await stream_service._resolve_source(await validate_source_url(url, current_user))
    probe_info = await FFprobeService.probe_stream(url)

    if not probe_info:
        raise HTTPException(status_code=500, detail="Failed to probe stream")

    return probe_info


HLS_FILE_NAME = re.compile(r"[A-Za-z0-9_-]+\.(m3u8|ts|m4s|mp4|vtt)")


@router.get("/hls/{stream_id}/{file_name}")
async def serve_hls_segment(
    stream_id: str, file_name: str, current_user: User = Depends(get_current_user)
):
    """
    Serve HLS segments and playlists

    For playlists (.m3u8), we read content into memory to avoid race conditions
    with FFmpeg constantly rewriting the file. For segments (.ts), we use
    FileResponse for efficient streaming.
    """
    from fastapi.responses import Response

    if not HLS_FILE_NAME.fullmatch(file_name) or not transcode_manager.touch_stream(stream_id):
        raise HTTPException(status_code=404, detail="Segment not found")
    output_dir = transcode_manager.get_hls_output_dir(stream_id)
    file_path = output_dir / file_name

    if not file_path.exists():
        if file_name.endswith(".m3u8"):
            import asyncio
            for _ in range(5):
                await asyncio.sleep(0.1)
                if file_path.exists():
                    break
                    
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Segment not found")

    common_headers = {}

    if file_name.endswith(".m3u8"):
        media_type = "application/vnd.apple.mpegurl"
        try:
            content = file_path.read_bytes()
            if len(content) == 0:
                import asyncio

                await asyncio.sleep(0.1)
                content = file_path.read_bytes()

            content_str = content.decode("utf-8")

            content = content_str.encode("utf-8")

            headers = {
                **common_headers,
                "Cache-Control": "no-cache",
            }
            return Response(content=content, media_type=media_type, headers=headers)
        except Exception as e:
            log_service.error(f"Error reading playlist {file_path}: {e}")
            raise HTTPException(status_code=500, detail="Error reading playlist")
    elif file_name.endswith(".ts"):
        media_type = "video/mp2t"
        headers = {
            **common_headers,
            "Cache-Control": "no-store",
        }
        return FileResponse(file_path, media_type=media_type, headers=headers)
    elif file_name.endswith(".m4s"):
        media_type = "video/iso.segment"
        headers = {
            **common_headers,
            "Cache-Control": "no-store",
        }
        return FileResponse(file_path, media_type=media_type, headers=headers)
    else:
        media_type = "application/octet-stream"
        return FileResponse(file_path, media_type=media_type, headers=common_headers)


@router.get("/transcodes")
async def get_active_transcodes(current_user: User = Depends(get_current_user)):
    """
    Get information about active transcoding sessions

    Requires authentication
    """
    return {
        "active_count": transcode_manager.get_active_count(),
        "max_concurrent": transcode_manager.max_concurrent,
        "queue_full_behavior": transcode_manager.queue_full_behavior,
        "active_transcodes": transcode_manager.get_active_transcodes(),
    }


@router.post("/transcode/{stream_id}/seek")
async def seek_transcode(
    stream_id: str, position: float = Query(..., ge=0, allow_inf_nan=False),
    current_user: User = Depends(get_current_user),
):
    """Seek within an active HLS transcode by gracefully restarting FFmpeg"""
    import asyncio
    from ..services.transcoding_service import TranscodingService
    from fastapi import HTTPException
    
    session = transcode_manager.active_transcodes.get(stream_id)
    if not session:
        raise HTTPException(status_code=404, detail="Playback session has ended")
    async with session["lock"]:
        stream_info = transcode_manager.get_stream_info(stream_id)
        if not stream_info:
            raise HTTPException(status_code=404, detail="Playback session has ended")
        duration = session.get("duration")
        if duration and position >= duration:
            raise HTTPException(status_code=400, detail="Seek position exceeds video duration")
        await transcode_manager.terminate_process(stream_id)
        transcode_manager.cleanup_hls_files(stream_id)
        output_dir = transcode_manager.get_hls_output_dir(stream_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        playlist_path = output_dir / "master.m3u8"
        cmd = TranscodingService.build_ffmpeg_command(
            input_url=stream_info["input_url"], output_path=str(playlist_path),
            transcode_settings=stream_info["transcode_settings"],
            client_profile=stream_info["client_profile"], probe_info=stream_info["probe_info"],
            audio_track_index=stream_info["audio_track_index"], start_time=position,
        )
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=None, stderr=None)
            transcode_manager.set_stream_start_time(stream_id, position)
            transcode_manager.register_process(stream_id, process, duration=duration,
                stream_info=stream_info,
                segment_duration=stream_info["transcode_settings"].get("hls_segment_duration", 4))
            if not await transcode_manager.wait_for_hls_ready(playlist_path, timeout=45, process=process):
                raise RuntimeError("FFmpeg did not produce a playable seek playlist")
            return {"status": "ok", "position": position}
        except Exception as exc:
            await transcode_manager.terminate_process(stream_id)
            raise HTTPException(status_code=500, detail="Unable to restart playback at this position") from exc


@router.get("/transcode/{stream_id}/segment-range")
async def get_segment_range(stream_id: str, current_user: User = Depends(get_current_user)):
    """Get the time range of segments already transcoded on disk for a stream.
    
    Used by the frontend smart seek to decide whether to seek natively
    or restart the transcode from a new position.
    """
    return transcode_manager.get_segment_range(stream_id)


@router.delete("/transcode/{stream_id}")
async def stop_transcode(
    stream_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Stop an active transcoding session and cleanup resources

    Called when user closes player or switches sources
    """
    log_service.info(f"Stopping transcode for stream_id: {stream_id}")
    await transcode_manager.release_slot_async(stream_id)
    return {"status": "stopped", "stream_id": stream_id}


@router.get("/transcode/{stream_id}/subtitles/{index}")
async def get_live_subtitles(
    stream_id: str, index: int, current_user: User = Depends(get_current_user),
):
    """Read cues already extracted by the playback process; never re-read its input."""
    from fastapi.responses import Response
    info = transcode_manager.get_stream_info(stream_id)
    if not info:
        raise HTTPException(status_code=404, detail="Playback session has ended")
    tracks = info.get("probe_info", {}).get("subtitle_tracks", [])
    if not any(t["index"] == index and t.get("supported") for t in tracks):
        raise HTTPException(status_code=400, detail="Choose a supported text subtitle track")
    directory = transcode_manager.get_hls_output_dir(stream_id)
    cues = []
    for path in sorted(directory.glob(f"subtitle_{index}_*.vtt")):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        blocks = text.replace("\r", "").split("\n\n")
        complete = blocks if text.endswith("\n") else blocks[:-1]
        cues.extend(block.strip() for block in complete if "-->" in block and len(block.strip().splitlines()) >= 2)
    return Response("WEBVTT\n\n" + "\n\n".join(cues) + "\n\n", media_type="text/vtt",
                    headers={"Cache-Control": "no-store", "X-Subtitle-Offset": str(transcode_manager.get_stream_start_time(stream_id))})


@router.get("/subtitle")
async def get_subtitle(
    index: int = Query(..., ge=0), source_id: Optional[int] = Query(None),
    url: Optional[str] = Query(None), db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    position: Optional[float] = None,
):
    """Extract an embedded text subtitle as WebVTT for the browser player."""
    import asyncio
    import hashlib
    from pathlib import Path
    from fastapi.responses import Response
    if source_id is not None:
        from sqlalchemy import select
        result = await db.execute(select(MediaSourceModel).where(MediaSourceModel.id == source_id))
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        await ensure_media_uuid_allowed(db, current_user, source.media_uuid)
        input_url = await stream_service._resolve_source(source.file_path)
    elif url:
        from ..core.url_guard import validate_source_url

        input_url = await stream_service._resolve_source(await validate_source_url(url, current_user))
    else:
        raise HTTPException(status_code=400, detail="Choose a local source or HTTP stream")
    probe = await FFprobeService.probe_stream(input_url)
    track = next((track for track in (probe or {}).get("subtitle_tracks", []) if track["index"] == index), None)
    if not track:
        raise HTTPException(status_code=404, detail="Subtitle track not found")
    if not track["supported"]:
        raise HTTPException(status_code=400, detail="Image subtitles cannot be displayed as text. Load an SRT/VTT file instead.")
    import math
    if position is not None and (not math.isfinite(position) or position < 0):
        raise HTTPException(status_code=400, detail="Invalid subtitle position")
    start = max(0, int(position // 240) * 240 - 30) if position is not None else None
    cache_dir = Path("/tmp/jf-resolve-subtitles")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / (hashlib.sha256(f"{input_url}:{index}:{start}".encode()).hexdigest() + ".vtt")
    if cache.exists():
        return Response(cache.read_bytes(), media_type="text/vtt")
    seek_args = ["-ss", str(start), "-copyts"] if start is not None else []
    duration_args = ["-to", str(start + 330)] if start is not None else []
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", *seek_args, "-i", input_url,
        *duration_args, "-map", f"0:s:{index}",
        "-f", "webvtt", "pipe:1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(status_code=504, detail="Subtitle extraction timed out. Try a local SRT/VTT file.") from exc
    if process.returncode != 0 or not stdout.startswith(b"WEBVTT"):
        raise HTTPException(status_code=502, detail="Unable to extract this subtitle track")
    cache.write_bytes(stdout)
    return Response(stdout, media_type="text/vtt")


@router.post("/transcode/{stream_id}/heartbeat")
async def heartbeat_transcode(stream_id: str, current_user: User = Depends(get_current_user)):
    if not transcode_manager.touch_stream(stream_id):
        raise HTTPException(status_code=404, detail="Playback session has ended")
    return {"status": "alive"}
