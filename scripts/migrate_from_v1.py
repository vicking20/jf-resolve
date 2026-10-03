"""Migrate a JF-Resolve 1.x install to the unified app.

Copies settings from the old database, points the Stream Manager plugin at
the old addon URL, and rewrites every old STRM folder so it uses the new
playback link. See scripts/MIGRATING.md.
"""

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experimental.backend.database import AsyncSessionLocal, init_db  # noqa: E402
from experimental.backend.services.init_settings import initialize_default_settings  # noqa: E402
from experimental.backend.services.operation_profile import apply_operation_profile  # noqa: E402
from experimental.backend.services.settings_manager import SettingsManager  # noqa: E402
from experimental.backend.services.strm_service import MARKER_FILE, StrmService  # noqa: E402
from experimental.backend.services.tmdb_service import TMDBService  # noqa: E402

PLUGIN_CONFIG = ROOT / "experimental/plugins/com.vicking20.stream-manager/config.json"
COPIED_SETTINGS = {
    "tmdb_api_key": "tmdb_api_key",
    "jellyfin_server_url": "jellyfin_server_url",
    "jellyfin_api_key": "jellyfin_api_key",
    "jellyfin_movie_path": "jellyfin_movie_path",
    "jellyfin_tv_path": "jellyfin_tv_path",
    "trigger_jellyfin_scan": "jellyfin_scan_enabled",
}


def read_old_db(path: Path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    settings = {}
    for row in db.execute("SELECT key, value FROM settings"):
        try:
            settings[row["key"]] = json.loads(row["value"]) if row["value"] else None
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]
    items = [dict(r) for r in db.execute("SELECT tmdb_id, media_type, title, folder_path FROM library_items")]
    db.close()
    return settings, items


def set_aside_old_strms(folder: Path) -> list:
    """Rename a 1.x folder's .strm files so new ones can be written.

    Only folders 1.x created (with the marker file) are touched.
    """
    if not (folder / MARKER_FILE).exists():
        return []
    moved = []
    for strm in folder.rglob("*.strm"):
        backup = strm.with_name(strm.name + ".v1")
        strm.rename(backup)
        moved.append(backup)
    return moved


def restore(backups: list) -> None:
    for backup in backups:
        backup.rename(backup.with_name(backup.name[: -len(".v1")]))


async def migrate(old_db: Path, public_url: str, dry_run: bool) -> int:
    old_settings, items = read_old_db(old_db)
    print(f"Old install: {len(old_settings)} settings, {len(items)} library items")

    await init_db()
    async with AsyncSessionLocal() as db:
        await initialize_default_settings(db)
        settings = SettingsManager(db)

        updates = {new: old_settings[old] for old, new in COPIED_SETTINGS.items() if old_settings.get(old) not in (None, "")}
        if public_url:
            updates["jfresolve_public_url"] = public_url.rstrip("/")
        for key, value in updates.items():
            shown = "***" if "key" in key else value
            print(f"  setting {key} = {shown}")

        manifest = old_settings.get("stremio_manifest_url")
        if manifest:
            print("  Stream Manager addon URL copied from the old install")

        if dry_run:
            for item in items:
                print(f"  would rewrite {item['media_type']} {item['title']} ({item['folder_path']})")
            print("Dry run: nothing was changed.")
            return 0

        await apply_operation_profile(settings, "jellyfin")
        await settings.update_many(updates)

        if manifest:
            config = json.loads(PLUGIN_CONFIG.read_text()) if PLUGIN_CONFIG.exists() else {}
            config["manifest_url"] = manifest
            PLUGIN_CONFIG.write_text(json.dumps(config, indent=4))

        tmdb = TMDBService(await settings.get("tmdb_api_key") or "")
        strm = StrmService(settings, tmdb)
        failed = 0
        try:
            for item in items:
                label = f"{item['media_type']} {item['title']}"
                backups = set_aside_old_strms(Path(item["folder_path"]))
                try:
                    result = await strm.generate(int(item["tmdb_id"]), item["media_type"])
                except Exception as e:
                    restore(backups)
                    failed += 1
                    print(f"  {label}: FAILED, old files left in place - {e}")
                    continue
                for backup in backups:
                    backup.unlink()
                old_folder = Path(item["folder_path"])
                if old_folder.resolve() != Path(result["folder"]).resolve() and (old_folder / MARKER_FILE).exists() \
                        and not any(old_folder.rglob("*.strm")):
                    shutil.rmtree(old_folder)
                print(f"  {label}: replaced {len(backups)} old file(s), wrote {result['created']} in {result['folder']}")
        finally:
            await tmdb.close()

    print(f"Done. {len(items) - failed} migrated, {failed} failed.")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--old-db", type=Path, default=ROOT / "data/jfresolve.db", help="1.x database (default: data/jfresolve.db)")
    parser.add_argument("--public-url", default="", help="URL Jellyfin uses to reach JF-Resolve, e.g. http://192.168.1.10:8765")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything")
    args = parser.parse_args()
    if not args.old_db.exists():
        raise SystemExit(f"No 1.x database at {args.old_db}")
    raise SystemExit(asyncio.run(migrate(args.old_db, args.public_url, args.dry_run)))


if __name__ == "__main__":
    main()
