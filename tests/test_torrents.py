import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from experimental.backend.services.torrent_service import TorrentService, normalize_magnet
from experimental.backend.services.stream_service import stream_service
from experimental.backend.addons import interfaces

HASH = 'a' * 40

class TorrentTests(unittest.IsolatedAsyncioTestCase):
    def test_magnet_validation_and_correct_file(self):
        self.assertIn('urn:btih:' + HASH, parse_qs(urlsplit(normalize_magnet(HASH)).query)['xt'])
        with self.assertRaises(HTTPException):
            normalize_magnet('magnet:?xt=garbage')
        files = [{'id': 1, 'path': 'sample.mkv', 'length': 1000}, {'id': 2, 'path': 'episode.mkv', 'length': 100}, {'id': 3, 'path': 'readme.txt', 'length': 9999}]
        self.assertEqual(TorrentService.select_file(files)['id'], 2)
        self.assertEqual(TorrentService.select_file(files, 1)['id'], 2)
        with self.assertRaises(HTTPException):
            TorrentService.select_file(files, 2)

    async def test_resolve_metadata_and_file_index(self):
        service = TorrentService()
        client = AsyncMock()
        client.post.side_effect = [SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'hash': HASH}), SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'file_stats': [{'id': 1, 'path': 'first.mkv', 'length': 50}, {'id': 2, 'path': 'second.mkv', 'length': 100}]})]
        client.__aenter__.return_value = client
        with patch.object(service, 'ensure_running', AsyncMock()), patch('experimental.backend.services.torrent_service.httpx.AsyncClient', return_value=client), patch('experimental.backend.services.torrent_service.asyncio.sleep', AsyncMock()):
            url = await service.resolve('magnet:?xt=urn:btih:' + HASH + '&so=0')
        self.assertEqual(parse_qs(urlsplit(url).query)['index'], ['1'])
        self.assertIn('/first.mkv?', url)

    async def test_magnet_routes_to_helper_not_filesystem(self):
        with patch('experimental.backend.services.torrent_service.torrent_service.resolve', AsyncMock(return_value='http://127.0.0.1:8091/video')) as resolve:
            self.assertEqual(await stream_service._resolve_source(HASH), 'http://127.0.0.1:8091/video')
            resolve.assert_awaited_once_with(HASH)

    async def test_stremio_magnet_keeps_file_index_and_trackers(self):
        path = Path('experimental/plugins/com.vicking20.stream-manager/plugin.py')
        spec = importlib.util.spec_from_file_location('stream_manager_test', path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'backend': SimpleNamespace(), 'backend.addons': SimpleNamespace(), 'backend.addons.interfaces': interfaces}):
            spec.loader.exec_module(module)
        plugin = module.Plugin.__new__(module.Plugin)
        plugin.config = {'max_results': 20}
        plugin.p2p_client = None
        plugin.client = SimpleNamespace(get_streams=AsyncMock(return_value=[{'infoHash': HASH, 'fileIdx': 0, 'sources': ['tracker:https://tracker.example/announce'], 'title': 'Test'}]))
        streams = await plugin.get_streams('tt123', 'movie')
        params = parse_qs(urlsplit(streams[0].url).query)
        self.assertEqual(params['so'], ['0'])
        self.assertEqual(params['tr'], ['https://tracker.example/announce'])
        self.assertEqual(streams[0].tag, 'P2P')
        fallback = module.Plugin.torrentio_p2p_url('https://torrentio.strem.fun/providers=yts|realdebrid=secret|debridoptions=nodownloadlinks/manifest.json')
        self.assertEqual(fallback, 'https://torrentio.strem.fun/providers=yts/manifest.json')
        plugin.config['max_results'] = 1
        plugin.client.get_streams = AsyncMock(return_value=[{'url': 'https://example.test/video.mp4', 'title': 'Debrid'}])
        plugin.p2p_client = SimpleNamespace(get_streams=AsyncMock(return_value=[{'infoHash': HASH, 'fileIdx': 0, 'title': 'P2P'}]))
        choices = await plugin.get_streams('tt123', 'movie')
        self.assertEqual(len(choices), 2)
        self.assertTrue(choices[1].url.startswith('magnet:'))
