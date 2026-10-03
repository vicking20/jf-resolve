import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from experimental.backend.services import init_settings
from experimental.backend.services.torrent_service import TorrentService


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json):
        self.calls.append(json)
        return FakeResponse({"DisableUpload": False, "DisableUPNP": False, "UploadRateLimit": 0, "CacheSize": 1})


class TorrentSharingTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_refuses_when_torrent_playback_is_off(self):
        service = TorrentService()
        with patch.object(service, "load_preferences", AsyncMock(return_value=(False, False, 0))):
            with self.assertRaises(HTTPException) as ctx:
                await service.resolve("magnet:?xt=urn:btih:" + "a" * 40)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_upload_settings_are_pushed_once_and_keep_other_settings(self):
        service = TorrentService()
        service.external = False
        calls = []
        with patch("experimental.backend.services.torrent_service.httpx.AsyncClient", lambda **_: FakeClient(calls)):
            await service.apply_sharing(False, 0)
            await service.apply_sharing(False, 0)
        self.assertEqual([c["action"] for c in calls], ["get", "set"])
        sets = calls[1]["sets"]
        self.assertEqual((sets["DisableUpload"], sets["DisableUPNP"], sets["UploadRateLimit"]), (True, True, 0))
        self.assertEqual(sets["CacheSize"], 1)

    async def test_external_torrserver_settings_are_left_alone(self):
        service = TorrentService()
        service.external = True
        calls = []
        with patch("experimental.backend.services.torrent_service.httpx.AsyncClient", lambda **_: FakeClient(calls)):
            await service.apply_sharing(False, 0)
        self.assertEqual(calls, [])

    async def test_seeding_is_off_by_default(self):
        class Store:
            def __init__(self, db):
                self.values = stores.setdefault("values", {})

            async def get(self, key, default=None):
                return self.values.get(key, default)

            async def set(self, key, value):
                self.values[key] = value

        stores = {}
        with patch.object(init_settings, "SettingsManager", Store), \
                patch.object(init_settings, "detect_and_set_hardware_acceleration", AsyncMock()):
            await init_settings.initialize_default_settings(None)
        values = stores["values"]
        self.assertTrue(values["torrent_playback_enabled"])
        self.assertFalse(values["torrent_allow_upload"])
        self.assertEqual(values["torrent_upload_limit_kbps"], 0)

if __name__ == "__main__":
    unittest.main()
