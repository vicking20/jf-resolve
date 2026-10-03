"""Main FastAPI application"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

from .api import (
    addons,
    auth,
    catalogues,
    discover,
    downloads,
    library,
    library_paths,
    library_scanner,
    media_images,
    media_unified,
    playback_tracking,
    requests,
    search,
    stream,
    strm,
    system,
    users,
    watchlist,
)
from .api import settings as settings_api
from .core.config import settings
from .core.dependencies import get_current_admin_user
from .database import AsyncSessionLocal, engine, get_db, init_db
from .models.user import User
from .services.scheduler_service import scheduler_service
from .services.settings_manager import SettingsManager
from .services.tmdb_service import TMDBService
from .services.transcode_manager import transcode_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    # Initialize database tables
    await init_db()

    # Initialize default settings
    from .services.init_settings import initialize_default_settings

    async with AsyncSessionLocal() as db:
        await initialize_default_settings(db)

    # Initialize transcode manager from settings
    async with AsyncSessionLocal() as db:
        settings_mgr = SettingsManager(db)
        await settings_mgr.load_cache()
        max_concurrent = await settings_mgr.get("max_concurrent_transcodes", 2)
        queue_behavior = await settings_mgr.get("queue_full_behavior", "wait")
        await transcode_manager.set_max_concurrent(max_concurrent)
        transcode_manager.set_queue_full_behavior(queue_behavior)

    # Load Plugins
    from .addons.manager import plugin_manager

    await plugin_manager.load_all()

    # Start scheduler
    await scheduler_service.start()

    # Initialize file watcher
    from .services.library import create_scanner_coordinator, get_file_watcher
    from .services.tmdb_service import TMDBService

    file_watcher = get_file_watcher()
    async with AsyncSessionLocal() as db:
        settings_mgr = SettingsManager(db)
        tmdb_key = await settings_mgr.get("tmdb_api_key")
        tmdb = TMDBService(tmdb_key) if tmdb_key else None

        # Create scanner coordinator and configure watchers
        coordinator = await create_scanner_coordinator(db, tmdb)

        # Start file watcher
        await file_watcher.start()

        logger.info("File watcher initialized and started")

        # Trigger initial scans if in active mode
        await coordinator.trigger_initial_scans_if_needed()

    # Cleanup stuck scan jobs
    async with AsyncSessionLocal() as db:
        # TODO: Implement stuck job cleanup if new scan job model is introduced
        pass

    from .services.download_service import download_service
    await download_service.reconcile_library()
    transcode_manager.start_watchdog()
    try:
        yield
    except asyncio.CancelledError:
        pass  # Suppress CancelledError during shutdown
    finally:
        # Shutdown - cleanup runs in finally block
        from .services.library import get_file_watcher

        file_watcher = get_file_watcher()
        await file_watcher.stop()

        await transcode_manager.stop_watchdog()
        from .services.download_service import download_service
        await download_service.close()
        await transcode_manager.cleanup_all()
        from .services.torrent_service import torrent_service
        await torrent_service.close()
        await scheduler_service.stop()
        await engine.dispose()


# Create FastAPI app with protected docs
app = FastAPI(
    title="JF-Resolve 2.0",
    description="Media library and streaming platform powered by TMDB and Stremio addons",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable default docs
    redoc_url=None,  # Disable default redoc
)


@app.exception_handler(401)
async def unauthorized_exception_handler(request: Request, exc: HTTPException):
    """Custom 401 handler to redirect pages to login"""
    # For API routes, return JSON
    if request.url.path.startswith("/api/"):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )
    # For page routes, redirect to login
    return RedirectResponse(url=f"/login?redirect_to={request.url.path}")


class SecurityHeadersMiddleware:
    """Add browser hardening headers to every HTTP response (pure ASGI, streaming-safe)."""

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"SAMEORIGIN"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"content-security-policy", b"frame-ancestors 'self'; object-src 'none'; base-uri 'self'"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    header for header in self.HEADERS if header[0] not in existing
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)

# CORS is only needed when another origin must call this API. Leave ALLOWED_ORIGINS
# unset for normal use; a wildcard never allows credentialed (cookie) requests.
if settings.ALLOWED_ORIGINS:
    allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials="*" not in allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Mount static files
app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))

# Include API routers
app.include_router(media_unified.router)
app.include_router(auth.router)
app.include_router(catalogues.router)
app.include_router(discover.router)
app.include_router(search.router)
app.include_router(library.router)
app.include_router(library_paths.router)
app.include_router(library_scanner.router)

app.include_router(playback_tracking.router)
app.include_router(watchlist.router)

app.include_router(settings_api.router)
app.include_router(system.router)
app.include_router(users.router)
app.include_router(stream.router)
app.include_router(strm.router)
app.include_router(requests.router)
app.include_router(downloads.router)


from .api import web_routes

app.include_router(media_images.router)
app.include_router(web_routes.router)
# Register fixed marketplace paths before the addon router’s /{plugin_id} route.
app.include_router(addons.router)


# Favicon redirect
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve favicon from static directory"""
    return FileResponse(settings.STATIC_DIR / "favicon.ico")


# Root API endpoint
@app.get("/api")
async def api_root():
    """API root"""
    return {
        "name": "JF-Resolve 2.0 API",
        "version": "2.0.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


# API Documentation
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(user: User = Depends(get_current_admin_user)):
    """Swagger UI - requires admin authentication"""
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="/static/vendor/js/swagger-ui-bundle.js",
        swagger_css_url="/static/vendor/css/swagger-ui.css",
        swagger_favicon_url="/static/favicon.ico",
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_html(user: User = Depends(get_current_admin_user)):
    """ReDoc - requires admin authentication"""
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=app.title + " - ReDoc",
        redoc_js_url="/static/vendor/js/redoc.standalone.js",
        redoc_favicon_url="/static/favicon.ico",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(user: User = Depends(get_current_admin_user)):
    """OpenAPI schema - requires admin authentication"""
    from fastapi.openapi.utils import get_openapi

    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.exception_handler(404)
async def custom_404_handler(request: Request, __):
    """Custom 404 handler - JSON for API/Accept: json, HTML for pages"""
    # HLS segments should return proper video error, not JSON
    # This prevents "No decoders for requested formats: application/json" errors
    if request.url.path.startswith("/api/stream/hls/"):
        from fastapi.responses import Response

        return Response(status_code=404, content=b"", media_type="video/mp2t")

    if (
        request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "").lower()
    ):
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": "Resource not found",
                "code": 404,
            },
        )
    return templates.TemplateResponse(request=request, name="404.html", context={"request": request}, status_code=404)


@app.exception_handler(Exception)
async def custom_500_handler(request: Request, exc: Exception):
    """Custom 500 handler - JSON for API, HTML for pages"""
    logger.error(f"Internal Server Error: {exc}", exc_info=True)
    is_debug = getattr(settings, "LOG_LEVEL", "INFO") == "DEBUG"
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": str(exc) if is_debug else "Internal server error",
                "code": 500,
            },
        )
    return templates.TemplateResponse(
        request=request, name="404.html",  # Generic error page fallback
        context={"request": request, "error_message": str(exc) if is_debug else None},
        status_code=500,
    )
