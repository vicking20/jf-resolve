"""Operation profiles for the standalone, Jellyfin, and hybrid workflows."""

from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from .settings_manager import SettingsManager


PROFILE_PRESETS: Dict[str, Dict[str, Any]] = {
    "standalone": {
        "web_player_enabled": True,
        "strm_generation_enabled": False,
        "jellyfin_integration_enabled": False,
        "jellyfin_scan_enabled": False,
        "local_library_enabled": True,
        "torrent_playback_enabled": True,
        "default_play_action": "web",
    },
    "jellyfin": {
        "web_player_enabled": False,
        "strm_generation_enabled": True,
        "jellyfin_integration_enabled": True,
        "jellyfin_scan_enabled": True,
        "local_library_enabled": False,
        "torrent_playback_enabled": False,
        "default_play_action": "jellyfin",
    },
    "hybrid": {
        "web_player_enabled": True,
        "strm_generation_enabled": True,
        "jellyfin_integration_enabled": True,
        "jellyfin_scan_enabled": True,
        "local_library_enabled": True,
        "torrent_playback_enabled": True,
        "default_play_action": "web",
    },
}


def get_profile_settings(profile: str) -> Dict[str, Any]:
    """Return an isolated settings map for a named profile."""
    if profile not in PROFILE_PRESETS:
        raise ValueError(f"Unknown operation profile: {profile}")
    return {"operation_profile": profile, **deepcopy(PROFILE_PRESETS[profile])}


async def apply_operation_profile(
    settings: "SettingsManager", profile: str
) -> Dict[str, Any]:
    """Persist a profile and its capability defaults atomically."""
    values = get_profile_settings(profile)
    await settings.update_many(values)
    return values
