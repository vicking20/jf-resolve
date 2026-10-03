from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.session import get_current_user
from ..database import get_db
from ..models.playback import PlaybackSession, SkipSegment
from ..models.user import User
from ..models.media_item import MediaItem
from .media_unified import get_media_resolver
from ..services.media_resolver import MediaResolver
from ..services.settings_manager import SettingsManager
from ..services.stream_service import stream_service
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/playback", tags=["playback"])


class PlaybackUpdate(BaseModel):
    """Create/update playback progress (Unified Schema)"""

    identifier: str
    media_type: str

    position_seconds: float
    duration_seconds: Optional[float] = None

    season: Optional[int] = None
    episode: Optional[int] = None

    quality: Optional[str] = None
    device: Optional[str] = None
    source_type: Optional[str] = None
    source_quality: Optional[str] = None
    audio_track: Optional[int] = None
    completed: bool = False


class SkipSegmentResponse(BaseModel):
    """Skip segment response"""

    segment_type: str
    start_time: float
    end_time: float
    duration: float
    confidence: float

    class Config:
        from_attributes = True


async def _get_media_info(
    db: AsyncSession,
    media_uuid: str,
) -> Optional[dict]:
    """Fetch media info for a persisted media item"""
    from ..models.media_item import MediaItem
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(MediaItem)
        .options(selectinload(MediaItem.external_ids))
        .where(MediaItem.uuid == media_uuid)
    )
    item = result.scalar_one_or_none()
    if item:
        return item.to_dict()
    return None


@router.post("/update")
@router.post("/progress")
async def update_progress(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver)
):
    """
    Update playback progress.
    If item is transient, it will be persisted first.
    """
    try:
        body = await request.json()
        data = PlaybackUpdate(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


    item_dict = await resolver.resolve(db, data.identifier, data.media_type)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Media not found")

    media_item = None
    if not item_dict.get("is_persisted"):
        media_item = await resolver.persist(db, item_dict)
    else:
        from ..models.media_item import MediaItem as MediaItemModel
        result = await db.execute(select(MediaItemModel).where(MediaItemModel.uuid == item_dict["uuid"]))
        media_item = result.scalar_one_or_none()

    if not media_item:
        raise HTTPException(status_code=500, detail="Failed to resolve/persist media item")

    query = select(PlaybackSession).where(
        PlaybackSession.user_id == current_user.id,
        PlaybackSession.media_uuid == media_item.uuid
    )

    if (data.media_type in ("tv", "video_series")
        and data.season is not None
        and data.episode is not None):
        query = query.where(
            PlaybackSession.season == data.season,
            PlaybackSession.episode == data.episode
        )

    result = await db.execute(query)
    session = result.scalar_one_or_none()

    completed = data.completed
    if data.duration_seconds and data.duration_seconds > 0:
        percentage = (data.position_seconds / data.duration_seconds) * 100
        if not completed:
            completed = percentage >= 92

    if session:
        session.current_time = data.position_seconds
        if data.duration_seconds:
            session.duration = data.duration_seconds
        session.completed = completed
        if data.quality: session.quality = data.quality
        if data.device: session.device = data.device
        if data.source_type: session.last_source_type = data.source_type
        if data.source_quality: session.last_source_quality = data.source_quality
        if data.audio_track is not None: session.last_audio_track = data.audio_track
        if completed: session.completed_at = datetime.utcnow()
    else:
        session = PlaybackSession(
            user_id=current_user.id,
            media_uuid=media_item.uuid,
            media_type=data.media_type,
            season=data.season,
            episode=data.episode,
            current_time=data.position_seconds,
            duration=data.duration_seconds,
            quality=data.quality,
            device=data.device,
            last_source_type=data.source_type,
            last_source_quality=data.source_quality,
            last_audio_track=data.audio_track,
            completed=completed,
        )
        if completed: session.completed_at = datetime.utcnow()
        db.add(session)

    await db.commit()
    await db.refresh(session)
    return session.to_dict()


@router.get("/progress/{identifier}")
async def get_playback_progress(
    identifier: str,
    media_type: Optional[str] = Query(None),
    season: Optional[int] = Query(None),
    episode: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get playback progress for any content.
    Identifier can be UUID or provider:id.
    """
    from ..services.media_resolver import MediaResolver
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService

    settings_mgr = SettingsManager(db)
    api_key = await settings_mgr.get("tmdb_api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB API key not configured")
    resolver = MediaResolver(TMDBService(api_key))

    item_dict = await resolver.resolve(db, identifier, media_type)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Media not found")

    query = select(PlaybackSession).where(
        PlaybackSession.user_id == current_user.id,
        PlaybackSession.media_uuid == item_dict["uuid"]
    )

    if (item_dict["media_type"] in ("tv", "video_series")
        and season is not None
        and episode is not None):
        query = query.where(
            PlaybackSession.season == season,
            PlaybackSession.episode == episode
        )

    result = await db.execute(query.order_by(PlaybackSession.updated_at.desc()).limit(1))
    session = result.scalar_one_or_none()

    if not session:
        return {"media_uuid": item_dict.get("uuid"), "media_type": item_dict["media_type"],
                "current_time": 0.0, "duration": None, "progress_percentage": 0.0,
                "completed": False, "season": season, "episode": episode}

    return session.to_dict()


@router.get("/continue-watching")
async def get_continue_watching(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get continue watching list for user.
    Uses unified media_uuid.
    """
    result = await db.execute(
        select(PlaybackSession)
        .where(
            PlaybackSession.user_id == current_user.id,
            PlaybackSession.completed == False,
            PlaybackSession.current_time > 30,
        )
        .order_by(desc(PlaybackSession.updated_at))
        .limit(limit)
    )
    sessions = result.scalars().all()

    items = []
    for session in sessions:
        item_dict = session.to_dict()
        media_info = await _get_media_info(db, session.media_uuid)
        if media_info:
            item_dict["media"] = media_info
            items.append(item_dict)

    return {"items": items}


@router.get("/progress/{identifier}/all")
async def get_all_playback_progress(
    identifier: str,
    media_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all playback progress records for an item.
    (Used for TV shows/Video Series to show progress on multiple episodes)
    """
    from ..services.media_resolver import MediaResolver
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService

    settings_mgr = SettingsManager(db)
    api_key = await settings_mgr.get("tmdb_api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB API key not configured")
    resolver = MediaResolver(TMDBService(api_key))

    item_dict = await resolver.resolve(db, identifier, media_type)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Media not found")

    result = await db.execute(
        select(PlaybackSession).where(
            PlaybackSession.user_id == current_user.id,
            PlaybackSession.media_uuid == item_dict["uuid"]
        )
    )
    items = result.scalars().all()
    return [item.to_dict() for item in items]


@router.delete("/progress/{session_id}")
async def delete_progress(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete playback progress

    Removes item from continue watching
    """
    result = await db.execute(
        select(PlaybackSession).where(
            and_(
                PlaybackSession.id == session_id,
                PlaybackSession.user_id == current_user.id,
            )
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Progress not found")

    await db.delete(session)
    await db.commit()

    return {"message": "Progress deleted successfully"}


@router.post("/progress/{identifier}/complete")
async def mark_completed(
    identifier: str,
    media_type: Optional[str] = Query(None),
    season: Optional[int] = None,
    episode: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark content as completed (watched).
    """
    from ..services.media_resolver import MediaResolver
    from ..services.settings_manager import SettingsManager
    from ..services.tmdb_service import TMDBService

    settings_mgr = SettingsManager(db)
    api_key = await settings_mgr.get("tmdb_api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB API key not configured")
    resolver = MediaResolver(TMDBService(api_key))

    item_dict = await resolver.resolve(db, identifier, media_type)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Media not found")

    media_item = None
    if not item_dict.get("is_persisted"):
        media_item = await resolver.persist(db, item_dict)
    else:
        from ..models.media_item import MediaItem as MediaItemModel
        result = await db.execute(select(MediaItemModel).where(MediaItemModel.uuid == item_dict["uuid"]))
        media_item = result.scalar_one_or_none()

    if not media_item:
        raise HTTPException(status_code=500, detail="Failed to resolve/persist media item")

    query = select(PlaybackSession).where(
        PlaybackSession.user_id == current_user.id,
        PlaybackSession.media_uuid == media_item.uuid
    )

    if (item_dict["media_type"] in ("tv", "video_series")
        and season is not None
        and episode is not None):
        query = query.where(
            PlaybackSession.season == season,
            PlaybackSession.episode == episode
        )

    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if session:
        session.completed = True
        session.completed_at = datetime.utcnow()
    else:
        session = PlaybackSession(
            user_id=current_user.id,
            media_uuid=media_item.uuid,
            media_type=item_dict["media_type"],
            season=season,
            episode=episode,
            current_time=0,
            completed=True,
            completed_at=datetime.utcnow()
        )
        db.add(session)
    
    await db.commit()
    return {"message": "Marked as completed"}


@router.get("/tmdb/{tmdb_id}")
async def get_tmdb_progress(
    tmdb_id: int,
    media_type: str = "movie",
    season: Optional[int] = None,
    episode: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get progress by TMDB ID
    """
    from .media_unified import get_media_resolver
    resolver = await get_media_resolver()
    item = await resolver.resolve(db, f"tmdb:{tmdb_id}", media_type)
    if not item:
        raise HTTPException(status_code=404, detail="Media not found")
        
    query = select(PlaybackSession).where(
        PlaybackSession.user_id == current_user.id,
        PlaybackSession.media_uuid == item.uuid
    )
    if media_type == "tv" and season is not None and episode is not None:
        query = query.where(
            PlaybackSession.season == season,
            PlaybackSession.episode == episode
        )
    result = await db.execute(query)
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="No progress found")
    return session.to_dict()


@router.delete("/session/{session_id}")
async def delete_playback_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a playback session"""
    result = await db.execute(
        select(PlaybackSession).where(
            and_(
                PlaybackSession.id == session_id,
                PlaybackSession.user_id == current_user.id,
            )
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Playback session not found")

    await db.delete(session)
    await db.commit()
    return {"message": "Playback session deleted"}


@router.get("/skip-segments/{tmdb_id}")
async def get_skip_segments(
    tmdb_id: int,
    media_type: str = Query(..., pattern="^(movie|tv)$"),
    season: Optional[int] = None,
    episode: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from .media_unified import get_media_resolver
    resolver = await get_media_resolver()
    item = await resolver.resolve(db, f"tmdb:{tmdb_id}", media_type)
    if not item:
        raise HTTPException(status_code=404, detail="Media not found")

    query = select(SkipSegment).where(
        and_(
            SkipSegment.media_uuid == item.uuid,
            SkipSegment.media_type == media_type,
        )
    )
    if media_type == "tv" and season is not None and episode is not None:
        query = query.where(
            and_(
                SkipSegment.season == season,
                SkipSegment.episode == episode,
            )
        )
    query = query.order_by(desc(SkipSegment.confidence))
    result = await db.execute(query)
    segments = result.scalars().all()
    return [
        SkipSegmentResponse(
            segment_type=seg.segment_type,
            start_time=seg.start_time,
            end_time=seg.end_time,
            duration=seg.duration,
            confidence=seg.confidence,
        )
        for seg in segments
    ]


@router.get("/proxy")
async def proxy_stream(
    request: Request,
    url: str = Query(..., description="URL to proxy"),
    audio_track: Optional[int] = Query(None, description="Audio track index"),
    force_transcode: bool = Query(False, description="Force transcoding"),
    range_header: Optional[str] = Header(None, alias="Range"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Proxy an external stream URL with transcoding support"""
    from ..core.url_guard import validate_source_url
    from ..services.log_service import log_service

    url = await validate_source_url(url, current_user)

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
        f"Proxy stream request: url={url[:100]}..., audio_track={audio_track}, "
        f"transcode_enabled={transcode_settings['transcode_enabled']}"
    )

    return await stream_service.get_stream_response(
        source_path=url,
        range_header=range_header,
        user_agent=user_agent,
        transcode_settings=transcode_settings,
        force_transcode=force_transcode,
        audio_track=audio_track,
    )
