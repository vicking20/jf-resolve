"""Database configuration and session management

Re-exports from core.database for backward compatibility.
All database configuration is centralized in core/database.py.
"""

from .core.database import (
    AsyncSessionLocal,
    Base,
    SessionLocal,
    get_db,
    get_db_session,
    get_sync_db,
    sync_engine,
)
from .core.database import (
    async_engine as engine,
)

AsyncSessionLocal = AsyncSessionLocal
SessionLocal = SessionLocal


async def init_db():
    """
    Initialize database (create tables)
    Safe to call multiple times - only creates tables that don't exist
    """
    from .models import (  # noqa: F401
        Catalogue,
        CatalogueItem,
        MediaExternalId,
        MediaItem,
        MediaSourceModel,
        Setting,
        User,
        WatchlistItem,
    )
    from .models.library_path import LibraryPath  # noqa: F401
    from .models.playback import (  # noqa: F401
        PlaybackSession,
        SkipSegment,
    )
    from .models.user_profile import UserProfile  # noqa: F401

    async with engine.begin() as conn:

        def create_tables(connection):
            Base.metadata.create_all(connection, checkfirst=True)

        await conn.run_sync(create_tables)


__all__ = [
    "Base",
    "engine",
    "sync_engine",
    "AsyncSessionLocal",
    "SessionLocal",
    "get_db",
    "get_db_session",
    "get_sync_db",
    "init_db",
]
