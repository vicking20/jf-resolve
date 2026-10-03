"""Scanner mode manager - determines scanning behavior per library path"""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ...models.library_path import LibraryPath
from ...models.media_item import MediaType
from ..settings_manager import SettingsManager

ScannerMode = Literal["lazy", "active"]


class ScannerModeManager:
    """
    Determines scanning behavior based on global settings and path configuration.

    Mode behaviors:
    - Lazy mode: Movies/TV use JIT scanning, Videos actively scanned
    - Active mode: All paths actively scanned and persisted to DB
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = SettingsManager(db)

    async def get_effective_mode(self, library_path: LibraryPath) -> ScannerMode:
        """
        Get the effective scanner mode for a library path.

        Priority:
        1. Path-level override (if set)
        2. Global setting
        3. In lazy mode, video/video_series always treated as "active"

        Returns:
            "lazy" or "active"
        """
        # 1. Check path-level override
        if library_path.scan_mode_override:
            return library_path.scan_mode_override

        # 2. Get global setting
        global_mode = await self.settings.get("library_scanner_mode", "lazy")

        # 3. In lazy mode, video/video_series paths always use active behavior
        if global_mode == "lazy":
            media_type = library_path.media_type
            if isinstance(media_type, str):
                # Handle string media_type
                if media_type in ["video", "video_series"]:
                    return "active"
            else:
                # Handle MediaType enum
                if media_type in [MediaType.VIDEO, MediaType.VIDEO_SERIES]:
                    return "active"

        return global_mode

    async def get_global_mode(self) -> ScannerMode:
        """Get the global scanner mode setting"""
        return await self.settings.get("library_scanner_mode", "lazy")

    async def set_global_mode(self, mode: ScannerMode):
        """Set the global scanner mode"""
        if mode not in ("lazy", "active"):
            raise ValueError(f"Invalid scanner mode: {mode}")
        await self.settings.set("library_scanner_mode", mode)


    def should_watch_path(self, mode: ScannerMode, media_type: str) -> bool:
        """
        Determine if path should have file watcher enabled.

        Args:
            mode: The effective scanner mode
            media_type: "movie", "tv", "video", or "video_series"

        Returns:
            True if file watcher should monitor this path
        """
        if mode == "active":
            return True

        # Lazy mode: only watch video/video_series paths
        return media_type in ["video", "video_series"]


    async def get_mode_description(self, mode: ScannerMode) -> str:
        """Get human-readable description of scanner mode"""
        descriptions = {
            "lazy": (
                "JIT (Just-In-Time) scanning for movies/TV - items appear when browsing. "
                "Videos and video series are actively scanned and monitored."
            ),
            "active": (
                "Active scanning - all media is actively scanned, matched with TMDB, "
                "persisted to database, and monitored for changes in real-time."
            ),
        }
        return descriptions.get(mode, "Unknown mode")

    def get_media_type_string(self, media_type) -> str:
        """
        Convert MediaType enum or string to consistent string format.

        Handles both string and enum inputs.
        """
        if isinstance(media_type, str):
            return media_type
        else:
            # MediaType enum
            return media_type.value if hasattr(media_type, "value") else str(media_type)
