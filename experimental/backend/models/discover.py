from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class DiscoverItem(Base):
    """
    Stores references to trending/popular media from TMDB for the Discover page.
    Links to MediaItem to allow reuse of existing detail and playback logic.
    """
    __tablename__ = "discover_items"

    id = Column(Integer, primary_key=True, index=True)
    media_uuid = Column(String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    
    tmdb_page = Column(Integer, nullable=False, default=1)
    
    cached_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    media_item = relationship("MediaItem")

    def __repr__(self):
        return f"<DiscoverItem category='{self.category}' pos={self.position} media={self.media_uuid}>"
