#!/usr/bin/env python3
"""
JF-Resolve Startup Script
"""

import asyncio
import os
import secrets
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def generate_env_file():
    """Generate .env file if it doesn't exist"""
    env_path = Path(__file__).resolve().parent.parent / ".env"

    if not env_path.exists():
        # Generate random secret key
        secret_key = secrets.token_urlsafe(48)
        content = f"SECRET_KEY={secret_key}\n"

        # Write to .env
        env_path.write_text(content)
        print("Generated .env file with random secret key")


async def run_main_server():
    """Run main API server"""
    import uvicorn

    from backend.config import settings
    from backend.database import AsyncSessionLocal
    from backend.services.settings_manager import SettingsManager

    # Defaults
    host = settings.HOST
    port = settings.PORT

    # Load overrides from DB if possible
    try:
        async with AsyncSessionLocal() as db:
            sm = SettingsManager(db)
            host = await sm.get("HOST", host)
            port = int(await sm.get("PORT", port))
    except Exception:
        # Fallback to defaults if table doesn't exist yet
        pass

    print(f"Main API server starting on http://{host}:{port}")

    config = uvicorn.Config(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Run the main API server with proper shutdown handling"""
    import signal

    # Generate .env if it doesn't exist
    generate_env_file()

    from backend.config import settings
    from backend.database import init_db
    from backend.services.vendor_manager import ensure_vendor_assets

    # Ensure vendor assets are present
    ensure_vendor_assets()

    # Initialize database BEFORE starting server
    print("Initializing database...")
    await init_db()
    print("Database initialized successfully.")

    print(f"""
    ╔══════════════════════════════════════╗
    ║               JF-Resolve 2.0         ║
    ╚══════════════════════════════════════╝

      Main API Server:   http://{settings.HOST}:{settings.PORT}
       - Web Interface:   http://{settings.HOST}:{settings.PORT}/
       - API Documentation: http://{settings.HOST}:{settings.PORT}/docs
       - Authentication:   Required for all API endpoints
       - Streaming:        All streaming handled through /api/stream/*

    First-time Setup: http://{settings.HOST}:{settings.PORT}

       SECURITY NOTES:
       - All endpoints require authentication
       - Safe to expose behind reverse proxy (nginx/Caddy recommended)
       - Use HTTPS in production
       - Keep your authentication token secure

    Press CTRL+C to stop gracefully
    """)

    # Create server task
    main_task = asyncio.create_task(run_main_server())

    # Handle shutdown signal
    shutdown_event = asyncio.Event()

    def handle_shutdown():
        print("\nShutting down server...")
        shutdown_event.set()

    # Windows event loops do not implement add_signal_handler.
    loop = asyncio.get_running_loop()
    registered_signals = []
    if os.name != "nt":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_shutdown)
            registered_signals.append(sig)

    try:
        # Wait for shutdown signal or task completion
        done, pending = await asyncio.wait(
            [main_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()

        # Wait for cancellation to complete
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Cleanup signal handlers
        for sig in registered_signals:
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
