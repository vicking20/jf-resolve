"""Authentication API routes"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.session import (
    clear_session_cookie,
    create_session_cookie,
    get_current_user,
    get_current_user_optional,
)
from ..core.request_security import client_ip, login_throttle
from ..database import get_db
from ..models.user import User
from ..schemas.auth import PasswordChange, UserCreate, UserLogin, UserResponse
from ..services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def load_user_response(db: AsyncSession, user_id: int) -> UserResponse:
    """Serialize a user without internal fields such as the password hash"""
    result = await db.execute(
        select(User).options(selectinload(User.profile)).where(User.id == user_id)
    )
    return UserResponse.model_validate(result.scalar_one())


@router.post("/register", response_model=UserResponse)
async def register(
    user_data: UserCreate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Register first user (disabled after first user exists)
    """
    auth_service = AuthService(db)

    if await auth_service.has_users():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled. User already exists.",
        )

    existing = await auth_service.get_user_by_username(user_data.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists"
        )

    user = await auth_service.create_user(
        username=user_data.username, password=user_data.password, is_superuser=True
    )

    await create_session_cookie(response, user, request)

    from ..core.config import settings as app_settings

    try:
        app_settings.SETUP_FLAG_FILE.touch(exist_ok=True)
    except Exception:
        pass

    return await load_user_response(db, user.id)


@router.post("/login")
async def login(
    credentials: UserLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Login with username and password
    """
    ip = client_ip(request)
    login_throttle.check(ip, credentials.username)

    auth_service = AuthService(db)

    user = await auth_service.authenticate_user(
        username=credentials.username, password=credentials.password
    )

    if not user:
        login_throttle.record_failure(ip, credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    login_throttle.reset_username(credentials.username)
    await create_session_cookie(response, user, request)

    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current user info
    """
    return await load_user_response(db, current_user.id)


@router.post("/logout")
async def logout(response: Response):
    """
    Logout user by clearing the session cookie.
    """
    await clear_session_cookie(response)
    return {"message": "Successfully logged out."}


@router.post("/change-password")
async def change_password(
    password_data: PasswordChange,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Change user password
    """
    auth_service = AuthService(db)

    if not auth_service.verify_password(
        password_data.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.hashed_password = auth_service.get_password_hash(
        password_data.new_password
    )
    await db.commit()

    await create_session_cookie(response, current_user, request)
    return {"message": "Password changed successfully"}


@router.get("/status")
async def auth_status(
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if setup is needed and return current user status.
    """
    auth_service = AuthService(db)
    has_users = await auth_service.has_users()

    user_response = await load_user_response(db, user.id) if user else None

    return {
        "setup_needed": not has_users,
        "is_authenticated": user is not None,
        "user": user_response,
    }
