"""Preserve exact movie/episode identity when an original file is saved."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select

from ..models.media import MediaSourceModel
from ..models.media_item import MediaItem, MediaType
from .media_resolver import MediaResolver
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService

SIDECAR = '.jf-resolve.json'


async def download_target(db, identifier=None, media_type=None, season=None, episode=None, source_id=None):
    """Resolve before enqueueing; never guess an identity from a download filename."""
    if identifier is None:
        if source_id is None:
            return None
        result = await db.execute(select(MediaSourceModel).where(MediaSourceModel.id == source_id))
        source = result.scalar_one_or_none()
        if source is None:
            raise HTTPException(status_code=404, detail='Source not found')
        identifier = source.media_uuid
    api = None
    try:
        if MediaResolver(None)._is_uuid(identifier):
            resolver = MediaResolver(None)
        else:
            key = await SettingsManager(db).get('tmdb_api_key')
            if not key:
                raise HTTPException(status_code=400, detail='TMDB configuration is required to link this media')
            api = TMDBService(key)
            resolver = MediaResolver(api)
        data = await resolver.resolve(db, identifier, media_type)
        if not data:
            raise HTTPException(status_code=404, detail='Media not found')
        item = await resolver.persist(db, data, is_persisted=True)
        parent = item
        if item.media_type in (MediaType.TV, MediaType.VIDEO_SERIES):
            if season is None or episode is None:
                raise HTTPException(status_code=400, detail='Select a season and episode before saving this show')
            result = await db.execute(select(MediaItem).where(
                MediaItem.parent_id == item.uuid, MediaItem.season_number == season,
                MediaItem.episode_number == episode,
            ))
            target = result.scalar_one_or_none()
            if target is None:
                target = MediaItem(uuid=str(uuid.uuid4()), parent_id=item.uuid,
                                   media_type=MediaType.EPISODE if item.media_type == MediaType.TV else MediaType.VIDEO,
                                   title=f'{item.title} - S{season:02d}E{episode:02d}',
                                   season_number=season, episode_number=episode, is_persisted=True)
                db.add(target)
                await db.flush()
            item = target
        elif item.parent_id:
            parent = await db.get(MediaItem, item.parent_id) or item
            season, episode = item.season_number, item.episode_number
        item.is_persisted = True
        parent.is_persisted = True
        title = parent.title
        if season is not None and episode is not None:
            title += f' - S{season:02d}E{episode:02d}'
        elif parent.year:
            title += f' ({parent.year})'
        query = {'autoPlay': 'true'}
        if season is not None and episode is not None:
            query.update(season=season, episode=episode)
        return {'media_uuid': item.uuid, 'parent_uuid': parent.uuid, 'media_type': parent.media_type.value,
                'title': title, 'year': parent.year, 'season': season, 'episode': episode,
                'tmdb_id': data.get('tmdb_id'), 'imdb_id': data.get('imdb_id'),
                'play_url': f'/media/{parent.media_type.value}/{parent.uuid}?' + urlencode(query)}
    finally:
        if api:
            await api.close()


def write_identity(destination, media):
    data = dict(media, filename=Path(destination).name, version=1)
    sidecar = Path(destination).parent / SIDECAR
    temporary = sidecar.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(sidecar)


def read_identity(file_path):
    path = Path(file_path)
    sidecar = path.parent / SIDECAR
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding='utf-8'))
        if data.get('filename') == path.name and data.get('media_uuid'):
            return data
    except (ValueError, OSError):
        pass
    return None


async def register_local_file(db, file_path, media, library_path_id=None):
    item = await db.get(MediaItem, media['media_uuid'])
    if item is None:
        raise ValueError('Download target is no longer in the database')
    path = Path(file_path).resolve()
    result = await db.execute(select(MediaSourceModel).where(MediaSourceModel.file_path == str(path)))
    source = result.scalar_one_or_none()
    if source is None:
        source = MediaSourceModel(media_uuid=item.uuid, source_type='local', file_path=str(path))
        db.add(source)
    source.media_uuid = item.uuid
    source.source_type = 'local'
    source.stream_title = path.name
    source.file_size = path.stat().st_size
    source.is_available = True
    source.priority = 0
    source.last_verified = datetime.now(timezone.utc)
    if library_path_id is not None:
        source.library_path_id = library_path_id
    item.is_persisted = True
    await db.flush()
    return source
