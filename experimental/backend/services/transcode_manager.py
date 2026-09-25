"""Transcode session manager with concurrency control"""

import asyncio
import re
import os
import signal
import time
from asyncio import Semaphore
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from .log_service import log_service


class TranscodeManager:
    """Manages concurrent transcoding sessions"""

    def __init__(self):
        self.active_transcodes: Dict[str, Dict] = {}
        self.semaphore: Optional[Semaphore] = None
        self.max_concurrent = 0  # 0 = unlimited
        self.queue_full_behavior = "wait"  # wait, direct_proxy, close_player
        self.watchdog_task = None

    async def set_max_concurrent(self, max_count: int):
        """
        Set max concurrent transcodes (0 = unlimited)

        Args:
            max_count: Maximum number of concurrent transcodes, 0 for unlimited
        """
        self.max_concurrent = max_count

        if max_count == 0:
            self.semaphore = None
            log_service.info("Transcode concurrency: UNLIMITED")
        else:
            self.semaphore = Semaphore(max_count)
            log_service.info(f"Transcode concurrency limit set to: {max_count}")

    def set_queue_full_behavior(self, behavior: str):
        """
        Set behavior when transcode queue is full

        Args:
            behavior: 'wait', 'direct_proxy', or 'close_player'
        """
        if behavior in ["wait", "direct_proxy", "close_player"]:
            self.queue_full_behavior = behavior
            log_service.info(f"Queue full behavior set to: {behavior}")
        else:
            log_service.info(
                f"Invalid queue full behavior: {behavior}, keeping: {self.queue_full_behavior}"
            )

    async def acquire_slot(self, stream_id: str, timeout: int = 30) -> Dict:
        """
        Acquire transcode slot - blocks if max concurrent reached

        Args:
            stream_id: Unique identifier for the stream
            timeout: Maximum seconds to wait for a slot

        Returns:
            Dict with 'acquired': bool, 'reason': str
        """
        try:
            if self.semaphore:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=timeout)

            self.active_transcodes[stream_id] = {
                "started_at": datetime.utcnow(),
                "status": "running",
                "process": None,
                "lock": asyncio.Lock(),
                "last_seen": time.monotonic(),
            }

            log_service.info(
                f"Transcode slot acquired for {stream_id} "
                f"(active: {self.get_active_count()}/{self.max_concurrent or 'unlimited'})"
            )
            return {"acquired": True, "reason": "ok"}

        except asyncio.TimeoutError:
            log_service.info(
                f"Transcode queue timeout for {stream_id} after {timeout}s"
            )
            return {
                "acquired": False,
                "reason": "timeout",
                "behavior": self.queue_full_behavior,
            }

    def release_slot(self, stream_id: str):
        """
        Release transcode slot

        Args:
            stream_id: Unique identifier for the stream
        """
        if stream_id not in self.active_transcodes:
            return
        if stream_id in self.active_transcodes:
            # Kill process if still running
            process = self.active_transcodes[stream_id].get("process")
            if process and process.returncode is None:
                try:
                    process.send_signal(signal.SIGTERM)
                    log_service.info(f"Terminated transcode process for {stream_id}")
                except Exception as e:
                    log_service.error(f"Failed to terminate process: {e}")

            del self.active_transcodes[stream_id]

        if self.semaphore:
            self.semaphore.release()

        log_service.info(
            f"Transcode slot released for {stream_id} "
            f"(active: {self.get_active_count()}/{self.max_concurrent or 'unlimited'})"
        )

    async def release_slot_async(self, stream_id: str, idle_before: Optional[float] = None):
        """
        Release transcode slot with proper cleanup - call from API endpoints

        Args:
            stream_id: Unique identifier for the stream
        """
        info = self.active_transcodes.get(stream_id)
        if info is None:
            return
        async with info["lock"]:
            if self.active_transcodes.get(stream_id) is not info:
                return
            if idle_before is not None and info.get("last_seen", 0) > idle_before:
                return
            await self.terminate_process(stream_id)
            self.cleanup_hls_files(stream_id)
            input_url = (info.get("stream_info") or {}).get("input_url")
            self.release_slot(stream_id)
            from .torrent_service import torrent_service
            active_inputs = [(entry.get("stream_info") or {}).get("input_url", "") for entry in self.active_transcodes.values()]
            await torrent_service.stop_unused(input_url, active_inputs)

    async def terminate_process(self, stream_id: str):
        """Wait for FFmpeg to exit before its output files can be removed."""
        info = self.active_transcodes.get(stream_id)
        if not info:
            return
        process = info.get("process")
        if process:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        info["process"] = None

    def set_stream_start_time(self, stream_id: str, start_time: float):
        """Set the start time offset for a stream (used for HLS seeking)"""
        if stream_id in self.active_transcodes:
            self.active_transcodes[stream_id]["start_time"] = start_time

    def get_stream_start_time(self, stream_id: str) -> float:
        """Get the start time offset for a stream. Defaults to 0."""
        if stream_id in self.active_transcodes:
            return self.active_transcodes[stream_id].get("start_time", 0.0)
        return 0.0

    def get_stream_info(self, stream_id: str) -> Optional[Dict]:
        """Retrieve stored stream initialization info for seek restarts"""
        info = self.active_transcodes.get(stream_id, {})
        return info.get("stream_info")

    def register_process(self, stream_id: str, process, duration: float = None, stream_info: dict = None, segment_duration: int = 4):
        """
        Register FFmpeg process for cleanup and store stream duration and info
        
        Args:
            stream_id: Unique identifier for the stream
            process: asyncio subprocess
            duration: Total duration of the media in seconds
            stream_info: Payload necessary to restart stream without fully re-resolving
            segment_duration: HLS segment duration in seconds (default 4)
        """
        if stream_id in self.active_transcodes:
            self.active_transcodes[stream_id]["process"] = process
            if duration is not None:
                self.active_transcodes[stream_id]["duration"] = duration
            if stream_info is not None:
                self.active_transcodes[stream_id]["stream_info"] = stream_info
            self.active_transcodes[stream_id]["segment_duration"] = segment_duration

    def get_active_count(self) -> int:
        """Get number of active transcodes"""
        return len(self.active_transcodes)

    def get_active_transcodes(self) -> Dict:
        """Get info about all active transcodes"""
        return {
            stream_id: {
                "started_at": info["started_at"].isoformat(),
                "status": info["status"],
                "duration": (datetime.utcnow() - info["started_at"]).total_seconds(),
            }
            for stream_id, info in self.active_transcodes.items()
        }

    def is_slot_available(self) -> bool:
        """Check if transcode slot is currently available"""
        if self.max_concurrent == 0:
            return True
        return self.get_active_count() < self.max_concurrent

    def touch_stream(self, stream_id: str) -> bool:
        info = self.active_transcodes.get(stream_id)
        if info is None:
            return False
        info["last_seen"] = time.monotonic()
        return True

    async def expire_idle_streams(self, timeout: float = 120):
        """Reap sessions whose browser heartbeat and media requests have stopped."""
        cutoff = time.monotonic() - timeout
        for stream_id, info in list(self.active_transcodes.items()):
            if info.get("last_seen", 0) <= cutoff:
                await self.release_slot_async(stream_id, idle_before=cutoff)

    def start_watchdog(self):
        if self.watchdog_task is not None:
            return
        async def watch():
            while True:
                await asyncio.sleep(15)
                try:
                    await self.expire_idle_streams()
                except Exception:
                    log_service.error("Failed to clean up idle transcodes")
        self.watchdog_task = asyncio.create_task(watch())

    async def stop_watchdog(self):
        if self.watchdog_task is None:
            return
        self.watchdog_task.cancel()
        try:
            await self.watchdog_task
        except asyncio.CancelledError:
            pass
        self.watchdog_task = None

    async def cleanup_all(self):
        """Cleanup all active transcodes (called on shutdown)"""
        log_service.info(f"Cleaning up {len(self.active_transcodes)} active transcodes")

        for stream_id in list(self.active_transcodes.keys()):
            await self.release_slot_async(stream_id)

        log_service.info("All transcodes cleaned up")

    @staticmethod
    def get_hls_output_dir(stream_id: str) -> Path:
        """
        Get HLS output directory for a stream

        Args:
            stream_id: Unique identifier for the stream

        Returns:
            Path to HLS output directory
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]+", stream_id):
            raise ValueError(f"Invalid stream id: {stream_id!r}")
        base_dir = Path("/tmp/jf-resolve-hls")
        output_dir = base_dir / stream_id
        return output_dir

    def get_segment_range(self, stream_id: str) -> Dict:
        """
        Get the time range of transcoded segments currently on disk.
        Only counts segments from the current transcode session.
        
        Returns:
            Dict with min_time, max_time, segment_count, is_complete
        """
        info = self.active_transcodes.get(stream_id, {})
        start_time = info.get("start_time", 0.0)
        playlist = self.get_hls_output_dir(stream_id) / "master.m3u8"
        durations = []
        complete = False
        try:
            lines = playlist.read_text().splitlines()
            complete = "#EXT-X-ENDLIST" in lines
            import re
            durations = [float(line.split(":", 1)[1].split(",", 1)[0])
                         for line in lines if line.startswith("#EXTINF:")]
        except (OSError, ValueError):
            pass
        max_time = start_time + sum(durations)
        if info.get("duration"):
            max_time = min(max_time, info["duration"])
        return {"min_time": start_time, "max_time": max_time,
                "segment_count": len(durations), "is_complete": complete}

    @staticmethod
    def cleanup_hls_files(stream_id: str):
        """
        Clean up HLS files for a stream

        Args:
            stream_id: Unique identifier for the stream
        """
        output_dir = TranscodeManager.get_hls_output_dir(stream_id)

        if output_dir.exists():
            try:
                import shutil

                shutil.rmtree(output_dir)
                log_service.info(f"Cleaned up HLS files for {stream_id}")
            except Exception as e:
                log_service.error(f"Failed to cleanup HLS files: {e}")

    @staticmethod
    async def wait_for_hls_ready(playlist_path: Path, timeout: int = 30, process=None) -> bool:
        """
        Wait for HLS playlist to be ready with at least one playable segment

        Args:
            playlist_path: Path to master.m3u8 file
            timeout: Maximum seconds to wait

        Returns:
            True if ready, False if timeout
        """
        import re

        start_time = asyncio.get_event_loop().time()
        output_dir = playlist_path.parent

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if playlist_path.exists():
                try:
                    content = playlist_path.read_text()

                    # Basic validation: playlist should have EXTINF entries
                    if "#EXTINF:" not in content:
                        log_service.stream(f"HLS playlist missing EXTINF entries: {playlist_path}")
                        await asyncio.sleep(0.5)
                        continue

                    # Find segment references in playlist
                    segments = re.findall(r"(segment_\d+\.ts)", content)

                    if segments:
                        # Check if at least the first segment file actually exists and has content
                        first_segment = output_dir / segments[0]
                        if first_segment.exists() and first_segment.stat().st_size > 1000:  # At least 1KB
                            # Additional validation: check if playlist has proper structure
                            lines = content.strip().split('\n')
                            if len(lines) >= 3 and lines[0] == "#EXTM3U":
                                log_service.info(
                                    f"HLS playlist ready: {playlist_path} "
                                    f"({len(segments)} segments, first: {first_segment.name})"
                                )
                                return True
                            else:
                                log_service.stream(f"HLS playlist malformed structure: {playlist_path}")
                        else:
                            log_service.stream(f"HLS segment too small or missing: {first_segment}")
                    else:
                        log_service.stream(f"HLS playlist has no segments: {playlist_path}")
                except Exception as e:
                    log_service.stream(f"HLS check error: {e}")

            if process is not None and process.returncode is not None:
                log_service.error(f"FFmpeg exited before producing a playable playlist ({process.returncode})")
                return False
            await asyncio.sleep(0.5)

        log_service.error(f"HLS playlist timeout after {timeout}s: {playlist_path}")
        return False


# Global transcode manager instance
transcode_manager = TranscodeManager()
