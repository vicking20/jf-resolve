"""Pydantic schemas for validation"""

from .auth import Token, UserCreate, UserLogin, UserResponse
from .library import LibraryItemCreate, LibraryItemList, LibraryItemResponse
from .search import MediaItem, SearchResult
from .settings import SettingsResponse, SettingsUpdate

__all__ = [
    "Token",
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "LibraryItemCreate",
    "LibraryItemResponse",
    "LibraryItemList",
    "SearchResult",
    "MediaItem",
    "SettingsUpdate",
    "SettingsResponse",
]
