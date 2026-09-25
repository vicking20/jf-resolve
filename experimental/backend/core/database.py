"""Enhanced database configuration with multi-database support"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)

# SQLAlchemy Base
Base = declarative_base()


def get_engine_kwargs(database_url: str) -> dict:
    """Get engine-specific kwargs based on database type"""
    kwargs = {
        "echo": settings.LOG_LEVEL == "DEBUG",
        "future": True,
    }

    if "sqlite" in database_url:
        # SQLite-specific settings
        kwargs["connect_args"] = {"check_same_thread": False}
        # Use StaticPool for SQLite to avoid connection issues
        kwargs["poolclass"] = StaticPool
    elif "postgresql" in database_url:
        # PostgreSQL-specific settings
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True  # Verify connections before using
        kwargs["pool_recycle"] = 3600  # Recycle connections after 1 hour
    else:
        # Generic settings for other databases
        kwargs["poolclass"] = NullPool

    return kwargs


# Create async engine
async_engine = create_async_engine(
    settings.DATABASE_URL,
    **get_engine_kwargs(settings.DATABASE_URL),
)

# Create sync engine (for migrations and scripts)
sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    **get_engine_kwargs(settings.SYNC_DATABASE_URL),
)

# Session factories
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

SessionLocal = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)


# SQLite optimization: Enable WAL mode and foreign keys
@event.listens_for(sync_engine, "connect")
def set_sqlite_pragma_sync(dbapi_conn, connection_record):
    """Set SQLite pragmas for better performance"""
    if "sqlite" in settings.SYNC_DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster writes
        cursor.execute("PRAGMA foreign_keys=ON")  # Enforce foreign keys
        cursor.execute("PRAGMA temp_store=MEMORY")  # Temp tables in memory
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.close()


# Async dependency for FastAPI
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency to get async database session

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Context manager for manual session handling
@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for manual session handling

    Usage:
        async with get_db_session() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Sync session for scripts
def get_sync_db() -> Session:
    """
    Get synchronous database session (for scripts)

    Usage:
        with get_sync_db() as db:
            result = db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def init_db() -> None:
    """
    Initialize database (create all tables)

    Safe to call multiple times - only creates tables that don't exist
    """
    logger.info(f"Initializing database: {settings.DATABASE_TYPE.value}")

    # Import all models to ensure they're registered with Base.metadata
    from ..models import Setting, User, MediaItem, MediaExternalId, MediaSourceModel  # noqa: F401

    # Import new models if they exist
    try:
        from ..models.addon import Addon, AddonPermission  # noqa: F401
        from ..models.playback import PlaybackSession, SkipSegment  # noqa: F401
        from ..models.user_profile import UserProfile  # noqa: F401
    except ImportError:
        # New models not yet created
        pass

    async with async_engine.begin() as conn:

        def create_tables(connection):
            # checkfirst=True makes create_all skip existing tables
            Base.metadata.create_all(connection, checkfirst=True)

        await conn.run_sync(create_tables)

    logger.info("Database initialized successfully")


async def drop_all_tables() -> None:
    """
    Drop all tables (WARNING: DESTRUCTIVE)

    Only use for testing or complete reset
    """
    logger.warning("Dropping all database tables")

    async with async_engine.begin() as conn:

        def drop_tables(connection):
            Base.metadata.drop_all(connection)

        await conn.run_sync(drop_tables)

    logger.info("All tables dropped")


async def check_connection() -> bool:
    """
    Check if database connection is working

    Returns:
        True if connection successful, False otherwise
    """
    try:
        async with async_engine.connect() as conn:
            await conn.execute("SELECT 1")
        logger.info("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False


async def dispose_engine() -> None:
    """Dispose of database engine (cleanup on shutdown)"""
    logger.info("Disposing database engine")
    await async_engine.dispose()


def create_indexes() -> None:
    """
    Create additional indexes for performance

    Run this after initial migration or when adding new indexes
    """
    from sqlalchemy import Index

    from ..models.media_item import MediaItem

    # TODO: Add indexes for new models when needed
    # Composite indexes for common queries
    indexes = [
    ]

    # Create indexes if they don't exist
    with sync_engine.begin() as conn:
        for index in indexes:
            try:
                index.create(conn, checkfirst=True)
                logger.info(f"Created index: {index.name}")
            except Exception as e:
                logger.warning(f"Failed to create index {index.name}: {e}")


# Export for backward compatibility
engine = async_engine
get_db_dependency = get_db
