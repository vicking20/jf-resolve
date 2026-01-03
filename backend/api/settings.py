"""Settings API routes"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user
from ..database import get_db
from ..models.user import User
from ..schemas.settings import SettingsResponse, SettingsUpdate
from ..services.scheduler_service import scheduler_service
from ..services.settings_manager import SettingsManager

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/", response_model=SettingsResponse)
async def get_all_settings(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Get all settings"""
    settings = SettingsManager(db)
    await settings.load_cache()

    all_settings = await settings.get_all()

    return SettingsResponse(settings=all_settings)


@router.put("/", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update multiple settings at once"""
    settings = SettingsManager(db)

    try:
        await settings.update_many(data.settings)
        updated = await settings.get_all()

        # Reconfigure scheduler if auto-populate or series update settings changed
        schedule_keys = {
            "auto_populate_enabled",
            "populate_frequency",
            "series_update_enabled",
            "series_update_frequency",
        }
        if schedule_keys.intersection(data.settings.keys()):
            await scheduler_service.configure_jobs()

        return SettingsResponse(settings=updated)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to update settings: {str(e)}"
        )


@router.get("/{key}")
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
