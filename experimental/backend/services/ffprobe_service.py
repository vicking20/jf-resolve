"""FFprobe service for media stream analysis"""

import asyncio
import json
from typing import Dict, List, Optional

from cachetools import TTLCache

from .log_service import log_service


class FFprobeService:
    """Service for probing media streams with FFprobe"""

    # In-memory cache for probe results (1 hour TTL, max 1000 items)
    _probe_cache = TTLCache(maxsize=1000, ttl=3600)

    @staticmethod
    async def probe_stream(stream_url: str, timeout: int = 10) -> Optional[Dict]:
        """
        Probe stream with FFprobe - results cached for 1 hour

        Returns: {
            'video_codec': 'h264',
            'audio_codec': 'aac',
            'audio_tracks': [...],
            'container': 'mp4',
            'duration': 7200,  # seconds
            'resolution': '1920x1080',
            'width': 1920,
            'height': 1080,
            'bitrate': 5000000,
            'hdr': False,
            'bit_depth': 8
        }
        """
        # Check cache first
        if stream_url in FFprobeService._probe_cache:
            log_service.info(f"FFprobe cache hit for {stream_url[:100]}")
            return FFprobeService._probe_cache[stream_url]

        log_service.info(f"Probing stream: {stream_url[:100]}...")

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            stream_url,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8") if stderr else "Unknown error"
                log_service.error(f"FFprobe failed for {stream_url}: {error_msg}")
                return None

            data = json.loads(stdout)
            result = FFprobeService._parse_probe_data(data)

            # Cache the result
            FFprobeService._probe_cache[stream_url] = result

            log_service.info(
                f"Probe successful: {result.get('video_codec', 'N/A')} / "
                f"{result.get('audio_codec', 'N/A')} / "
                f"{result.get('duration', 0):.1f}s / "
                f"{'HDR' if result.get('hdr') else 'SDR'}"
            )

            return result

        except asyncio.TimeoutError:
            log_service.error(f"FFprobe timeout for {stream_url}")
            return None
        except json.JSONDecodeError as e:
            log_service.error(f"FFprobe JSON decode error: {e}")
            return None
        except Exception as e:
            log_service.error(f"FFprobe error: {e}")
            return None

    @staticmethod
    def _parse_probe_data(data: Dict) -> Dict:
        """Extract relevant info from FFprobe output"""
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            None,
        )
        audio_streams = [
            s for s in data.get("streams", []) if s.get("codec_type") == "audio"
        ]

        # Detect HDR
        hdr = False
        bit_depth = 8

        if video_stream:
            # Check for HDR color transfer characteristics
            color_transfer = video_stream.get("color_transfer", "")
            hdr = color_transfer in [
                "smpte2084",  # HDR10
                "arib-std-b67",  # HLG (Hybrid Log-Gamma)
            ]

            # Get bit depth
            bit_depth = int(video_stream.get("bits_per_raw_sample", 8) or 8)

            # Additional HDR checks
            color_space = video_stream.get("color_space", "")
            if color_space in ["bt2020nc", "bt2020c"]:
                hdr = True

        # Parse audio tracks with metadata
        audio_tracks = []
        for audio_idx, stream in enumerate(audio_streams):
            audio_tracks.append(
                {
                    "index": audio_idx,  # Audio-relative index for FFmpeg -map 0:a:X
                    "stream_index": stream.get(
                        "index"
                    ),  # Global stream index for reference
                    "codec": stream.get("codec_name"),
                    "language": stream.get("tags", {}).get("language", "und"),
                    "title": stream.get("tags", {}).get("title", ""),
                    "channels": stream.get("channels", 2),
                    "sample_rate": stream.get("sample_rate"),
                    "bitrate": int(stream.get("bit_rate", 0))
                    if stream.get("bit_rate")
                    else None,
                }
            )

        format_info = data.get("format", {})
        container = format_info.get("format_name", "").split(",")[0]

        return {
            "video_codec": video_stream.get("codec_name") if video_stream else None,
            "audio_codec": audio_streams[0].get("codec_name")
            if audio_streams
            else None,
            "audio_tracks": audio_tracks,
            "subtitle_tracks": [
                {"index": index, "codec": stream.get("codec_name"),
                 "language": stream.get("tags", {}).get("language", "und"),
                 "title": stream.get("tags", {}).get("title", ""),
                 "supported": stream.get("codec_name") in {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}}
                for index, stream in enumerate(s for s in data.get("streams", []) if s.get("codec_type") == "subtitle")
            ],
            "container": container,
            "duration": float(format_info.get("duration", 0)),
            "resolution": (
                f"{video_stream.get('width', 0)}x{video_stream.get('height', 0)}"
                if video_stream
                else None
            ),
            "width": video_stream.get("width", 0) if video_stream else 0,
            "height": video_stream.get("height", 0) if video_stream else 0,
            "bitrate": int(format_info.get("bit_rate", 0)),
            "hdr": hdr,
            "bit_depth": bit_depth,
        }

    @staticmethod
    async def validate_stream(
        stream_url: str, media_type: str, min_duration_seconds: int = 120
    ) -> Dict:
        """
        Validate if stream is legitimate content or error/download page

        Returns: {
            'valid': True/False,
            'reason': 'duration_too_short' | 'probe_failed' | 'ok',
            'duration': 123.45
        }
        """
        probe_result = await FFprobeService.probe_stream(stream_url)

        if not probe_result:
            return {"valid": False, "reason": "probe_failed", "duration": 0}

        duration = probe_result.get("duration", 0)

        if duration < min_duration_seconds:
            log_service.warning(
                f"Stream rejected: duration {duration}s < minimum {min_duration_seconds}s"
            )
            return {
                "valid": False,
                "reason": "duration_too_short",
                "duration": duration,
            }

        return {"valid": True, "reason": "ok", "duration": duration}

    @staticmethod
    def clear_cache():
        """Clear the probe cache (useful for testing or manual refresh)"""
        FFprobeService._probe_cache.clear()
        log_service.info("FFprobe cache cleared")

    @staticmethod
    def get_cache_info() -> Dict:
        """Get cache statistics"""
        return {
            "size": len(FFprobeService._probe_cache),
            "maxsize": FFprobeService._probe_cache.maxsize,
            "ttl": FFprobeService._probe_cache.ttl,
        }
