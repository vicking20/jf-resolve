import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class MediaType(str, enum.Enum):
    """Unified Media types"""

    MOVIE = "movie"  # Movies that can be matched with TMDB
    TV = "tv"  # Shows that can be matched with TMDB
    EPISODE = "episode"
    VIDEO = "video"  # Local/User videos
    VIDEO_SERIES = "video_series"  # Video series for user / local series
    COLLECTION = "collection"  # For grouping items


class MediaItem(Base):
    """
    Unified table for all media (Movies, Series, Episodes, Local Videos)
    """

    __tablename__ = "media_items"

    uuid = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    media_type = Column(Enum(MediaType), nullable=False, index=True)

    # Core Metadata
    title = Column(String(500), nullable=False, index=True)
    original_title = Column(String(500), nullable=True)
    year = Column(Integer, nullable=True, index=True)

    # Hierarchy
    parent_id = Column(
        String(36),
        ForeignKey("media_items.uuid", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Episode specific
    season_number = Column(Integer, nullable=True)
    episode_number = Column(Integer, nullable=True)

    # Flexible storage for provider results
    # image_paths: { "poster": "...", "backdrop": "...", "stills": [...] }
    image_paths = Column(JSON, nullable=True, default=dict)

    # raw_metadata: full blob from TMDB/IMDb
    metadata_blob = Column(JSON, nullable=True, default=dict)

    # Lifecycle
    is_persisted = Column(
        Boolean, default=False, index=True
    )  # True if "Added to Library"
    metadata_stale_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    parent = relationship("MediaItem", remote_side=[uuid], backref="children")
    external_ids = relationship(
        "MediaExternalId", back_populates="media_item", cascade="all, delete-orphan"
    )
    sources = relationship(
        "MediaSourceModel", backref="media_item", cascade="all, delete-orphan"
    )
    watchlist_entries = relationship(
        "WatchlistItem", back_populates="media_item", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<MediaItem {self.media_type} title='{self.title}' uuid={self.uuid}>"

    def to_dict(self):
        """Convert to dictionary with flattened essential metadata"""
        data = {
            "uuid": self.uuid,
            "media_type": self.media_type.value,
            "title": self.title,
            "original_title": self.original_title,
            "year": self.year,
            "parent_id": self.parent_id,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
            "image_paths": self.image_paths,
            # "metadata": self.metadata_blob,  # Exclude raw blob to reduce redundancy
            "is_persisted": self.is_persisted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

        # Include external IDs for easy access
        data["tmdb_id"] = self.get_external_id("tmdb")
        data["imdb_id"] = self.get_external_id("imdb")

        # Extract essential metadata fields to top level
        if self.metadata_blob:
            # Core fields
            data["overview"] = self.metadata_blob.get("overview")
            data["vote_average"] = self.metadata_blob.get("vote_average")
            data["content_rating"] = self.metadata_blob.get("content_rating")
            data["director"] = self.metadata_blob.get("director")
            data["cast"] = self.metadata_blob.get("cast", [])
            data["genre_names"] = self.metadata_blob.get("genre_names", [])
            data["runtime"] = self.metadata_blob.get("runtime")
            data["release_date"] = self.metadata_blob.get("release_date") or self.metadata_blob.get("first_air_date")
            data["number_of_seasons"] = self.metadata_blob.get("number_of_seasons")
            data["number_of_episodes"] = self.metadata_blob.get("number_of_episodes")
            data["collection"] = self.metadata_blob.get("collection")

        # Include top-level images for UI compatibility
        if self.image_paths:
            data["poster_path"] = self.image_paths.get("poster")
            data["backdrop_path"] = self.image_paths.get("backdrop")

        # Also provide all external IDs as a dict
        data["external_ids"] = {
            ext.provider: ext.external_id for ext in self.external_ids
        }

        return data

    def get_external_id(self, provider: str) -> Optional[str]:
        """Get external ID for a specific provider"""
        for ext in self.external_ids:
            if ext.provider == provider:
                return ext.external_id
        return None
