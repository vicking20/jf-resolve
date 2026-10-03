"""Plugin manager - discovers and loads plugins"""

import builtins
import importlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .interfaces import (
    BasePlugin,
    CatalogueProvider,
    SourceProvider,
    UIExtension,
    UserContentSourceProvider,
)
from ..core.config import settings


def get_plugin_logger(plugin_id: str) -> logging.Logger:
    """Get a logger for a specific plugin that writes to logs/plugin.log"""
    logger = logging.getLogger(f"plugin.{plugin_id}")
    if not logger.handlers:
        data_log_file = settings.BASE_DIR / "data" / "logs" / "plugin.log"
        data_log_file.parent.mkdir(parents=True, exist_ok=True)
        data_handler = logging.FileHandler(data_log_file)

        formatter = logging.Formatter(f"[%(asctime)s] [{plugin_id}] %(levelname)s: %(message)s")
        data_handler.setFormatter(formatter)

        logger.addHandler(data_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False

    return logger

logger = logging.getLogger(__name__)


class SafePluginWrapper:
    """
    Wraps a plugin instance to catch and log all errors automatically.
    Any method call that raises an exception will be logged and return a safe default.
    """

    def __init__(self, plugin: BasePlugin, manifest: dict):
        self._plugin = plugin
        self._manifest = manifest
        self._plugin_name = manifest.get("name", "Unknown")

    def __getattr__(self, name: str) -> Any:
        """Intercept all attribute access and wrap callable methods"""
        attr = getattr(self._plugin, name)

        if callable(attr):

            async def safe_wrapper(*args, **kwargs):
                try:
                    result = attr(*args, **kwargs)
                    if hasattr(result, "__await__"):
                        return await result
                    return result
                except Exception as e:
                    logger.error(
                        f"Plugin '{self._plugin_name}' error in {name}(): {e}",
                        exc_info=True,
                    )
                    if name in ["get_catalogues", "get_items", "get_streams"]:
                        return []
                    elif name in ["get_ui_extensions"]:
                        return {}
                    return None

            return safe_wrapper

        return attr

    @property
    def manifest(self):
        """Expose manifest without wrapping"""
        return self._manifest

    @property
    def id(self):
        """Expose manifest ID"""
        return self._manifest.get("id")

    @property
    def path(self):
        """Expose path without wrapping"""
        return self._plugin.path


class PluginManager:
    """Manages plugin discovery and loading"""

    def __init__(self, plugins_dir: Path = None):
        if plugins_dir is None:
            plugins_dir = Path(__file__).parent.parent.parent / "plugins"

        self.plugins_dir = plugins_dir
        self.plugins: Dict[str, BasePlugin] = {}
        self.catalogue_providers: List[CatalogueProvider] = []
        self.source_providers: List[
            SourceProvider
        ] = []
        self.user_content_providers: List[
            UserContentSourceProvider
        ] = []
        self.ui_extensions: List[UIExtension] = []

    async def load_all(self):
        """Discover and load all plugins"""
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return

        for plugin_dir in self.plugins_dir.iterdir():
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue

            manifest_path = plugin_dir / "manifest.json"
            if not manifest_path.exists():
                logger.warning(f"No manifest.json in {plugin_dir.name}")
                continue

            try:
                await self._load_plugin(plugin_dir, manifest_path)
            except Exception as e:
                logger.error(
                    f"Failed to load plugin {plugin_dir.name}: {e}", exc_info=True
                )

    async def _load_plugin(self, plugin_dir: Path, manifest_path: Path):
        """Load a single plugin"""
        with open(manifest_path) as f:
            manifest = json.load(f)

        plugin_id = manifest.get("id")
        if not plugin_id:
            logger.error(
                f"Plugin in {plugin_dir.name} is missing 'id' in manifest.json"
            )
            return

        if plugin_id in self.plugins:
            logger.error(
                f"Duplicate plugin ID detected: {plugin_id}. Skipping folder {plugin_dir.name}"
            )
            return

        display_name = manifest.get("name", plugin_id)

        entry_point = manifest.get("entry_point", "plugin.py")

        module_path = plugin_dir / entry_point
        if not module_path.exists():
            raise FileNotFoundError(f"Entry point not found: {entry_point}")

        spec = importlib.util.spec_from_file_location(
            f"plugins.{plugin_dir.name}", module_path
        )
        module = importlib.util.module_from_spec(spec)
        backend_package = __package__.rsplit(".", 1)[0]

        def plugin_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and (name == "backend" or name.startswith("backend.")):
                target = backend_package + name[len("backend"):]
                imported = importlib.import_module(target)
                return imported if fromlist else importlib.import_module(backend_package)
            return builtins.__import__(name, globals, locals, fromlist, level)

        module.__dict__["__builtins__"] = {
            **vars(builtins), "__import__": plugin_import
        }
        spec.loader.exec_module(module)

        if not hasattr(module, "Plugin"):
            raise AttributeError(f"No 'Plugin' class found in {entry_point}")

        from .interfaces import PluginManifest

        manifest_obj = PluginManifest(**manifest)
        plugin_instance = module.Plugin(manifest=manifest_obj, path=str(plugin_dir))

        safe_plugin = SafePluginWrapper(plugin_instance, manifest)

        self.plugins[plugin_id] = safe_plugin

        if isinstance(plugin_instance, CatalogueProvider):
            self.catalogue_providers.append(safe_plugin)
        if isinstance(plugin_instance, SourceProvider):
            self.source_providers.append(safe_plugin)
        if isinstance(plugin_instance, UserContentSourceProvider):
            self.user_content_providers.append(safe_plugin)
        if isinstance(plugin_instance, UIExtension):
            self.ui_extensions.append(safe_plugin)

        plugin_instance.load_config()

        await safe_plugin.on_load()

        logger.info(
            f'Loaded plugin: {plugin_id} ("{display_name}") v{manifest.get("version", "0.0.0")}'
        )


    async def get_catalogue_items(self, catalogue_id: str, page: int = 1) -> List[dict]:
        """Get items from a specific catalogue (errors handled automatically)"""
        for provider in self.catalogue_providers:
            items = await provider.get_items(catalogue_id, page)
            if items:
                return items
        return []

    async def get_all_streams(
        self, imdb_id: str, media_type: str, season: int = None, episode: int = None
    ) -> Dict[str, List[dict]]:
        """Get streams from all TMDB source providers (errors handled automatically)"""
        all_streams = {}
        for provider in self.source_providers:
            streams = await provider.get_streams(imdb_id, media_type, season, episode)
            if streams:
                plugin_name = provider.manifest.get("name") or provider.manifest.get(
                    "id"
                )
                all_streams[plugin_name] = [
                    s if isinstance(s, dict) else s.model_dump() for s in streams
                ]
        return all_streams

    async def get_streams_for_video(
        self,
        video_uuid: str,
        title: str,
        id_source: Optional[str] = None,
        external_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[dict]]:
        """Get streams for user video from all user content providers"""
        all_streams = {}
        for provider in self.user_content_providers:
            supported = getattr(provider._plugin, "supported_sources", None)
            if supported is not None and id_source not in supported:
                continue

            streams = await provider.get_streams_for_video(
                video_uuid, title, id_source, external_id, metadata
            )
            if streams:
                plugin_name = provider.manifest.get("name") or provider.manifest.get(
                    "id"
                )
                all_streams[plugin_name] = [
                    s if isinstance(s, dict) else s.model_dump() for s in streams
                ]
        return all_streams

    async def get_streams_for_video_series(
        self,
        series_uuid: str,
        title: str,
        season: int,
        episode: int,
        id_source: Optional[str] = None,
        external_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, List[dict]]:
        """Get streams for user video series episode from all user content providers"""
        all_streams = {}
        for provider in self.user_content_providers:
            supported = getattr(provider._plugin, "supported_sources", None)
            if supported is not None and id_source not in supported:
                continue

            streams = await provider.get_streams_for_video_series(
                series_uuid, title, season, episode, id_source, external_id, metadata
            )
            if streams:
                plugin_name = provider.manifest.get("name") or provider.manifest.get(
                    "id"
                )
                all_streams[plugin_name] = [
                    s if isinstance(s, dict) else s.model_dump() for s in streams
                ]
        return all_streams

    async def unload_plugin(self, plugin_id: str):
        """Unload and disable a plugin"""
        plugin = self.plugins.get(plugin_id)
        if plugin:
            await plugin.on_unload()
            self.catalogue_providers = [
                p for p in self.catalogue_providers if p.manifest.id != plugin_id
            ]
            self.source_providers = [
                p for p in self.source_providers if p.manifest.id != plugin_id
            ]
            self.user_content_providers = [
                p for p in self.user_content_providers if p.manifest.id != plugin_id
            ]
            self.ui_extensions = [
                p for p in self.ui_extensions if p.manifest.id != plugin_id
            ]

            del self.plugins[plugin_id]
            logger.info(f"Unloaded plugin: {plugin_id}")

    async def delete_plugin(self, plugin_id: str, delete_data: bool = False):
        """Uninstall a plugin and optionally delete its data folder"""
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            return

        plugin_dir = plugin.path
        await self.unload_plugin(plugin_id)

        if plugin_dir.exists():
            if delete_data:
                import shutil

                shutil.rmtree(plugin_dir)
                logger.info(f"Deleted plugin and data: {plugin_dir}")
            else:
                (plugin_dir / "plugin.py").unlink(missing_ok=True)
                (plugin_dir / "manifest.json").unlink(missing_ok=True)
                logger.info(f"Deleted plugin code but kept data in: {plugin_dir}")

    def get_plugin_ui(self, plugin_id: str) -> Optional[str]:
        """Fetch the config.html content for a plugin"""
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            logger.error(f"UI requested for unknown plugin: {plugin_id}")
            return None

        if not plugin.has_ui:
            logger.error(f"UI requested for plugin without UI: {plugin_id}")
            return None

        ui_path = plugin.path / "config.html"
        logger.info(f"Loading UI from {ui_path}")
        try:
            return ui_path.read_text()
        except Exception as e:
            logger.error(f"Failed to read UI for {plugin_id}: {e}")
            return None

    def get_plugin_config(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Fetch the current config for a plugin"""
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            return None
        return plugin.config

    async def save_plugin_config(self, plugin_id: str, config: Dict[str, Any]):
        """Save new config for a plugin and update it"""
        plugin = self.plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin {plugin_id} not found")

        plugin.config.update(config)

        save_coro = plugin.save_config()
        if hasattr(save_coro, "__await__"):
            await save_coro

        return plugin.config


plugin_manager = PluginManager()
