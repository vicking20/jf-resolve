#!/usr/bin/env bash
# Start JF-Resolve in experimental mode in the background (see run.sh for stop/status/logs)
exec "$(dirname "${BASH_SOURCE[0]}")/run.sh" start experimental
