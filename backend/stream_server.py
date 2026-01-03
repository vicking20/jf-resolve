"""Streaming service - separate FastAPI app for stream resolution only"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import stream
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    import asyncio
    # Startup - Initialize services for stream server if needed
    try:
        yield
    except asyncio.CancelledError:
        pass  # Suppress CancelledError during shutdown
    finally:
        pass


# FastAPI app for streaming only
stream_app = FastAPI(
    title="JF-Resolve 2.0 Streaming Service",
    description="Stream resolution service for Jellyfin integration",
    version="2.0.0",
    lifespan=lifespan,
)

# Add CORS middleware - permissive by default, configurable via settings
jellyfin_origins = ["*"]
allow_credentials = False

if settings.JELLYFIN_CORS_ORIGINS:
    jellyfin_origins = settings.JELLYFIN_CORS_ORIGINS.split(",")
    allow_credentials = True

stream_app.add_middleware(
    CORSMiddleware,
    allow_origins=jellyfin_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include only the streaming router
stream_app.include_router(stream.router)


@stream_app.get("/")
async def stream_root():
    """Streaming service root"""
    return {
        "service": "JF-Resolve Streaming Service",
        "purpose": "Stream resolution for Jellyfin",
        "version": "2.0.0",
        "warning": "This service should only be accessed by Jellyfin on localhost",
    }


@stream_app.get("/health")
async def stream_health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "streaming"}
