import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from experimental.backend.addons.interfaces import PluginManifest
from experimental.backend.services import init_settings


PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "experimental/plugins/com.vicking20.stream-manager/plugin.py"
)


def load_stream_manager_plugin():
    sys.modules.setdefault("backend", importlib.import_module("experimental.backend"))
    spec = importlib.util.spec_from_file_location("test_stream_manager_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class UnknownQualityStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_quality_is_kept_as_a_playable_stream(self):
        module = load_stream_manager_plugin()

        class FakeClient:
            async def get_streams(self, media_type, identifier):
                self.request = (media_type, identifier)
                return [{
                    "name": "Torrentio RD+",
                    "title": "Classic.Show.S02E04.WEB.x264",
                    "url": "https://example.test/video",
                    "behaviorHints": {"filename": "Classic.Show.S02E04.mkv"},
                }]

        with tempfile.TemporaryDirectory() as plugin_dir:
            plugin = module.Plugin(
                PluginManifest(
                    id="test.stream-manager",
                    name="Stream Manager",
                    version="1.0.0",
                    description="test",
                ),
                plugin_dir,
            )
            plugin.config = {"max_results": 20, "show_stream_details": False}
            plugin.client = FakeClient()
            plugin.p2p_client = None
            streams = await plugin.get_streams("tt123", "tv", 2, 4)

        self.assertEqual(plugin.client.request, ("tv", "tt123:2:4"))
        self.assertEqual(len(streams), 1)
        self.assertEqual(streams[0].quality, "unknown")
        self.assertEqual(streams[0].url, "https://example.test/video")


class UnknownQualitySettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_fallback_order_is_upgraded_without_reordering(self):
        values = {"quality_fallback_order": ["1080p", "720p", "480p"]}

        class FakeSettingsManager:
            def __init__(self, _db):
                pass

            async def get(self, key, default=None):
                return values.get(key, default)

            async def set(self, key, value):
                values[key] = value

        with (
            patch.object(init_settings, "SettingsManager", FakeSettingsManager),
            patch.object(
                init_settings,
                "detect_and_set_hardware_acceleration",
                AsyncMock(),
            ),
        ):
            await init_settings.initialize_default_settings(object())

        self.assertEqual(
            values["quality_fallback_order"],
            ["1080p", "720p", "480p", "unknown"],
        )


if __name__ == "__main__":
    unittest.main()
