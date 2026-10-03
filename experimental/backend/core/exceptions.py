"""Custom application exceptions"""

from typing import Any, Dict, Optional


class JFResolveException(Exception):
    """Base exception for all application errors"""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class AuthenticationError(JFResolveException):
    """Authentication failed"""

    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(message, code="AUTH_FAILED", **kwargs)


class InvalidCredentialsError(AuthenticationError):
    """Invalid username or password"""

    def __init__(self, message: str = "Invalid credentials", **kwargs):
        super().__init__(message, code="INVALID_CREDENTIALS", **kwargs)


class TokenExpiredError(AuthenticationError):
    """JWT token has expired"""

    def __init__(self, message: str = "Token expired", **kwargs):
        super().__init__(message, code="TOKEN_EXPIRED", **kwargs)


class ExternalServiceError(JFResolveException):
    """Base exception for external service errors"""

    pass


class TMDBError(ExternalServiceError):
    """TMDB API error"""

    def __init__(self, message: str = "TMDB API error", **kwargs):
        super().__init__(message, code="TMDB_ERROR", **kwargs)


class IMDBIdNotFoundError(TMDBError):
    """IMDB ID not found for TMDB item"""

    def __init__(self, tmdb_id: int, media_type: str, **kwargs):
        super().__init__(
            f"No IMDB ID for {media_type}:{tmdb_id}",
            code="NO_IMDB_ID",
            details={"tmdb_id": tmdb_id, "media_type": media_type},
            **kwargs,
        )


