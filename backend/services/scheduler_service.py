"""Background scheduler for automated tasks"""

import asyncio
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from .library_service import LibraryService
from .log_service import log_service
from .populate_service import PopulateService
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService


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

                # Configure auto-populate job
                await self._configure_auto_populate_job(settings)

                # Configure series update job
                await self._configure_series_update_job(settings)
        except Exception as e:
            # On first run, database might not exist yet - just skip configuration
            log_service.info(
                f"Skipping scheduler configuration (database not ready): {e}"
            )

    async def _configure_auto_populate_job(self, settings: SettingsManager):
        """Configure or remove auto-populate job based on settings"""
        job_id = "auto_populate"

        # Remove existing job if present
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        # Check if enabled
        enabled = await settings.get("auto_populate_enabled", False)
        if not enabled:
            log_service.info("Auto-populate is disabled")
            return

        # Get frequency
        frequency = await settings.get("populate_frequency", "daily")
        trigger = self._get_cron_trigger(frequency)

        if trigger:
            self.scheduler.add_job(
                self._run_auto_populate,
                trigger=trigger,
                id=job_id,
                name="Auto-populate library",
                replace_existing=True,
            )
            log_service.info(f"Auto-populate scheduled: {frequency}")
        else:
            log_service.error(f"Invalid auto-populate frequency: {frequency}")

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

    async def _run_auto_populate(self):
        """Execute auto-populate task"""
        log_service.info("Running scheduled auto-populate")

        async with AsyncSessionLocal() as db:
            try:
                settings = SettingsManager(db)
                await settings.load_cache()

                tmdb_key = await settings.get("tmdb_api_key")
                if not tmdb_key:
                    log_service.error(
                        "Auto-populate skipped: TMDB API key not configured"
                    )
                    return

                tmdb = TMDBService(tmdb_key)
                try:
                    library = LibraryService(db, tmdb, settings)
                    populate_service = PopulateService(db, tmdb, library, settings)

                    result = await populate_service.run_auto_populate()
                    log_service.info(
                        f"Auto-populate completed: {result.get('message')}"
                    )
                finally:
                    await tmdb.close()

            except Exception as e:
                log_service.error(f"Auto-populate failed: {e}")

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


# Global scheduler instance
scheduler_service = SchedulerService()
