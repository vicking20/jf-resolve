#!/usr/bin/env python3
"""
Fix STRM file URLs
"""

import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))


async def get_stream_url_from_db() -> str:
    """Get the correct stream server URL from database settings"""
    try:
        from backend.database import AsyncSessionLocal
        from backend.services.settings_manager import SettingsManager

        async with AsyncSessionLocal() as db:
            settings = SettingsManager(db)

            # Check for explicit stream_server_url
            stream_url = await settings.get("stream_server_url")
            if stream_url:
                print(f"Using explicit stream_server_url from settings: {stream_url}")
                return stream_url.rstrip("/")

            jellyfin_url = await settings.get("jellyfin_server_url")
            if jellyfin_url:
                parsed = urlparse(jellyfin_url)
                scheme = parsed.scheme or "http"
                hostname = parsed.hostname or "localhost"
                derived_url = f"{scheme}://{hostname}:8766"
                print(
                    f"Derived stream URL from Jellyfin URL ({jellyfin_url}): {derived_url}"
                )
                return derived_url

            # Fallback
            print("No Jellyfin URL configured, using localhost:8766")
            return "http://localhost:8766"
    except Exception as e:
        print(f"Warning: Could not read from database: {e}")
        print("You can manually specify the URL with --new-url parameter")
        return None


def fix_strm_file_url(content: str, new_base_url: str) -> str:
    """
    Replace the base URL
    """
    pattern = r"^(https?://[^/]+)(/api/stream/resolve/.*)$"
    match = re.match(pattern, content)

    if match:
        old_base = match.group(1)
        path_and_query = match.group(2)
        new_url = f"{new_base_url}{path_and_query}"
        return new_url

    return None


def fix_strm_files(base_path: Path, new_base_url: str, dry_run: bool = False):
    """
    Recursively find and fix STRM files with correct stream server URL

    Args:
        base_path: Root directory to search for STRM files
        new_base_url: New base URL (e.g., "http://192.168.9.254:8766")
        dry_run: If True, only show what would be changed without making changes
    """
    if not base_path.exists():
        print(f"❌ Error: Path {base_path} does not exist")
        return 0

    fixed_count = 0
    error_count = 0
    already_correct = 0

    print(f"Searching for STRM files in: {base_path}")
    print(f"Updating base URL to: {new_base_url}")
    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")
    else:
        print("WRITE MODE - Files will be modified\n")

    strm_files = list(base_path.rglob("*.strm"))

    if not strm_files:
        print(f"No STRM files found in {base_path}")
        return 0

    print(f"Found {len(strm_files)} STRM files\n")

    for strm_file in strm_files:
        try:
            content = strm_file.read_text().strip()

            new_content = fix_strm_file_url(content, new_base_url)

            if new_content and new_content != content:
                print(f"{strm_file.relative_to(base_path)}")
                print(f"   OLD: {content}")
                print(f"   NEW: {new_content}")

                if not dry_run:
                    strm_file.write_text(new_content)
                    print(f"   Updated")
                else:
                    print(f"   Would be updated (dry run)")

                print()
                fixed_count += 1
            elif new_content == content:
                already_correct += 1
            else:
                print(
                    f"Skipped {strm_file.relative_to(base_path)}: Unrecognized URL format"
                )
                print(f"   Content: {content}\n")

        except Exception as e:
            print(f"❌ Error processing {strm_file}: {e}")
            error_count += 1

    print("\n" + "=" * 60)
    print(f"   Summary:")
    print(f"   Total STRM files: {len(strm_files)}")
    print(f"   Files {'that would be ' if dry_run else ''}fixed: {fixed_count}")
    print(f"   Already correct: {already_correct}")
    print(f"   Errors: {error_count}")
    print("=" * 60)

    return fixed_count


async def async_main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Fix STRM file URLs to use correct stream server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect URL from database and do a dry run
  python scripts/fix_strm_urls.py /path/to/jellyfin/movies --dry-run

  # Auto-detect and apply changes
  python scripts/fix_strm_urls.py /path/to/jellyfin/movies

  # Manually specify the stream server URL
  python scripts/fix_strm_urls.py /path/to/movies --new-url http://192.168.9.254:8766

  # Fix multiple directories
  python scripts/fix_strm_urls.py /jellyfin/movies /jellyfin/tv --dry-run
        """,
    )

    parser.add_argument(
        "paths", nargs="+", type=str, help="One or more paths to search for STRM files"
    )

    parser.add_argument(
        "--new-url",
        type=str,
        help="Manually specify the new base URL (e.g., http://192.168.9.254:8766). "
        "If not provided, will auto-detect from database settings.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes",
    )

    args = parser.parse_args()

    # Get the new URL
    if args.new_url:
        new_base_url = args.new_url.rstrip("/")
        print(f"Using manually specified URL: {new_base_url}\n")
    else:
        print("Auto-detecting stream server URL from database...\n")
        new_base_url = await get_stream_url_from_db()

        if not new_base_url:
            print("\n❌ Could not determine stream server URL.")
            print("   Please specify manually with --new-url parameter.")
            print("   Example: --new-url http://192.168.9.254:8766")
            sys.exit(1)

        print()

    total_fixed = 0

    for path_str in args.paths:
        path = Path(path_str).expanduser().resolve()

        if len(args.paths) > 1:
            print(f"\n{'=' * 60}")
            print(f"Processing: {path}")
            print(f"{'=' * 60}\n")

        fixed = fix_strm_files(path, new_base_url, args.dry_run)
        total_fixed += fixed

    if len(args.paths) > 1:
        print(f"\n{'=' * 60}")
        print(
            f"Grand Total: {total_fixed} files {'would be ' if args.dry_run else ''}fixed"
        )
        print(f"{'=' * 60}")

    if args.dry_run and total_fixed > 0:
        print("\nTip: Run without --dry-run to actually apply the changes")


def main():
    """Synchronous wrapper for async main"""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
