"""Authentication API routes"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..schemas.auth import PasswordChange, Token, UserCreate, UserLogin, UserResponse
from ..services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])

security = HTTPBearer(auto_error=False)
security_required = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_required),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user from JWT token"""
    token = credentials.credentials

    username = AuthService.verify_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = AuthService(db)
    user = await auth_service.get_user_by_username(username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get current authenticated user from JWT token, or None if not authenticated"""
    if credentials is None:
        return None
    try:
        token = credentials.credentials

        # Verify token and get username
        username = AuthService.verify_token(token)
        if username is None:
            return None

        # Get user from database
        auth_service = AuthService(db)
        user = await auth_service.get_user_by_username(username)
        if user is None or not user.is_active:
            return None

        return user
    except:
        return None


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
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

    # Create user (first user is superuser)
    user = await auth_service.create_user(
        username=user_data.username, password=user_data.password, is_superuser=True
    )

    # Create setup completion flag file
    from ..config import settings as app_settings

    try:
        app_settings.SETUP_FLAG_FILE.touch(exist_ok=True)
    except Exception:
        pass  # Non-critical if flag file creation fails

    return user


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """
    Login with username and password
    """
    auth_service = AuthService(db)

    user = await auth_service.authenticate_user(
        username=credentials.username, password=credentials.password
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    # Create access token
    access_token = auth_service.create_access_token(data={"sub": user.username})

    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get current user info
    """
    return current_user


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout user (client should discard token)
    """
    return {"message": "Successfully logged out. Please discard your token."}


@router.post("/change-password")
async def change_password(
    password_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Change user password
    """
    auth_service = AuthService(db)

    # Verify current password
    if not auth_service.verify_password(
        password_data.current_password, current_user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    # Update password
    current_user.hashed_password = auth_service.get_password_hash(
        password_data.new_password
    )
    await db.commit()

    return {"message": "Password changed successfully"}


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """
    Check if setup is needed
    """
    auth_service = AuthService(db)
    has_users = await auth_service.has_users()

    return {"setup_needed": not has_users, "has_users": has_users}
