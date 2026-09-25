"""Validation for user-supplied playback/download source URLs.

Endpoints such as /api/playback/proxy accept a URL chosen by the browser. Without
checks, a signed-in user could make the server read local files (``/etc/passwd``,
``.env``) or fetch services on the private network. Only remote HTTP(S) URLs and
magnet links are accepted, and non-admins may not target private addresses.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from ..services.torrent_service import is_torrent


def _is_private_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global


async def _host_is_private(host: str) -> bool:
    try:
        return _is_private_address(host)
    except ValueError:
        pass  # Not an IP literal; resolve the hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False  # Unresolvable hosts cannot be fetched; let the request fail normally
    return any(_is_private_address(info[4][0]) for info in infos)


async def validate_source_url(url: str, user) -> str:
    """Return ``url`` if it is an acceptable remote source, otherwise raise 400/403."""
    url = (url or "").strip()
    if is_torrent(url):
        return url

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only HTTP(S) sources or magnet links are allowed")

    # Admins can already run code via plugins, so they may use LAN-hosted addons.
    if not user.is_superuser and await _host_is_private(parts.hostname):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sources on private network addresses are not allowed")
    return url
