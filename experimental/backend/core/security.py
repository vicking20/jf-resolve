"""Security utilities: JWT, hashing, CSRF protection, rate limiting"""

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter
from slowapi.util import get_remote_address

from .config import settings
from .exceptions import (
    AuthenticationError,
    InvalidCredentialsError,
    TokenExpiredError,
)
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


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt

    Args:
        password: Plain text password

    Returns:
        Hashed password
    """
    return pwd_context.hash(password)


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


def create_refresh_token(data: Dict[str, Any]) -> str:
    """
    Create a JWT refresh token (longer expiration)

    Args:
        data: Data to encode in token

    Returns:
        Encoded JWT refresh token
    """
    expires_delta = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return create_access_token(data, expires_delta)


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


def get_token_from_header(credentials: HTTPAuthorizationCredentials) -> str:
    """
    Extract token from Authorization header

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        JWT token string
    """
    return credentials.credentials


# ===== CSRF Protection =====


def generate_csrf_token() -> str:
    """
    Generate a CSRF token

    Returns:
        Random CSRF token
    """
    return secrets.token_urlsafe(32)


def verify_csrf_token(token: str, session_token: str) -> bool:
    """
    Verify CSRF token matches session token

    Args:
        token: CSRF token from request
        session_token: CSRF token from session

    Returns:
        True if tokens match, False otherwise
    """
    return secrets.compare_digest(token, session_token)


# ===== Rate Limiting =====


def get_rate_limit_key(request: Request) -> str:
    """
    Get rate limit key from request

    Uses IP address by default, but can be extended to use user ID

    Args:
        request: FastAPI request

    Returns:
        Rate limit key (IP address)
    """
    return get_remote_address(request)


def check_rate_limit(
    request: Request,
    max_requests: int = 60,
    window_seconds: int = 60,
) -> None:
    """
    Check if request exceeds rate limit

    Args:
        request: FastAPI request
        max_requests: Maximum requests allowed
        window_seconds: Time window in seconds

    Raises:
        HTTPException: If rate limit exceeded
    """
    # This is a simple implementation
    # For production, use Redis-backed rate limiting
    pass  # Implementation would go here


# ===== API Key Management =====


def generate_api_key() -> str:
    """
    Generate a secure API key

    Returns:
        Random API key
    """
    return secrets.token_urlsafe(32)


def verify_api_key(api_key: str, stored_key: str) -> bool:
    """
    Verify API key matches stored key

    Args:
        api_key: API key from request
        stored_key: API key from database

    Returns:
        True if keys match, False otherwise
    """
    return secrets.compare_digest(api_key, stored_key)


# ===== Security Headers =====


def get_security_headers() -> Dict[str, str]:
    """
    Get security headers for HTTP responses

    Returns:
        Dictionary of security headers
    """
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'",
    }


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


def sanitize_path(path: str) -> str:
    """
    Sanitize a file path to prevent traversal attacks

    Args:
        path: Original path

    Returns:
        Sanitized path

    Raises:
        ValueError: If path contains suspicious patterns
    """
    if ".." in path or path.startswith("/"):
        raise ValueError("Suspicious path detected")

    return path


# ===== Addon Security =====


def generate_addon_token(addon_id: str) -> str:
    """
    Generate a JWT token for addon authentication

    Args:
        addon_id: Addon identifier

    Returns:
        JWT token for addon
    """
    return create_access_token(
        data={"sub": addon_id, "type": "addon"},
        expires_delta=timedelta(days=365),  # Longer expiration for addons
    )


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
