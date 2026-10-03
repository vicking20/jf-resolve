import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PluginManifest(BaseModel):
    id: str
    name: str
    version: str
    description: str
    author: Optional[str] = None
    capabilities: List[str] = []


class BasePlugin(ABC):
    """Base class for all plugins"""

    default_config: Dict[str, Any] = {}

    def __init__(self, manifest: PluginManifest, path: str):
        self.manifest = manifest
        self.path = Path(path)
        self.enabled = False
        self.config: Dict[str, Any] = {}

        from .manager import get_plugin_logger
        self.logger = get_plugin_logger(manifest.id)

    @property
    def has_ui(self) -> bool:
        """Check if plugin provides a configuration UI"""
        return (self.path / "config.html").exists()

    async def on_load(self):
        """Called when plugin is loaded/enabled"""
        pass

    async def on_unload(self):
        """Called when plugin is unloaded/disabled"""
        pass

    def load_config(self) -> Dict[str, Any]:
        """
        Load config.json from plugin directory and merge with default_config.
        """
        config_path = self.path / "config.json"

        current_config = self.default_config.copy()

        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    file_config = json.load(f)
                    current_config.update(file_config)
            except Exception as e:
                logging.getLogger(__name__).error(
                    f"Failed to load config for {self.manifest.id}: {e}"
                )

        self.config = current_config
        if not config_path.exists() and self.default_config:
            self.save_config()

        return self.config

    def save_config(self):
        """Save current self.config to plugin_dir/config.json"""
        config_path = self.path / "config.json"
        try:
            with open(config_path, "w") as f:
                json.dump(self.config, f, indent=4)
        except Exception as e:
            logging.getLogger(__name__).error(
                f"Failed to save config for {self.manifest.id}: {e}"
            )


class CatalogueItem(BaseModel):
    id: str
    title: str
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    overview: Optional[str] = None
    media_type: str
    year: Optional[int] = None
    rating: Optional[float] = None

    class Config:
        extra = "allow"


class CatalogueDefinition(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    media_type: Optional[str] = None
    provider_id: Optional[str] = None


class CatalogueProvider(ABC):
    """Interface for plugins that provide media catalogues"""

    @abstractmethod
    async def get_catalogues(self) -> List[CatalogueDefinition]:
        """Return list of catalogues this plugin provides"""
        pass

    @abstractmethod
    async def get_items(self, catalogue_id: str, page: int = 1) -> List[CatalogueItem]:
        """Fetch items for a specific catalogue"""
        pass


class Stream(BaseModel):
    title: str
    url: str
    resolution: Optional[str] = None
    quality: Optional[str] = None
    size: Optional[str] = None
    tag: Optional[str] = None
    language: Optional[str] = None
    is_cached: bool = False
    raw_title: Optional[str] = None
    headers: Optional[Dict[str, str]] = None


class SourceProvider(ABC):
    """Interface for plugins that provide media streams for TMDB content"""

    @abstractmethod
    async def get_streams(
        self,
        imdb_id: str,
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[Stream]:
        """Return list of playable streams for TMDB content (requires IMDB ID)"""
        pass


class UserContentSourceProvider(ABC):
    """
    Interface for plugins that provide streams for user content (Video/VideoSeries).

    Unlike SourceProvider which requires IMDB ID, this interface works with:
    - Provider-specific identifiers (id_source + external_id)
    - Title-based matching
    - Direct URL transformation (e.g., cloud storage APIs)

    The `id_source` field allows addons to quickly filter compatible items:
    - "kitsu" → Kitsu anime addon
    - "anilist" → AniList addon
    - "gdrive" → Google Drive addon
    - "dropbox" → Dropbox addon
    - "local" or None → Local files only, no addon streams

    Example use cases:
    - Kitsu addon: Only process items where id_source="kitsu"
    - Google Drive addon: Transform file IDs to playable URLs
    - Generic addon: Process all items regardless of id_source
    """

    supported_sources: Optional[List[str]] = None

    @abstractmethod
    async def get_streams_for_video(
        self,
        video_uuid: str,
        title: str,
        id_source: Optional[str] = None,
        external_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Stream]:
        """
        Return list of playable streams for a user video.

        Args:
            video_uuid: Internal UUID of the video
            title: Video title (for title-based matching)
            id_source: Source provider identifier (e.g., "kitsu", "gdrive")
            external_id: Provider-specific ID (e.g., Kitsu anime ID, Drive file ID)
            metadata: Optional additional metadata (year, duration, etc.)

        Returns empty list if id_source doesn't match supported_sources.
        """
        pass

    @abstractmethod
    async def get_streams_for_video_series(
        self,
        series_uuid: str,
        title: str,
        season: int,
        episode: int,
        id_source: Optional[str] = None,
        external_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Stream]:
        """
        Return list of playable streams for a video series episode.

        Args:
            series_uuid: Internal UUID of the series
            title: Series title
            season: Season number
            episode: Episode number
            id_source: Source provider identifier
            external_id: Provider-specific ID
            metadata: Optional additional metadata

        Returns empty list if id_source doesn't match supported_sources.
        """
        pass


class Injection(BaseModel):
    target_id: str
    content: str
    scripts: Optional[str] = None


class UIExtension(ABC):
    """Interface for plugins that modify the UI"""

    @abstractmethod
    async def get_injections(self, page_context: str) -> List[Injection]:
        """Return HTML/JS injections for a page"""
        pass
