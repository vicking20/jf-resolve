"""Settings management service"""

import json
import os
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.setting import Setting


class SettingsManager:
    """Manage application settings with environment variable overrides"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._cache: Dict[str, Any] = {}

    async def load_cache(self):
        """Load all settings into cache"""
        result = await self.db.execute(select(Setting))
        settings = result.scalars().all()

        for setting in settings:
            try:
                self._cache[setting.key] = (
                    json.loads(setting.value) if setting.value else None
                )
            except json.JSONDecodeError:
                self._cache[setting.key] = setting.value

    async def get(self, key: str, default: Any = None) -> Any:
        """Get setting value with environment variable override"""
        # Check environment variable override
        env_key = key.upper()
        env_value = os.getenv(env_key)
        if env_value is not None:
            try:
                return json.loads(env_value)
            except (json.JSONDecodeError, TypeError):
                return env_value

        # Check cache
        if key in self._cache:
            return self._cache[key]

        # Query database
        result = await self.db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()

        if setting:
            try:
                value = json.loads(setting.value) if setting.value else default
            except json.JSONDecodeError:
                value = setting.value or default

            self._cache[key] = value
            return value

        return default

    async def set(self, key: str, value: Any):
        """Set setting value"""
        # Serialize value
        if isinstance(value, (dict, list)):
            json_value = json.dumps(value)
        elif isinstance(value, bool):
            json_value = json.dumps(value)
        else:
            json_value = str(value)

        # Upsert in database
        result = await self.db.execute(select(Setting).where(Setting.key == key))
        setting = result.scalar_one_or_none()

        if setting:
            setting.value = json_value
        else:
            setting = Setting(key=key, value=json_value)
            self.db.add(setting)

        await self.db.commit()

        # Update cache
        self._cache[key] = value

    async def get_all(self) -> Dict[str, Any]:
        """Get all settings"""
        if not self._cache:
            await self.load_cache()
        return self._cache.copy()

    async def update_many(self, settings: Dict[str, Any]):
        """Update multiple settings at once"""
        for key, value in settings.items():
            await self.set(key, value)
