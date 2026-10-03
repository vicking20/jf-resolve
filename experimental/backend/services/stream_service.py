import mimetypes
import os
from pathlib import Path
from typing import Dict, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from .log_service import log_service


class StreamService:
    def __init__(self):
        # Use a browser-like User-Agent to avoid being blocked
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        self.client = httpx.AsyncClient(
            timeout=90.0,  # Increased for slow streams
            follow_redirects=True,
            headers=headers,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    async def get_stream_response(
        self,
        source_path: str,
        range_header: Optional[str] = None,
        user_agent: Optional[str] = None,
        transcode_settings: Optional[Dict] = None,
        force_transcode: bool = False,
        audio_track: Optional[int] = None,
    ):
        """
        Generic stream handler with transcoding support

        Args:
            source_path: Path to source (local file, STRM, or URL)
            range_header: HTTP Range header
            user_agent: User-Agent for client detection
            transcode_settings: Transcoding settings from DB
            force_transcode: Force transcoding even if compatible
            audio_track: Specific audio track index
        """
        # Import here to avoid circular imports
        from .client_detection_service import ClientDetectionService
        from .ffprobe_service import FFprobeService
        from .transcode_manager import transcode_manager
        from .transcoding_service import TranscodingService

        from .torrent_service import is_torrent
        torrent_source = is_torrent(source_path)
        if torrent_source:
            force_transcode = True
            transcode_settings = dict(transcode_settings or {}, transcode_enabled=True)

        # 1. Resolve source to actual URL
        stream_url = await self._resolve_source(source_path)

        # 2. Check if transcoding is enabled
        if not transcode_settings or not transcode_settings.get(
            "transcode_enabled", True
        ):
            # Direct proxy - no transcoding
            # Note: We don't have probe_info here, so we can't validate safety
            log_service.info("Transcoding disabled - direct streaming without validation")
            return await self._handle_direct_stream(
                stream_url, range_header, source_path, None
            )

        # 3. Probe stream to get codec info
        probe_info = await FFprobeService.probe_stream(stream_url)

        if not probe_info:
            # Probe failed - try to guess from URL extension if it's MKV
            # MKV containers are never browser-compatible, so we should transcode
            url_lower = stream_url.lower()
            if ".mkv" in url_lower or "matroska" in url_lower:
                log_service.info(
                    f"Probe failed but detected MKV from URL, forcing transcode"
                )
                # Create minimal probe_info to trigger transcoding
                probe_info = {
                    "video_codec": "unknown",
                    "audio_codec": "unknown",
                    "container": "matroska",
                    "hdr": False,
                }
            else:
                # Can't determine format - fall back to direct proxy
                log_service.info(
                    f"Probe failed for {stream_url[:100]}, using direct proxy"
                )
                return await self._handle_direct_stream(
                    stream_url, range_header, source_path, None
                )

        # 4. Detect client capabilities
        client_profile = ClientDetectionService.detect_client_capabilities(
            user_agent or ""
        )

        # 5. Check if transcoding needed
        needs_transcode = ClientDetectionService.needs_transcoding(
            client_profile, probe_info
        )

        if force_transcode:
            needs_transcode = True
            log_service.info("Forced transcoding enabled")

        # 6. Direct streaming if compatible
        if not needs_transcode:
            # Additional validation for direct streaming safety
            can_direct = ClientDetectionService.can_direct_stream(probe_info)

            if can_direct:
                reason = ClientDetectionService.get_transcode_reason(
                    client_profile, probe_info
                )
                log_service.info(f"Stream compatible ({reason}) - direct proxy")
                return await self._handle_direct_stream(
                    stream_url, range_header, source_path, probe_info
                )
            else:
                # Client detection said compatible but direct streaming is not safe
                # Force transcoding to ensure browser compatibility
                log_service.info("Stream needs transcoding for browser compatibility")
                needs_transcode = True

        # 7. Transcoding required
        reason = ClientDetectionService.get_transcode_reason(client_profile, probe_info)
        log_service.info(f"Transcoding required: {reason}")

        # Generate unique stream ID
        import uuid

        # Each playback owns its files and process; cleanup must never affect
        # another request for the same URL.
        stream_id = uuid.uuid4().hex

        # Acquire transcode slot
        slot_result = await transcode_manager.acquire_slot(stream_id, timeout=30)

        if not slot_result["acquired"]:
            # Queue full - handle based on settings
            behavior = slot_result.get("behavior", "wait")

            if behavior == "direct_proxy" and not torrent_source:
                log_service.info("Transcode queue full, falling back to direct proxy")
                return await self._handle_direct_stream(
                    stream_url, range_header, source_path, probe_info
                )
            elif behavior == "close_player":
                raise HTTPException(
                    status_code=503,
                    detail="Transcoding queue full. Please try again later.",
                    headers={"Retry-After": "30"},
                )
            raise HTTPException(status_code=503, detail="No transcoding slot is available. Please try again.")

        try:
            # Start transcoding
            transcode_result = await TranscodingService.start_transcode(
                stream_id=stream_id,
                input_url=stream_url,
                transcode_settings=transcode_settings,
                client_profile=client_profile,
                probe_info=probe_info,
                audio_track_index=audio_track,
            )

            # Return JSON with HLS URL - frontend will handle loading it
            hls_url = f"/api/stream/hls/{stream_id}/master.m3u8"
            log_service.info(f"Transcoding ready, HLS playlist: {hls_url}")
            return JSONResponse(
                content={
                    "type": "hls",
                    "url": hls_url,
                    "stream_id": stream_id,
                    "probe_info": probe_info,
                },
                headers={"X-Stream-Type": "hls"},
            )

        except Exception as e:
            log_service.error(f"Transcoding failed: {e}")
            await transcode_manager.release_slot_async(stream_id)
            if torrent_source:
                raise HTTPException(status_code=502, detail="Unable to prepare torrent playback. Check that peers are available.") from e
            # Fall back to direct proxy
            return await self._handle_direct_stream(
                stream_url, range_header, source_path, probe_info
            )

    async def _resolve_source(self, source_path: str) -> str:
        """Resolve source path to actual stream URL"""
        # 1. Resolve STRM files to their underlying URL
        if source_path.endswith(".strm"):
            try:
                import asyncio

                def read_strm():
                    with open(source_path, "r") as f:
                        return f.read().strip()

                resolved_url = await asyncio.to_thread(read_strm)
                log_service.info(f"Resolved STRM {source_path} to {resolved_url}")
                return await self._resolve_source(resolved_url)
            except Exception as e:
                log_service.error(f"Failed to resolve STRM file {source_path}: {e}")
                raise HTTPException(
                    status_code=500, detail="Failed to resolve media source"
                )

        from .torrent_service import is_torrent, torrent_service
        if is_torrent(source_path):
            return await torrent_service.resolve(source_path)

        # 2. Remote URLs
        if source_path.startswith("http"):
            return source_path

        # 3. Local files - return as file:// URL for ffprobe/ffmpeg compatibility
        return f"file://{os.path.abspath(source_path)}"

    async def _handle_direct_stream(
        self, stream_url: str, range_header: Optional[str], source_path: str, probe_info: Optional[Dict] = None
    ):
        """Handle direct streaming (no transcoding)"""
        # For local files (file:// URLs)
        if stream_url.startswith("file://"):
            file_path = stream_url.replace("file://", "")
            return self._stream_local_file(file_path, range_header, probe_info)

        # For remote URLs
        return await self._proxy_remote_stream(stream_url, range_header, probe_info)

    def _stream_local_file(self, file_path: str, range_header: Optional[str] = None, probe_info: Optional[Dict] = None):
        """Stream a local file with Range support and dynamic Content-Type"""
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found")

        file_size = path.stat().st_size

        # Use probe_info for accurate Content-Type if available
        if probe_info and probe_info.get("container"):
            container = probe_info["container"].lower()
            if container == "mp4":
                content_type = "video/mp4"
            elif container == "matroska":
                content_type = "video/x-matroska"
            elif container == "avi":
                content_type = "video/x-msvideo"
            elif container == "webm":
                content_type = "video/webm"
            elif container == "mov":
                content_type = "video/quicktime"
            else:
                # Fallback to mime type guessing
                mime_type, _ = mimetypes.guess_type(file_path)
                content_type = mime_type or "video/mp4"
        else:
            # Fallback to mime type guessing
            mime_type, _ = mimetypes.guess_type(file_path)
            content_type = mime_type or "video/mp4"

        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            try:
                range_str = range_header.replace("bytes=", "")
                parts = range_str.split("-")
                start = int(parts[0]) if parts[0] else 0
                if parts[1]:
                    end = int(parts[1])
                status_code = 206
            except Exception:
                pass

        def iter_file():
            with open(path, "rb") as f:
                f.seek(start)
                chunk_size = 1024 * 1024  # 1MB chunks
                bytes_left = (end - start) + 1
                while bytes_left > 0:
                    read_size = min(chunk_size, bytes_left)
                    data = f.read(read_size)
                    if not data:
                        break
                    yield data
                    bytes_left -= len(data)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str((end - start) + 1),
            "Content-Type": content_type,
        }

        return StreamingResponse(iter_file(), status_code=status_code, headers=headers)

    async def _proxy_remote_stream(self, url: str, range_header: Optional[str] = None, probe_info: Optional[Dict] = None):
        """Proxy a remote HTTP stream with proper header forwarding"""
        headers = {
            # Additional headers to look more like a real browser
            "Accept": "*/*",
            "Accept-Encoding": "identity",  # Don't request compression for video
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "video",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }
        if range_header:
            headers["Range"] = range_header

        try:

            # We need to get the headers from the remote first to pass them to StreamingResponse
            # But we don't want to make two requests.
            # httpx allows us to peek at headers if we use the stream context manager.

            # This is a bit tricky with StreamingResponse.
            # Let's try to get the HEAD or just a limited GET first.
            # Actually, we can just use the stream context directly.

            response = await self.client.send(
                self.client.build_request("GET", url, headers=headers), stream=True
            )

            if response.status_code >= 400:
                await response.aclose()
                raise HTTPException(
                    status_code=response.status_code, detail="Remote stream error"
                )

            forward_headers = {}
            for key in [
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
            ]:
                if key in response.headers:
                    forward_headers[key] = response.headers[key]

            if "Content-Type" not in forward_headers:
                # Use probe_info for accurate Content-Type if available
                if probe_info and probe_info.get("container"):
                    container = probe_info["container"].lower()
                    if container == "mp4":
                        forward_headers["Content-Type"] = "video/mp4"
                    elif container == "matroska":
                        forward_headers["Content-Type"] = "video/x-matroska"
                    elif container == "avi":
                        forward_headers["Content-Type"] = "video/x-msvideo"
                    elif container == "webm":
                        forward_headers["Content-Type"] = "video/webm"
                    elif container == "mov":
                        forward_headers["Content-Type"] = "video/quicktime"
                    else:
                        forward_headers["Content-Type"] = "video/mp4"
                else:
                    forward_headers["Content-Type"] = "video/mp4"

            return StreamingResponse(
                response.aiter_bytes(chunk_size=1024 * 1024),
                status_code=response.status_code,
                headers=forward_headers,
                background=response.aclose,  # Ensure it's closed using the instance method
            )

        except httpx.RequestError as e:
            log_service.error(f"HTTP Request failed for {url}: {e}")
            raise HTTPException(status_code=502, detail="Bad Gateway to remote stream")

    @staticmethod
    def get_transcode_command(input_path: str, quality: str = "720p"):
        """Generate ffmpeg command for transcoding to HLS (WIP)"""
        profiles = {
            "720p": {"width": 1280, "height": 720, "bitrate": "2500k"},
            "480p": {"width": 854, "height": 480, "bitrate": "1000k"},
        }

        p = profiles.get(quality, profiles["720p"])

        cmd = [
            "ffmpeg",
            "-i",
            input_path,
            "-vf",
            f"scale={p['width']}:{p['height']}",
            "-c:v",
            "libx264",
            "-b:v",
            p["bitrate"],
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-f",
            "hls",
            "-hls_time",
            "10",
            "-hls_list_size",
            "0",
            "pipe:1",
        ]
        return cmd


stream_service = StreamService()
