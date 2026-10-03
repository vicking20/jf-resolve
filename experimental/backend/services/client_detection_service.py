"""Client detection and codec compatibility service"""

from typing import Dict

from .log_service import log_service


class ClientDetectionService:
    """Service for detecting client capabilities and codec support"""

    CODEC_PROFILES = {
        "chrome": {
            "video": ["h264", "vp8", "vp9", "av1"],
            "audio": ["aac", "mp3", "opus", "vorbis", "flac"],
            "containers": ["mp4", "mov", "webm"],
            "hdr": False,
            "max_bitrate": None,
        },
        "chromium": {
            "video": ["h264", "vp8", "vp9", "av1"],
            "audio": ["aac", "mp3", "opus", "vorbis", "flac"],
            "containers": ["mp4", "mov", "webm"],
            "hdr": False,
            "max_bitrate": None,
        },
        "edge": {
            "video": ["h264", "vp8", "vp9", "av1"],
            "audio": ["aac", "mp3", "opus", "vorbis", "flac"],
            "containers": ["mp4", "mov", "webm"],
            "hdr": False,
            "max_bitrate": None,
        },
        "safari": {
            "video": ["h264", "hevc"],
            "audio": ["aac", "mp3", "alac"],
            "containers": ["mp4", "mov"],
            "hdr": True,
            "max_bitrate": None,
        },
        "firefox": {
            "video": ["h264", "vp8", "vp9", "av1"],
            "audio": ["aac", "mp3", "opus", "vorbis", "flac"],
            "containers": ["mp4", "mov", "webm"],
            "hdr": False,
            "max_bitrate": None,
        },
        "default": {
            "video": ["h264"],
            "audio": ["aac", "mp3"],
            "containers": ["mp4", "mov"],
            "hdr": False,
            "max_bitrate": None,
        },
    }

    @staticmethod
    def detect_client_capabilities(user_agent: str) -> Dict:
        """
        Detect client browser/device and return codec support profile

        Args:
            user_agent: User-Agent header string

        Returns:
            Dict with supported codecs and capabilities
        """
        if not user_agent:
            return ClientDetectionService.CODEC_PROFILES["default"].copy()

        user_agent_lower = user_agent.lower()

        if "edg/" in user_agent_lower or "edge/" in user_agent_lower:
            browser = "edge"
        elif "chrome" in user_agent_lower or "crios" in user_agent_lower:
            browser = "chrome"
        elif "safari" in user_agent_lower and "chrome" not in user_agent_lower:
            browser = "safari"
        elif "firefox" in user_agent_lower or "fxios" in user_agent_lower:
            browser = "firefox"
        elif "chromium" in user_agent_lower:
            browser = "chromium"
        else:
            browser = "default"

        profile = ClientDetectionService.CODEC_PROFILES[browser].copy()

        if "android" in user_agent_lower:
            profile["device"] = "android"
            if "h265" not in profile["video"] and "hevc" not in profile["video"]:
                profile["video"].append("hevc")
        elif "iphone" in user_agent_lower or "ipad" in user_agent_lower:
            profile["device"] = "ios"
            profile["hdr"] = True
        elif "mac os x" in user_agent_lower or "macintosh" in user_agent_lower:
            profile["device"] = "macos"
            profile["hdr"] = True
        elif "windows" in user_agent_lower:
            profile["device"] = "windows"
        elif "linux" in user_agent_lower:
            profile["device"] = "linux"
        else:
            profile["device"] = "unknown"

        is_mobile = any(
            keyword in user_agent_lower
            for keyword in ["mobile", "android", "iphone", "ipad"]
        )
        profile["is_mobile"] = is_mobile

        log_service.stream(
            f"Client detection: {browser} on {profile.get('device', 'unknown')} - "
            f"video: {profile.get('video', [])} - audio: {profile.get('audio', [])} - "
            f"containers: {profile.get('containers', [])}"
        )

        return profile

    @staticmethod
    def needs_transcoding(client_profile: Dict, stream_info: Dict) -> bool:
        """
        Determine if stream needs transcoding based on client capabilities

        Args:
            client_profile: Client capability profile from detect_client_capabilities()
            stream_info: Stream info from FFprobe

        Returns:
            True if transcoding is needed, False if direct streaming is possible
        """
        if not stream_info:
            log_service.stream(
                "Transcode check: stream_info is empty, requires transcode"
            )
            return True

        log_service.stream(
            f"Transcode check - Stream info: video={stream_info.get('video_codec')}, "
            f"audio={stream_info.get('audio_codec')}, container={stream_info.get('container')}, "
            f"hdr={stream_info.get('hdr')}, bitrate={stream_info.get('bitrate')}"
        )
        log_service.stream(
            f"Transcode check - Client profile: video={client_profile.get('video')}, "
            f"audio={client_profile.get('audio')}, containers={client_profile.get('containers')}, "
            f"hdr={client_profile.get('hdr')}"
        )

        video_codec = stream_info.get("video_codec")
        if video_codec and video_codec not in client_profile.get("video", []):
            log_service.stream(
                f"Transcode required: video codec '{video_codec}' not in supported list"
            )
            return True

        audio_codec = stream_info.get("audio_codec")
        if audio_codec and audio_codec not in client_profile.get("audio", []):
            log_service.stream(
                f"Transcode required: audio codec '{audio_codec}' not in supported list"
            )
            return True

        container = stream_info.get("container")
        if container and container not in client_profile.get("containers", []):
            log_service.stream(
                f"Transcode required: container '{container}' not in supported list {client_profile.get('containers')}"
            )
            return True

        if stream_info.get("hdr") and not client_profile.get("hdr"):
            log_service.stream(
                "Transcode required: HDR content but client doesn't support HDR"
            )
            return True

        max_bitrate = client_profile.get("max_bitrate")
        if max_bitrate and stream_info.get("bitrate", 0) > max_bitrate:
            log_service.stream(
                f"Transcode required: bitrate {stream_info.get('bitrate')} exceeds max {max_bitrate}"
            )
            return True

        log_service.stream("No transcode required: stream is compatible with client")
        return False

    @staticmethod
    def get_transcode_reason(client_profile: Dict, stream_info: Dict) -> str:
        """
        Get human-readable reason why transcoding is needed

        Returns:
            String describing why transcoding is required, or "compatible" if not needed
        """
        if not stream_info:
            return "stream_info_unavailable"

        reasons = []

        video_codec = stream_info.get("video_codec")
        if video_codec and video_codec not in client_profile.get("video", []):
            reasons.append(f"video_codec:{video_codec}")

        audio_codec = stream_info.get("audio_codec")
        if audio_codec and audio_codec not in client_profile.get("audio", []):
            reasons.append(f"audio_codec:{audio_codec}")

        container = stream_info.get("container")
        if container and container not in client_profile.get("containers", []):
            reasons.append(f"container:{container}")

        if stream_info.get("hdr") and not client_profile.get("hdr"):
            reasons.append("hdr_tone_mapping_required")

        max_bitrate = client_profile.get("max_bitrate")
        if max_bitrate and stream_info.get("bitrate", 0) > max_bitrate:
            reasons.append(f"bitrate_too_high:{stream_info.get('bitrate')}")

        if not reasons:
            return "compatible"

        return ", ".join(reasons)

    @staticmethod
    def can_direct_stream(probe_info: Dict) -> bool:
        """
        Check if stream can be safely direct streamed to browsers without transcoding.
        This is more conservative than needs_transcoding() - only allows known working combinations.

        Args:
            probe_info: Stream info from FFprobe

        Returns:
            True if safe for direct streaming, False if should transcode
        """
        if not probe_info:
            return False

        container = probe_info.get("container", "").lower()
        video_codec = probe_info.get("video_codec", "").lower()
        audio_codec = probe_info.get("audio_codec", "").lower()

        if container == "mp4":
            if video_codec == "h264":
                if audio_codec in ["aac", "mp3"]:
                    return True
            elif not video_codec or video_codec == "h264":
                if audio_codec in ["aac", "mp3"]:
                    return True

        if container == "webm":
            if video_codec in ["vp8", "vp9"]:
                if audio_codec in ["vorbis", "opus"]:
                    return True

        log_service.stream(
            f"Direct streaming not safe for {container}/{video_codec}/{audio_codec} - will transcode"
        )
        return False
