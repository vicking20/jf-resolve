import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experimental.backend import main
from experimental.backend.api import web_routes
from experimental.backend.core import database as core_database
from experimental.backend.core.request_security import login_throttle
from experimental.backend.core.url_guard import validate_source_url
from experimental.backend.database import Base
from experimental.backend.services.transcode_manager import transcode_manager


class SourceUrlGuardTests(unittest.IsolatedAsyncioTestCase):
    user = SimpleNamespace(is_superuser=False)
    admin = SimpleNamespace(is_superuser=True)

    async def assertRejected(self, url, user, code):
        with self.assertRaises(HTTPException) as ctx:
            await validate_source_url(url, user)
        self.assertEqual(ctx.exception.status_code, code)

    async def test_local_paths_and_other_schemes_are_rejected_for_everyone(self):
        for url in ('/etc/passwd', '.env', 'file:///etc/passwd', 'ftp://example.com/a', 'movie.strm', ''):
            await self.assertRejected(url, self.admin, 400)

    async def test_private_addresses_are_rejected_for_non_admins(self):
        for url in ('http://127.0.0.1:8091/echo', 'http://localhost/', 'http://192.168.9.254:8765/',
                    'http://10.0.0.1/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/', 'http://169.254.169.254/'):
            await self.assertRejected(url, self.user, 403)
        self.assertEqual(await validate_source_url('http://192.168.9.254/x.mkv', self.admin), 'http://192.168.9.254/x.mkv')

    async def test_public_urls_and_magnets_are_accepted(self):
        self.assertEqual(await validate_source_url('https://93.184.216.34/a.mkv', self.user), 'https://93.184.216.34/a.mkv')
        magnet = 'magnet:?xt=urn:btih:' + 'a' * 40
        self.assertEqual(await validate_source_url(magnet, self.user), magnet)


class AppSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.temp.name) / 'test.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async def get_test_db():
            async with self.sessions() as session:
                yield session

        main.app.dependency_overrides[core_database.get_db] = get_test_db
        self.patches = [patch.object(web_routes, 'AsyncSessionLocal', self.sessions)]
        for p in self.patches:
            p.start()
        login_throttle._failures.clear()

        self.admin = self.client()
        response = await self.admin.post('/api/auth/register', json={'username': 'admin', 'password': 'adminpass1'})
        self.assertEqual(response.status_code, 200)
        self.register_body = response.json()
        response = await self.admin.post('/api/users/', json={'username': 'viewer', 'password': 'viewerpass1'})
        self.assertEqual(response.status_code, 200)
        self.viewer = self.client()
        self.assertEqual((await self.login(self.viewer, 'viewer', 'viewerpass1')).status_code, 200)

    async def asyncTearDown(self):
        for c in (self.admin, self.viewer):
            await c.aclose()
        for p in self.patches:
            p.stop()
        main.app.dependency_overrides.clear()
        login_throttle._failures.clear()
        await self.engine.dispose()
        self.temp.cleanup()

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test', **kwargs)

    async def login(self, client, username, password, headers=None):
        return await client.post('/api/auth/login', json={'username': username, 'password': password}, headers=headers)

    async def test_user_responses_never_include_password_hash(self):
        self.assertNotIn('hashed_password', self.register_body)
        me = await self.admin.get('/api/auth/me')
        self.assertEqual(me.json()['username'], 'admin')
        self.assertNotIn('hashed_password', me.json())
        users = await self.admin.get('/api/users/')
        self.assertEqual(len(users.json()), 2)
        self.assertTrue(all('hashed_password' not in u for u in users.json()))

    async def test_proxy_cannot_read_local_files(self):
        for url in ('/etc/passwd', str(Path(main.__file__).resolve()), 'http://127.0.0.1:8765/api'):
            response = await self.viewer.get('/api/playback/proxy', params={'url': url})
            self.assertIn(response.status_code, (400, 403), url)
            self.assertNotIn(b'root:', response.content)
        response = await self.viewer.get('/api/stream/probe', params={'url': '/etc/passwd'})
        self.assertEqual(response.status_code, 400)
        response = await self.viewer.get('/api/downloads/file', params={'url': 'http://10.0.0.1/secret'})
        self.assertEqual(response.status_code, 403)

    async def test_hls_requires_login_and_active_stream(self):
        anonymous = self.client()
        self.assertEqual((await anonymous.get('/api/stream/hls/%2e%2e/passwd')).status_code, 401)
        await anonymous.aclose()
        for path in ('/api/stream/hls/%2e%2e/passwd', '/api/stream/hls/unknown/master.m3u8'):
            self.assertEqual((await self.viewer.get(path)).status_code, 404, path)
        self.assertEqual((await self.viewer.get('/api/stream/transcode/x/segment-range')).status_code, 200)
        with self.assertRaises(ValueError):
            transcode_manager.get_hls_output_dir('..')

        stream_id = 'a' * 32
        output = transcode_manager.get_hls_output_dir(stream_id)
        output.mkdir(parents=True, exist_ok=True)
        (output / 'segment_0000.ts').write_bytes(b'segment')
        transcode_manager.active_transcodes[stream_id] = {'last_seen': 0}
        try:
            self.assertEqual((await self.viewer.get(f'/api/stream/hls/{stream_id}/segment_0000.ts')).content, b'segment')
            self.assertEqual((await self.viewer.get(f'/api/stream/hls/{stream_id}/..')).status_code, 404)
        finally:
            transcode_manager.active_transcodes.pop(stream_id, None)
            transcode_manager.cleanup_hls_files(stream_id)

    async def test_malformed_cookie_is_treated_as_logged_out(self):
        client = self.client(cookies={'jfresolve_session': 'garbage'})
        self.assertEqual((await client.get('/api/auth/me')).status_code, 401)
        self.assertEqual((await client.get('/')).status_code, 307)
        await client.aclose()

    async def test_login_is_throttled_per_client_ip(self):
        attacker = {'CF-Connecting-IP': '203.0.113.9'}
        for _ in range(10):
            self.assertEqual((await self.login(self.client(), 'admin', 'wrong', attacker)).status_code, 401)
        blocked = await self.login(self.client(), 'admin', 'adminpass1', attacker)
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('retry-after', blocked.headers)
        other = await self.login(self.client(), 'admin', 'adminpass1', {'CF-Connecting-IP': '198.51.100.7'})
        self.assertEqual(other.status_code, 200)

    async def test_password_change_invalidates_existing_sessions(self):
        other_browser = self.client()
        await self.login(other_browser, 'viewer', 'viewerpass1')
        response = await self.viewer.post('/api/auth/change-password',
                                          json={'current_password': 'viewerpass1', 'new_password': 'newpass123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await other_browser.get('/api/auth/me')).status_code, 401)
        self.assertEqual((await self.viewer.get('/api/auth/me')).status_code, 200)
        await other_browser.aclose()

    async def test_cookie_is_secure_over_https_tunnel(self):
        response = await self.login(self.client(), 'admin', 'adminpass1', {'X-Forwarded-Proto': 'https'})
        self.assertIn('secure', response.headers['set-cookie'].lower())
        response = await self.login(self.client(), 'admin', 'adminpass1')
        self.assertNotIn('secure', response.headers['set-cookie'].lower())

    async def test_no_credentialed_cors_and_security_headers(self):
        response = await self.admin.get('/api/auth/me', headers={'Origin': 'https://evil.example'})
        self.assertNotIn('access-control-allow-origin', response.headers)
        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
        self.assertEqual(response.headers['x-frame-options'], 'SAMEORIGIN')
        self.assertNotIn('data_dir', (await self.client().get('/api/system/status')).json())

    async def test_image_routes_require_login_and_valid_ids(self):
        self.assertEqual((await self.client().get('/api/media/images/movie/1/poster?path=/a.jpg')).status_code, 401)
        self.assertEqual((await self.viewer.get('/api/media/images/movie/../poster?path=/a.jpg')).status_code, 404)
        self.assertEqual((await self.viewer.get('/api/media/images/movie/a.b/poster?path=/a.jpg')).status_code, 400)
        self.assertEqual((await self.viewer.get('/api/media/images/movie/1/poster?path=/../../x')).status_code, 400)
        response = await self.viewer.get('/api/media/images/video/some-uuid/poster', params={'path': '/etc/passwd'})
        self.assertNotIn(b'root:', response.content)

    async def test_admin_only_and_route_fixes(self):
        self.assertEqual((await self.viewer.delete('/api/library/tmdb:1')).status_code, 403)
        self.assertEqual((await self.admin.post('/api/addons/install',
                                                params={'plugin_id': '../../x', 'repository_url': 'https://x'})).status_code, 400)
        response = await self.admin.get('/api/settings/scanner-mode')
        self.assertEqual(response.status_code, 200)
        self.assertIn('mode', response.json())
        self.assertNotIn('key', response.json())


if __name__ == '__main__':
    unittest.main()
