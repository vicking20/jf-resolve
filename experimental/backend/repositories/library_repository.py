"""Unified Media repository for media library management"""

from typing import List, Optional, Union

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.media_item import MediaItem, MediaType
from ..models.media_external_id import MediaExternalId


class MediaRepository:
    """Repository for unified MediaItem operations"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, uuid: str) -> Optional[MediaItem]:
        """Get media by UUID"""
        result = await self.db.execute(select(MediaItem).where(MediaItem.uuid == uuid))
        return result.scalar_one_or_none()

    async def get_by_external_id(self, provider: str, external_id: str) -> Optional[MediaItem]:
        """Get media by external ID (e.g. tmdb:123)"""
        result = await self.db.execute(
            select(MediaItem)
            .join(MediaExternalId)
            .where(
                MediaExternalId.provider == provider,
                MediaExternalId.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    async def get_all(
        self, 
        media_type: Optional[MediaType] = None,
        offset: int = 0, 
        limit: int = 100,
        is_persisted: bool = True
    ) -> List[MediaItem]:
        """Get all media items from library"""
        query = select(MediaItem).where(MediaItem.is_persisted == is_persisted)

        if media_type:
            query = query.where(MediaItem.media_type == media_type)

        query = query.order_by(MediaItem.created_at.desc())
        query = query.offset(offset).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def search(self, query: str, media_type: Optional[MediaType] = None, limit: int = 100) -> List[MediaItem]:
        """Search media items by title"""
        search_pattern = f"%{query}%"
        stmt = select(MediaItem).where(MediaItem.title.ilike(search_pattern))
        
        if media_type:
            stmt = stmt.where(MediaItem.media_type == media_type)
            
        result = await self.db.execute(stmt.order_by(MediaItem.title).limit(limit))
        return list(result.scalars().all())

    async def get_recently_added(self, media_type: Optional[MediaType] = None, limit: int = 20) -> List[MediaItem]:
        """Get recently added media"""
        stmt = select(MediaItem).where(MediaItem.is_persisted == True)
        if media_type:
            stmt = stmt.where(MediaItem.media_type == media_type)
            
        result = await self.db.execute(
            stmt.order_by(MediaItem.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def count(self, media_type: Optional[MediaType] = None) -> int:
        """Count media items in library"""
        query = select(func.count()).select_from(MediaItem).where(MediaItem.is_persisted == True)
        if media_type:
            query = query.where(MediaItem.media_type == media_type)
        result = await self.db.execute(query)
        return result.scalar_one()

    async def delete(self, uuid: str) -> bool:
        """Delete media item from library"""
        item = await self.get(uuid)
        if item:
            await self.db.delete(item)
            await self.db.commit()
            return True
        return False
