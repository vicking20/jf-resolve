"""Convert magnets to seekable HTTP streams through a project-local TorrServer."""
import asyncio
from collections import Counter
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[3]
VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.webm', '.avi', '.mov', '.m4v', '.ts'}


def normalize_magnet(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r'[a-fA-F0-9]{40}|[a-zA-Z2-7]{32}', value):
        return 'magnet:?' + urlencode({'xt': 'urn:btih:' + value})
    if value.startswith('magnet:?'):
        hashes = parse_qs(urlsplit(value).query).get('xt', [])
        if any(re.fullmatch(r'urn:btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', h) for h in hashes):
            return value
    raise HTTPException(status_code=400, detail='Enter a valid BitTorrent magnet link')


def is_torrent(value: str) -> bool:
    return value.startswith('magnet:') or bool(re.fullmatch(r'[a-fA-F0-9]{40}|[a-zA-Z2-7]{32}', value))


class TorrentService:
    def __init__(self):
        self.url = os.environ.get('JF_TORRSERVER_URL', 'http://127.0.0.1:8091').rstrip('/')
        self.external = bool(os.environ.get('JF_TORRSERVER_URL'))
        self.process = None
        self.lock = asyncio.Lock()
        self.log = None
        self.holds = Counter()
        self.applied_sharing = None

    def hold(self, url):
        self.holds[url] += 1

    def release(self, url):
        if self.holds[url] <= 1:
            self.holds.pop(url, None)
        else:
            self.holds[url] -= 1

    async def ensure_running(self):
        async with self.lock:
            async with httpx.AsyncClient(timeout=2) as client:
                try:
                    response = await client.get(self.url + '/echo')
                    if response.is_success and any(name in response.text for name in ('TorrServer', 'MatriX')):
                        return
                except httpx.HTTPError:
                    pass
            if self.external:
                raise HTTPException(status_code=503, detail='Cannot reach the configured TorrServer')
            binary = ROOT / 'data/torrserver/bin' / ('TorrServer.exe' if os.name == 'nt' else 'TorrServer')
            if not binary.exists():
                raise HTTPException(status_code=503, detail='P2P helper is missing. Run: python scripts/install_torrserver.py')
            if self.process is None or self.process.returncode is not None:
                folder = ROOT / 'data/torrserver'
                if self.log:
                    self.log.close()
                self.log = (folder / 'server.log').open('ab')
                self.applied_sharing = None
                self.process = await asyncio.create_subprocess_exec(
                    str(binary), '--ip', '127.0.0.1', '--port', '8091', '--path', str(folder),
                    stdout=self.log, stderr=self.log,
                )
            async with httpx.AsyncClient(timeout=2) as client:
                for _ in range(40):
                    if self.process.returncode is not None:
                        break
                    try:
                        response = await client.get(self.url + '/echo')
                        if response.is_success and any(name in response.text for name in ('TorrServer', 'MatriX')):
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.25)
            raise HTTPException(status_code=503, detail='P2P helper could not start. Check data/torrserver/server.log')

    @staticmethod
    def select_file(files, index=None):
        if index is not None:
            selected = next((f for f in files if f['id'] == index + 1), None)
            if selected is None or Path(selected['path']).suffix.lower() not in VIDEO_EXTENSIONS:
                raise HTTPException(status_code=400, detail='The selected torrent file is not a video')
            return selected
        videos = [f for f in files if Path(f['path']).suffix.lower() in VIDEO_EXTENSIONS]
        if not videos:
            raise HTTPException(status_code=400, detail='This torrent contains no supported video file')
        full = [f for f in videos if not re.search(r'(^|[ /._-])sample([ /._-]|$)', f['path'], re.I)]
        return max(full or videos, key=lambda f: f.get('length', 0))

    @staticmethod
    async def load_preferences():
        from ..database import AsyncSessionLocal
        from .settings_manager import SettingsManager

        async with AsyncSessionLocal() as db:
            settings = SettingsManager(db)
            return (
                bool(await settings.get('torrent_playback_enabled', True)),
                bool(await settings.get('torrent_allow_upload', False)),
                max(0, int(await settings.get('torrent_upload_limit_kbps', 0) or 0)),
            )

    async def apply_sharing(self, allow_upload: bool, upload_limit_kbps: int):
        """Push upload/UPnP preferences into the bundled TorrServer when they change.

        An external TorrServer (JF_TORRSERVER_URL) keeps its own settings.
        """
        wanted = (allow_upload, upload_limit_kbps)
        if self.external or self.applied_sharing == wanted:
            return
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(self.url + '/settings', json={'action': 'get'})
            response.raise_for_status()
            sets = response.json()
            sets.update({
                'DisableUpload': not allow_upload,
                'DisableUPNP': not allow_upload,
                'UploadRateLimit': upload_limit_kbps if allow_upload else 0,
            })
            response = await client.post(self.url + '/settings', json={'action': 'set', 'sets': sets})
            response.raise_for_status()
        self.applied_sharing = wanted

    async def resolve(self, magnet: str) -> str:
        enabled, allow_upload, upload_limit = await self.load_preferences()
        if not enabled:
            raise HTTPException(status_code=403, detail='Torrent playback is turned off in Settings')
        magnet = normalize_magnet(magnet)
        selected = parse_qs(urlsplit(magnet).query).get('so', [None])[0]
        if selected is not None and not selected.isdigit():
            raise HTTPException(status_code=400, detail='Choose a single torrent file index')
        await self.ensure_running()
        try:
            await self.apply_sharing(allow_upload, upload_limit)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail='Could not apply upload settings to the P2P helper') from exc
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(self.url + '/torrents', json={'action': 'add', 'link': magnet, 'save_to_db': False})
                response.raise_for_status()
                status = response.json()
                hash_ = status.get('hash')
                if not hash_:
                    raise HTTPException(status_code=502, detail='Torrent helper returned no torrent hash')
                deadline = time.monotonic() + 90
                while not status.get('file_stats'):
                    if time.monotonic() >= deadline:
                        raise HTTPException(status_code=504, detail='No torrent metadata arrived. This magnet may have no reachable peers.')
                    await asyncio.sleep(1)
                    response = await client.post(self.url + '/torrents', json={'action': 'get', 'hash': hash_})
                    response.raise_for_status()
                    status = response.json()
                file = self.select_file(status['file_stats'], int(selected) if selected is not None else None)
                return self.url + '/stream/' + quote(Path(file['path']).name, safe='') + '?' + urlencode({'link': hash_, 'index': file['id'], 'play': ''})
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail='The P2P helper could not resolve this torrent') from exc

    async def stop_unused(self, input_url, active_inputs):
        if self.process is None or not input_url or not input_url.startswith(self.url + '/stream/'):
            return
        hash_ = parse_qs(urlsplit(input_url).query).get('link', [None])[0]
        if any(parse_qs(urlsplit(url).query).get('link', [None])[0] == hash_ for url in list(active_inputs) + list(self.holds)):
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(self.url + '/torrents', json={'action': 'drop', 'hash': hash_})
        except httpx.HTTPError:
            pass

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.log:
            self.log.close()
            self.log = None
        self.process = None


torrent_service = TorrentService()
