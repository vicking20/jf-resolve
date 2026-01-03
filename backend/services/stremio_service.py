import asyncio
import json
import time
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .log_service import log_service


class StremioService:
    """Stremio addon manifest integration"""

    # Rate limiting: minimum delay between requests (in seconds)
    _last_request_time = 0
    _request_delay = 0.5  # 500ms

    def __init__(self, manifest_url: str):
        self.manifest_url = self.normalize_url(manifest_url)

        # Create requests session with retries
        self.session = requests.Session()

        # Setup retry strategy
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

        # Set browser-like headers
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def normalize_url(url: str) -> str:
        """
        Normalize Stremio manifest URL:
        - Convert stremio:// to https://
        - Remove /manifest.json
        """
        if not url:
            return ""

        if url.startswith("stremio://"):
            url = url.replace("stremio://", "https://", 1)

        # Remove /manifest.json and slashes
        url = url.replace("/manifest.json", "").rstrip("/")

        return url

    async def _rate_limited_request(self):
        """
        Implement rate limiting to avoid getting blocked by stream provider
        """
        current_time = time.time()
        time_since_last = current_time - StremioService._last_request_time

        if time_since_last < self._request_delay:
            delay = self._request_delay - time_since_last
            log_service.info(f"Rate limiting: waiting {delay:.2f}s before request")
            await asyncio.sleep(delay)

        StremioService._last_request_time = time.time()

    def _log_response_error_details(self, response: requests.Response, identifier: str):
        """
        Log
        """
        log_service.error(f"Response details for {identifier}:")
        log_service.error(f"  Status: {response.status_code}")
        log_service.error(f"  Headers: {dict(response.headers)}")
        log_service.error(
            f"  Content-Type: {response.headers.get('content-type', 'unknown')}"
        )
        log_service.error(f"  Content-Length: {len(response.content)} bytes")

        # Log
        try:
            preview = response.content[:500].decode("utf-8", errors="replace")
            log_service.error(f"  Content preview: {preview}")
        except Exception:
            log_service.error(
                f"  Content preview: <binary data, first 100 bytes: {response.content[:100]}>"
            )

    def _parse_json_safe(
        self, response: requests.Response, identifier: str
    ) -> Optional[Dict]:
        """
        Parse JSON
        """
        try:
            # Parse from bytes directly
            data = json.loads(response.content)
            return data
        except json.JSONDecodeError as e:
            log_service.error(f"JSON decode error for {identifier}: {e}")
            self._log_response_error_details(response, identifier)
            return None
        except Exception as e:
            log_service.error(
                f"Unexpected error parsing response for {identifier}: {e}"
            )
            self._log_response_error_details(response, identifier)
            return None

    async def get_movie_streams(self, imdb_id: str) -> List[Dict]:
        """
        Get streams for a movie
        """
        # Rate limiting
        await self._rate_limited_request()

        url = f"{self.manifest_url}/stream/movie/{imdb_id}.json"

        log_service.info(f"Fetching Stremio streams from: {url}")

        try:
            # Use asyncio.to_thread
            response = await asyncio.to_thread(self.session.get, url, timeout=30)

            if response.status_code != 200:
                log_service.error(
                    f"Stremio API returned {response.status_code} for movie {imdb_id}"
                )
                self._log_response_error_details(response, f"movie {imdb_id}")
                return []

            # Parse JSON
            data = self._parse_json_safe(response, f"movie {imdb_id}")
            if data is None:
                return []

            streams = data.get("streams", [])
            log_service.info(f"Received {len(streams)} streams for movie {imdb_id}")
            return streams

        except requests.RequestException as e:
            log_service.error(f"HTTP error for movie {imdb_id}: {e} - URL: {url}")
            return []
        except Exception as e:
            log_service.error(
                f"Unexpected error fetching streams for movie {imdb_id}: {e}"
            )
            return []

    async def get_episode_streams(
        self, imdb_id: str, season: int, episode: int
    ) -> List[Dict]:
        """
        Get streams for a TV episode
        GET {manifest_url}/stream/series/{imdb_id}:{season}:{episode}.json
        """
        # Rate limiting
        await self._rate_limited_request()

        url = f"{self.manifest_url}/stream/series/{imdb_id}:{season}:{episode}.json"

        log_service.info(f"Fetching Stremio streams from: {url}")

        try:
            # Use asyncio.to_thread
            response = await asyncio.to_thread(self.session.get, url, timeout=30)

            if response.status_code != 200:
                log_service.error(
                    f"Stremio API returned {response.status_code} for series {imdb_id}:{season}:{episode}"
                )
                self._log_response_error_details(
                    response, f"series {imdb_id}:{season}:{episode}"
                )
                return []

            # Parse JSON
            data = self._parse_json_safe(
                response, f"series {imdb_id}:{season}:{episode}"
            )
            if data is None:
                return []

            streams = data.get("streams", [])
            log_service.info(
                f"Received {len(streams)} streams for episode {imdb_id}:{season}:{episode}"
            )
            return streams

        except requests.RequestException as e:
            log_service.error(
                f"HTTP error for series {imdb_id}:{season}:{episode}: {e} - URL: {url}"
            )
            return []
        except Exception as e:
            log_service.error(
                f"Unexpected error fetching streams for series {imdb_id}:{season}:{episode}: {e}"
            )
            return []

    @staticmethod
    def detect_quality(stream: Dict) -> str:
        """
        Detect quality from stream title/name
        Priority based on C# reference: 4K/2160p > 1440p > 1080p > 720p > 480p
        """
        title = stream.get("title", "").lower()
        name = stream.get("name", "").lower()
        text = f"{title} {name}"

        # 4K / 2160p
        if any(ind in text for ind in ["4k", "2160p", "2160"]):
            return "4k"

        # 1440p
        if any(ind in text for ind in ["1440p", "1440"]):
            return "1440p"

        # 1080p / FHD
        if any(ind in text for ind in ["1080p", "1080", "fhd"]):
            return "1080p"

        # 720p / HD
        if any(ind in text for ind in ["720p", "720", "hd"]):
            return "720p"

        # 480p
        if any(ind in text for ind in ["480p", "480"]):
            return "480p"

        return "unknown"

    async def select_stream(
        self,
        streams: List[Dict],
        quality: str,
        index: int,
        fallback_enabled: bool = True,
        fallback_order: List[str] = None,
    ) -> Optional[str]:
        """
        Select stream by quality and index
        """
        if not streams:
            return None

        if fallback_order is None:
            fallback_order = ["1080p", "720p", "4k", "480p"]

        # Try requested quality first
        quality_streams = [s for s in streams if self.detect_quality(s) == quality]

        if quality_streams:
            # Fallback to last available if index is too high
            idx = index
            if idx >= len(quality_streams):
                log_service.info(
                    f"Requested index {index} out of range for quality {quality}. "
                    f"Falling back to index {len(quality_streams) - 1}."
                )
                idx = len(quality_streams) - 1

            return quality_streams[idx].get("url")

        # Fallback to other qualities if enabled
        if fallback_enabled:
            log_service.info(
                f"Quality {quality} not found, trying fallback order: {fallback_order}"
            )
            for fallback_quality in fallback_order:
                if fallback_quality == quality:
                    continue

                fallback_streams = [
                    s for s in streams if self.detect_quality(s) == fallback_quality
                ]

                if fallback_streams:
                    log_service.info(f"Selected fallback quality: {fallback_quality}")
                    return fallback_streams[0].get("url")

        # Last resort: return first available stream
        if streams:
            log_service.info("No quality match found, using first available stream")
            return streams[0].get("url")

        return None

    async def close(self):
        """Close HTTP session"""
        self.session.close()
