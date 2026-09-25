"""Background scheduler for automated tasks"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..models.media import LibraryScanJob
from ..models.media_item import MediaItem, MediaType
from ..models.media_external_id import MediaExternalId
from .library_service import LibraryService
from .log_service import log_service
from .metadata_service import metadata_service
from .populate_service import PopulateService
from .tmdb_service import TMDBService
from .catalogue_service import CatalogueService
from .settings_manager import SettingsManager


class SchedulerService:
    """Manages scheduled background tasks"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False

    async def start(self):
        """Start the scheduler"""
        if self.is_running:
            return

        log_service.info("Starting background scheduler")
        self.scheduler.start()
        self.is_running = True

        # Schedule initial job configuration check
        await self.configure_jobs()

        log_service.info("Background scheduler started successfully")

    async def stop(self):
        """Stop the scheduler"""
        if not self.is_running:
            return

        log_service.info("Stopping background scheduler")
        try:
            # Shutdown scheduler in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self.scheduler.shutdown, False), timeout=2.0
            )
        except asyncio.TimeoutError:
            log_service.error("Scheduler shutdown timed out, forcing stop")
        except Exception as e:
            log_service.error(f"Error stopping scheduler: {e}")
        finally:
            self.is_running = False
            log_service.info("Background scheduler stopped")

    async def configure_jobs(self):
        """Configure scheduled jobs based on current settings"""
        try:
            async with AsyncSessionLocal() as db:
                settings = SettingsManager(db)
                await settings.load_cache()


                # Configure series update job
                await self._configure_series_update_job(settings)

                # Configure cache cleanup job
                await self._configure_cache_cleanup_job(settings)

                # Configure job cleanup job
                await self._configure_job_cleanup_job()

                # Configure temporary media cleanup job
                await self._configure_temporary_media_cleanup_job()

                # Configure Catalogue Sync job
                await self._configure_catalogue_sync_job(settings)

                # Run initial sync if no catalogues have items
                await self.trigger_initial_sync_if_needed()
        except Exception as e:
            # On first run, database might not exist yet - just skip configuration
            log_service.info(
                f"Skipping scheduler configuration (database not ready): {e}"
            )


    async def _configure_series_update_job(self, settings: SettingsManager):
        """Configure or remove series update job based on settings"""
        job_id = "series_update"

        # Remove existing job if present
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Check if enabled
        enabled = await settings.get("series_update_enabled", False)
        if not enabled:
            log_service.info("Series update is disabled")
            return

        # Get frequency
        frequency = await settings.get("series_update_frequency", "daily")
        trigger = self._get_cron_trigger(frequency)

        if trigger:
            self.scheduler.add_job(
                self._run_series_update,
                trigger=trigger,
                id=job_id,
                name="Update TV series",
                replace_existing=True,
            )
            log_service.info(f"Series update scheduled: {frequency}")
        else:
            log_service.error(f"Invalid series update frequency: {frequency}")

    async def _configure_catalogue_sync_job(self, settings: SettingsManager):
        """Configure or remove catalogue sync job (Runs every 24 hours)"""
        job_id = "catalogue_sync"

        # Remove existing job if present
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Runs daily at 2 AM
        trigger = CronTrigger(hour=2, minute=0)

        self.scheduler.add_job(
            self._run_catalogue_sync,
            trigger=trigger,
            id=job_id,
            name="Sync Media Catalogues",
            replace_existing=True,
        )
        log_service.info("Catalogue sync scheduled: daily at 2 AM")

    async def trigger_initial_sync_if_needed(self):
        """Trigger an immediate sync if the database is fresh/empty"""
        async with AsyncSessionLocal() as db:
            from ..models.catalogue import CatalogueItem
            from sqlalchemy import func
            result = await db.execute(select(func.max(CatalogueItem.added_at)))
            latest = result.scalar_one_or_none()
            if latest is not None and latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            if latest is None or latest < datetime.now(timezone.utc) - timedelta(hours=24):
                log_service.info("Catalogues are missing or stale, triggering catalogue sync...")
                # Run in background to not block scheduler startup
                asyncio.create_task(self._run_catalogue_sync())

    def _get_cron_trigger(self, frequency: str) -> Optional[CronTrigger]:
        """Convert frequency string to cron trigger"""
        # All jobs run at 3 AM to avoid peak usage times
        hour = 3
        minute = 0

        if frequency == "daily":
            # Every day at 3 AM
            return CronTrigger(hour=hour, minute=minute)
        elif frequency == "3days":
            # Every 3 days at 3 AM (days divisible by 3)
            return CronTrigger(day="*/3", hour=hour, minute=minute)
        elif frequency == "weekly":
            # Every Monday at 3 AM
            return CronTrigger(day_of_week="mon", hour=hour, minute=minute)
        elif frequency == "monthly":
            # First day of every month at 3 AM
            return CronTrigger(day=1, hour=hour, minute=minute)
        else:
            return None


    async def _run_series_update(self):
        """Execute series update task"""
        log_service.info("Running scheduled series update")

        async with AsyncSessionLocal() as db:
            try:
                settings = SettingsManager(db)
                await settings.load_cache()

                tmdb_key = await settings.get("tmdb_api_key")
                if not tmdb_key:
                    log_service.error(
                        "Series update skipped: TMDB API key not configured"
                    )
                    return

                tmdb = TMDBService(tmdb_key)
                try:
                    library = LibraryService(db, tmdb, settings)
                    populate_service = PopulateService(db, tmdb, library, settings)

                    result = await populate_service.run_series_update()
                    log_service.info(
                        f"Series update completed: {result.get('message')}"
                    )
                finally:
                    await tmdb.close()

            except Exception as e:
                log_service.error(f"Series update failed: {e}")

    async def _run_catalogue_sync(self):
        """Execute catalogue sync task"""
        log_service.info("Running media catalogue sync")

        async with AsyncSessionLocal() as db:
            try:
                cat_service = CatalogueService(db)
                await cat_service.initialize_default_catalogues()
                success = await cat_service.sync_all_catalogues()
                if success:
                    log_service.info("Media catalogue sync completed successfully")
            except Exception as e:
                log_service.error(f"Media catalogue sync failed: {e}")

    async def _configure_cache_cleanup_job(self, settings: SettingsManager):
        """Configure or remove cache cleanup job"""
        job_id = "cache_cleanup"

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Get days setting (0 = disabled)
        cleanup_days = await settings.get("cache_cleanup_days", 30)
        try:
            cleanup_days = int(cleanup_days)
        except:
            cleanup_days = 30

        if cleanup_days <= 0:
            log_service.info("Cache cleanup is disabled")
            return

        # Run daily at 4 AM to check for expired items
        trigger = CronTrigger(hour=4, minute=0)

        self.scheduler.add_job(
            self._run_cache_cleanup,
            trigger=trigger,
            id=job_id,
            name="Clean expired cache",
            replace_existing=True,
            args=[cleanup_days],
        )
        log_service.info(
            f"Cache cleanup scheduled: runs daily, expires items > {cleanup_days} days"
        )

    async def _run_cache_cleanup(self, days: int):
        """Execute cache cleanup task, protecting library items"""
        log_service.info(f"Running scheduled cache cleanup (items > {days} days)")
        try:
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                # Collect protected IDs (Any TMDB ID in our database should be protected)
                protected_ids = set()
                result = await db.execute(
                    select(MediaExternalId.external_id)
                    .where(MediaExternalId.provider == "tmdb")
                )
                protected_ids.update([int(tid) for tid in result.scalars().all() if tid.isdigit()])

            count = await metadata_service.clear_expired_cache(
                max_age_days=days, 
                protected_ids=protected_ids
            )
            log_service.info(f"Cache cleanup completed: removed {count} expired items")
        except Exception as e:
            log_service.error(f"Cache cleanup failed: {e}")

    async def _configure_job_cleanup_job(self):
        """Configure job cleanup job to run daily"""
        job_id = "job_cleanup"

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Run daily at 5 AM
        trigger = CronTrigger(hour=5, minute=0)

        self.scheduler.add_job(
            self._run_job_cleanup,
            trigger=trigger,
            id=job_id,
            name="Clean old scan jobs",
            replace_existing=True,
        )
        log_service.info(
            "Job cleanup scheduled: runs daily, removes jobs older than 30 days"
        )

    async def _run_job_cleanup(self):
        """Execute job cleanup task"""
        from datetime import timedelta

        log_service.info("Running scheduled job cleanup (jobs > 30 days)")
        try:
            cutoff = datetime.utcnow() - timedelta(days=30)
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    delete(LibraryScanJob).where(
                        LibraryScanJob.created_at < cutoff,
                        LibraryScanJob.status.in_(["completed", "failed"]),
                    )
                )
                count = result.rowcount
                await db.commit()
            log_service.info(f"Job cleanup completed: removed {count} old jobs")
        except Exception as e:
            log_service.error(f"Job cleanup failed: {e}")

    async def _configure_temporary_media_cleanup_job(self):
        """Configure cleanup for temporary media items"""
        job_id = "temporary_media_cleanup"

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Run daily at 3 AM (before cache cleanup)
        from apscheduler.triggers.cron import CronTrigger

        trigger = CronTrigger(hour=3, minute=0)

        self.scheduler.add_job(
            self._run_temporary_media_cleanup,
            trigger=trigger,
            id=job_id,
            name="Clean temporary media",
            replace_existing=True,
        )
        log_service.info(
            "Temporary media cleanup scheduled: runs daily at 3 AM, removes items > 30 days"
        )

    async def _run_temporary_media_cleanup(self):
        """Execute temporary media cleanup task"""
        from datetime import datetime, timedelta, timezone, timedelta, timezone

        from sqlalchemy import and_, delete, select

        from ..database import get_db
        from ..models.media_item import MediaItem
        from ..models.playback import PlaybackSession

        log_service.info("Running scheduled temporary media cleanup (items > 30 days)")

        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)

            async with get_db() as db:
                total_removed = 0

                # Clean temporary/transient MediaItems
                # (Items that were created but never persisted to library)
                result = await db.execute(
                    select(MediaItem).where(
                        and_(MediaItem.is_persisted == False, MediaItem.created_at < cutoff_date)
                    )
                )
                temp_items = result.scalars().all()
                for item in temp_items:
                    # Delete associated playback sessions
                    await db.execute(
                        delete(PlaybackSession).where(
                            PlaybackSession.media_uuid == item.uuid
                        )
                    )
                    await db.delete(item)
                    total_removed += 1

                await db.commit()

                log_service.info(
                    f"Temporary media cleanup completed: removed {total_removed} expired items"
                )

        except Exception as e:
            log_service.error(f"Temporary media cleanup failed: {e}")


# Global scheduler instance
scheduler_service = SchedulerService()
