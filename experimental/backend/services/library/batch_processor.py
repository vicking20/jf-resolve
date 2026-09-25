"""Batch processor with rate limiting for large library imports"""

import asyncio
import time
from typing import Callable, List, Optional

from ..log_service import log_service


class AsyncRateLimiter:
    """
    Simple async rate limiter.

    Limits number of operations per time window.
    """

    def __init__(self, max_calls: int, time_window: float):
        """
        Args:
            max_calls: Maximum number of calls allowed
            time_window: Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
        self._lock = asyncio.Lock()

    async def __aenter__(self):
        async with self._lock:
            now = time.time()

            # Remove old calls outside the window
            self.calls = [
                call_time
                for call_time in self.calls
                if now - call_time < self.time_window
            ]

            # Wait if we're at the limit
            if len(self.calls) >= self.max_calls:
                sleep_time = self.time_window - (now - self.calls[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    # Recalculate after sleep
                    now = time.time()
                    self.calls = [
                        call_time
                        for call_time in self.calls
                        if now - call_time < self.time_window
                    ]

            # Record this call
            self.calls.append(time.time())

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class BatchProcessor:
    """
    Process files in batches with rate limiting and backpressure control.

    Handles:
    - Concurrent file I/O limiting
    - TMDB API rate limiting
    - Database commit batching
    - Progress reporting
    """

    def __init__(
        self,
        max_concurrent: int = 5,
        batch_size: int = 50,
        delay_between_batches: float = 0.5,
        tmdb_rate_limit: int = 40,  # TMDB allows 40 requests per 10 seconds
        tmdb_time_window: float = 10.0,
    ):
        """
        Args:
            max_concurrent: Maximum parallel file processing operations
            batch_size: Number of items to process before committing to DB
            delay_between_batches: Delay in seconds between batches (breathing room)
            tmdb_rate_limit: Maximum TMDB API calls allowed
            tmdb_time_window: Time window for TMDB rate limit (seconds)
        """
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.delay_between_batches = delay_between_batches

        # Semaphore for limiting concurrent file operations
        self.semaphore = asyncio.Semaphore(max_concurrent)

        # Rate limiter for TMDB API calls
        self.tmdb_limiter = AsyncRateLimiter(tmdb_rate_limit, tmdb_time_window)

    def chunk_list(self, items: List, chunk_size: int) -> List[List]:
        """Split a list into chunks"""
        return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    async def process_files(
        self,
        files: List[str],
        processor_func: Callable,
        commit_func: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None,
    ):
        """
        Process files in batches with rate limiting.

        Args:
            files: List of file paths to process
            processor_func: Async function to process each file
                           Should accept (file_path, batch_processor) and return result
            commit_func: Optional async function to call after each batch
            progress_callback: Optional function to report progress
                              Called with (processed_count, total_count, new_items, updated_items)
        """
        total = len(files)
        processed = 0
        new_items = 0
        updated_items = 0

        log_service.info(
            f"Starting batch processing of {total} files "
            f"(batches of {self.batch_size}, max {self.max_concurrent} concurrent)"
        )

        batches = self.chunk_list(files, self.batch_size)

        for batch_idx, batch in enumerate(batches, 1):
            batch_start = time.time()

            # Process batch with concurrency limit
            tasks = []
            for file_path in batch:
                task = self._process_with_semaphore(file_path, processor_func)
                tasks.append(task)

            # Wait for all tasks in this batch
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Count results
            for result in results:
                if isinstance(result, Exception):
                    log_service.error(f"Processing error: {result}")
                elif isinstance(result, dict):
                    if result.get("created"):
                        new_items += 1
                    elif result.get("updated"):
                        updated_items += 1

            processed += len(batch)

            # Commit after batch if commit function provided
            if commit_func:
                await commit_func()

            # Report progress
            if progress_callback:
                await progress_callback(processed, total, new_items, updated_items)

            batch_duration = time.time() - batch_start
            log_service.info(
                f"Batch {batch_idx}/{len(batches)} completed: "
                f"{len(batch)} files in {batch_duration:.2f}s "
                f"(~{len(batch) / batch_duration:.1f} files/s)"
            )

            # Breathing room between batches
            if batch_idx < len(batches):
                await asyncio.sleep(self.delay_between_batches)

        log_service.info(
            f"Batch processing complete: {processed} files, "
            f"{new_items} new, {updated_items} updated"
        )

        return {
            "total": total,
            "processed": processed,
            "new_items": new_items,
            "updated_items": updated_items,
        }

    async def _process_with_semaphore(self, file_path: str, processor_func: Callable):
        """Process a single file with semaphore limit"""
        async with self.semaphore:
            try:
                return await processor_func(file_path, self)
            except Exception as e:
                log_service.error(f"Error processing {file_path}: {e}")
                return e

    async def with_tmdb_limit(self, coro):
        """
        Execute a coroutine with TMDB rate limiting.

        Usage:
            result = await batch_processor.with_tmdb_limit(
                tmdb.search_movies(title)
            )
        """
        async with self.tmdb_limiter:
            return await coro

    def get_adaptive_settings(self, file_count: int, has_tmdb: bool) -> dict:
        """
        Get adaptive batch processor settings based on library size.

        Args:
            file_count: Total number of files
            has_tmdb: Whether TMDB matching is needed

        Returns:
            Dict with recommended settings
        """
        if file_count < 100:
            # Small library - process quickly
            return {
                "max_concurrent": 10,
                "batch_size": 50,
                "delay_between_batches": 0.1,
            }
        elif file_count < 1000:
            # Medium library
            return {
                "max_concurrent": 8 if has_tmdb else 15,
                "batch_size": 50,
                "delay_between_batches": 0.3,
            }
        else:
            # Large library - more conservative
            return {
                "max_concurrent": 5 if has_tmdb else 20,
                "batch_size": 100,
                "delay_between_batches": 0.5,
            }
