"""File watcher service - real-time monitoring of library paths"""

import asyncio
import os
import time
from typing import Dict, Optional

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

    class Observer:
        pass

    class FileSystemEventHandler:
        pass

    class FileSystemEvent:
        pass


from ...models.library_path import LibraryPath
from ..log_service import log_service
from .active_scanner import ActiveScanner
from .base_scanner import VIDEO_EXTENSIONS


class LibraryEventHandler(FileSystemEventHandler):
    """
    Watchdog event handler for a single library path.

    Filters events and queues them for debounced processing.
    """

    def __init__(self, library_path: LibraryPath, event_queue: asyncio.Queue):
        super().__init__()
        self.library_path = library_path
        self.event_queue = event_queue
        self.loop = asyncio.get_event_loop()

    def _is_video_file(self, path: str) -> bool:
        """Check if file is a video file"""
        ext = os.path.splitext(path)[1].lower()
        return ext in VIDEO_EXTENSIONS

    def _queue_event(self, event: FileSystemEvent, event_type: str):
        """Queue event for async processing"""
        if not event.is_directory and self._is_video_file(event.src_path):
            asyncio.run_coroutine_threadsafe(
                self.event_queue.put(
                    {
                        "type": event_type,
                        "path": event.src_path,
                        "library_path": self.library_path,
                        "timestamp": time.time(),
                    }
                ),
                self.loop,
            )

    def on_created(self, event):
        """Called when a file is created"""
        self._queue_event(event, "created")

    def on_deleted(self, event):
        """Called when a file is deleted"""
        self._queue_event(event, "deleted")

    def on_modified(self, event):
        """Called when a file is modified"""
        self._queue_event(event, "modified")

    def on_moved(self, event):
        """Called when a file is moved/renamed"""
        if hasattr(event, "src_path"):
            self._queue_event(event, "deleted")
        if hasattr(event, "dest_path") and self._is_video_file(event.dest_path):
            mock_event = type(
                "Event", (), {"is_directory": False, "src_path": event.dest_path}
            )()
            self._queue_event(mock_event, "created")


class LibraryFileWatcher:
    """
    Real-time file system watcher using watchdog.

    Monitors library paths and triggers incremental scans.

    Features:
    - Debounced event processing (waits for file operations to complete)
    - Automatic retry on errors
    - Graceful degradation if watchdog not available
    """

    def __init__(self, active_scanner: Optional[ActiveScanner] = None):
        if not WATCHDOG_AVAILABLE:
            log_service.warning(
                "Watchdog library not available - file watching disabled. "
                "Install with: pip install watchdog"
            )
            self.enabled = False
            return

        self.enabled = True
        self.observer = Observer()
        self.handlers: Dict[int, LibraryEventHandler] = {}
        self.watches: Dict[int, object] = {}
        self.event_queue = asyncio.Queue()
        self.debounce_delay = 2.0
        self.active_scanner = active_scanner
        self._processing_task = None
        self._running = False

    async def start(self):
        """Start the file watcher"""
        if not self.enabled:
            log_service.info("File watcher disabled (watchdog not available)")
            return

        if self._running:
            return

        log_service.info("Starting file watcher service")
        self.observer.start()
        self._running = True

        self._processing_task = asyncio.create_task(self._process_events())

        log_service.info("File watcher service started")

    async def stop(self):
        """Stop the file watcher"""
        if not self.enabled or not self._running:
            return

        log_service.info("Stopping file watcher service")

        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join(timeout=5.0)

        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass

        self._running = False
        log_service.info("File watcher service stopped")

    def watch_path(self, library_path: LibraryPath):
        """
        Add a library path to the watcher.

        Args:
            library_path: The library path to watch
        """
        if not self.enabled:
            return

        if library_path.id in self.handlers:
            log_service.debug(f"Path already watched: {library_path.path}")
            return

        if not os.path.exists(library_path.path):
            log_service.warning(
                f"Cannot watch path (does not exist): {library_path.path}"
            )
            return

        try:
            handler = LibraryEventHandler(library_path, self.event_queue)
            watch = self.observer.schedule(handler, library_path.path, recursive=True)

            self.handlers[library_path.id] = handler
            self.watches[library_path.id] = watch

            log_service.info(f"Now watching: {library_path.path}")
        except Exception as e:
            log_service.error(f"Failed to watch path {library_path.path}: {e}")

    def unwatch_path(self, library_path_id: int):
        """
        Remove a library path from the watcher.

        Args:
            library_path_id: The library path ID to stop watching
        """
        if not self.enabled:
            return

        if library_path_id not in self.handlers:
            return

        try:
            watch = self.watches.pop(library_path_id, None)
            if watch:
                self.observer.unschedule(watch)

            self.handlers.pop(library_path_id, None)
            log_service.info(f"Stopped watching library path {library_path_id}")
        except Exception as e:
            log_service.error(f"Error unwatching library path {library_path_id}: {e}")

    async def reconfigure(self, watched_paths: list):
        """
        Reconfigure watched paths based on scanner mode.

        Args:
            watched_paths: List of LibraryPath objects that should be watched
        """
        if not self.enabled:
            return

        current_ids = set(self.handlers.keys())
        new_ids = {p.id for p in watched_paths}

        to_remove = current_ids - new_ids
        for path_id in to_remove:
            self.unwatch_path(path_id)

        to_add = new_ids - current_ids
        for path in watched_paths:
            if path.id in to_add:
                self.watch_path(path)

    async def _process_events(self):
        """
        Process file events with debouncing.

        Collects events and waits for debounce delay before processing.
        """
        pending_events: Dict[str, dict] = {}

        while self._running:
            try:
                try:
                    event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                    pending_events[event["path"]] = event
                except asyncio.TimeoutError:
                    pass

                now = time.time()
                ready_paths = [
                    path
                    for path, evt in pending_events.items()
                    if now - evt["timestamp"] >= self.debounce_delay
                ]

                for path in ready_paths:
                    event = pending_events.pop(path)
                    await self._handle_file_change(event)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"Error processing file events: {e}")
                await asyncio.sleep(1.0)

    async def _handle_file_change(self, event: dict):
        """
        Handle a single file change event.

        Args:
            event: Event dict with type, path, library_path, timestamp
        """
        if not self.active_scanner:
            log_service.warning("No active scanner configured for file watcher")
            return

        event_type = event["type"]
        file_path = event["path"]
        library_path = event["library_path"]

        try:
            if event_type == "created":
                log_service.info(f"File created: {file_path}")
                await self.active_scanner.process_single_file(file_path, library_path)

            elif event_type == "deleted":
                log_service.info(f"File deleted: {file_path}")
                from .cleanup_service import CleanupService

                cleanup = CleanupService(self.active_scanner.db)
                await cleanup.cleanup_single_file(file_path)

            elif event_type == "modified":
                log_service.debug(f"File modified: {file_path}")

        except Exception as e:
            log_service.error(f"Error handling file change {file_path}: {e}")


    def get_watched_paths(self) -> list:
        """Get list of currently watched library path IDs"""
        return list(self.handlers.keys())

    def get_stats(self) -> dict:
        """Get file watcher statistics"""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "watched_paths": len(self.handlers),
            "pending_events": self.event_queue.qsize() if self.enabled else 0,
        }


_file_watcher_instance = None


def get_file_watcher() -> LibraryFileWatcher:
    """Get the global file watcher instance"""
    global _file_watcher_instance
    if _file_watcher_instance is None:
        _file_watcher_instance = LibraryFileWatcher()
    return _file_watcher_instance
