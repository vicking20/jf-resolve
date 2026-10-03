"""Playback regressions. Run: venv/bin/python -m unittest discover -s tests."""
import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from experimental.backend.api.stream import get_subtitle, get_live_subtitles, seek_transcode, serve_hls_segment
from experimental.backend.api.playback_tracking import get_playback_progress
from experimental.backend.services.ffprobe_service import FFprobeService
from experimental.backend.services.stream_service import stream_service
from experimental.backend.services.transcoding_service import TranscodingService
from experimental.backend.services.transcode_manager import TranscodeManager, transcode_manager


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_waits_for_exit_and_repeated_stop_does_not_release_twice(self):
        manager = TranscodeManager()
        await manager.set_max_concurrent(1)
        await manager.acquire_slot('test')
        process = SimpleNamespace(returncode=None, terminate=lambda: events.append('terminate'),
                                  wait=AsyncMock(side_effect=lambda: events.append('exit')))
        events = []
        manager.register_process('test', process)
        manager.cleanup_hls_files = lambda _: events.append('delete')
        await manager.release_slot_async('test')
        await manager.release_slot_async('test')
        self.assertEqual(events, ['terminate', 'exit', 'delete'])
        self.assertEqual(manager.semaphore._value, 1)

    async def test_idle_stream_expires_but_live_heartbeat_keeps_session(self):
        manager = TranscodeManager()
        await manager.acquire_slot('abandoned')
        await manager.acquire_slot('alive')
        manager.active_transcodes['abandoned']['last_seen'] = 0
        manager.active_transcodes['alive']['last_seen'] = 0
        manager.touch_stream('alive')
        expired = []
        manager.cleanup_hls_files = lambda stream_id: expired.append(stream_id)
        await manager.expire_idle_streams(timeout=120)
        self.assertEqual(expired, ['abandoned'])
        self.assertIn('alive', manager.active_transcodes)
        self.assertNotIn('abandoned', manager.active_transcodes)


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_unwatched_media_returns_zero_progress(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
        with patch('experimental.backend.services.settings_manager.SettingsManager.get', new=AsyncMock(return_value='test-key')), \
             patch('experimental.backend.services.media_resolver.MediaResolver.resolve', new=AsyncMock(return_value={'uuid': 'fixture', 'media_type': 'tv'})), \
             patch('experimental.backend.services.tmdb_service.TMDBService', return_value=object()):
            result = await get_playback_progress('tmdb:123', media_type='tv', season=1, episode=1, db=db, current_user=SimpleNamespace(id=1))
        self.assertEqual(result['current_time'], 0)
        self.assertFalse(result['completed'])
        self.assertEqual(result['progress_percentage'], 0)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class PlaybackIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='jf-playback-test-')
        cls.video = Path(cls.temp.name) / 'fixture.mkv'
        subtitle = Path(cls.temp.name) / 'fixture.srt'
        subtitle.write_text('1\n00:00:01,000 --> 00:00:03,000\nFirst cue\n\n2\n00:00:19,000 --> 00:00:21,000\nLate cue\n')
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=24',
                        '-f', 'lavfi', '-i', 'sine=frequency=440', '-i', str(subtitle),
                        '-map', '0:v', '-map', '1:a', '-map', '2:s', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-g', '96', '-c:a', 'aac',
                        '-c:s', 'srt', '-metadata:s:s:0', 'language=eng', '-t', '24', str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    async def asyncTearDown(self):
        await transcode_manager.cleanup_all()

    async def start(self):
        response = await stream_service.get_stream_response(str(self.video), user_agent='Firefox',
            transcode_settings={'transcode_enabled': True, 'hardware_acceleration': 'none',
                                'transcode_preset': 'ultrafast', 'hls_segment_duration': 4},
            force_transcode=True)
        return json.loads(response.body)

    async def test_sessions_are_isolated_seek_has_local_timeline_and_fresh_segments(self):
        first, second = await self.start(), await self.start()
        self.assertNotEqual(first['stream_id'], second['stream_id'])
        await transcode_manager.release_slot_async(first['stream_id'])
        stream_id = second['stream_id']
        live = await get_live_subtitles(stream_id, 0, current_user=SimpleNamespace(id=1))
        self.assertIn(b'First cue', live.body)
        self.assertEqual(live.headers['x-subtitle-offset'], '0.0')
        playlist = transcode_manager.get_hls_output_dir(stream_id) / 'master.m3u8'
        self.assertTrue(playlist.exists())
        result = await seek_transcode(stream_id, 18.0, current_user=SimpleNamespace(id=1))
        self.assertEqual(result['position'], 18.0)
        live = await get_live_subtitles(stream_id, 0, current_user=SimpleNamespace(id=1))
        self.assertIn(b'Late cue', live.body)
        self.assertEqual(live.headers['x-subtitle-offset'], '18.0')
        content = playlist.read_text()
        self.assertIn('#EXT-X-MEDIA-SEQUENCE:0', content)
        response = await serve_hls_segment(stream_id, 'master.m3u8')
        self.assertNotIn(b'#EXT-X-START', response.body)
        segment = next(line for line in content.splitlines() if line.endswith('.ts'))
        response = await serve_hls_segment(stream_id, segment)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        info = transcode_manager.get_segment_range(stream_id)
        self.assertEqual(info['min_time'], 18.0)
        self.assertGreater(info['max_time'], 18.0)
        self.assertLessEqual(info['max_time'], second['probe_info']['duration'])

    async def test_cancel_stops_a_running_ffmpeg_before_removing_output(self):
        stream_id = uuid.uuid4().hex
        await transcode_manager.acquire_slot(stream_id)
        directory = transcode_manager.get_hls_output_dir(stream_id)
        directory.mkdir(parents=True)
        playlist = directory / 'master.m3u8'
        probe = await FFprobeService.probe_stream(str(self.video))
        cmd = TranscodingService.build_ffmpeg_command(str(self.video), str(playlist),
            {'hardware_acceleration': 'none', 'hls_segment_duration': 4},
            {'video': ['h264'], 'audio': ['aac']}, probe)
        cmd.insert(cmd.index('-i'), '-re')
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        transcode_manager.register_process(stream_id, process)
        self.assertTrue(await transcode_manager.wait_for_hls_ready(playlist, timeout=10, process=process))
        self.assertIsNone(process.returncode)
        await transcode_manager.release_slot_async(stream_id)
        self.assertIsNotNone(process.returncode)
        self.assertFalse(directory.exists())
        self.assertNotIn(stream_id, transcode_manager.active_transcodes)

    async def test_embedded_text_subtitles_are_discovered_and_extracted(self):
        probe = await FFprobeService.probe_stream(str(self.video))
        self.assertEqual(probe['subtitle_tracks'][0]['language'], 'eng')
        self.assertTrue(probe['subtitle_tracks'][0]['supported'])
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            scalar_one_or_none=lambda: SimpleNamespace(file_path=str(self.video), media_uuid="m1"))))
        response = await get_subtitle(index=0, source_id=1, url=None, db=db, current_user=SimpleNamespace(id=1, is_superuser=True))
        self.assertTrue(response.body.startswith(b'WEBVTT'))
        self.assertIn(b'Late cue', response.body)
        chunk = await get_subtitle(index=0, source_id=1, url=None, db=db, current_user=SimpleNamespace(id=1, is_superuser=True), position=19)
        self.assertIn(b'Late cue', chunk.body)
        self.assertIn(b'00:19.000', chunk.body)


if __name__ == '__main__':
    unittest.main()
