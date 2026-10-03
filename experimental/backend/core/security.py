"""Security utilities: JWT, hashing, CSRF protection, rate limiting"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi.security import HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings
from .exceptions import AuthenticationError, TokenExpiredError
from .logging import get_logger

logger = get_logger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer token scheme
security = HTTPBearer()

# Rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"]
    if settings.RATE_LIMIT_ENABLED
    else [],
)


# ===== Password Hashing =====


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its hash

    Args:
        plain_password: Plain text password
        hashed_password: Hashed password from database

    Returns:
        True if password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


# ===== JWT Token Management =====


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token

    Args:
        data: Data to encode in token (e.g., {"sub": user_id})
        expires_delta: Token expiration time (default from config)

    Returns:
        Encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode.update({"exp": expire, "iat": datetime.utcnow()})

    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate a JWT token

    Args:
        token: JWT token to decode

    Returns:
        Decoded token payload

    Raises:
        TokenExpiredError: If token has expired
        AuthenticationError: If token is invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token has expired")
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {str(e)}")


# ===== CSRF Protection =====


# ===== Rate Limiting =====


# ===== API Key Management =====


# ===== Security Headers =====


# ===== Input Sanitization =====


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove path separators and dangerous characters
    dangerous_chars = ["/", "\\", "..", "\0", "<", ">", ":", '"', "|", "?", "*"]
    sanitized = filename

    for char in dangerous_chars:
        sanitized = sanitized.replace(char, "")

    return sanitized.strip()


# ===== Addon Security =====


def verify_addon_token(token: str) -> str:
    """
    Verify and extract addon ID from token

    Args:
        token: JWT token

    Returns:
        Addon ID

    Raises:
        AuthenticationError: If token is invalid
    """
    payload = decode_token(token)

    if payload.get("type") != "addon":
        raise AuthenticationError("Invalid token type")

    addon_id = payload.get("sub")
    if not addon_id:
        raise AuthenticationError("Missing addon ID in token")

    return addon_id
