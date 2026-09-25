#!/usr/bin/env python3
"""Start the unified JF-Resolve application."""

import argparse
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Start JF-Resolve")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=["normal", "experimental", "standalone", "jellyfin", "hybrid"],
        help="Deprecated. Choose Standalone, Jellyfin, or Hybrid in the web setup.",
    )
    args = parser.parse_args()

    if args.mode:
        print("Mode is now configured in the web setup or Settings. Starting the unified app.")

    command = [sys.executable, "-m", "uvicorn", "experimental.backend.main:app",
               "--host", args.host, "--port", str(args.port)]
    if args.reload:
        command.append("--reload")
    if args.workers > 1:
        command.extend(["--workers", str(args.workers)])

    print(f"JF-Resolve starting at http://{args.host}:{args.port}")
    try:
        subprocess.run(command, check=True)
    except KeyboardInterrupt:
        print("\nJF-Resolve stopped.")
    except subprocess.CalledProcessError as exc:
        print(f"JF-Resolve exited with status {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
