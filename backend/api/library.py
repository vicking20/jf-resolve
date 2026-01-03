"""Library management API routes"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.auth import get_current_user
from ..database import get_db
from ..models.library_item import LibraryItem
from ..models.user import User
from ..schemas.library import LibraryItemCreate, LibraryItemList, LibraryItemResponse
from ..services.library_service import LibraryService
from ..services.settings_manager import SettingsManager
from ..services.tmdb_service import TMDBService

router = APIRouter(prefix="/api/library", tags=["library"])


async def get_library_service(db: AsyncSession) -> LibraryService:
    """Get library service instance"""
    settings = SettingsManager(db)
    api_key = await settings.get("tmdb_api_key")

    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    tmdb = TMDBService(api_key)
    return LibraryService(db, tmdb, settings)


@router.post("/add", response_model=LibraryItemResponse, status_code=201)
async def add_to_library(
    item_data: LibraryItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add item to library and create STRM files
    """
    library = await get_library_service(db)

    try:
        item = await library.add_to_library(
            tmdb_id=item_data.tmdb_id,
            media_type=item_data.media_type,
            quality_versions=item_data.quality_versions,
            user_id=current_user.id,
            added_via=item_data.added_via,
        )
        return item
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to add to library: {str(e)}"
        )


@router.get("/items", response_model=LibraryItemList)
async def list_library_items(
    type: str = Query("all", regex="^(all|movie|tv)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List library items with pagination
    """
    # Build query
    query = select(LibraryItem)

    if type != "all":
        query = query.where(LibraryItem.media_type == type)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    result = await db.execute(count_query)
    total = result.scalar()

    # Pagination
    query = query.offset((page - 1) * limit).limit(limit)
    query = query.order_by(LibraryItem.created_at.desc())

    # Execute query
    result = await db.execute(query)
    items = result.scalars().all()

    return LibraryItemList(items=items, total=total, page=page, limit=limit)


@router.get("/items/{item_id}", response_model=LibraryItemResponse)
async def get_library_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get specific library item"""
    result = await db.execute(select(LibraryItem).where(LibraryItem.id == item_id))
    item = result.scalar_one_or_none()

    if not item:
        raise HTTPException(status_code=404, detail="Library item not found")

    return item


@router.delete("/items/{item_id}")
async def remove_from_library(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove item from library and delete STRM files"""
    library = await get_library_service(db)

    try:
        await library.remove_from_library(item_id)
        return {"message": "Item removed from library"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove: {str(e)}")


@router.post("/purge")
async def purge_library(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Delete all [jfr] tagged items"""
    library = await get_library_service(db)

    try:
        result = await library.purge_all_jfr_items()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to purge: {str(e)}")


@router.post("/refresh/{item_id}")
async def refresh_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Refresh metadata and check for new episodes"""
    library = await get_library_service(db)

    try:
        result = await library.refresh_item(item_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh: {str(e)}")


@router.post("/scan")
async def trigger_manual_scan(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger manual Jellyfin library scan"""
    library = await get_library_service(db)

    jellyfin_url = await library.settings.get("jellyfin_server_url")
    jellyfin_key = await library.settings.get("jellyfin_api_key")

    if not jellyfin_url or not jellyfin_key:
        raise HTTPException(
            status_code=400,
            detail="Jellyfin server URL and API key must be configured first",
        )

    try:
        import httpx

        from ..services.library_service import log_service

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{jellyfin_url}/Library/Refresh",
                headers={"X-Emby-Token": jellyfin_key},
            )
            response.raise_for_status()
            return {"message": "Jellyfin library scan triggered successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to trigger Jellyfin scan: {str(e)}"
        )
