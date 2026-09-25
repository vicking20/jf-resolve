import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


from .media_item import MediaType


class MediaSourceModel(Base):
    """
    Track media source files (local files, STRM files, etc.)
    Links to MediaItem via uuid
    """

    __tablename__ = "media_sources"

    id = Column(Integer, primary_key=True, index=True)

    # Link to media_items
    media_uuid = Column(String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True)
    
    # Library association
    library_path_id = Column(
        Integer, ForeignKey("library_paths.id", ondelete="SET NULL"), nullable=True
    )

    # Source info
    # source_type: local, strm, url, stream (addon)
    source_type = Column(String(20), default="local")
    file_path = Column(String(1000), nullable=True, index=True)  # For local/strm files
    stream_url = Column(String(2000), nullable=True)  # Direct URL for url/stream types
    stream_title = Column(String(255), nullable=True)  # Display name for the source
    file_size = Column(BigInteger, nullable=True)
    duration = Column(Float, nullable=True)  # Duration in seconds

    # Quality info
    resolution = Column(String(20), nullable=True)  # 2160p, 1080p, 720p, etc.
    codec = Column(String(50), nullable=True)
    bitrate = Column(Integer, nullable=True)

    # Availability
    is_available = Column(Boolean, default=True)
    last_verified = Column(DateTime(timezone=True), nullable=True)

    # Priority
    priority = Column(
        Integer, default=0
    )  # Higher = preferred (0 = local/strm, 1 = streams)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "media_uuid": self.media_uuid,
            "source_type": self.source_type,
            "file_path": self.file_path,
            "stream_url": self.stream_url,
            "stream_title": self.stream_title,
            "file_size": self.file_size,
            "duration": self.duration,
            "resolution": self.resolution,
            "codec": self.codec,
            "is_available": self.is_available,
        }

    @property
    def playback_url(self) -> str:
        """Get the URL to use for playback"""
        if self.source_type == "local":
            return self.file_path
        elif self.source_type == "strm":
            # STRM file - read URL from file
            return self.file_path  # Resolved at playback time
        elif self.source_type in ("url", "stream"):
            return self.stream_url
        return self.file_path or self.stream_url
class LibraryScanJob(Base):
    """
    Track background library scan jobs
    """

    __tablename__ = "library_scan_jobs"

    id = Column(Integer, primary_key=True, index=True)

    # Scanned path (null = all paths)
    library_path_id = Column(
        Integer, ForeignKey("library_paths.id", ondelete="SET NULL"), nullable=True
    )

    # Status
    # status: pending, running, completed, failed
    status = Column(String(20), default="pending", index=True)
    cancelled = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)

    # Progress stats
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    matched_files = Column(Integer, default=0)
    failed_files = Column(Integer, default=0)
    new_media_added = Column(Integer, default=0)
    existing_media_updated = Column(Integer, default=0)

    # Performance
    start_time = Column(Float, nullable=True)  # time.time()
    end_time = Column(Float, nullable=True)

    # Ownership and Lifecycle
    triggered_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "library_path_id": self.library_path_id,
            "status": self.status,
            "cancelled": self.cancelled,
            "error_message": self.error_message,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "matched_files": self.matched_files,
            "failed_files": self.failed_files,
            "new_media_added": self.new_media_added,
            "existing_media_updated": self.existing_media_updated,
            "progress_percentage": (self.processed_files / self.total_files * 100)
            if self.total_files > 0
            else 0,
            "started_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
