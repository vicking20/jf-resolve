"""Playback tracking models"""

from datetime import timedelta

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class PlaybackSession(Base):
    """
    Track user playback session for all unified media items.
    """

    __tablename__ = "playback_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    media_uuid = Column(
        String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True
    )
    
    media_type = Column(String(20), nullable=False, index=True)

    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)

    current_time = Column(Float, default=0.0)
    duration = Column(Float, nullable=True)

    last_source_type = Column(String(20), nullable=True)
    last_source_quality = Column(String(50), nullable=True)
    last_audio_track = Column(Integer, nullable=True)

    quality = Column(String(50), nullable=True)
    device = Column(String(100), nullable=True)
    completed = Column(Boolean, default=False)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
 
    user = relationship("User", back_populates="playback_sessions")
    media_item = relationship("MediaItem")

    @property
    def expires_at(self):
        return self.updated_at + timedelta(days=30) if self.updated_at else None

    @property
    def progress_percentage(self) -> float:
        if not self.duration or self.duration == 0:
            return 0.0
        return min(100.0, (self.current_time / self.duration) * 100)

    def to_dict(self):
        return {
            "id": self.id,
            "media_uuid": self.media_uuid,
            "media_type": self.media_type,
            "season": self.season,
            "episode": self.episode,
            "current_time": self.current_time,
            "duration": self.duration,
            "progress_percentage": self.progress_percentage,
            "completed": self.completed,
            "quality": self.quality,
            "device": self.device,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class SkipSegment(Base):
    """
    Store skip segments for content (intro, outro, credits)
    Linked to unified MediaItem
    """

    __tablename__ = "skip_segments"

    id = Column(Integer, primary_key=True, index=True)

    media_uuid = Column(
        String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True
    )
    
    media_type = Column(String(20), nullable=False, index=True)

    season = Column(Integer, nullable=True)
    episode = Column(Integer, nullable=True)

    segment_type = Column(String(50), nullable=False)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)

    confidence = Column(Float, default=1.0)
    source = Column(String(100))

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self):
        return {
            "id": self.id,
            "media_uuid": self.media_uuid,
            "media_type": self.media_type,
            "season": self.season,
            "episode": self.episode,
            "segment_type": self.segment_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "confidence": self.confidence,
        }


