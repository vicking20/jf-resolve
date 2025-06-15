import sys
sys.path.insert(0, 'libs')
import hashlib
import base64
import urllib.parse
import bencodepy

def torrent_to_magnet(torrent_file_path):
    try:
        # Read and decode torrent file
        with open(torrent_file_path, 'rb') as f:
            torrent_data = f.read()        
        decoded_data = bencodepy.decode(torrent_data)
        # Calculate info hash
        info = decoded_data[b'info']
        info_bencoded = bencodepy.encode(info)
        info_hash = hashlib.sha1(info_bencoded).digest()
        info_hash_b32 = base64.b32encode(info_hash).decode()
        # Build magnet link
        magnet_link = f"magnet:?xt=urn:btih:{info_hash_b32}"
        # Add display name
        if b'name' in info:
            name = info[b'name'].decode('utf-8', errors='replace')
            magnet_link += f"&dn={urllib.parse.quote(name)}"
        # Add trackers
        magnet_link = _add_trackers(magnet_link, decoded_data)
        return magnet_link
    except Exception as e:
        raise Exception(f"Failed to convert torrent to magnet: {e}")

def _add_trackers(magnet_link, decoded_data):
    """Add trackers to magnet link"""
    # Single announce
    if b'announce' in decoded_data:
        announce = decoded_data[b'announce']
        if isinstance(announce, list):
            for tracker in announce:
                magnet_link += f"&tr={urllib.parse.quote(tracker.decode())}"
        else:
            magnet_link += f"&tr={urllib.parse.quote(announce.decode())}"
    # Announce list
    if b'announce-list' in decoded_data:
        for tier in decoded_data[b'announce-list']:
            if isinstance(tier, list):
                for tracker in tier:
                    magnet_link += f"&tr={urllib.parse.quote(tracker.decode())}"
    return magnet_link