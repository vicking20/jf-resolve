import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from experimental.backend.addons.interfaces import Subtitle
from experimental.backend.addons.manager import PluginManager
from experimental.backend.services import external_subtitles


class ExternalSubtitleTests(unittest.IsolatedAsyncioTestCase):
    def test_tokens_round_trip_and_reject_tampering(self):
        token = external_subtitles.sign("https://subs.example/1.srt")
        self.assertEqual(external_subtitles.unsign(token), "https://subs.example/1.srt")
        with self.assertRaises(HTTPException):
            external_subtitles.unsign(token + "x")

    def test_decode_handles_bom_and_legacy_encodings(self):
        self.assertEqual(external_subtitles.decode(b"\xef\xbb\xbf1\n"), "1\n")
        self.assertEqual(external_subtitles.decode("café".encode("cp1252")), "café")

    async def test_fetch_rejects_files_that_are_not_subtitles(self):
        token = external_subtitles.sign("https://subs.example/1.srt")

        class Response:
            def raise_for_status(self):
                pass

            async def aiter_bytes(self):
                yield b"<html>not found</html>"

        class Client:
            def __init__(self, **_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def stream(self, method, url):
                client = self

                class Ctx:
                    async def __aenter__(self):
                        return Response()

                    async def __aexit__(self, *exc):
                        return False

                return Ctx()

        with patch.object(external_subtitles, "validate_source_url", AsyncMock(side_effect=lambda url, user: url)), \
                patch.object(external_subtitles.httpx, "AsyncClient", Client):
            with self.assertRaises(HTTPException) as ctx:
                await external_subtitles.fetch(token, SimpleNamespace(is_superuser=False))
        self.assertEqual(ctx.exception.status_code, 422)

    async def test_manager_collects_subtitles_from_every_provider(self):
        manager = PluginManager.__new__(PluginManager)
        provider = SimpleNamespace(
            manifest={"name": "Subs"},
            get_subtitles=AsyncMock(return_value=[Subtitle(url="https://s/1.srt", language="English")]),
        )
        broken = SimpleNamespace(manifest={"id": "broken"}, get_subtitles=AsyncMock(return_value=None))
        manager.subtitle_providers = [provider, broken]
        result = await manager.get_all_subtitles("tt1", "tv", 1, 2)
        provider.get_subtitles.assert_awaited_once_with("tt1", "tv", 1, 2)
        self.assertEqual(result, [{"url": "https://s/1.srt", "language": "English", "label": None, "provider": "Subs"}])


if __name__ == "__main__":
    unittest.main()
