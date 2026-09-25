#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${JFR_DOCKER_IMAGE:-jf-resolve:local}"
CONTAINER="${JFR_DOCKER_CONTAINER:-jf-resolve}"
PORT="${JFR_PORT:-8765}"

mkdir -p "$ROOT/data" "$ROOT/experimental/data"
docker build -t "$IMAGE" "$ROOT"

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "Container '$CONTAINER' already exists. Remove it with: docker rm -f $CONTAINER"
    exit 1
fi

docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    -p "$PORT:8765" \
    -v "$ROOT/data:/app/data" \
    -v "$ROOT/experimental/data:/app/experimental/data" \
    -v "$ROOT/experimental/plugins:/app/experimental/plugins" \
    "$IMAGE"

echo "JF-Resolve is running at http://127.0.0.1:$PORT"
echo "View logs with: docker logs -f $CONTAINER"
