"""Main FastAPI application"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import auth, discover, library, search, stream, system
from .api import settings as settings_api
from .api.auth import get_current_user, get_current_user_optional
from .config import settings
from .database import AsyncSessionLocal, engine, init_db
from .models.user import User
from .services.scheduler_service import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    await scheduler_service.start()
    try:
        yield
    except asyncio.CancelledError:
        pass  # Suppress CancelledError during shutdown
    finally:
        # Shutdown - cleanup runs in finally block
        await scheduler_service.stop()
        await engine.dispose()


# Create FastAPI app with protected docs
app = FastAPI(
    title="JF-Resolve 2.0",
    description="TMDB to Jellyfin streaming integration via Stremio manifests",
    version="2.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disable default docs
    redoc_url=None,  # Disable default redoc
)

# Add CORS middleware
# If ALLOWED_ORIGINS is not set, default to ["*"] for maximum compatibility
allowed_origins = ["*"]
allow_credentials = False  # Credentials cannot be used with "*"

if hasattr(settings, "ALLOWED_ORIGINS") and settings.ALLOWED_ORIGINS:
    allowed_origins = settings.ALLOWED_ORIGINS.split(",")
    allow_credentials = True  # Credentials allowed with specific origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(settings.STATIC_DIR)), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=str(settings.TEMPLATES_DIR))

# Include API routers
app.include_router(auth.router)
app.include_router(discover.router)
app.include_router(search.router)
app.include_router(library.router)
app.include_router(settings_api.router)
app.include_router(system.router)


# Template routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Homepage - Discover page"""
    from .database import AsyncSessionLocal
    from .services.auth_service import AuthService

    # Check if setup is needed
    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        if not await auth.has_users():
            # Redirect to setup if not configured
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/setup")

    # Return discover page
    return templates.TemplateResponse("discover.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    """First-time setup wizard"""
    from .database import AsyncSessionLocal
    from .services.auth_service import AuthService

    # Check if setup flag file exists
    if settings.SETUP_FLAG_FILE.exists():
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/login")

    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        if await auth.has_users():
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/login")

    return templates.TemplateResponse("setup.html", {"request": request})


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    """Search page"""
    return templates.TemplateResponse("search.html", {"request": request})


@app.get("/library", response_class=HTMLResponse)
async def library_page(request: Request):
    """Library management page"""
    return templates.TemplateResponse("library.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page"""
    return templates.TemplateResponse("settings.html", {"request": request})


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    """Logs viewer page"""
    return templates.TemplateResponse("logs.html", {"request": request})


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
async def custom_swagger_ui_html(current_user: User = Depends(get_current_user)):
    """Swagger UI - requires authentication"""
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_html(current_user: User = Depends(get_current_user)):
    """ReDoc - requires authentication"""
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=app.title + " - ReDoc",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js",
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(current_user: User = Depends(get_current_user)):
    """OpenAPI schema - requires authentication"""
    from fastapi.openapi.utils import get_openapi

    return get_openapi(title=app.title, version=app.version, routes=app.routes)


# 404 Handler
@app.exception_handler(404)
async def custom_404_handler(request: Request, __):
    """Custom 404 page"""
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
