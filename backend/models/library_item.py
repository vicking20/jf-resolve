"""Library item model"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from ..database import Base


class LibraryItem(Base):
    """Library item (movie or TV show)"""

    __tablename__ = "library_items"

    id = Column(Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, nullable=False, index=True)
    imdb_id = Column(String(20), index=True)
    media_type = Column(String(10), nullable=False, index=True)  # 'movie' or 'tv'
    title = Column(String(255), nullable=False)
    year = Column(Integer)
    poster_path = Column(Text)
    backdrop_path = Column(Text)
    overview = Column(Text)

    # For TV shows
    total_seasons = Column(Integer)
    total_episodes = Column(Integer)
    last_season_checked = Column(Integer, default=0)
    last_episode_checked = Column(Integer, default=0)

    # STRM file tracking
    folder_path = Column(Text, nullable=False)
    quality_versions = Column(Text)  # JSON array: ["1080p", "4k"]

    # Metadata
    added_by_user_id = Column(Integer, ForeignKey("users.id"), default=1)
    added_via = Column(String(20))  # 'search', 'auto_populate'
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<LibraryItem {self.media_type}:{self.tmdb_id} - {self.title}>"
