import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from experimental.backend.api import downloads
from experimental.backend.core.session import get_current_user
from experimental.backend.database import get_db
from experimental.backend.services.download_service import DownloadService, download_name
from experimental.backend.services.torrent_service import torrent_service


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'original.mkv'
        self.content = b'original media bytes' * 60000
        self.source.write_bytes(self.content)
        self.service = DownloadService(self.folder / 'downloads')

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_server_save_keeps_original_and_survives_restart(self):
        job = self.service.start(str(self.source), 'Title')
        await self.service.tasks[job['id']]
        self.assertEqual(self.service.jobs[job['id']]['status'], 'completed')
        self.assertEqual(self.service.completed_file(job['id']).read_bytes(), self.content)
        restored = DownloadService(self.service.folder)
        self.assertEqual(restored.completed_file(job['id']).read_bytes(), self.content)
        self.assertFalse(list(self.service.folder.rglob('*.part')))
        self.assertNotIn('source', self.service.jobs[job['id']])

    async def test_cancel_cleans_up_and_no_credentials_are_persisted(self):
        entered = asyncio.Event()
        async def blocked(source):
            entered.set()
            await asyncio.Event().wait()
        with patch('experimental.backend.services.stream_service.stream_service._resolve_source', side_effect=blocked):
            job = self.service.start('https://example.test/secret-token/file.mkv', 'Title')
            await entered.wait()
            await self.service.cancel(job['id'])
        self.assertEqual(self.service.jobs[job['id']]['status'], 'cancelled')
        self.assertNotIn('secret-token', (self.service.folder / 'jobs.json').read_text())
        self.assertFalse(list(self.service.folder.rglob('*.part')))

    async def test_admin_gating_and_device_original_file(self):
        app = FastAPI()
        app.include_router(downloads.router)
        user = SimpleNamespace(id=1, is_superuser=False)
        app.dependency_overrides[get_current_user] = lambda: user
        db = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(file_path=str(self.source)))))
        app.dependency_overrides[get_db] = lambda: db
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(downloads, 'download_service', self.service), patch('experimental.backend.services.download_library_service.download_target', AsyncMock(return_value=None)):
                response = await client.post('/api/downloads/jobs', json={'source_id': 1})
                self.assertEqual(response.status_code, 403)
                self.assertEqual((await client.get('/api/downloads/jobs')).status_code, 403)
                response = await client.get('/api/downloads/file?source_id=1')
                self.assertEqual(response.content, self.content)
                self.assertIn('attachment', response.headers['content-disposition'])
                user.is_superuser = True
                response = await client.post('/api/downloads/jobs', json={'source_id': 1})
                self.assertEqual(response.status_code, 202)
                id_ = response.json()['id']
                await self.service.tasks[id_]
                response = await client.get(f'/api/downloads/jobs/{id_}/file')
                self.assertEqual(response.content, self.content)

    async def test_remote_device_download_preserves_bytes_and_range(self):
        app = FastAPI(); app.include_router(downloads.router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=False)
        app.dependency_overrides[get_db] = lambda: None
        observed = []
        def handle(request):
            observed.append(request.headers.get('range'))
            return httpx.Response(206, content=b'original', headers={'Content-Range':'bytes 0-7/100','Content-Length':'8'})
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(downloads.httpx, 'AsyncClient', return_value=upstream):
                response = await client.get('/api/downloads/file?url=https://example.test/movie.mkv', headers={'Range':'bytes=0-7'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'original')
        self.assertEqual(observed, ['bytes=0-7'])
        self.assertTrue(upstream.is_closed)
        self.assertIn("movie.mkv", response.headers['content-disposition'])
        self.assertFalse(torrent_service.holds)

    async def test_cancel_mid_transfer_removes_partial_file(self):
        entered = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'x' * 1048576
                entered.set()
                await asyncio.Event().wait()
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())))
        with patch('experimental.backend.services.download_service.httpx.AsyncClient', return_value=upstream):
            job = self.service.start('https://example.test/movie.mkv', 'Title')
            await entered.wait()
            self.assertTrue(list(self.service.folder.rglob('*.part')))
            await self.service.cancel(job['id'])
        self.assertEqual(self.service.jobs[job['id']]['status'], 'cancelled')
        self.assertFalse(list(self.service.folder.rglob('*.part')))
        self.assertTrue(upstream.is_closed)
        self.assertFalse(torrent_service.holds)

    async def test_torrent_is_not_dropped_during_download(self):
        input_url = torrent_service.url + '/stream/video.mkv?link=' + 'a' * 40
        process = SimpleNamespace(returncode=None)
        with patch.object(torrent_service, 'process', process), patch('experimental.backend.services.torrent_service.httpx.AsyncClient') as client:
            torrent_service.hold(input_url)
            try:
                await torrent_service.stop_unused(input_url, [])
                client.assert_not_called()
            finally:
                torrent_service.release(input_url)
        self.assertNotIn(input_url, torrent_service.holds)

    def test_safe_filename(self):
        self.assertEqual(download_name('https://example.test/movie.mkv', '../Title'), '.._Title.mkv')
