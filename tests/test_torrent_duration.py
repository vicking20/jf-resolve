import asyncio
import json
import unittest
from unittest.mock import patch

from experimental.backend.services.ffprobe_service import FFprobeService
from experimental.backend.services.torrent_service import torrent_service
from experimental.backend.services.transcode_manager import transcode_manager


class FakeStderr:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _):
        return self.chunks.pop(0) if self.chunks else b""


class TorrentDurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_duration_is_read_from_ffmpeg_output_split_across_chunks(self):
        stream_id = "watch-test"
        transcode_manager.active_transcodes[stream_id] = {}
        process = type("P", (), {"stderr": FakeStderr([b"Input #0, matroska\n  Dura", b"tion: 00:52:13.04, start: 0", b"frame= 1\r" * 50])})()
        transcode_manager.watch_output(stream_id, process)
        await asyncio.sleep(0.05)
        self.assertAlmostEqual(transcode_manager.active_transcodes.pop(stream_id)["duration"], 3133.04)

    async def test_known_duration_is_not_overwritten(self):
        stream_id = "watch-known"
        transcode_manager.active_transcodes[stream_id] = {"duration": 100.0}
        process = type("P", (), {"stderr": FakeStderr([b"Duration: 01:00:00.00,"])})()
        transcode_manager.watch_output(stream_id, process)
        await asyncio.sleep(0.05)
        self.assertEqual(transcode_manager.active_transcodes.pop(stream_id)["duration"], 100.0)

    async def test_torrserver_urls_get_a_longer_header_only_probe(self):
        seen = {}

        class Proc:
            returncode = 0

            async def communicate(self):
                return json.dumps({"format": {}, "streams": []}).encode(), b""

        async def fake_exec(*cmd, **kwargs):
            seen["cmd"] = cmd
            return Proc()

        async def fake_wait_for(coro, timeout):
            seen["timeout"] = timeout
            return await coro

        url = torrent_service.url + "/stream/Show.S01E01.mkv?link=abc&index=1&play="
        FFprobeService.clear_cache()
        with patch("asyncio.create_subprocess_exec", fake_exec), patch("asyncio.wait_for", fake_wait_for):
            await FFprobeService.probe_stream(url)
        self.assertGreaterEqual(seen["timeout"], 45)
        self.assertIn("-probesize", seen["cmd"])
        self.assertEqual(seen["cmd"][-1], url)


if __name__ == "__main__":
    unittest.main()
