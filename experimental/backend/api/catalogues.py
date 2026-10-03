from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_current_admin_user
from ..core.session import get_current_user
from ..database import get_db
from ..models.catalogue import Catalogue, CatalogueItem
from ..models.media_item import MediaItem
from ..models.user import User
from ..services.catalogue_service import CatalogueService

router = APIRouter(prefix="/api/catalogues", tags=["catalogues"])


class CatalogueCreate(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    order_config: Optional[Dict[str, Any]] = None


@router.post("/sync")
async def sync_all_catalogues(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Trigger a sync of all catalogues (Admin only)"""
    catalogue_service = CatalogueService(db)
    success = await catalogue_service.sync_all_catalogues()
    if success:
        return {"message": "Catalogue sync triggered successfully"}
    else:
        raise HTTPException(
            status_code=500, detail="Catalogue sync failed. Check logs for details."
        )


@router.get("/")
async def list_catalogues(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """List all catalogues"""
    result = await db.execute(select(Catalogue).order_by(Catalogue.created_at))
    return result.scalars().all()


@router.post("/")
async def create_catalogue(
    data: CatalogueCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Create a new catalogue (Admin only)"""
    new_cat = Catalogue(
        name=data.name,
        slug=data.slug,
        description=data.description,
        order_config=data.order_config,
    )
    db.add(new_cat)
    await db.commit()
    await db.refresh(new_cat)
    return new_cat


@router.get("/{slug}")
async def get_catalogue_details(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get catalogue items"""
    result = await db.execute(select(Catalogue).where(Catalogue.slug == slug))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Catalogue not found")

    return cat


@router.get("/{slug}/items")
async def get_catalogue_items(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get items for a catalogue"""
    from sqlalchemy.orm import selectinload
    
    result = await db.execute(
        select(MediaItem)
        .options(selectinload(MediaItem.external_ids))
        .join(CatalogueItem, CatalogueItem.media_uuid == MediaItem.uuid)
        .join(Catalogue, Catalogue.id == CatalogueItem.catalogue_id)
        .where(Catalogue.slug == slug)
        .order_by(CatalogueItem.position)
    )
    items = result.scalars().all()
    
    seen = set()
    unique_items = []
    for item in items:
        if item.uuid not in seen:
            seen.add(item.uuid)
            unique_items.append(item.to_dict())
    return unique_items


@router.post("/{slug}/items")
async def add_item_to_catalogue(
    slug: str,
    media_uuid: str,
    position: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Add item to catalogue"""
    result = await db.execute(select(Catalogue).where(Catalogue.slug == slug))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Catalogue not found")

    check_stmt = select(CatalogueItem).where(
        CatalogueItem.catalogue_id == cat.id, CatalogueItem.media_uuid == media_uuid
    )
    existing = await db.execute(check_stmt)
    if existing.scalar_one_or_none():
        return {"message": "Already in catalogue"}

    new_item = CatalogueItem(
        catalogue_id=cat.id, media_uuid=media_uuid, position=position
    )
    db.add(new_item)
    await db.commit()
    return {"message": "Added to catalogue"}


@router.delete("/{slug}/items/{media_uuid}")
async def remove_item_from_catalogue(
    slug: str,
    media_uuid: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Remove item from catalogue"""
    result = await db.execute(
        select(CatalogueItem)
        .join(Catalogue, Catalogue.id == CatalogueItem.catalogue_id)
        .where(Catalogue.slug == slug, CatalogueItem.media_uuid == media_uuid)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found in catalogue")

    await db.delete(item)
    await db.commit()
    return {"message": "Removed from catalogue"}
