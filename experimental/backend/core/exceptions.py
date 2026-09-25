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


# Authentication & Authorization
class AuthenticationError(JFResolveException):
    """Authentication failed"""

    def __init__(self, message: str = "Authentication failed", **kwargs):
        super().__init__(message, code="AUTH_FAILED", **kwargs)


class AuthorizationError(JFResolveException):
    """User not authorized for this action"""

    def __init__(self, message: str = "Not authorized", **kwargs):
        super().__init__(message, code="NOT_AUTHORIZED", **kwargs)


class InvalidCredentialsError(AuthenticationError):
    """Invalid username or password"""

    def __init__(self, message: str = "Invalid credentials", **kwargs):
        super().__init__(message, code="INVALID_CREDENTIALS", **kwargs)


class TokenExpiredError(AuthenticationError):
    """JWT token has expired"""

    def __init__(self, message: str = "Token expired", **kwargs):
        super().__init__(message, code="TOKEN_EXPIRED", **kwargs)


# Library & Media
class LibraryError(JFResolveException):
    """Base exception for library operations"""

    pass


class ItemAlreadyExistsError(LibraryError):
    """Item already exists in library"""

    def __init__(self, tmdb_id: int, media_type: str, **kwargs):
        super().__init__(
            f"{media_type}:{tmdb_id} already in library",
            code="ITEM_EXISTS",
            details={"tmdb_id": tmdb_id, "media_type": media_type},
            **kwargs,
        )


class ItemNotFoundError(LibraryError):
    """Item not found in library"""

    def __init__(self, item_id: int = None, tmdb_id: int = None, **kwargs):
        super().__init__(
            f"Item not found",
            code="ITEM_NOT_FOUND",
            details={"item_id": item_id, "tmdb_id": tmdb_id},
            **kwargs,
        )


class InvalidMediaTypeError(LibraryError):
    """Invalid media type provided"""

    def __init__(self, media_type: str, **kwargs):
        super().__init__(
            f"Invalid media type: {media_type}",
            code="INVALID_MEDIA_TYPE",
            details={"media_type": media_type},
            **kwargs,
        )


# External Services
class ExternalServiceError(JFResolveException):
    """Base exception for external service errors"""

    pass


class TMDBError(ExternalServiceError):
    """TMDB API error"""

    def __init__(self, message: str = "TMDB API error", **kwargs):
        super().__init__(message, code="TMDB_ERROR", **kwargs)


class StremioError(ExternalServiceError):
    """Stremio addon error"""

    def __init__(self, message: str = "Stremio error", **kwargs):
        super().__init__(message, code="STREMIO_ERROR", **kwargs)


class NoStreamsAvailableError(StremioError):
    """No streams available from addon"""

    def __init__(self, imdb_id: str = None, **kwargs):
        super().__init__(
            "No streams available",
            code="NO_STREAMS",
            details={"imdb_id": imdb_id},
            **kwargs,
        )


class IMDBIdNotFoundError(TMDBError):
    """IMDB ID not found for TMDB item"""

    def __init__(self, tmdb_id: int, media_type: str, **kwargs):
        super().__init__(
            f"No IMDB ID for {media_type}:{tmdb_id}",
            code="NO_IMDB_ID",
            details={"tmdb_id": tmdb_id, "media_type": media_type},
            **kwargs,
        )


# Configuration
class ConfigurationError(JFResolveException):
    """Configuration error"""

    def __init__(self, message: str = "Configuration error", **kwargs):
        super().__init__(message, code="CONFIG_ERROR", **kwargs)


class MissingConfigError(ConfigurationError):
    """Required configuration missing"""

    def __init__(self, key: str, **kwargs):
        super().__init__(
            f"Missing required config: {key}",
            code="MISSING_CONFIG",
            details={"key": key},
            **kwargs,
        )


# Addon System
class AddonError(JFResolveException):
    """Base exception for addon system"""

    pass


class AddonNotFoundError(AddonError):
    """Addon not found"""

    def __init__(self, addon_id: str, **kwargs):
        super().__init__(
            f"Addon not found: {addon_id}",
            code="ADDON_NOT_FOUND",
            details={"addon_id": addon_id},
            **kwargs,
        )


class AddonPermissionDeniedError(AddonError):
    """Addon lacks required permission"""

    def __init__(self, addon_id: str, permission: str, **kwargs):
        super().__init__(
            f"Addon {addon_id} lacks permission: {permission}",
            code="ADDON_PERMISSION_DENIED",
            details={"addon_id": addon_id, "permission": permission},
            **kwargs,
        )


class AddonValidationError(AddonError):
    """Addon manifest validation failed"""

    def __init__(self, addon_id: str, errors: list, **kwargs):
        super().__init__(
            f"Addon validation failed: {addon_id}",
            code="ADDON_INVALID",
            details={"addon_id": addon_id, "errors": errors},
            **kwargs,
        )


class AddonExecutionError(AddonError):
    """Addon execution failed"""

    def __init__(self, addon_id: str, error: str, **kwargs):
        super().__init__(
            f"Addon execution failed: {addon_id}",
            code="ADDON_EXECUTION_FAILED",
            details={"addon_id": addon_id, "error": error},
            **kwargs,
        )


# Validation
class ValidationError(JFResolveException):
    """Input validation error"""

    def __init__(self, message: str, field: str = None, **kwargs):
        super().__init__(
            message,
            code="VALIDATION_ERROR",
            details={"field": field},
            **kwargs,
        )


# Database
class DatabaseError(JFResolveException):
    """Database operation error"""

    def __init__(self, message: str = "Database error", **kwargs):
        super().__init__(message, code="DATABASE_ERROR", **kwargs)


# File System
class FileSystemError(JFResolveException):
    """File system operation error"""

    def __init__(self, message: str, path: str = None, **kwargs):
        super().__init__(
            message,
            code="FILESYSTEM_ERROR",
            details={"path": path},
            **kwargs,
        )


class UnsafePathError(FileSystemError):
    """Attempting to delete path without safety marker"""

    def __init__(self, path: str, **kwargs):
        super().__init__(
            f"Unsafe path operation: {path}",
            code="UNSAFE_PATH",
            details={"path": path},
            **kwargs,
        )
