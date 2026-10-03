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

Base = declarative_base()


def get_engine_kwargs(database_url: str) -> dict:
    """Get engine-specific kwargs based on database type"""
    kwargs = {
        "echo": settings.LOG_LEVEL == "DEBUG",
        "future": True,
    }

    if "sqlite" in database_url:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    elif "postgresql" in database_url:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 3600
    else:
        kwargs["poolclass"] = NullPool

    return kwargs


async_engine = create_async_engine(
    settings.DATABASE_URL,
    **get_engine_kwargs(settings.DATABASE_URL),
)

sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    **get_engine_kwargs(settings.SYNC_DATABASE_URL),
)

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


@event.listens_for(sync_engine, "connect")
def set_sqlite_pragma_sync(dbapi_conn, connection_record):
    """Set SQLite pragmas for better performance"""
    if "sqlite" in settings.SYNC_DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.close()


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

    from ..models import Setting, User, MediaItem, MediaExternalId, MediaSourceModel  # noqa: F401

    try:
        from ..models.addon import Addon, AddonPermission  # noqa: F401
        from ..models.playback import PlaybackSession, SkipSegment  # noqa: F401
        from ..models.user_profile import UserProfile  # noqa: F401
    except ImportError:
        pass

    async with async_engine.begin() as conn:

        def create_tables(connection):
            Base.metadata.create_all(connection, checkfirst=True)

        await conn.run_sync(create_tables)

    logger.info("Database initialized successfully")


engine = async_engine
get_db_dependency = get_db
