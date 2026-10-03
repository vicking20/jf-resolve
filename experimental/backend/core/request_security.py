"""Client identity behind reverse proxies, and login brute-force throttling."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from .config import settings


def _trusted_proxies() -> set[str]:
    return {ip.strip() for ip in settings.TRUSTED_PROXIES.split(",") if ip.strip()}


def _from_trusted_proxy(request: Request) -> bool:
    return bool(request.client) and request.client.host in _trusted_proxies()


def client_ip(request: Request) -> str:
    """Real client IP; forwarding headers are only honoured from trusted proxies."""
    peer = request.client.host if request.client else "unknown"
    if _from_trusted_proxy(request):
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
            "x-forwarded-for", ""
        ).split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return _from_trusted_proxy(request) and request.headers.get("x-forwarded-proto") == "https"


class LoginThrottle:
    """In-memory sliding window of failed logins per client IP and per username."""

    WINDOW_SECONDS = 15 * 60
    MAX_FAILURES_PER_IP = 10
    MAX_FAILURES_PER_USERNAME = 20

    def __init__(self):
        self._failures: dict[str, deque] = defaultdict(deque)

    def _recent(self, key: str, now: float) -> deque:
        entries = self._failures[key]
        while entries and now - entries[0] > self.WINDOW_SECONDS:
            entries.popleft()
        if not entries:
            self._failures.pop(key, None)
        return entries

    def check(self, ip: str, username: str) -> None:
        now = time.monotonic()
        ip_entries = self._recent(f"ip:{ip}", now)
        user_entries = self._recent(f"user:{username.lower()}", now)
        if len(ip_entries) >= self.MAX_FAILURES_PER_IP or len(user_entries) >= self.MAX_FAILURES_PER_USERNAME:
            oldest = min(e[0] for e in (ip_entries, user_entries) if e)
            retry_after = max(1, int(self.WINDOW_SECONDS - (now - oldest)))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    def record_failure(self, ip: str, username: str) -> None:
        now = time.monotonic()
        if len(self._failures) > 10_000:
            for key in list(self._failures):
                self._recent(key, now)
        self._failures[f"ip:{ip}"].append(now)
        self._failures[f"user:{username.lower()}"].append(now)

    def reset_username(self, username: str) -> None:
        self._failures.pop(f"user:{username.lower()}", None)


login_throttle = LoginThrottle()
