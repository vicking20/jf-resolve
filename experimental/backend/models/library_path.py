"""Library path models for local media management"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.sql import func

from ..core.database import Base
from ..models.media import MediaType


class LibraryPath(Base):
    """
    User-configured library paths

    Points to directories containing local media files
    """

    __tablename__ = "library_paths"

    id = Column(Integer, primary_key=True, index=True)

    # Path details
    name = Column(String(100), nullable=False)  # "Movies", "TV Shows", "Anime"
    path = Column(String(500), nullable=False)  # "/mnt/media/movies"
    media_type = Column(SQLEnum(MediaType), nullable=False)  # movie, tv, or video

    # Status
    enabled = Column(Boolean, default=True, nullable=False)

    # Scanning
    scan_interval = Column(Integer, default=0)  # Minutes, 0 = manual only
    last_scanned = Column(DateTime(timezone=True))
    scan_mode_override = Column(
        String(20), nullable=True
    )  # Override global mode: "lazy" or "active"
    watch_enabled = Column(
        Boolean, default=True, nullable=False
    )  # Enable file watching

    # Owner
    created_by_user_id = Column(Integer, ForeignKey("users.id"))

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<LibraryPath {self.name} ({self.path})>"

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "media_type": self.media_type.value
            if hasattr(self.media_type, "value")
            else str(self.media_type),
            "enabled": self.enabled,
            "scan_interval": self.scan_interval,
            "scan_mode_override": self.scan_mode_override,
            "watch_enabled": self.watch_enabled,
            "last_scanned": self.last_scanned.isoformat()
            if self.last_scanned
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
