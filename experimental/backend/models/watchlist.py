from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class WatchlistItem(Base):
    """
    User watchlist mapping user_id to media_uuid
    """
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    media_uuid = Column(String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True)
    
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "media_uuid", name="uix_user_watchlist"),
    )

    user = relationship("User")
    media_item = relationship("MediaItem", back_populates="watchlist_entries")

    def __repr__(self):
        return f"<WatchlistItem user={self.user_id} media={self.media_uuid}>"
