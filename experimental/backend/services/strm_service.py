"""STRM output for Jellyfin (ported from the pre-2.0 library service).

Each movie gets one .strm file and each TV episode gets one .strm file. The
files point back at /api/strm/play, which resolves streams on demand, so the
folders never go stale when addon results change.
"""

import asyncio
import json
import secrets
import shutil
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx

from .log_service import log_service
from .settings_manager import SettingsManager
from .tmdb_service import TMDBService

MARKER_FILE = ".jfresolve"
MAX_VERSIONS = 5


def sanitize_filename(name: str) -> str:
    """Remove characters that are invalid in file and folder names"""
    name = name.replace(":", "")
    for char in '<>"/\\|?*':
        name = name.replace(char, "")
    return name.strip()


def folder_name(title: str, year: Optional[int]) -> str:
    clean = sanitize_filename(title)
    return f"{clean} ({year})" if year else clean


async def get_strm_token(settings: SettingsManager) -> str:
    """Shared secret embedded in STRM URLs; created on first use."""
    token = await settings.get("strm_access_token")
    if not token:
        token = secrets.token_urlsafe(32)
        await settings.set("strm_access_token", token)
    return token


class StrmService:
    """Create, refresh and remove STRM folders for library items"""

    def __init__(self, settings: SettingsManager, tmdb: TMDBService):
        self.settings = settings
        self.tmdb = tmdb

    async def is_enabled(self) -> bool:
        return bool(await self.settings.get("strm_generation_enabled", False))

    async def _play_url(self, media_type: str, tmdb_id: int, **params) -> str:
        base = (await self.settings.get("jfresolve_public_url") or "http://127.0.0.1:8765").rstrip("/")
        query = {k: v for k, v in params.items() if v is not None}
        query["token"] = await get_strm_token(self.settings)
        return f"{base}/api/strm/play/{media_type}/{tmdb_id}?{urlencode(query)}"

    async def _base_path(self, media_type: str) -> Path:
        key = "jellyfin_movie_path" if media_type == "movie" else "jellyfin_tv_path"
        value = await self.settings.get(key)
        if not value:
            raise ValueError(f"{key} is not configured; set it in Settings before generating STRM files")
        return Path(value)

    async def folder_for(self, media_type: str, title: str, year: Optional[int]) -> Path:
        return await self._base_path(media_type) / folder_name(title, year)

    @staticmethod
    async def _write(path: Path, content: str) -> None:
        await asyncio.to_thread(path.write_text, content)
        await asyncio.to_thread(path.chmod, 0o644)

    async def _write_metadata(self, folder: Path, data: Dict) -> None:
        await self._write(folder / MARKER_FILE, "")
        await self._write(folder / ".metadata.json", json.dumps(data, indent=2))

    async def generate(self, tmdb_id: int, media_type: str, scan: bool = True) -> Dict:
        """Create (or top up) the STRM folder for a title.

        Existing episode files are left alone, so calling this again after a
        new season airs only adds the missing episodes.
        """
        details = await self.tmdb.get_details(tmdb_id, media_type)
        if not details:
            raise ValueError(f"TMDB has no {media_type} with id {tmdb_id}")

        date_key = "release_date" if media_type == "movie" else "first_air_date"
        title = details.get("title") or details.get("name") or "Unknown"
        year = None
        if details.get(date_key):
            try:
                year = int(details[date_key].split("-")[0])
            except (ValueError, IndexError):
                pass

        imdb_id = (details.get("external_ids") or {}).get("imdb_id")
        if not imdb_id:
            raise ValueError("IMDB ID not found - addons cannot resolve streams for this title")

        folder = await self.folder_for(media_type, title, year)
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        clean_title = sanitize_filename(title)
        created = 0

        if media_type == "movie":
            base = folder_name(title, year)
            versions = max(1, min(MAX_VERSIONS, int(await self.settings.get("strm_movie_versions", 1) or 1)))
            if versions == 1:
                wanted = {folder / f"{base}.strm": None}
            else:
                wanted = {folder / f"{base} - Option {n}.strm": n - 1 for n in range(1, versions + 1)}
            for old in folder.glob("*.strm"):
                if old not in wanted:
                    await asyncio.to_thread(old.unlink)
            for strm_path, index in wanted.items():
                await self._write(strm_path, await self._play_url("movie", tmdb_id, index=index))
            created = len(wanted)
        else:
            for season in details.get("seasons", []):
                season_num = season.get("season_number")
                if not season_num:
                    continue
                season_details = await self.tmdb.get_season_details(tmdb_id, season_num) or {}
                season_folder = folder / f"Season {season_num:02d}"
                await asyncio.to_thread(season_folder.mkdir, parents=True, exist_ok=True)
                for episode in season_details.get("episodes", []):
                    episode_num = episode.get("episode_number", 0)
                    episode_title = sanitize_filename(episode.get("name") or f"Episode {episode_num}")
                    strm_path = season_folder / (
                        f"{clean_title} ({year}) - S{season_num:02d}E{episode_num:02d} - {episode_title}.strm"
                    )
                    if await asyncio.to_thread(strm_path.exists):
                        continue
                    await self._write(
                        strm_path,
                        await self._play_url("tv", tmdb_id, season=season_num, episode=episode_num),
                    )
                    created += 1

        await self._write_metadata(
            folder,
            {"tmdb_id": tmdb_id, "imdb_id": imdb_id, "media_type": media_type, "title": title, "year": year},
        )
        log_service.info(f"STRM: wrote {created} file(s) for {media_type}:{tmdb_id} ({title}) in {folder}")
        if created and scan:
            await self.trigger_jellyfin_scan()
        return {"folder": str(folder), "created": created}

    async def rewrite_movies(self) -> Dict:
        """Rewrite every movie folder JF-Resolve created, e.g. after changing versions."""
        base = await self._base_path("movie")
        rewritten, failed = 0, []
        for metadata in sorted(base.glob("*/.metadata.json")):
            folder = metadata.parent
            if not (folder / MARKER_FILE).exists():
                continue
            try:
                tmdb_id = int(json.loads(metadata.read_text())["tmdb_id"])
                await self.generate(tmdb_id, "movie", scan=False)
                rewritten += 1
            except Exception as e:
                failed.append(f"{folder.name}: {e}")
        if rewritten:
            await self.trigger_jellyfin_scan()
        return {"rewritten": rewritten, "failed": failed}

    async def remove(self, media_type: str, title: str, year: Optional[int]) -> bool:
        """Delete a title's STRM folder, but only if JF-Resolve created it."""
        folder = await self.folder_for(media_type, title, year)
        if not await asyncio.to_thread((folder / MARKER_FILE).exists):
            return False
        await asyncio.to_thread(shutil.rmtree, folder)
        log_service.info(f"STRM: removed {folder}")
        await self.trigger_jellyfin_scan()
        return True

    async def trigger_jellyfin_scan(self) -> None:
        """Ask Jellyfin to rescan its libraries, if configured"""
        if not await self.settings.get("jellyfin_scan_enabled", False):
            return
        url = (await self.settings.get("jellyfin_server_url") or "").rstrip("/")
        api_key = await self.settings.get("jellyfin_api_key")
        if not url or not api_key:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(f"{url}/Library/Refresh", headers={"Authorization": f'MediaBrowser Token="{api_key}"'})
                response.raise_for_status()
            log_service.info("STRM: triggered Jellyfin library scan")
        except Exception as e:
            log_service.error(f"STRM: failed to trigger Jellyfin scan: {e}")
