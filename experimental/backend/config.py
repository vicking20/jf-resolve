"""Configuration management"""

import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

    # Secret Key
    SECRET_KEY: str = "change-this-to-a-random-secret-key"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/jfresolve.db"

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    ALGORITHM: str = "HS256"

    # Directories
    BASE_DIR: Path = Path(__file__).parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    LOGS_DIR: Path = DATA_DIR / "logs"
    TEMPLATES_DIR: Path = BASE_DIR / "frontend" / "templates"
    STATIC_DIR: Path = BASE_DIR / "frontend" / "static"
    SETUP_FLAG_FILE: Path = DATA_DIR / ".setup_complete"

    # Optional overrides (can be configured in UI)
    HOST: str = "0.0.0.0"
    PORT: int = 8765
    TMDB_API_KEY: Optional[str] = None
    STREMIO_MANIFEST_URL: Optional[str] = None
    ALLOWED_ORIGINS: Optional[str] = None  # Comma-separated origins for main API
    TRUSTED_PROXIES: Optional[str] = None  # Read by core.config; declared so .env validates

    class Config:
        env_file = ".env"
        case_sensitive = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure directories exist
        self.DATA_DIR.mkdir(exist_ok=True)
        self.LOGS_DIR.mkdir(exist_ok=True)


# Global settings instance
settings = Settings()
