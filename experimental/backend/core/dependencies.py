"""FastAPI dependency injection for clean route handlers"""


from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from .config import settings
from .database import get_db
from .logging import get_logger
from .session import get_current_user

logger = get_logger(__name__)


# ===== Authentication Dependencies =====


async def get_current_admin_user(
    user: User = Depends(get_current_user),
):
    """
    Get current admin user

    Args:
        user: Current user from session

    Returns:
        User model instance

    Raises:
        HTTPException: If user is not admin
    """
    if not user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )

    return user


async def get_admin_user_page(
    user: User = Depends(get_current_user),
):
    """
    Get current admin user for page routes.
    Redirects to home if not admin.
    """
    if not user.is_superuser:
        from fastapi.responses import RedirectResponse

        logger.warning(f"Unauthorized access attempt to admin page by user: {user.username}")
        # We use a RedirectResponse for page routes
        return RedirectResponse(url="/")

    return user


# ===== Addon Dependencies =====


# ===== Service Dependencies =====


async def get_tmdb_service(db: AsyncSession = Depends(get_db)):
    """
    Get TMDB service instance

    Args:
        db: Database session

    Returns:
        TMDBService instance
    """
    from ..infrastructure.tmdb.client import TMDBClient
    from ..services.settings_manager import SettingsManager

    settings_manager = SettingsManager(db)
    await settings_manager.load_cache()

    api_key = await settings_manager.get("tmdb_api_key")

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="TMDB API key not configured",
        )

    return TMDBClient(api_key)


# ===== Configuration Dependencies =====


# ===== Content Rating Dependencies =====


async def get_user_content_filter(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get user's content rating filter settings

    Args:
        user: Current user from session
        db: Database session

    Returns:
        Tuple of (max_content_rating, enable_parental_controls, allowed_paths)
    """
    from sqlalchemy import select

    from ..models.user_profile import ContentRating, UserProfile

    # Load user profile if exists
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = result.scalar_one_or_none()

    # Get max content rating
    if profile and profile.enable_parental_controls:
        max_content_rating = profile.max_content_rating
    else:
        max_content_rating = ContentRating.ALL

    # Get allowed paths from permissions
    allowed_paths = None
    if (
        not user.is_superuser
        and user.permissions
        and "allowed_paths" in user.permissions
    ):
        allowed_paths = user.permissions.get("allowed_paths", [])
        if allowed_paths and len(allowed_paths) > 0:
            # Only apply path filter if there are paths specified
            pass
        else:
            allowed_paths = None

    return (
        max_content_rating,
        profile.enable_parental_controls if profile else False,
        allowed_paths,
    )


# ===== Pagination Dependencies =====


