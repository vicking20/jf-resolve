"""Enhanced configuration system with hybrid Pydantic + DB support"""

import os
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseType(str, Enum):
    """Supported database types"""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class AppConfig(BaseSettings):
    """
    Application configuration

    Hybrid approach:
    - System config (immutable) → Pydantic (env vars)
    - User preferences (mutable) → Database (via SettingsManager)
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ===== Core System Settings (Immutable) =====
    SECRET_KEY: str = Field(
        default="change-this-to-a-random-secret-key",
        description="JWT secret key - CHANGE THIS IN PRODUCTION",
    )

    # Database
    DATABASE_TYPE: DatabaseType = Field(
        default=DatabaseType.SQLITE, description="Database type (sqlite or postgresql)"
    )

    # SQLite config
    SQLITE_PATH: str = Field(
        default="./data/jfresolve.db", description="SQLite database path"
    )

    # PostgreSQL config (optional, for advanced users)
    POSTGRES_HOST: Optional[str] = Field(default=None, description="PostgreSQL host")
    POSTGRES_PORT: int = Field(default=5432, description="PostgreSQL port")
    POSTGRES_USER: Optional[str] = Field(default=None, description="PostgreSQL user")
    POSTGRES_PASSWORD: Optional[str] = Field(
        default=None, description="PostgreSQL password"
    )
    POSTGRES_DB: Optional[str] = Field(
        default=None, description="PostgreSQL database name"
    )

    # JWT
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=1440, description="Access token expiry (24 hours)"
    )
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(
        default=30, description="Refresh token expiry (30 days)"
    )
    ALGORITHM: str = Field(default="HS256", description="JWT algorithm")

    # Server
    HOST: str = Field(default="0.0.0.0", description="Main server host")
    PORT: int = Field(default=8765, description="Main server port")

    # Directories
    BASE_DIR: Path = Field(
        default_factory=lambda: Path(__file__).parent.parent.parent,
        description="Base directory",
    )

    # CORS
    ALLOWED_ORIGINS: str = Field(default="", description="CORS allowed origins (empty disables CORS)")

    # Reverse proxies (e.g. the cloudflared host) whose forwarding headers are trusted
    # for the client IP and HTTPS detection. Comma-separated IPs.
    TRUSTED_PROXIES: str = Field(
        default="127.0.0.1,::1", description="Trusted reverse proxy IPs"
    )

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = Field(default=True, description="Enable rate limiting")
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=60, description="Max requests per minute per IP"
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    LOG_TO_FILE: bool = Field(default=True, description="Enable file logging")

    # ===== Computed/Derived Properties =====

    @property
    def DATA_DIR(self) -> Path:
        """Data directory path"""
        path = self.BASE_DIR / "data"
        path.mkdir(exist_ok=True)
        return path

    @property
    def LOGS_DIR(self) -> Path:
        """Logs directory path"""
        path = self.DATA_DIR / "logs"
        path.mkdir(exist_ok=True)
        return path

    @property
    def FRONTEND_DIR(self) -> Path:
        """Frontend directory path"""
        return self.BASE_DIR / "frontend"

    @property
    def TEMPLATES_DIR(self) -> Path:
        """Templates directory path"""
        # Check new structure first, fallback to old
        new_path = self.FRONTEND_DIR / "templates"
        old_path = self.BASE_DIR / "templates"
        return new_path if new_path.exists() else old_path

    @property
    def STATIC_DIR(self) -> Path:
        """Static files directory path"""
        # Check new structure first, fallback to old
        new_path = self.FRONTEND_DIR / "static"
        old_path = self.BASE_DIR / "static"
        return new_path if new_path.exists() else old_path

    @property
    def SETUP_FLAG_FILE(self) -> Path:
        """Setup completion flag file"""
        return self.DATA_DIR / ".setup_complete"

    @property
    def DATABASE_URL(self) -> str:
        """
        Construct database URL based on type

        Returns async-compatible URL for SQLAlchemy
        """
        if self.DATABASE_TYPE == DatabaseType.SQLITE:
            # Convert relative to absolute path
            db_path = Path(self.SQLITE_PATH)
            if not db_path.is_absolute():
                db_path = self.BASE_DIR / db_path

            # Ensure parent directory exists
            db_path.parent.mkdir(parents=True, exist_ok=True)

            return f"sqlite+aiosqlite:///{db_path}"

        elif self.DATABASE_TYPE == DatabaseType.POSTGRESQL:
            if not all(
                [
                    self.POSTGRES_HOST,
                    self.POSTGRES_USER,
                    self.POSTGRES_PASSWORD,
                    self.POSTGRES_DB,
                ]
            ):
                raise ValueError(
                    "PostgreSQL requires: POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB"
                )

            return (
                f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

        else:
            raise ValueError(f"Unsupported database type: {self.DATABASE_TYPE}")

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """
        Synchronous database URL for migrations and scripts

        Returns:
            Sync-compatible URL (without async drivers)
        """
        if self.DATABASE_TYPE == DatabaseType.SQLITE:
            db_path = Path(self.SQLITE_PATH)
            if not db_path.is_absolute():
                db_path = self.BASE_DIR / db_path
            return f"sqlite:///{db_path}"

        elif self.DATABASE_TYPE == DatabaseType.POSTGRESQL:
            return (
                f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Warn if using default secret key"""
        if v == "change-this-to-a-random-secret-key":
            import warnings

            warnings.warn(
                "Using default SECRET_KEY! Generate a secure key for production.",
                UserWarning,
            )
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v_upper

    def is_production(self) -> bool:
        """Check if running in production mode"""
        return os.getenv("ENVIRONMENT", "development").lower() == "production"

    def is_development(self) -> bool:
        """Check if running in development mode"""
        return not self.is_production()


# Global config instance
settings = AppConfig()


# Export commonly used paths for backward compatibility
DATABASE_URL = settings.DATABASE_URL
SECRET_KEY = settings.SECRET_KEY
TEMPLATES_DIR = settings.TEMPLATES_DIR
STATIC_DIR = settings.STATIC_DIR
DATA_DIR = settings.DATA_DIR
LOGS_DIR = settings.LOGS_DIR
