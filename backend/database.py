"""Database configuration and session management"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

database_url = settings.DATABASE_URL
if database_url.startswith("sqlite:///"):
    database_url = database_url.replace("sqlite:///", "sqlite+aiosqlite:///")

engine = create_async_engine(
    database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
)

sync_database_url = database_url.replace("+aiosqlite", "")
if not sync_database_url.startswith("sqlite:///"):
    sync_database_url = sync_database_url.replace("sqlite://", "sqlite:///")
sync_engine = create_engine(
    sync_database_url,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in sync_database_url else {},
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

SessionLocal = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)
Base = declarative_base()


async def get_db():
    """Dependency to get async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_sync_db():
    """Get sync database session (for scripts)"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def init_db():
    """
    Initialize database (create tables)
    Safe to call multiple times - only creates tables that don't exist
    """
    # Import all models to ensure they're registered with Base.metadata
    from .models import User, LibraryItem, Setting, FailoverState  # noqa: F401

    async with engine.begin() as conn:

        def create_tables(connection):
            # checkfirst=True makes create_all skip existing tables
            Base.metadata.create_all(connection, checkfirst=True)

        await conn.run_sync(create_tables)
