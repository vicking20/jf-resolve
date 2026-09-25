"""API routes for user management"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import load_user_response
from ..core.dependencies import get_current_admin_user
from ..database import get_db
from ..models.user import User
from ..schemas.auth import UserCreate, UserResponse
from ..services.auth_service import AuthService

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/", response_model=List[UserResponse])
async def get_all_users(
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
):
    """List all users (admin only)"""
    from sqlalchemy.orm import selectinload

    result = await db.execute(select(User).options(selectinload(User.profile)))
    users = result.scalars().all()
    return users


@router.post("/", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
):
    """Create a new user (admin only)"""
    auth_service = AuthService(db)
    existing = await auth_service.get_user_by_username(user_data.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )
    user = await auth_service.create_user(
        username=user_data.username,
        password=user_data.password,
        is_superuser=user_data.is_superuser,
    )
    return await load_user_response(db, user.id)


class UserUpdate(BaseModel):
    """Update user request"""

    username: str | None = None
    is_active: bool
    is_superuser: bool
    permissions: dict | None = None
    password: str | None = None
    enable_parental_controls: bool | None = None
    max_content_rating: str | None = None


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    update_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
):
    """Update a user's status, permissions, and profile settings (admin only)"""
    from ..models.user_profile import ContentRating, UserProfile

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Check if username is being changed and if it already exists
    if update_data.username and update_data.username != user.username:
        auth_service = AuthService(db)
        existing = await auth_service.get_user_by_username(update_data.username)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )
        user.username = update_data.username

    user.is_active = update_data.is_active
    user.is_superuser = update_data.is_superuser
    user.permissions = update_data.permissions

    if update_data.password:
        if len(update_data.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters",
            )
        auth_service = AuthService(db)
        user.hashed_password = auth_service.get_password_hash(update_data.password)

    # Handle profile updates
    if (
        update_data.enable_parental_controls is not None
        or update_data.max_content_rating is not None
    ):
        # Get or create user profile
        profile_result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        profile = profile_result.scalar_one_or_none()

        if not profile:
            profile = UserProfile(user_id=user_id)
            db.add(profile)

        if update_data.enable_parental_controls is not None:
            profile.enable_parental_controls = update_data.enable_parental_controls

        if update_data.max_content_rating is not None:
            # Validate content rating value
            try:
                profile.max_content_rating = ContentRating(
                    update_data.max_content_rating
                )
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid content rating: {update_data.max_content_rating}",
                )

    if user.id == admin_user.id and (not user.is_active or not user.is_superuser):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot deactivate or demote their own account.",
        )

    await db.commit()
    return await load_user_response(db, user.id)


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(get_current_admin_user),
):
    """Delete a user (admin only)"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot delete their own account.",
        )

    await db.delete(user)
    await db.commit()
    return None
