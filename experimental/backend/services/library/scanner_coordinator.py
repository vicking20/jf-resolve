"""Scanner coordinator - unified interface for library scanning"""

from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.library_path import LibraryPath
from ...models.media import LibraryScanJob
from ..log_service import log_service
from ..settings_manager import SettingsManager
from ..tmdb_service import TMDBService
from .active_scanner import ActiveScanner
from .batch_processor import BatchProcessor
from .file_watcher import get_file_watcher
from .jit_scanner import JITScanner
from .scanner_mode_manager import ScannerModeManager


class ScannerCoordinator:
    """
    Coordinates library scanning across different modes.

    Provides a unified interface for:
    - Running scans (active or JIT)
    - Managing file watchers
    - Switching between scanner modes
    """

    def __init__(
        self,
        db: AsyncSession,
        tmdb: Optional[TMDBService] = None,
    ):
        self.db = db
        self.tmdb = tmdb
        self.mode_manager = ScannerModeManager(db)

        # Initialize scanners
        self.batch_processor = BatchProcessor()
        self.active_scanner = ActiveScanner(db, tmdb, self.batch_processor)
        self.jit_scanner = JITScanner(tmdb)

        # Get file watcher (global singleton)
        self.file_watcher = get_file_watcher()

    async def run_scan(
        self,
        library_path: LibraryPath,
        job: Optional[LibraryScanJob] = None,
        force_rescan: bool = False,
        smart: bool = True,
    ) -> Dict:
        """
        Run a library scan with appropriate scanner based on mode.

        Smart scanning automatically chooses between full and incremental scans.

        Args:
            library_path: The path to scan
            job: Optional scan job for progress tracking
            force_rescan: Force full rescan (overrides smart)
            smart: Use smart scan selection (incremental when possible)

        Returns:
            Scan statistics dict
        """
        mode = await self.mode_manager.get_effective_mode(library_path)

        log_service.info(
            f"Running scan of {library_path.path} in {mode} mode (smart={smart})"
        )

        # Only active mode and video paths get persisted
        if mode == "active" or library_path.media_type in ["video", "video_series"]:
            # Determine scan type (full vs incremental)
            use_incremental = False

            if smart and not force_rescan:
                # TODO: Re-enable when file_manifests table exists
                # For now, always use full scan
                log_service.info("Using full scan (manifest feature disabled)")

            # Run the appropriate scan
            if use_incremental:
                return await self.active_scanner.incremental_scan(library_path, job)
            else:
                return await self.active_scanner.full_scan(
                    library_path, job, force_rescan
                )
        else:
            # Lazy mode for movies/TV - no scanning needed
            # (will be done JIT when browsing)
            log_service.info(
                f"Lazy mode enabled for {library_path.media_type} - "
                "items will be scanned on-demand"
            )
            return {
                "mode": "lazy",
                "message": "JIT scanning enabled - browse library to see items",
            }


    async def search_library(
        self,
        library_path: LibraryPath,
        query: str,
        limit: int = 50,
    ):
        """
        Search within a library path.

        Mode-aware: uses JIT search for lazy mode, DB search for active.
        """
        mode = await self.mode_manager.get_effective_mode(library_path)

        if mode == "lazy" and library_path.media_type in ["movie", "tv"]:
            # JIT search
            return await self.jit_scanner.search_jit(library_path, query, limit)
        else:
            # DB search
            from ..library_service import LibraryService

            library_service = LibraryService(
                self.db, self.tmdb, SettingsManager(self.db)
            )
            return await library_service.search_library(library_path.id, query, limit)

    async def configure_file_watchers(self):
        """
        Configure file watchers based on current scanner mode and library paths.

        Should be called:
        - On app startup
        - When scanner mode changes
        - When library paths are added/removed
        """
        # Get all enabled library paths
        result = await self.db.execute(
            select(LibraryPath).where(
                LibraryPath.enabled == True,
                LibraryPath.watch_enabled == True,
            )
        )
        paths = result.scalars().all()

        # Filter paths that should be watched based on mode
        watched_paths = []
        for path in paths:
            mode = await self.mode_manager.get_effective_mode(path)
            media_type = self.mode_manager.get_media_type_string(path.media_type)

            if self.mode_manager.should_watch_path(mode, media_type):
                watched_paths.append(path)

        # Configure file watcher
        if self.file_watcher.enabled:
            # Set active scanner for the watcher
            self.file_watcher.active_scanner = self.active_scanner
            await self.file_watcher.reconfigure(watched_paths)

            log_service.info(
                f"File watcher configured: watching {len(watched_paths)} paths"
            )
        else:
            log_service.warning("File watcher not available (watchdog not installed)")

    async def set_scanner_mode(self, mode: str):
        """
        Change the global scanner mode and reconfigure watchers.

        Args:
            mode: "lazy" or "active"
        """
        await self.mode_manager.set_global_mode(mode)
        await self.configure_file_watchers()

        log_service.info(f"Scanner mode changed to: {mode}")

        # Trigger initial scans if switching to active mode
        if mode == "active":
            await self.trigger_initial_scans_if_needed()

    async def get_scanner_status(self) -> Dict:
        """
        Get current scanner status and configuration.

        Returns:
            Status dict with mode, watched paths, cache stats, etc.
        """
        global_mode = await self.mode_manager.get_global_mode()

        # Get watched paths
        watched_path_ids = []
        if self.file_watcher.enabled:
            watched_path_ids = self.file_watcher.get_watched_paths()

        # Get JIT cache stats
        cache_stats = self.jit_scanner.get_cache_stats()

        # Get file watcher stats
        watcher_stats = (
            self.file_watcher.get_stats() if self.file_watcher.enabled else {}
        )

        # Get recent scan jobs for status
        from ...models.media import LibraryScanJob
        
        scan_jobs_result = await self.db.execute(
            select(LibraryScanJob)
            .order_by(LibraryScanJob.created_at.desc())
            .limit(5)
        )
        recent_scans = []
        for job in scan_jobs_result.scalars().all():
            recent_scans.append({
                "id": job.id,
                "library_path_id": job.library_path_id,
                "status": job.status,
                "created_at": job.created_at.isoformat() if job.created_at is not None else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at is not None else None,
            })

        return {
            "global_mode": global_mode,
            "mode_description": await self.mode_manager.get_mode_description(
                global_mode
            ),
            "file_watcher": watcher_stats,
            "watched_paths": watched_path_ids,
            "jit_cache": cache_stats,
            "recent_scans": recent_scans,
        }


    async def trigger_initial_scans_if_needed(self):
        """
        Trigger initial scans for active mode paths that haven't been scanned recently.
        
        This should be called:
        - On server startup
        - When switching to active mode
        """
        from sqlalchemy import select
        from ...models.library_path import LibraryPath
        
        # Get all enabled library paths
        result = await self.db.execute(
            select(LibraryPath).where(LibraryPath.enabled == True)
        )
        paths = result.scalars().all()
        
        for library_path in paths:
            mode = await self.mode_manager.get_effective_mode(library_path)
            
            # Only scan paths that should use active scanning
            if mode == "active":
                # TODO: Re-enable manifest check when file_manifests table is created
                # For now, always trigger initial scan for active paths
                log_service.info(f"Triggering initial active scan for {library_path.path}")
                try:
                    # Run scan in background to not block startup
                    import asyncio
                    asyncio.create_task(self.run_scan(library_path, force_rescan=True))
                except Exception as e:
                    log_service.error(f"Failed to trigger initial scan for {library_path.path}: {e}")


async def create_scanner_coordinator(
    db: AsyncSession,
    tmdb: Optional[TMDBService] = None,
) -> ScannerCoordinator:
    """
    Factory function to create and initialize a scanner coordinator.

    Args:
        db: Database session
        tmdb: Optional TMDB service instance

    Returns:
        Initialized ScannerCoordinator
    """
    coordinator = ScannerCoordinator(db, tmdb)

    # Configure file watchers on creation
    await coordinator.configure_file_watchers()

    return coordinator
