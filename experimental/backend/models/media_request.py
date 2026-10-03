"""User requests for titles to be added to the Jellyfin STRM library"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from ..core.database import Base


class MediaRequest(Base):
    """A user's request for a title; approval writes its STRM files"""

    __tablename__ = "media_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    media_type = Column(String(10), nullable=False)
    title = Column(String(500), nullable=False)
    year = Column(Integer, nullable=True)
    poster_path = Column(String(500), nullable=True)
    content_rating = Column(String(20), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    note = Column(Text, nullable=True)
    decided_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "tmdb_id", "media_type", name="uix_user_media_request"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tmdb_id": self.tmdb_id,
            "media_type": self.media_type,
            "title": self.title,
            "year": self.year,
            "poster_path": self.poster_path,
            "content_rating": self.content_rating,
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
        }
