"""Cookie-based session management"""

import hashlib
import hmac

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_db
from .request_security import is_https


class SessionData(BaseModel):
    """Pydantic model for session data"""

    user_id: int
    pwd: str


serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="session")

SESSION_COOKIE_NAME = "jfresolve_session"
SESSION_DURATION_SECONDS = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def password_fingerprint(hashed_password: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), hashed_password.encode(), hashlib.sha256
    ).hexdigest()[:32]


async def create_session_cookie(response: Response, user, request: Request):
    """Creates and sets a session cookie"""
    session_data = SessionData(user_id=user.id, pwd=password_fingerprint(user.hashed_password))
    session_token = serializer.dumps(session_data.model_dump())
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        secure=is_https(request) or settings.is_production(),
        samesite="lax",
    )


async def clear_session_cookie(response: Response):
    """Clears the session cookie"""
    response.delete_cookie(SESSION_COOKIE_NAME, httponly=True, samesite="lax")


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    """
    FastAPI dependency to get the current user from a session cookie.

    Raises HTTPException if the user is not authenticated.
    """
    session_token = request.cookies.get(SESSION_COOKIE_NAME)

    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        data = serializer.loads(session_token, max_age=SESSION_DURATION_SECONDS)
        session_data = SessionData(**data)
    except (BadData, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    from sqlalchemy import select

    from ..models.user import User

    result = await db.execute(select(User).where(User.id == session_data.user_id))
    user = result.scalar_one_or_none()

    if user is None or not hmac.compare_digest(
        session_data.pwd, password_fingerprint(user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
        )

    return user


async def get_current_user_optional(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """
    FastAPI dependency to get an optional user from a session cookie.

    Returns the user if authenticated, otherwise returns None.
    """
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None
