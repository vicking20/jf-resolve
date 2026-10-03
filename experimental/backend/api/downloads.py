"""Original-file downloads to a device or admin-controlled server storage."""
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ..core.dependencies import get_current_admin_user
from ..core.session import get_current_user
from ..core.url_guard import validate_source_url
from ..database import get_db
from ..models.user import User
from ..models.media import MediaSourceModel
from ..services.download_service import download_name, download_service
from ..services.stream_service import stream_service
from ..services.torrent_service import torrent_service

router = APIRouter(prefix='/api/downloads', tags=['downloads'])


class DownloadRequest(BaseModel):
    url: Optional[str] = None
    source_id: Optional[int] = None
    title: Optional[str] = None
    media_identifier: Optional[str] = None
    media_type: Optional[str] = None
    season: Optional[int] = Field(None, ge=0)
    episode: Optional[int] = Field(None, ge=0)


async def source_path(source_id, url, db, user):
    if source_id is not None:
        result = await db.execute(select(MediaSourceModel).where(MediaSourceModel.id == source_id))
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail='Source not found')
        return source.file_path
    if url:
        return await validate_source_url(url, user)
    raise HTTPException(status_code=400, detail='Choose an HTTP source or magnet link')


@router.get('/file')
async def download_to_device(request: Request, url: Optional[str] = None, source_id: Optional[int] = None,
                             title: Optional[str] = None, db: AsyncSession = Depends(get_db),
                             user: User = Depends(get_current_user)):
    source = await source_path(source_id, url, db, user)
    input_url = await stream_service._resolve_source(source)
    filename = download_name(input_url, title)
    if input_url.startswith('file://'):
        return FileResponse(unquote(urlsplit(input_url).path), filename=filename, media_type='application/octet-stream')
    torrent_service.hold(input_url)
    client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=True, headers=stream_service.client.headers)
    response = None
    cleaned = False
    async def cleanup():
        nonlocal cleaned
        if cleaned:
            return
        cleaned = True
        if response:
            await response.aclose()
        await client.aclose()
        torrent_service.release(input_url)
        from ..services.transcode_manager import transcode_manager
        await torrent_service.stop_unused(input_url, [(info.get('stream_info') or {}).get('input_url', '') for info in transcode_manager.active_transcodes.values()])
    try:
        headers = {'Accept-Encoding': 'identity'}
        if request.headers.get('range'):
            headers['Range'] = request.headers['range']
        response = await client.send(client.build_request('GET', input_url, headers=headers), stream=True)
        response.raise_for_status()
    except BaseException as exc:
        await cleanup()
        if isinstance(exc, httpx.HTTPError):
            raise HTTPException(status_code=502, detail='Source could not be downloaded') from exc
        raise
    async def chunks():
        try:
            async for chunk in response.aiter_bytes(1024 * 1024):
                yield chunk
        finally:
            await cleanup()
    headers = {key: response.headers[key] for key in ('Content-Length', 'Content-Range', 'Accept-Ranges') if key in response.headers}
    headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(filename, safe='')
    return StreamingResponse(chunks(), status_code=response.status_code, media_type='application/octet-stream',
                             headers=headers, background=BackgroundTask(cleanup))


@router.post('/jobs', status_code=202)
async def save_on_server(payload: DownloadRequest, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_admin_user)):
    source = await source_path(payload.source_id, payload.url, db, user)
    from ..services.download_library_service import download_target
    media = await download_target(db, payload.media_identifier, payload.media_type, payload.season, payload.episode, payload.source_id)
    await db.commit()
    return download_service.start(source, media['title'] if media else payload.title, media=media)


@router.get('/jobs')
async def list_downloads(user: User = Depends(get_current_admin_user)):
    return sorted(download_service.jobs.values(), key=lambda job: job['created_at'], reverse=True)


@router.delete('/jobs/{id_}')
async def cancel_download(id_: str, user: User = Depends(get_current_admin_user)):
    return await download_service.cancel(id_)


@router.get('/jobs/{id_}/file')
async def download_saved_file(id_: str, user: User = Depends(get_current_admin_user)):
    path = download_service.completed_file(id_)
    return FileResponse(path, filename=path.name, media_type='application/octet-stream')
