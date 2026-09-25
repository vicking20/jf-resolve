from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from ..core.database import Base


class MediaExternalId(Base):
    """
    Mapping table for external provider IDs (IMDb, TMDB, etc.)
    """
    __tablename__ = "media_external_ids"

    id = Column(Integer, primary_key=True, index=True)
    media_uuid = Column(String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True)
    
    provider = Column(String(50), nullable=False, index=True)  # "imdb", "tmdb", "anilist", etc.
    external_id = Column(String(100), nullable=False, index=True)
    
    # Scoped by media_type if needed (e.g. TMDB has same IDs for movie/tv)
    # But usually provider + external_id is enough if we enforce unique per item
    
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uix_provider_id"),
    )

    media_item = relationship("MediaItem", back_populates="external_ids")

    def __repr__(self):
        return f"<MediaExternalId {self.provider}:{self.external_id} -> {self.media_uuid}>"
