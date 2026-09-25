#!/usr/bin/env bash
#
# JF-Resolve background runner
#
# Usage:
#   ./run.sh start   [normal|experimental]   # default: experimental
#   ./run.sh stop
#   ./run.sh restart [normal|experimental]
#   ./run.sh status
#   ./run.sh logs                            # follow the current log
#
# Environment overrides:
#   JFR_HOST (default 0.0.0.0)   JFR_PORT (default 8765)   PYTHON (default python3)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

HOST="${JFR_HOST:-0.0.0.0}"
PORT="${JFR_PORT:-8765}"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/venv"
RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
PID_FILE="$RUN_DIR/jfresolve.pid"
MODE_FILE="$RUN_DIR/jfresolve.mode"

mkdir -p "$RUN_DIR" "$LOG_DIR"

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

current_mode() {
    cat "$MODE_FILE" 2>/dev/null || echo "unknown"
}

ensure_venv() {
    local req="$1"
    if [[ ! -x "$VENV/bin/python" ]]; then
        echo "Creating virtual environment in $VENV ..."
        "$PYTHON" -m venv "$VENV"
    fi

    # Only reinstall when the requirements file has changed since the last install
    local stamp="$VENV/.requirements.sha256"
    local hash
    hash="$( (sha256sum "$req" 2>/dev/null || shasum -a 256 "$req") | cut -d' ' -f1)"
    if [[ "$(cat "$stamp" 2>/dev/null)" != "$hash" ]]; then
        echo "Installing dependencies from $req ..."
        "$VENV/bin/pip" install --quiet --upgrade pip
        "$VENV/bin/pip" install --quiet -r "$req"
        echo "$hash" > "$stamp"
    else
        echo "Dependencies up to date."
    fi
}

ensure_env_file() {
    # The app signs sessions with SECRET_KEY; never run exposed with the built-in default
    if ! grep -qs '^SECRET_KEY=' "$ROOT/.env"; then
        echo "Generating SECRET_KEY in .env ..."
        echo "SECRET_KEY=$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')" >> "$ROOT/.env"
        chmod 600 "$ROOT/.env"
    fi
}

start() {
    local mode="${1:-experimental}"
    if [[ "$mode" != "normal" && "$mode" != "experimental" ]]; then
        echo "Unknown mode '$mode' (expected normal or experimental)" >&2
        exit 1
    fi
    if [[ ! -f "$ROOT/$mode/backend/main.py" ]]; then
        echo "Error: $mode/backend/main.py not found - '$mode' mode is not available in this checkout." >&2
        exit 1
    fi

    if is_running; then
        if [[ "$(current_mode)" == "$mode" ]]; then
            echo "JF-Resolve ($mode) is already running (PID $(cat "$PID_FILE"))."
            exit 0
        fi
        echo "JF-Resolve is running in $(current_mode) mode; switching to $mode ..."
        stop
    fi

    local req="$ROOT/requirements.txt"
    [[ -f "$ROOT/$mode/requirements.txt" ]] && req="$ROOT/$mode/requirements.txt"
    ensure_venv "$req"
    ensure_env_file

    local log="$LOG_DIR/jfresolve-$mode.log"
    echo "Starting JF-Resolve ($mode) on $HOST:$PORT ..."
    echo "===== $(date '+%F %T') starting $mode on $HOST:$PORT =====" >> "$log"

    # Run uvicorn directly (not via start.py) so the PID we record is the server itself.
    # setsid detaches it from this shell so it survives logout / ssh disconnect.
    local detach=()
    command -v setsid >/dev/null && detach=(setsid)
    nohup ${detach[@]+"${detach[@]}"} "$VENV/bin/python" -m uvicorn "$mode.backend.main:app" \
        --host "$HOST" --port "$PORT" >> "$log" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    echo "$mode" > "$MODE_FILE"

    # Wait for the server to answer
    for _ in $(seq 1 30); do
        if ! is_running; then
            echo "JF-Resolve exited during startup. Last log lines:" >&2
            tail -n 20 "$log" >&2
            rm -f "$PID_FILE"
            exit 1
        fi
        # Any HTTP status (even a redirect to login) means the server is listening
        if [[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null)" != "000" ]]; then
            echo "JF-Resolve ($mode) is up - PID $(cat "$PID_FILE"), http://$HOST:$PORT"
            echo "Logs: $log"
            return 0
        fi
        sleep 1
    done
    echo "JF-Resolve is running (PID $(cat "$PID_FILE")) but not answering on port $PORT yet. Check $log"
}

stop() {
    if ! is_running; then
        echo "JF-Resolve is not running."
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    echo "Stopping JF-Resolve ($(current_mode), PID $pid) ..."
    kill "$pid"
    for _ in $(seq 1 15); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "Did not exit gracefully; killing."
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "Stopped."
}

status() {
    if is_running; then
        echo "JF-Resolve is running: mode=$(current_mode) PID=$(cat "$PID_FILE") port=$PORT"
    else
        echo "JF-Resolve is not running."
        exit 3
    fi
}

logs() {
    tail -n 100 -f "$LOG_DIR/jfresolve-$(current_mode).log"
}

case "${1:-}" in
    start)   start "${2:-experimental}" ;;
    stop)    stop ;;
    restart) stop; start "${2:-$(current_mode | sed 's/unknown/experimental/')}" ;;
    status)  status ;;
    logs)    logs ;;
    *)
        sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
