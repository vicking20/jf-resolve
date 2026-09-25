import asyncio
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "experimental/backend/services/operation_profile.py"
)
SPEC = importlib.util.spec_from_file_location("operation_profile", MODULE_PATH)
operation_profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operation_profile)

PROFILE_PRESETS = operation_profile.PROFILE_PRESETS
apply_operation_profile = operation_profile.apply_operation_profile
get_profile_settings = operation_profile.get_profile_settings


class FakeSettings:
    def __init__(self):
        self.updated = None

    async def update_many(self, values):
        self.updated = values


class OperationProfileTests(unittest.TestCase):
    def test_profiles_expose_the_expected_outputs(self):
        self.assertTrue(PROFILE_PRESETS["standalone"]["web_player_enabled"])
        self.assertFalse(PROFILE_PRESETS["standalone"]["strm_generation_enabled"])
        self.assertTrue(PROFILE_PRESETS["jellyfin"]["strm_generation_enabled"])
        self.assertFalse(PROFILE_PRESETS["jellyfin"]["web_player_enabled"])
        self.assertTrue(PROFILE_PRESETS["hybrid"]["web_player_enabled"])
        self.assertTrue(PROFILE_PRESETS["hybrid"]["strm_generation_enabled"])

    def test_profile_settings_are_not_shared(self):
        first = get_profile_settings("hybrid")
        first["web_player_enabled"] = False
        self.assertTrue(get_profile_settings("hybrid")["web_player_enabled"])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            get_profile_settings("legacy")

    def test_apply_persists_one_complete_map(self):
        settings = FakeSettings()
        applied = asyncio.run(apply_operation_profile(settings, "hybrid"))
        self.assertEqual(settings.updated, applied)
        self.assertEqual(applied["operation_profile"], "hybrid")


if __name__ == "__main__":
    unittest.main()
