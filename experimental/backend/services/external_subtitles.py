"""External subtitle files from subtitle plugins.

Plugin URLs are handed to the browser as signed tokens, so the download
endpoint only ever fetches URLs a plugin returned.
"""

import httpx
from fastapi import HTTPException
from itsdangerous import BadData, URLSafeTimedSerializer

from ..core.config import settings
from ..core.url_guard import validate_source_url

MAX_BYTES = 5 * 1024 * 1024
TOKEN_MAX_AGE = 12 * 3600

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="external-subtitle")


def sign(url: str) -> str:
    return _serializer.dumps(url)


def unsign(token: str) -> str:
    try:
        return _serializer.loads(token, max_age=TOKEN_MAX_AGE)
    except BadData:
        raise HTTPException(status_code=400, detail="Subtitle link expired; reopen the subtitle menu")


def decode(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


async def fetch(token: str, user) -> str:
    url = await validate_source_url(unsign(token), user)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content = b""
                async for chunk in response.aiter_bytes():
                    content += chunk
                    if len(content) > MAX_BYTES:
                        raise HTTPException(status_code=413, detail="Subtitle file is too large")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not download the subtitle file")
    text = decode(content)
    if "-->" not in text:
        raise HTTPException(status_code=422, detail="Subtitle file is not SRT or VTT")
    return text
