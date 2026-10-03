from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..addons.manager import plugin_manager
from ..core.session import get_current_user
from ..database import get_db
from ..models.media_external_id import MediaExternalId
from ..models.user import User
from ..services import external_subtitles
from ..services.access_control import ensure_item_allowed
from ..services.media_resolver import MediaResolver
from ..services.settings_manager import SettingsManager
from ..services.log_service import log_service
from ..services.source_orchestrator import source_orchestrator
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/media", tags=["media-unified"])


async def get_media_resolver(db: AsyncSession = Depends(get_db)) -> MediaResolver:
    settings_mgr = SettingsManager(db)
    api_key = await settings_mgr.get("tmdb_api_key")
    if not api_key:
        raise HTTPException(status_code=500, detail="TMDB API key not configured")
    tmdb = TMDBService(api_key)
    return MediaResolver(tmdb)


@router.get("/{identifier}")
async def get_media_details(
    identifier: str,
    media_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """
    Get unified media details.
    Resolves by UUID or provider:id (e.g. tmdb:123).
    """
    item = await resolver.resolve(
        db, 
        identifier, 
        media_type, 
        user_id=current_user.id if current_user else None
    )
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    await ensure_item_allowed(db, current_user, item)


    return item


@router.get("/{identifier}/sources")
async def get_media_sources(
    identifier: str,
    media_type: Optional[str] = Query(None),
    season: Optional[int] = None,
    episode: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """
    Get prioritized sources for a media item.

    JIT Auto-Persist: If item is transient (from TMDB search) AND JIT scanner
    finds matching local files, the item will be automatically persisted to DB.
    This enables lazy library building - only items with local files get persisted.
    """
    item = await resolver.resolve(db, identifier, media_type)
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    await ensure_item_allowed(db, current_user, item)

    media_uuid = item.get("uuid")
    imdb_id = item.get("imdb_id")
    tmdb_id = item.get("tmdb_id")

    if tmdb_id and not imdb_id:
        imdb_id = await resolver.tmdb.get_imdb_id(int(tmdb_id), item["media_type"])

        if imdb_id and media_uuid:
            from sqlalchemy.exc import IntegrityError
            try:
                db.add(
                    MediaExternalId(
                        media_uuid=media_uuid, provider="imdb", external_id=imdb_id
                    )
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                log_service.info(f"IMDb ID {imdb_id} already linked in database, skipping")

    sources = await source_orchestrator.get_sources(
        db=db,
        media_uuid=media_uuid,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
        media_type=item["media_type"],
        title=item["title"],
        year=item.get("year"),
        season=season,
        episode=episode,
        transient_item=item if not media_uuid else None,
        resolver=resolver if not media_uuid else None,
    )

    return sources




@router.get("/{identifier}/subtitles")
async def get_media_subtitles(
    identifier: str,
    media_type: Optional[str] = Query(None),
    season: Optional[int] = None,
    episode: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    resolver: MediaResolver = Depends(get_media_resolver),
):
    """External subtitles from subtitle plugins, as signed download tokens."""
    item = await resolver.resolve(db, identifier, media_type)
    if not item:
        raise HTTPException(status_code=404, detail="Media item not found")
    await ensure_item_allowed(db, current_user, item)

    imdb_id = item.get("imdb_id")
    if not imdb_id and item.get("tmdb_id"):
        imdb_id = await resolver.tmdb.get_imdb_id(int(item["tmdb_id"]), item["media_type"])
    if not imdb_id or item["media_type"] not in ("movie", "tv"):
        return []

    subtitles = await plugin_manager.get_all_subtitles(imdb_id, item["media_type"], season, episode)
    return [
        {
            "token": external_subtitles.sign(sub["url"]),
            "language": sub.get("language") or "und",
            "label": sub.get("label"),
            "provider": sub.get("provider"),
        }
        for sub in subtitles
        if sub.get("url")
    ]
