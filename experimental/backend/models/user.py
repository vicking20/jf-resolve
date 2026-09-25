"""User model"""

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class User(Base):
    """User account model"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    permissions = Column(JSON)  # e.g. {"allowed_paths": [], "max_age_rating": 12}
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    profile = relationship("UserProfile", backref="user", uselist=False, lazy="select")
    playback_sessions = relationship(
        "PlaybackSession", back_populates="user", cascade="all, delete-orphan"
    )



    def __repr__(self):
        return f"<User {self.username}>"
