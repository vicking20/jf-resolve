# updater.py
import os
import json
from config_loader import load_config
from datetime import datetime, timedelta
from rd_resolve_id import resolve_rd_id
import time
from delete_rd_torrent import delete_rd_torrent

cfg = load_config()
download_path = cfg.get("DOWNLOADS_PATH", "./media/downloads")
movie_path = cfg.get("JELLYFIN_MOVIE_PATH", "./media/radarr/movies")
tv_path = cfg.get("JELLYFIN_TV_PATH", "./media/sonarr/tvshows")
crawl_path = cfg.get("JELLYFIN_CRAWL_PATH", "./media/jellyfin/crawl")

now = datetime.now()
threshold = timedelta(days=4)  # how long before we try updating again
pending_threshold = timedelta(days=4)  # for deleting stale pending torrents

def is_old_enough(fetched_at_str):
    """Check if fetched_at in JSON is older than threshold."""
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str)
        return (now - fetched_at) >= threshold
    except Exception:
        return False

def should_delete(fetched_at_str):
    """Check if pending torrent should be deleted."""
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str)
        return (now - fetched_at) > pending_threshold
    except Exception:
        return False

def update_metadata(json_path, is_pending=False):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    torrent_id = data.get("torrent_id")
    fetched_at = data.get("fetched_at")
    first_file_status = data.get("files", [{}])[0].get("status")

    if not torrent_id:
        print(f"Missing torrent_id in {json_path}")
        return

    # Check for stale pending torrents
    if is_pending and first_file_status not in ["downloaded", "magnet_conversion"] and should_delete(fetched_at):
        print(f"Stale pending torrent detected: {torrent_id} - deleting...")
        if delete_rd_torrent(torrent_id):
            parent_dir = os.path.dirname(json_path)
            try:
                for f in os.listdir(parent_dir):
                    file_path = os.path.join(parent_dir, f)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                os.rmdir(parent_dir)
                print(f"Deleted folder: {parent_dir}")
            except Exception as e:
                print(f"Error deleting files in {parent_dir}: {e}")
        return  # Skip rest if deleted

    print(f"Updating links for torrent_id: {torrent_id}")
    direct_links = resolve_rd_id(torrent_id)

    if not direct_links:
        print(f"No links found for torrent_id: {torrent_id}")
        return

    # Current status
    current_status = direct_links[0].get('status') if direct_links else 'unknown'
    ready_links = [link for link in direct_links if link.get('status') == 'downloaded']

    if not ready_links:
        print(f"Links not ready yet for torrent_id: {torrent_id}")
        print(f"Current status: {current_status}")

        if current_status == "magnet_conversion":
            print(f"Torrent {torrent_id} still in magnet conversion phase, updating metadata...")
            updated = {
                "torrent_id": torrent_id,
                "fetched_at": datetime.now().isoformat(),
                "status": "magnet_conversion",
                "message": "Torrent is being processed by Real-Debrid",
                "files": direct_links,
                **{k: data.get(k) for k in ["original_filename", "extracted_title",
                                            "extracted_year", "folder_name",
                                            "season", "episode"]}
            }

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(updated, f, indent=2)
            print(f"Updated magnet conversion status in {json_path}")

        return

    # Ready links, save metadata
    updated = {
        "torrent_id": torrent_id,
        "fetched_at": datetime.now().isoformat(),
        "files": ready_links,
        **{k: data.get(k) for k in ["original_filename", "extracted_title",
                                    "extracted_year", "folder_name",
                                    "season", "episode"]}
    }

    parent_dir = os.path.dirname(json_path)
    date_str = datetime.now().strftime("%Y-%m-%d")
    new_json_name = f"{date_str}.json"
    new_json_path = os.path.join(parent_dir, new_json_name)

    with open(new_json_path, 'w', encoding='utf-8') as f:
        json.dump(updated, f, indent=2)

    # Create .strm files
    for file_info in ready_links:
        original_filename = file_info["filename"]
        strm_filename = original_filename + ".strm"
        strm_path = os.path.join(parent_dir, strm_filename)
        with open(strm_path, 'w', encoding='utf-8') as sf:
            sf.write(file_info["download_url"])

    print(f"Updated {len(ready_links)} links saved to {new_json_path}")

    if is_pending and os.path.exists(json_path):
        os.remove(json_path)
        print(f"Removed old pending file: {os.path.basename(json_path)}")

    time.sleep(15)

def scan_and_update(path):
    if not os.path.exists(path):
        print(f"Path does not exist: {path}")
        return

    for root, dirs, files in os.walk(path):
        json_files = sorted([f for f in files if f.endswith(".json")])

        for fname in json_files:
            if not fname.endswith(".json"):
                continue

            full_path = os.path.join(root, fname)
            try:
                is_pending = fname.endswith("-pending.json")

                # Load JSON and check fetched_at before updating
                with open(full_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                fetched_at = data.get("fetched_at")
                if not fetched_at:
                    print(f"No fetched_at in {fname}, skipping...")
                    continue

                if is_pending:
                    print(f"Checking pending file: {fname}")
                    update_metadata(full_path, is_pending=True)
                elif is_old_enough(fetched_at):
                    print(f"Fetched_at is old enough for: {fname}")
                    update_metadata(full_path)
                else:
                    print(f"Skipping recent file: {fname}")
            except Exception as e:
                print(f"Error processing {fname}: {e}")

def main():
    print("Starting link update process...")
    for path in [movie_path, tv_path, download_path, crawl_path]:
        print(f"\nScanning path: {path}")
        scan_and_update(path)
        time.sleep(15)

if __name__ == "__main__":
    main()