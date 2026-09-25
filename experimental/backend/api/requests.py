"""Title requests for the Jellyfin STRM library.

Requests only exist while STRM output is on (Jellyfin or Hybrid profile).
Admin requests are fulfilled immediately; everyone else's wait for an admin.
Restricted users may request titles above their rating so an admin can make
the call, but the web app's age gate still applies to playback here, and
Jellyfin's own parental controls govern the STRM library.
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user, get_current_user
from ..database import get_db
from ..models.media_request import MediaRequest
from ..models.user import User
from ..services.settings_manager import SettingsManager
from ..services.strm_service import StrmService
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/requests", tags=["requests"])


class RequestCreate(BaseModel):
    tmdb_id: int
    media_type: Literal["movie", "tv"]


class RequestDecision(BaseModel):
    note: Optional[str] = None


async def _require_strm_mode(settings: SettingsManager) -> None:
    if not await settings.get("strm_generation_enabled", False):
        raise HTTPException(
            status_code=409,
            detail="Requests are only available when STRM output is enabled (Jellyfin or Hybrid mode)",
        )


async def _fulfil(db: AsyncSession, settings: SettingsManager, req: MediaRequest, admin: User) -> None:
    """Write the STRM files and record the outcome on the request."""
    tmdb = TMDBService(await settings.get("tmdb_api_key") or "")
    try:
        await StrmService(settings, tmdb).generate(req.tmdb_id, req.media_type)
        req.status, req.note = "approved", None
    except Exception as e:
        req.status, req.note = "failed", str(e)
    finally:
        await tmdb.close()
    req.decided_by = admin.id
    req.decided_at = datetime.now(timezone.utc)
    await db.commit()


async def _get_request(db: AsyncSession, request_id: int) -> MediaRequest:
    req = await db.get(MediaRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    return req


@router.post("")
async def create_request(
    data: RequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    settings = SettingsManager(db)
    await _require_strm_mode(settings)

    existing = (
        await db.execute(
            select(MediaRequest).where(
                MediaRequest.user_id == current_user.id,
                MediaRequest.tmdb_id == data.tmdb_id,
                MediaRequest.media_type == data.media_type,
            )
        )
    ).scalar_one_or_none()
    if existing and existing.status in ("pending", "approved"):
        return existing.to_dict()

    tmdb = TMDBService(await settings.get("tmdb_api_key") or "")
    try:
        details = await tmdb.get_details(data.tmdb_id, data.media_type)
        parsed = tmdb.parse_media_item(details, data.media_type) if details else None
    finally:
        await tmdb.close()
    if not parsed:
        raise HTTPException(status_code=400, detail="Could not load this title from TMDB")

    req = existing or MediaRequest(user_id=current_user.id, tmdb_id=data.tmdb_id, media_type=data.media_type)
    req.title = parsed["title"] or "Unknown"
    req.year = parsed.get("year")
    req.poster_path = parsed.get("poster_path")
    req.content_rating = parsed.get("content_rating")
    req.status, req.note, req.decided_by, req.decided_at = "pending", None, None, None
    if not existing:
        db.add(req)
    await db.commit()

    if current_user.is_superuser:
        await _fulfil(db, settings, req, current_user)
    await db.refresh(req)
    return req.to_dict()


@router.get("/status")
async def request_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Whether requests are available, for showing the Request button."""
    enabled = bool(await SettingsManager(db).get("strm_generation_enabled", False))
    return {"enabled": enabled, "is_admin": current_user.is_superuser}


@router.get("")
async def list_requests(
    all_users: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's own requests; admins can pass all_users=true to review everyone's."""
    stmt = select(MediaRequest, User.username).join(User, User.id == MediaRequest.user_id)
    if not (all_users and current_user.is_superuser):
        stmt = stmt.where(MediaRequest.user_id == current_user.id)
    rows = (await db.execute(stmt.order_by(MediaRequest.created_at.desc()))).all()
    return [{**req.to_dict(), "username": username} for req, username in rows]


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    settings = SettingsManager(db)
    await _require_strm_mode(settings)
    req = await _get_request(db, request_id)
    await _fulfil(db, settings, req, admin)
    return req.to_dict()


@router.post("/{request_id}/deny")
async def deny_request(
    request_id: int,
    decision: RequestDecision,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    req = await _get_request(db, request_id)
    req.status, req.note = "denied", decision.note
    req.decided_by = admin.id
    req.decided_at = datetime.now(timezone.utc)
    await db.commit()
    return req.to_dict()


@router.delete("/{request_id}")
async def cancel_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Users can withdraw their own pending requests; admins can delete any."""
    req = await _get_request(db, request_id)
    if req.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending" and not current_user.is_superuser:
        raise HTTPException(status_code=400, detail="Only pending requests can be withdrawn")
    await db.delete(req)
    await db.commit()
    return {"message": "Request removed"}
