#!/usr/bin/env bash
# Normal is now the Jellyfin profile in the unified application.
echo "Starting the unified app. Select Jellyfin in setup or Settings."
exec "$(dirname "${BASH_SOURCE[0]}")/run.sh" start experimental
