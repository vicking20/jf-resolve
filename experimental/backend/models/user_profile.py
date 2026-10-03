"""User profile model with extended user information"""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.sql import func

from ..core.database import Base


class ContentRating(str, enum.Enum):
    """Content rating preferences for parental controls"""

    ALL = "all"  # All content
    FAMILY = "family"  # G, PG
    TEEN = "teen"  # PG-13
    MATURE = "mature"  # R
    ADULT = "adult"  # NC-17, Unrated


class UserProfile(Base):
    """
    Extended user profile information

    Includes:
    - Profile customization (avatar, bio)
    - Parental controls (content rating)
    - Preferences (language, quality)
    - Statistics
    """

    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Profile customization
    display_name = Column(String(100))  # Optional display name
    avatar_url = Column(Text)  # Profile picture URL
    bio = Column(Text)  # User bio

    # Parental controls
    max_content_rating = Column(
        SQLEnum(ContentRating),
        default=ContentRating.ALL,
        nullable=False,
    )
    enable_parental_controls = Column(Boolean, default=False)

    # Preferences
    preferred_language = Column(String(10), default="en")  # ISO 639-1
    preferred_quality = Column(String(10), default="1080p")  # Default stream quality
    auto_play = Column(Boolean, default=True)  # Auto-play next episode
    show_adult_content = Column(Boolean, default=True)

    # UI Preferences
    theme = Column(String(20), default="dark")  # UI theme
    layout = Column(String(20), default="grid")  # Library layout (grid, list)

    # Statistics
    total_watch_time_minutes = Column(Integer, default=0)
    total_items_watched = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    # user = relationship("User", back_populates="profile")

    def __repr__(self):
        return f"<UserProfile user_id={self.user_id} display_name={self.display_name}>"

    def to_dict(self):
        """Convert profile to dictionary"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "bio": self.bio,
            "max_content_rating": self.max_content_rating.value
            if self.max_content_rating
            else None,
            "enable_parental_controls": self.enable_parental_controls,
            "preferred_language": self.preferred_language,
            "preferred_quality": self.preferred_quality,
            "auto_play": self.auto_play,
            "show_adult_content": self.show_adult_content,
            "theme": self.theme,
            "layout": self.layout,
            "total_watch_time_minutes": self.total_watch_time_minutes,
            "total_items_watched": self.total_items_watched,
        }
