from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..core.database import Base


class Catalogue(Base):
    """
    Catalogue for Home page sections (e.g. "Recently Added", "Trending", "Custom")
    """
    __tablename__ = "catalogues"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(String(500), nullable=True)
    
    order_config = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items = relationship("CatalogueItem", back_populates="catalogue", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Catalogue {self.name}>"


class CatalogueItem(Base):
    """
    Items within a catalogue, mapping to media_items
    """
    __tablename__ = "catalogue_items"

    id = Column(Integer, primary_key=True, index=True)
    catalogue_id = Column(Integer, ForeignKey("catalogues.id", ondelete="CASCADE"), nullable=False, index=True)
    media_uuid = Column(String(36), ForeignKey("media_items.uuid", ondelete="CASCADE"), nullable=False, index=True)
    
    position = Column(Integer, default=0)
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    catalogue = relationship("Catalogue", back_populates="items")
    media_item = relationship("MediaItem")

    def __repr__(self):
        return f"<CatalogueItem cat={self.catalogue_id} media={self.media_uuid}>"
