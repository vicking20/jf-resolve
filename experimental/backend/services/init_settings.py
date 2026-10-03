"""Initialize default settings"""

from sqlalchemy.ext.asyncio import AsyncSession

from .settings_manager import SettingsManager


async def initialize_default_settings(db: AsyncSession):
    """Initialize default settings if they don't exist"""
    settings = SettingsManager(db)

    # Core settings
    defaults = {
        # Operation profile and independently configurable capabilities.
        # Standalone matches the behavior of the current web application.
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
        # Jellyfin / STRM output. Paths must be visible to Jellyfin.
        "jfresolve_public_url": "http://127.0.0.1:8765",
        "jellyfin_server_url": "",
        "jellyfin_api_key": "",
        "jellyfin_movie_path": "",
        "jellyfin_tv_path": "",
        # TMDB Integration
        "tmdb_api_key": "",
        # Metadata & Caching
        "metadata_cache_days": 30,
        # Streaming & Quality
        "quality_fallback_order": ["2160p", "1080p", "720p", "480p", "unknown"],
        # Player (feature toggle, not implemented yet)
        "custom_player_enabled": False,
        "auto_play_next_episode": False,
        "default_subtitle_language": "",
        "default_audio_language": "",
        # Library Scanning
        "auto_scan_on_startup": False,
        "watch_for_changes": False,
        "scan_interval_hours": 0,  # 0 = manual only
        # User Activity Tracking
        "log_user_logins": True,
        "log_playback_events": True,
        "activity_retention_days": 90,
        # UI Preferences
        "show_stream_details": False,
        # Backup (feature toggle)
        "backup_enabled": False,
        "backup_schedule": "0 2 * * *",  # 2 AM daily
        "backup_retention_count": 7,
        # Transcoding Settings
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
        "queue_full_behavior": "wait",  # wait, direct_proxy, close_player
        # Plugin Marketplace
        "plugin_repositories": [
            "https://raw.githubusercontent.com/vicking20/jf-resolve-plugin-repo/main/master.json"
        ],
    }

    for key, default_value in defaults.items():
        value = await settings.get(key)
        if value is None:
            await settings.set(key, default_value)

    # Older installations saved the fallback order before unknown-quality
    # addon results were supported. Keep custom ordering and add the missing
    # last-resort bucket instead of replacing the user's preferences.
    fallback_order = await settings.get("quality_fallback_order", [])
    if isinstance(fallback_order, list) and "unknown" not in fallback_order:
        await settings.set("quality_fallback_order", [*fallback_order, "unknown"])

    # Detect and set hardware acceleration if available
    await detect_and_set_hardware_acceleration(settings)


async def detect_and_set_hardware_acceleration(settings: SettingsManager):
    """Detect available hardware acceleration and update setting if needed"""
    from .transcoding_service import TranscodingService

    current_hw = await settings.get("hardware_acceleration")

    # Auto-detect only if set to 'autodetect'
    if current_hw != "autodetect":
        return

    available_hw = TranscodingService.detect_hardware_acceleration()

    # Prefer NVIDIA > VideoToolbox > Intel > AMD
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

    # Repair the previous default feed without changing custom repositories.
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
