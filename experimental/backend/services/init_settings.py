"""Initialize default settings"""

from sqlalchemy.ext.asyncio import AsyncSession

from .settings_manager import SettingsManager


async def initialize_default_settings(db: AsyncSession):
    """Initialize default settings if they don't exist"""
    settings = SettingsManager(db)

    defaults = {
        "operation_profile": "standalone",
        "web_player_enabled": True,
        "strm_generation_enabled": False,
        "jellyfin_integration_enabled": False,
        "jellyfin_scan_enabled": False,
        "local_library_enabled": True,
        "torrent_playback_enabled": True,
        "torrent_allow_upload": False,
        "torrent_upload_limit_kbps": 0,
        "default_play_action": "web",
        "jfresolve_public_url": "http://127.0.0.1:8765",
        "jellyfin_server_url": "",
        "jellyfin_api_key": "",
        "jellyfin_movie_path": "",
        "jellyfin_tv_path": "",
        "tmdb_api_key": "",
        "metadata_cache_days": 30,
        "quality_fallback_order": ["2160p", "1080p", "720p", "480p", "unknown"],
        "custom_player_enabled": False,
        "auto_play_next_episode": False,
        "default_subtitle_language": "",
        "default_audio_language": "",
        "auto_scan_on_startup": False,
        "watch_for_changes": False,
        "scan_interval_hours": 0,
        "log_user_logins": True,
        "log_playback_events": True,
        "activity_retention_days": 90,
        "show_stream_details": False,
        "backup_enabled": False,
        "backup_schedule": "0 2 * * *",
        "backup_retention_count": 7,
        "transcode_enabled": True,
        "hardware_acceleration": "autodetect",
        "max_concurrent_transcodes": 0,
        "transcode_preset": "veryfast",
        "hdr_tone_mapping": True,
        "target_video_codec": "h264",
        "target_audio_codec": "aac",
        "hls_segment_duration": 4,
        "buffer_size_seconds": 6,
        "auto_skip_invalid_streams": True,
        "min_valid_duration_seconds": 120,
        "queue_full_behavior": "wait",
        "plugin_repositories": [
            "https://raw.githubusercontent.com/vicking20/jf-resolve-plugin-repo/main/master.json"
        ],
    }

    for key, default_value in defaults.items():
        value = await settings.get(key)
        if value is None:
            await settings.set(key, default_value)

    fallback_order = await settings.get("quality_fallback_order", [])
    if isinstance(fallback_order, list) and "unknown" not in fallback_order:
        await settings.set("quality_fallback_order", [*fallback_order, "unknown"])

    await detect_and_set_hardware_acceleration(settings)


async def detect_and_set_hardware_acceleration(settings: SettingsManager):
    """Detect available hardware acceleration and update setting if needed"""
    from .transcoding_service import TranscodingService

    current_hw = await settings.get("hardware_acceleration")

    if current_hw != "autodetect":
        return

    available_hw = TranscodingService.detect_hardware_acceleration()

    if "nvidia" in available_hw:
        recommended = "nvidia"
    elif "videotoolbox" in available_hw:
        recommended = "videotoolbox"
    elif "intel" in available_hw:
        recommended = "intel"
    elif "amd" in available_hw:
        recommended = "amd"
    else:
        recommended = "none"

    await settings.set("hardware_acceleration", recommended)
    from .log_service import log_service

    log_service.info(f"Auto-detected hardware acceleration: {recommended}")

    old_feed = "https://raw.githubusercontent.com/vicking20/jf-resolve-plugin-repo/main/manifest.json"
    official_feed = "https://raw.githubusercontent.com/vicking20/jf-resolve-plugin-repo/main/master.json"
    repositories = await settings.get("plugin_repositories", [])
    if old_feed in repositories:
        repositories = list(dict.fromkeys(
            official_feed if url == old_feed else url for url in repositories
        ))
        await settings.set("plugin_repositories", repositories)
        await settings.set("marketplace_cache", {})
        await settings.set("marketplace_cache_time", "")
