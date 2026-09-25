import asyncio
import json
import logging
import re
import time
from urllib.parse import urlencode, urlsplit, urlunsplit
from typing import Dict, List, Optional

import requests
from backend.addons.interfaces import BasePlugin, SourceProvider, Stream
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class StremioClient:
    """Internal Stremio client for the plugin"""

    _last_request_time = 0
    _request_delay = 0.2  # 200ms

    def __init__(self, manifest_url: str):
        self.manifest_url = self.normalize_url(manifest_url)
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy, pool_connections=10, pool_maxsize=20
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            }
        )

    @staticmethod
    def normalize_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("stremio://"):
            url = url.replace("stremio://", "https://", 1)
        url = url.replace("/manifest.json", "").rstrip("/")
        return url

    async def _rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - StremioClient._last_request_time
        if time_since_last < self._request_delay:
            await asyncio.sleep(self._request_delay - time_since_last)
        StremioClient._last_request_time = time.time()

    async def get_streams(self, media_type: str, id_string: str) -> List[Dict]:
        await self._rate_limit()
        # Stremio uses 'series' instead of 'tv' in URL
        type_str = "series" if media_type == "tv" else "movie"
        url = f"{self.manifest_url}/stream/{type_str}/{id_string}.json"

        try:
            response = await asyncio.to_thread(self.session.get, url, timeout=15)
            if response.status_code == 200:
                return response.json().get("streams", [])
            return []
        except Exception as e:
            logger.error(f"Stremio fetch error: {e}")
            return []

    def close(self):
        self.session.close()


class Plugin(BasePlugin, SourceProvider):
    """
    Self-contained Stream Manager plugin.
    Fetches streams from Stremio addons based on manifest URL.
    """

    default_config = {
        "manifest_url": "",
        "p2p_fallback": True,
        "p2p_manifest_url": "",
        "max_results": 20,
        "show_stream_details": False,
        "priority_keywords": "4k,1080p,rd+",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = None
        self.p2p_client = None

    @staticmethod
    def torrentio_p2p_url(url):
        parts = urlsplit(url.replace("stremio://", "https://", 1))
        if parts.hostname != "torrentio.strem.fun":
            return None
        fields = parts.path.strip("/").split("/")
        if len(fields) != 2 or fields[-1] != "manifest.json":
            return None
        options = fields[0].split("|")
        debrid = {"realdebrid", "alldebrid", "premiumize", "debridlink", "torbox", "debridoptions"}
        remaining = [option for option in options if option.split("=", 1)[0] not in debrid]
        if len(remaining) == len(options):
            return None
        path = "/" + ("|".join(remaining) + "/" if remaining else "") + "manifest.json"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    async def on_load(self):
        self.load_config()
        url = self.config.get("manifest_url")
        if url:
            self.client = StremioClient(url)
            if self.config.get("p2p_fallback", True):
                fallback = self.config.get("p2p_manifest_url") or self.torrentio_p2p_url(url)
                if fallback:
                    self.p2p_client = StremioClient(fallback)

    async def on_unload(self):
        if self.p2p_client:
            self.p2p_client.close()
            self.p2p_client = None
        if self.client:
            self.client.close()
            self.client = None

    def _parse_stream_info(self, s: Dict) -> Dict:
        """Parse quality, size, and clean title from Stremio stream object"""
        title_raw = s.get("title", "")
        name_raw = s.get("name", "")
        combined = f"{name_raw}\n{title_raw}"

        # 1. Detect Quality/Resolution
        quality = "unknown"
        res_match = re.search(r"(4k|2160p|1440p|1080p|720p|480p|360p)", combined, re.I)
        if res_match:
            quality = res_match.group(1).lower()
        elif "fhd" in combined.lower():
            quality = "1080p"
        elif "hd" in combined.lower():
            quality = "720p"

        # 2. Detect Size
        size = None
        # Common pattern: 💾 12.5 GB or 📦 2.1 GB
        size_match = re.search(r"[💾📦]\s*([\d.]+\s*[KMG]B)", combined, re.I)
        if size_match:
            size = size_match.group(1).strip()
        else:
            # Fallback for plain text size: 12.5 GB
            size_text = re.search(r"(\d+\.?\d*)\s*(GB|MB)", combined, re.I)
            if size_text:
                size = f"{size_text.group(1)} {size_text.group(2).upper()}"

        # 3. Clean Title
        # Extract filename if available in behaviorHints
        clean_title = s.get("behaviorHints", {}).get("filename")
        if not clean_title:
            # Otherwise use first line of title or name
            clean_title = title_raw.split("\n")[0] or name_raw.split("\n")[0]
            # Strip common junk if it looks like a filename
            clean_title = clean_title.strip()

        return {
            "quality": quality,
            "size": size,
            "clean_title": clean_title,
            "raw_title": combined.strip(),
        }

    async def get_streams(
        self,
        imdb_id: str,
        media_type: str,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> List[Stream]:
        if not self.client:
            logger.warning("stream-manager: No manifest URL configured.")
            return []

        id_string = imdb_id
        if media_type == "tv" and season is not None and episode is not None:
            id_string = f"{imdb_id}:{season}:{episode}"

        max_results = int(self.config.get("max_results", 20))
        if self.p2p_client:
            primary, fallback = await asyncio.gather(
                self.client.get_streams(media_type, id_string),
                self.p2p_client.get_streams(media_type, id_string),
            )
            raw_streams = primary[:max_results] + [s for s in fallback if s.get("infoHash") and not s.get("url")][:max_results]
        else:
            raw_streams = (await self.client.get_streams(media_type, id_string))[:max_results]
        streams = []
        show_details = self.config.get("show_stream_details", False)

        seen_urls = set()
        for s in raw_streams:
            stream_url = s.get("url")
            if not stream_url and s.get("infoHash"):
                params = [("xt", "urn:btih:" + s["infoHash"])]
                if s.get("fileIdx") is not None:
                    params.append(("so", str(s["fileIdx"])))
                for source in s.get("sources", []):
                    if source.startswith("tracker:"):
                        params.append(("tr", source[len("tracker:"):]))
                stream_url = "magnet:?" + urlencode(params)
            if not stream_url or stream_url in seen_urls:
                continue
            seen_urls.add(stream_url)
            info = self._parse_stream_info(s)

            # Use raw title (messy) if show_details is True, otherwise use clean title
            display_title = info["raw_title"] if show_details else info["clean_title"]

            # Map Stremio headers
            headers = {}
            bh = s.get("behaviorHints", {})
            if bh.get("proxyHeaders"):
                headers = bh.get("proxyHeaders", {}).get("request", {})

            streams.append(
                Stream(
                    title=display_title,
                    url=stream_url,
                    tag="P2P" if stream_url.startswith("magnet:") else None,
                    quality=info["quality"],
                    resolution=info["quality"].upper()
                    if info["quality"] != "unknown"
                    else None,
                    size=info["size"],
                    is_cached=any(
                        mark in info["raw_title"] for mark in ["⚡", "✅", "[RD]"]
                    ),
                    raw_title=info["raw_title"],
                    headers=headers,
                )
            )

        return streams
