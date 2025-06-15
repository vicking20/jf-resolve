# delete_rd_torrent.py
import requests
import os
from config_loader import load_config

def delete_rd_torrent(torrent_id):
    """Delete a torrent from Real-Debrid using its torrent ID."""
    config = load_config()
    api_key = config.get('RD_API_KEY')
    if not api_key:
        print("Missing RD_API_KEY in config.")
        return False

    url = f"https://api.real-debrid.com/rest/1.0/torrents/delete/{torrent_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = requests.delete(url, headers=headers)
        if response.status_code == 204:
            print(f"Successfully deleted torrent {torrent_id} from Real-Debrid.")
            return True
        else:
            print(f"Failed to delete torrent {torrent_id}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Exception during RD delete: {e}")
        return False
