#!/bin/bash
set -e

echo "Starting JF-Resolve Add-on..."

# Setup persistent data directory
# Remove the data directory from the image if it's not a symlink
if [ -d "/app/data" ] && [ ! -L "/app/data" ]; then
    echo "Removing default data directory..."
    rm -rf /app/data
fi

# Link /app/data to /data (HA persistent storage)
echo "Linking /app/data to /data..."
ln -sf /data /app/data

# Ensure logs directory exists in persistent storage
mkdir -p /data/logs

# Handle .env persistence
# We link /app/.env to /data/.env so the generated secret key persists
echo "Linking .env file..."
rm -f /app/.env
ln -sf /data/.env /app/.env

# Start application
echo "Executing startup script..."
exec python3 scripts/run.py
