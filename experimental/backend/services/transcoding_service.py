"""Transcoding service with HLS and hardware acceleration support"""

import asyncio
import os
from pathlib import Path
from typing import Dict, List, Optional

from .log_service import log_service


class TranscodingService:
    """Service for transcoding media streams to HLS"""

    @staticmethod
    def build_ffmpeg_command(
        input_url: str,
        output_path: str,
        transcode_settings: Dict,
        client_profile: Dict,
        probe_info: Dict,
        audio_track_index: Optional[int] = None,
        start_time: float = 0.0,
    ) -> List[str]:
        """
        Build FFmpeg command with hardware acceleration support

        Args:
            input_url: Source stream URL
            output_path: Output HLS playlist path
            transcode_settings: Transcoding settings from DB
            client_profile: Client capability profile
            probe_info: Stream information from FFprobe
            audio_track_index: Optional specific audio track to use

        Returns:
            List of command arguments for FFmpeg
        """
        cmd = ["ffmpeg"]

        hw_accel = transcode_settings.get("hardware_acceleration", "none")

        if hw_accel == "nvidia":
            cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
            video_encoder = "h264_nvenc"
            hevc_encoder = "hevc_nvenc"
        elif hw_accel == "intel":
            cmd.extend(["-hwaccel", "qsv"])
            video_encoder = "h264_qsv"
            hevc_encoder = "hevc_qsv"
        elif hw_accel == "amd":
            cmd.extend(["-hwaccel", "vulkan"])
            video_encoder = "h264_amf"
            hevc_encoder = "hevc_amf"
        elif hw_accel == "videotoolbox":
            cmd.extend(["-hwaccel", "videotoolbox"])
            video_encoder = "h264_videotoolbox"
            hevc_encoder = "hevc_videotoolbox"
        else:
            video_encoder = "libx264"
            hevc_encoder = "libx265"

        if start_time > 0:
            cmd.extend(["-ss", str(start_time)])

        cmd.extend(["-i", input_url])

        video_codec = probe_info.get("video_codec")
        target_video_codec = transcode_settings.get("target_video_codec", "h264")

        if (
            video_codec
            and video_codec != "unknown"
            and video_codec in client_profile.get("video", [])
        ):
            cmd.extend(["-c:v", "copy"])
            log_service.info(f"Video: copying {video_codec} (compatible)")
        else:
            encoder = hevc_encoder if target_video_codec == "hevc" else video_encoder
            cmd.extend(["-c:v", encoder])

            if hw_accel == "none":
                preset = transcode_settings.get("transcode_preset", "veryfast")
                cmd.extend(["-preset", preset])

            if "videotoolbox" in encoder:
                cmd.extend(["-b:v", "4000k", "-maxrate", "4500k", "-bufsize", "8000k"])

            elif "nvenc" in encoder or "qsv" in encoder or "amf" in encoder:
                cmd.extend(["-cq", "23"])
            else:
                cmd.extend(["-crf", "23"])
            
            cmd.extend(["-pix_fmt", "yuv420p"])

            log_service.info(
                f"Video: transcoding {video_codec} -> {target_video_codec} ({encoder})"
            )

            if (
                probe_info.get("hdr")
                and not client_profile.get("hdr")
                and transcode_settings.get("hdr_tone_mapping", True)
            ):
                log_service.info("Applying HDR to SDR tone mapping")

                cmd.extend(
                    [
                        "-vf",
                        "zscale=t=linear:npl=100,format=gbrpf32le,"
                        "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                        "zscale=t=bt709:m=bt709:r=tv,format=yuv420p",
                    ]
                )

        audio_codec = probe_info.get("audio_codec")
        target_audio_codec = transcode_settings.get("target_audio_codec", "aac")

        if audio_track_index is not None:
            cmd.extend(["-map", "0:v:0", "-map", f"0:a:{audio_track_index}"])
            log_service.info(
                f"Stream mapping: video=0:v:0, audio=0:a:{audio_track_index}"
            )
        else:
            cmd.extend(["-map", "0:v:0", "-map", "0:a:0?"])
            log_service.info(
                "Stream mapping: video=0:v:0, audio=0:a:0 (default first track)"
            )

        if (
            audio_codec
            and audio_codec != "unknown"
            and audio_codec in client_profile.get("audio", [])
        ):
            cmd.extend(["-c:a", "copy"])
            log_service.info(f"Audio: copying {audio_codec} (compatible)")
        else:
            if target_audio_codec == "aac":
                cmd.extend(["-c:a", "aac", "-ac", "2", "-b:a", "192k"])
            elif target_audio_codec == "opus":
                cmd.extend(["-c:a", "libopus", "-ac", "2", "-b:a", "128k"])
            elif target_audio_codec == "mp3":
                cmd.extend(["-c:a", "libmp3lame", "-ac", "2", "-b:a", "192k"])
            else:
                cmd.extend(["-c:a", "aac", "-ac", "2", "-b:a", "192k"])

            log_service.info(
                f"Audio: transcoding {audio_codec} -> {target_audio_codec}"
            )

        segment_duration = transcode_settings.get("hls_segment_duration", 4)
        
        start_number = 0

        cmd.extend(
            [
                "-f",
                "hls",
                "-hls_time",
                str(segment_duration),
                "-hls_list_size",
                "0",
                "-hls_segment_type",
                "mpegts",
                "-hls_flags",
                "independent_segments+temp_file",
                "-start_number",
                str(start_number),
                "-hls_segment_filename",
                str(
                    Path(output_path).parent / "segment_%04d.ts"
                ),
                output_path,
            ]
        )

        for track in probe_info.get("subtitle_tracks", []):
            if not track.get("supported"):
                continue
            cmd.extend([
                "-map", f"0:s:{track['index']}", "-c:s", "webvtt",
                "-f", "segment", "-segment_time", "30",
                "-segment_format", "webvtt", "-segment_format_options", "flush_packets=1",
                "-reset_timestamps", "0",
                str(Path(output_path).parent / f"subtitle_{track['index']}_%05d.vtt"),
            ])
        return cmd

    @staticmethod
    async def start_transcode(
        stream_id: str,
        input_url: str,
        transcode_settings: Dict,
        client_profile: Dict,
        probe_info: Dict,
        audio_track_index: Optional[int] = None,
    ) -> Dict:
        """
        Start transcoding process

        Args:
            stream_id: Unique stream identifier
            input_url: Source stream URL
            transcode_settings: Transcoding settings
            client_profile: Client capabilities
            probe_info: Stream information
            audio_track_index: Optional specific audio track

        Returns:
            Dict with playlist_path and process info
        """
        from .transcode_manager import transcode_manager

        output_dir = transcode_manager.get_hls_output_dir(stream_id)
        if output_dir.exists():
            transcode_manager.cleanup_hls_files(stream_id)
        
        output_dir.mkdir(parents=True, exist_ok=True)

        playlist_path = output_dir / "master.m3u8"

        ffmpeg_cmd = TranscodingService.build_ffmpeg_command(
            input_url,
            str(playlist_path),
            transcode_settings,
            client_profile,
            probe_info,
            audio_track_index,
        )

        log_service.info(f"Starting transcode: {' '.join(ffmpeg_cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=None,
                stderr=asyncio.subprocess.PIPE,
            )

            duration_val = probe_info.get("duration")
            if duration_val:
                try:
                    duration_val = float(duration_val)
                except ValueError:
                    duration_val = None
            
            stream_info = {
                "input_url": input_url,
                "transcode_settings": transcode_settings,
                "client_profile": client_profile,
                "probe_info": probe_info,
                "audio_track_index": audio_track_index,
            }
            segment_duration = transcode_settings.get("hls_segment_duration", 4)
            transcode_manager.register_process(
                stream_id, process, 
                duration=duration_val, 
                stream_info=stream_info,
                segment_duration=segment_duration
            )
            transcode_manager.watch_output(stream_id, process)

            ready = await transcode_manager.wait_for_hls_ready(
                playlist_path, timeout=45, process=process
            )

            if not ready:
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except Exception:
                    process.kill()

                stderr_output = ""
                try:
                    stderr_output = (await process.stderr.read()).decode(
                        "utf-8", errors="replace"
                    )
                except Exception:
                    pass

                error_msg = f"Transcode failed to produce HLS playlist in time"
                if stderr_output:
                    log_service.error(f"FFmpeg stderr: {stderr_output}")
                    error_msg += f". FFmpeg error: {stderr_output[:500]}"

                raise Exception(error_msg)

            log_service.info(f"Transcode started successfully for {stream_id}")

            return {
                "playlist_path": str(playlist_path),
                "output_dir": str(output_dir),
                "process": process,
            }

        except Exception as e:
            log_service.error(f"Failed to start transcode: {e}")
            await transcode_manager.terminate_process(stream_id)
            transcode_manager.cleanup_hls_files(stream_id)
            raise

    @staticmethod
    def detect_hardware_acceleration() -> List[str]:
        """
        Detect available hardware acceleration methods

        Returns:
            List of available acceleration methods
        """
        available = ["none"]

        try:
            import subprocess

            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
            if result.returncode == 0:
                available.append("nvidia")
        except Exception:
            pass

        if os.path.exists("/dev/dri"):
            available.append("intel")

        if os.uname().sysname == "Darwin":
            available.append("videotoolbox")

        try:
            result = subprocess.run(
                ["lspci"], capture_output=True, timeout=5, text=True
            )
            if "AMD" in result.stdout or "Radeon" in result.stdout:
                available.append("amd")
        except Exception:
            pass

        log_service.info(f"Available hardware acceleration: {available}")
        return available

    @staticmethod
    def get_stream_id_from_params(
        media_id: int,
        media_type: str,
        quality: str = "1080p",
        season: Optional[int] = None,
        episode: Optional[int] = None,
        source_index: int = 0,
    ) -> str:
        """
        Generate unique stream ID from parameters

        Args:
            media_id: Media ID (can be TMDB ID or DB ID)
            media_type: 'movie' or 'tv'
            quality: Quality string
            season: Season number (TV only)
            episode: Episode number (TV only)
            source_index: Source index

        Returns:
            Unique stream ID
        """
        if media_type == "movie":
            return f"movie_{media_id}_{quality}_{source_index}"
        else:
            return f"tv_{media_id}_s{season}e{episode}_{quality}_{source_index}"
