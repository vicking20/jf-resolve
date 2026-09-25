"""Settings repository for user-configurable settings"""

import json
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.setting import Setting
from .base import BaseRepository


class SettingsRepository(BaseRepository[Setting]):
    """Repository for application settings"""

    def __init__(self, db: AsyncSession):
        super().__init__(Setting, db)

    async def get_setting(self, key: str) -> Optional[Setting]:
        """
        Get a setting by key

        Args:
            key: Setting key

        Returns:
            Setting instance or None
        """
        result = await self.db.execute(select(Setting).where(Setting.key == key))
        return result.scalar_one_or_none()

    async def get_value(self, key: str, default: Any = None) -> Any:
        """
        Get setting value (deserialize JSON if needed)

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        setting = await self.get_setting(key)

        if setting is None:
            return default

        # Try to deserialize JSON
        try:
            return json.loads(setting.value)
        except (json.JSONDecodeError, TypeError):
            return setting.value

    async def set_value(self, key: str, value: Any) -> Setting:
        """
        Set setting value (serialize to JSON if needed)

        Args:
            key: Setting key
            value: Setting value

        Returns:
            Setting instance
        """
        # Serialize value if it's not a string
        if not isinstance(value, str):
            value = json.dumps(value)

        setting = await self.get_setting(key)

        if setting:
            # Update existing
            setting.value = value
            await self.db.commit()
            await self.db.refresh(setting)
        else:
            # Create new
            setting = await self.create(key=key, value=value)

        return setting

    async def delete_setting(self, key: str) -> bool:
        """
        Delete a setting

        Args:
            key: Setting key

        Returns:
            True if deleted, False if not found
        """
        return await self.delete_by(key=key) > 0

    async def get_all_settings(self) -> Dict[str, Any]:
        """
        Get all settings as a dictionary

        Returns:
            Dictionary of key-value pairs
        """
        settings = await self.get_all()

        result = {}
        for setting in settings:
            try:
                result[setting.key] = json.loads(setting.value)
            except (json.JSONDecodeError, TypeError):
                result[setting.key] = setting.value

        return result

    async def bulk_set(self, settings_dict: Dict[str, Any]) -> None:
        """
        Set multiple settings at once

        Args:
            settings_dict: Dictionary of key-value pairs
        """
        for key, value in settings_dict.items():
            await self.set_value(key, value)

    async def exists_key(self, key: str) -> bool:
        """
        Check if a setting key exists

        Args:
            key: Setting key

        Returns:
            True if exists, False otherwise
        """
        return await self.exists(key=key)
