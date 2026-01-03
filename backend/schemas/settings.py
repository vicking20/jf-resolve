"""Settings schemas"""

from typing import Any, Dict

from pydantic import BaseModel


class SettingsUpdate(BaseModel):
    """Update settings"""

    settings: Dict[str, Any]


class SettingsResponse(BaseModel):
    """Settings response"""

    settings: Dict[str, Any]
