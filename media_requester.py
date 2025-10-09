#media_requester.py
from config_loader import load_config
import requests
import time
import json
import os

cfg = load_config()
CACHE_FILE = "requested_cache.json"
EXCLUDE_FILE = "exclude_ids.json"
base_url = cfg["JELLYSEERR_URL"].rstrip("/")
HEADERS = {
    "X-Api-Key": cfg["JELLYSEER_API_KEY"]
}

# Use a session for connection reuse
session = requests.Session()
session.headers.update(HEADERS)

def load_json_file(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json_file(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def get_exclude_ids():
    data = load_json_file(EXCLUDE_FILE)
    # Normalize keys to string sets for easy comparison
    return {k: set(map(str, v)) for k, v in data.items()}

def get_requested_cache():
    return load_json_file(CACHE_FILE)

def add_to_requested_cache(cache, media_type, media_id):
    key = f"{media_type}:{media_id}"
    cache[key] = True

def get_js_auth():
    auth_url = f"{base_url}/api/v1/auth/me"
    if not cfg.get("USE_JELLYSEERR", False):
        print("Jellyseerr requests disabled")
        return None
    try:
        resp = session.get(auth_url)
        resp.raise_for_status()
        user_id = resp.json().get("id")
        print(f"Authenticated as user ID: {user_id}")
        return user_id
    except requests.RequestException as e:
        print(f"Failed to authenticate with Jellyseerr: {e}")
        return None

def get_discover_ids():
    if not cfg.get("USE_JELLYSEERR", False):
        print("Skipping Jellyseerr discover fetch")
        return []

    exclude_ids = get_exclude_ids()
    requested_cache = get_requested_cache()
    ids = []

    aggr = cfg.get("aggresiveness", 1)
    try:
        aggr = int(aggr)
    except ValueError:
        aggr = 1
    aggr = max(1, min(500, aggr))

    def fetch_and_collect(endpoint: str):
        for page in range(1, aggr + 1):
            try:
                url = f"{base_url}/api/v1/discover/{endpoint}?page={page}"
                resp = session.get(url)
                resp.raise_for_status()
                data = resp.json()
                page_items = []
                for item in data.get("results", []):
                    media_type = item.get("mediaType", "movie")
                    media_id = item.get("id")
                    if not media_id:
                        continue
                    media_id_str = str(media_id)

                    # Check exclusion
                    if media_type in exclude_ids and media_id_str in exclude_ids[media_type]:
                        print(f"Skipping excluded {media_type} ID {media_id}")
                        continue
                    # Check cache
                    key = f"{media_type}:{media_id_str}"
                    if key in requested_cache:
                        print(f"Skipping already requested {media_type} ID {media_id}")
                        continue

                    page_items.append({
                        "mediaType": media_type,
                        "mediaId": media_id 
                    })
                print(f"Fetched {len(page_items)} new items from {endpoint}, page {page}")
                ids.extend(page_items)
            except requests.RequestException as e:
                print(f"Error fetching {endpoint}, page {page}: {e}")
                break

    if cfg.get("trending", False):
        fetch_and_collect("trending")
    if cfg.get("popular_movies", False):
        fetch_and_collect("movies")

    print(f"Total new collected IDs: {len(ids)}")
    return ids

def update_requested_cache_from_jellyseerr():
    """
    Syncs local cache with all requests from Jellyseerr
    """
    requested_cache = get_requested_cache()
    page = 1
    total_pages = 1

    while page <= total_pages:
        try:
            resp = session.get(f"{base_url}/request?page={page}")
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"Failed to fetch requests: {e}")
            break

        total_pages = data.get("pageInfo", {}).get("pages", 1)
        results = data.get("results", [])

        for r in results:
            media = r.get("media", {})
            media_type = media.get("mediaType")
            media_id = media.get("id")
            if media_type and media_id:
                key = f"{media_type}:{media_id}"
                requested_cache[key] = True

        page += 1
        time.sleep(0.2)

    save_json_file(CACHE_FILE, requested_cache)
    print(f"Updated cache with {len(requested_cache)} total requests from Jellyseerr.")
    return requested_cache

def make_requests(media_items, user_id):
    request_url = f"{base_url}/api/v1/request"
    count = 0
    requested_cache = get_requested_cache()
    for item in media_items:
        data = {
            "mediaType": item["mediaType"],
            "mediaId": item["mediaId"],
            "userId": user_id
        }
        if item["mediaType"] == "tv":
            data["seasons"] = [1]  # only first season youll need to make a request manually for more seasons in jellyseerr

        try:
            print(f"Sending payload: {data}")
            resp = session.post(request_url, json=data)
            resp.raise_for_status()
            print(f"Requested {item['mediaType']} ID {item['mediaId']}")
            count += 1
            add_to_requested_cache(requested_cache, item["mediaType"], str(item["mediaId"]))
            save_json_file(CACHE_FILE, requested_cache)
        except requests.RequestException as e:
            print(f"Failed to request {item['mediaType']} ID {item['mediaId']}: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    print(f"Error details: {e.response.json()}")
                except:
                    print(f"Response text: {e.response.text}")
        time.sleep(0.5)

    print(f"\nTotal successful requests: {count}")

def run_jellyseerr_discovery():
    user_id = get_js_auth()
    if not user_id:
        return

    update_requested_cache_from_jellyseerr()
    media_items = get_discover_ids()
    if media_items:
        make_requests(media_items, user_id)

if __name__ == "__main__":
    run_jellyseerr_discovery()