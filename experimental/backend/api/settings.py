"""Settings API routes"""

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..models.user import User
from ..schemas.settings import SettingsResponse, SettingsUpdate
from ..services.settings_manager import SettingsManager

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ScannerModeRequest(BaseModel):
    mode: Literal["lazy", "active"]


class OperationProfileRequest(BaseModel):
    profile: Literal["standalone", "jellyfin", "hybrid"]


@router.get("/")
async def get_all_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Get all settings"""
    settings = SettingsManager(db)
    await settings.load_cache()

    all_settings = await settings.get_all()

    return SettingsResponse(settings=all_settings)


@router.put("/")
async def update_settings(
    data: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Update multiple settings at once"""
    settings = SettingsManager(db)

    try:
        await settings.update_many(data.settings)
        updated = await settings.get_all()

        return SettingsResponse(settings=updated)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update settings: {str(e)}"
        )


@router.put("/operation-profile")
async def set_operation_profile(
    data: OperationProfileRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Apply one of the supported deployment profiles."""
    from ..services.operation_profile import apply_operation_profile

    settings = SettingsManager(db)
    applied = await apply_operation_profile(settings, data.profile)
    return {"profile": data.profile, "settings": applied}


@router.get("/scanner-mode")
async def get_scanner_mode(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Get current scanner mode configuration"""
    from ..services.library import ScannerModeManager

    mode_manager = ScannerModeManager(db)
    mode = await mode_manager.get_global_mode()
    description = await mode_manager.get_mode_description(mode)

    return {"mode": mode, "description": description}


@router.post("/scanner-mode")
async def set_scanner_mode(
    data: ScannerModeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Set scanner mode and reconfigure file watchers"""
    from ..services.library import create_scanner_coordinator
    from ..services.tmdb_service import TMDBService

    settings = SettingsManager(db)
    tmdb_key = await settings.get("tmdb_api_key")
    tmdb = TMDBService(tmdb_key) if tmdb_key else None

    try:
        coordinator = await create_scanner_coordinator(db, tmdb)
        await coordinator.set_scanner_mode(data.mode)

        return {
            "mode": data.mode,
            "message": f"Scanner mode changed to {data.mode}",
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update scanner mode: {str(e)}"
        )


@router.get("/{key}")
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Get specific setting"""
    settings = SettingsManager(db)
    value = await settings.get(key)

    return {"key": key, "value": value}


@router.put("/{key}")
async def update_setting(
    key: str,
    value: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Update specific setting"""
    settings = SettingsManager(db)

    try:
        await settings.set(key, value.get("value"))
        updated_value = await settings.get(key)

        return {"key": key, "value": updated_value}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update setting: {str(e)}"
        )
