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
threshold = timedelta(days=30)

def is_old_enough(date_str):
    try:
        date = datetime.strptime(date_str, "%Y-%m-%d")
        return now - date >= threshold
    except ValueError:
        return False
 
def should_delete(fetched_at_str):
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str)
        return (now - fetched_at) > timedelta(days=2)
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

    #Check if it should be deleted before proceeding
    if first_file_status != "downloaded" and should_delete(fetched_at):
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
        return  # Skip the rest if deleted

    print(f"Updating links for torrent_id: {torrent_id}")
    direct_links = resolve_rd_id(torrent_id)
    if not direct_links:
        print(f"No links found for torrent_id: {torrent_id}")
        return

    ready_links = [link for link in direct_links if link.get('status') == 'downloaded']
    if not ready_links:
        print(f"Links not ready yet for torrent_id: {torrent_id}")
        print(f"Current status: {direct_links[0].get('status') if direct_links else 'unknown'}")
        return

    updated = {
        "torrent_id": torrent_id,
        "fetched_at": datetime.now().isoformat(),
        "files": ready_links,
    }

    parent_dir = os.path.dirname(json_path)
    date_str = datetime.now().strftime("%Y-%m-%d")
    new_json_name = f"{date_str}.json"
    new_json_path = os.path.join(parent_dir, new_json_name)

    with open(new_json_path, 'w', encoding='utf-8') as f:
        json.dump(updated, f, indent=2)

    for file_info in ready_links:
        base_name = os.path.splitext(file_info["filename"])[0]
        strm_path = os.path.join(parent_dir, base_name + ".strm")
        with open(strm_path, 'w', encoding='utf-8') as sf:
            sf.write(file_info["download_url"])

    print(f"Updated {len(ready_links)} links saved to {new_json_path}")

    # If it was a pending file, remove it
    if is_pending and os.path.exists(json_path):
        os.remove(json_path)
        print(f"Removed old pending file: {os.path.basename(json_path)}")

    time.sleep(15)

def scan_and_update(path):
    if not os.path.exists(path):
        print(f"Path does not exist: {path}")
        return
    for root, dirs, files in os.walk(path):
        pending_files = sorted([f for f in files if f.endswith("-pending.json")])
        regular_files = sorted([f for f in files if f.endswith(".json") and not f.endswith("-pending.json")])
        for fname in pending_files + regular_files:
            if fname.endswith('.json'):
                full_path = os.path.join(root, fname)
                try:
                    is_pending = fname.endswith('-pending.json')
                    name_part = fname.replace('-pending.json', '').replace('.json', '')

                    if is_pending:
                        print(f"Checking pending file: {fname}")
                        update_metadata(full_path, is_pending=True)
                    elif is_old_enough(name_part):
                        print(f"Processing aged file: {fname}")
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