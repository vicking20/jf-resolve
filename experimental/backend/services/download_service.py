"""Tracked downloads of original files to persistent server storage."""
import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import aiofiles
import httpx
from fastapi import HTTPException

from .torrent_service import ROOT, torrent_service


def download_name(input_url, title=None):
    original = Path(unquote(urlsplit(input_url).path)).name
    extension = Path(original).suffix
    if not re.fullmatch(r'\.[a-zA-Z0-9]{1,8}', extension):
        extension = '.bin'
    name = (title.strip() + extension) if title and title.strip() else (original or 'media' + extension)
    safe = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', '_', name)
    return safe if len(safe) <= 180 else safe[:170] + extension


class DownloadService:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else ROOT / 'data/downloads'
        self.jobs = {}
        self.tasks = {}
        self.semaphore = asyncio.Semaphore(2)
        index = self.folder / 'jobs.json'
        if index.exists():
            self.jobs = json.loads(index.read_text())
            for job in self.jobs.values():
                if job['status'] in ('queued', 'downloading', 'resolving'):
                    job.update(status='interrupted', error='Server stopped before download finished. Start the download again.')

    def persist(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / 'jobs.json.tmp'
        temporary.write_text(json.dumps(self.jobs))
        temporary.replace(self.folder / 'jobs.json')

    def start(self, source, title=None, media=None):
        if len(self.tasks) >= 10:
            raise HTTPException(status_code=429, detail='Download queue is full')
        id_ = uuid.uuid4().hex
        job = {'id': id_, 'title': title or 'Media download', 'filename': None, 'status': 'queued',
               'bytes_downloaded': 0, 'total_bytes': None, 'error': None,
               'created_at': datetime.now(timezone.utc).isoformat(), 'media': media, 'local_source_id': None, 'library_error': None}
        self.jobs[id_] = job
        self.persist()
        self.tasks[id_] = asyncio.create_task(self.run(id_, source, title))
        self.tasks[id_].add_done_callback(lambda _: self.tasks.pop(id_, None))
        return job.copy()

    async def run(self, id_, source, title):
        from .stream_service import stream_service
        job = self.jobs[id_]
        input_url = None
        partial = None
        try:
            async with self.semaphore:
                job['status'] = 'resolving'
                input_url = await stream_service._resolve_source(source)
                torrent_service.hold(input_url)
                job['filename'] = download_name(input_url, title)
                directory = self.folder / id_
                directory.mkdir(parents=True, exist_ok=True)
                destination = directory / job['filename']
                partial = destination.with_name(destination.name + '.part')
                job['status'] = 'downloading'
                self.persist()
                async with aiofiles.open(partial, 'wb') as output:
                    if input_url.startswith('file://'):
                        path = Path(unquote(urlsplit(input_url).path))
                        job['total_bytes'] = path.stat().st_size
                        async with aiofiles.open(path, 'rb') as incoming:
                            while chunk := await incoming.read(1024 * 1024):
                                await output.write(chunk)
                                job['bytes_downloaded'] += len(chunk)
                    else:
                        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15), follow_redirects=True, headers=stream_service.client.headers) as client:
                            async with client.stream('GET', input_url, headers={'Accept-Encoding': 'identity'}) as response:
                                response.raise_for_status()
                                length = response.headers.get('content-length')
                                job['total_bytes'] = int(length) if length and length.isdigit() else None
                                async for chunk in response.aiter_bytes(1024 * 1024):
                                    await output.write(chunk)
                                    job['bytes_downloaded'] += len(chunk)
                if job['total_bytes'] is not None and job['bytes_downloaded'] != job['total_bytes']:
                    raise ValueError('Incomplete media file')
                if job.get('media'):
                    from .download_library_service import write_identity
                    write_identity(destination, job['media'])
                partial.replace(destination)
                job['status'] = 'completed'
                if job.get('media'):
                    await self.link_to_library(job)
        except asyncio.CancelledError:
            job['status'] = 'cancelled'
            raise
        except Exception:
            job.update(status='failed', error='Download failed. The source may be unavailable, stalled, or storage may be full.')
        finally:
            if partial:
                partial.unlink(missing_ok=True)
            if input_url:
                torrent_service.release(input_url)
                from .transcode_manager import transcode_manager
                active = [(info.get('stream_info') or {}).get('input_url', '') for info in transcode_manager.active_transcodes.values()]
                await torrent_service.stop_unused(input_url, active)
            self.persist()

    async def link_to_library(self, job):
        from ..database import AsyncSessionLocal
        from .download_library_service import register_local_file
        try:
            async with AsyncSessionLocal() as db:
                path = self.completed_file(job['id'])
                source = await register_local_file(db, path, job['media'])
                await db.commit()
                job['local_source_id'] = source.id
                job['library_error'] = None
        except Exception:
            job['library_error'] = 'File saved, but its library link needs retrying.'
        self.persist()

    async def reconcile_library(self):
        for job in self.jobs.values():
            if job['status'] == 'completed' and job.get('media'):
                await self.link_to_library(job)

    def completed_file(self, id_):
        job = self.jobs.get(id_)
        if not job:
            raise HTTPException(status_code=404, detail='Download not found')
        if job['status'] != 'completed':
            raise HTTPException(status_code=409, detail='Download has not finished')
        path = self.folder / id_ / job['filename']
        if not path.is_file():
            raise HTTPException(status_code=404, detail='Downloaded file is missing')
        return path

    async def cancel(self, id_):
        if id_ not in self.jobs:
            raise HTTPException(status_code=404, detail='Download not found')
        task = self.tasks.get(id_)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.jobs[id_]['status'] = 'cancelled'
            self.persist()
        return self.jobs[id_]

    async def close(self):
        for task in list(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)


download_service = DownloadService()
