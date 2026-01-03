#!/usr/bin/env python3
"""
JF-Resolve Startup Script
"""

import asyncio
import os
import secrets
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def generate_env_file():
    """Generate .env file if it doesn't exist"""
    env_path = Path(__file__).parent.parent / ".env"
    env_example = Path(__file__).parent.parent / ".env.example"

    if not env_path.exists():
        if env_example.exists():
            # Copy example and generate secret key
            content = env_example.read_text()

            # Generate random secret key
            secret_key = secrets.token_urlsafe(48)

            # Replace placeholder with actual secret
            content = content.replace(
                "SECRET_KEY=change-this-to-a-random-secret-key-minimum-32-characters",
                f"SECRET_KEY={secret_key}",
            )

            # Write to .env
            env_path.write_text(content)
            print("Generated .env file with random secret key")
        else:
            print("Warning: .env.example not found, using default configuration")


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


async def run_stream_server():
    """Run streaming server (All interfaces)"""
    import uvicorn
    from backend.config import settings
    from backend.database import AsyncSessionLocal
    from backend.services.settings_manager import SettingsManager

    # Defaults
    host = settings.STREAM_HOST
    port = settings.STREAM_PORT

    # Load overrides from DB if possible
    try:
        async with AsyncSessionLocal() as db:
            sm = SettingsManager(db)
            host = await sm.get("STREAM_HOST", host)
            port = int(await sm.get("STREAM_PORT", port))
    except Exception:
        # Fallback to defaults if table doesn't exist yet
        pass

    print(
        f"Streaming server starting on http://{host}:{port} (listening on all interfaces)"
    )

    config = uvicorn.Config(
        "backend.stream_server:stream_app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Run both servers concurrently with proper shutdown handling"""
    import signal

    # Generate .env if it doesn't exist
    generate_env_file()

    from backend.config import settings
    from backend.database import init_db

    # Initialize database BEFORE starting servers
    print("Initializing database...")
    await init_db()
    print("Database initialized successfully.")

    print(f"""
    ╔══════════════════════════════════════╗
    ║               JF-Resolve             ║
    ╚══════════════════════════════════════╝

      Main API Server:   http://{settings.HOST}:{settings.PORT}
       - Web Interface:   http://{settings.HOST}:{settings.PORT}/
       - API Documentation: http://{settings.HOST}:{settings.PORT}/docs
       - Authentication:   Required for all API endpoints

      Streaming Server:  http://{settings.STREAM_HOST}:{settings.STREAM_PORT}
       - Purpose:          Jellyfin stream resolution only
       - Access:           All interfaces (configurable)
       - Authentication:   None (designed for Jellyfin)
       - WARNING:          Configure proper firewall rules!

    First-time Setup: http://{settings.HOST}:{settings.PORT}

       SECURITY NOTES:
       - Main API requires authentication (protect all admin functions)
       - Streaming service should be restricted via firewall if possible
       - Never expose streaming port to untrusted networks
       - Keep your authentication token secure

    Press CTRL+C to stop both servers gracefully
    """)

    # Create server tasks
    main_task = asyncio.create_task(run_main_server())
    stream_task = asyncio.create_task(run_stream_server())

    # Handle shutdown signal
    shutdown_event = asyncio.Event()

    def handle_shutdown():
        print("\nShutting down servers...")
        shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_shutdown)

    try:
        # Wait for shutdown signal or task completion
        done, pending = await asyncio.wait(
            [main_task, stream_task, asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
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
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
