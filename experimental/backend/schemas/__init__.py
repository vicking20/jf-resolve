"""Pydantic schemas for validation"""

from .auth import Token, UserCreate, UserLogin, UserResponse
from .media import MediaCreate, MediaList, MediaResponse
from .search import MediaItem, SearchResult
from .settings import SettingsResponse, SettingsUpdate

__all__ = [
    "Token",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "MediaCreate",
    "MediaResponse",
    "MediaList",
    "SearchResult",
    "MediaItem",
    "SettingsUpdate",
    "SettingsResponse",
]
