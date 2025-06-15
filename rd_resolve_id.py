import sys
sys.path.insert(0, 'libs')
import requests
from config_loader import load_config

def resolve_rd_id(torrent_id):
    try:
        # Load config and setup API
        config = load_config()
        api_key = config.get('RD_API_KEY')
        if not api_key:
            print("Error: RD_API_KEY not found in configuration")
            return []
        base_url = "https://api.real-debrid.com/rest/1.0"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        def _api_request(method, endpoint, data=None, expected_status=200):
            """Make API request with error handling"""
            url = f"{base_url}/{endpoint}"
            response = requests.request(method, url, headers=headers, data=data)
            if response.status_code != expected_status:
                error_msg = f"API request failed: {response.status_code}"
                try:
                    error_details = response.json()
                    if 'error' in error_details:
                        error_msg = f"Real-Debrid API Error: {error_details['error']}"
                except:
                    pass
                raise Exception(error_msg)
            return response.json() if response.text else None
        # Get torrent info
        print(f"Getting torrent info for ID: {torrent_id}")
        info = _api_request('GET', f'torrents/info/{torrent_id}')
        if not info:
            print("No torrent info found")
            return []
        status = info.get('status')
        print(f"Torrent status: {status}")
        # Check if torrent is downloaded
        if status != "downloaded":
            print(f"Torrent not ready yet (status: {status})")
            # Return partial info for tracking purposes
            files = info.get('files', [])
            return [
                {
                    "status": status,
                    "filename": f.get("path", "Unknown file"),
                    "torrent_id": torrent_id,
                    "filesize": f.get("bytes", 0)
                }
                for f in files if f.get("selected", 0) == 1
            ]
        # Get download links
        links = info.get('links', [])
        if not links:
            print("No download links found")
            return []
        print(f"Found {len(links)} download link(s), getting direct URLs...")
        direct_links = []
        for link in links:
            try:
                # Unrestrict each link
                unrestricted = _api_request('POST', 'unrestrict/link', data={'link': link})
                download_url = unrestricted.get('download')
                filename = unrestricted.get('filename')
                filesize = unrestricted.get('filesize', 0)                
                if download_url:
                    direct_links.append({
                        'status': 'downloaded',
                        'filename': filename,
                        'download_url': download_url,
                        'filesize': filesize,
                        'torrent_id': torrent_id
                    })
                    print(f"{filename}")
                else:
                    print(f"Failed to unrestrict link: {link}")
            except Exception as e:
                print(f"Error unrestricting link {link}: {e}")
                continue
        print(f"Successfully resolved {len(direct_links)} direct download link(s)")
        return direct_links
    except Exception as e:
        print(f"Error resolving torrent ID {torrent_id}: {e}")
        return []

def main():
    """Command-line interface for testing"""
    if len(sys.argv) < 2:
        print("Usage: python rd_id_resolver.py <torrent_id>")
        print("Example: python rd_id_resolver.py ABCD1234567890")
        return    
    torrent_id = sys.argv[1]
    links = resolve_rd_id(torrent_id)
    if links:
        print(f"\nResolved {len(links)} link(s) for torrent ID {torrent_id}:")
        for i, link_info in enumerate(links, 1):
            filesize_mb = link_info.get('filesize', 0) / (1024 * 1024) if link_info.get('filesize') else 0
            print(f"\n{i}. {link_info['filename']}")
            print(f"   Status: {link_info['status']}")
            if filesize_mb > 0:
                print(f"   Size: {filesize_mb:.1f} MB")
            if link_info.get('download_url'):
                print(f"   Direct Download: {link_info['download_url']}")
    else:
        print(f"\nNo links could be resolved for torrent ID: {torrent_id}")

if __name__ == "__main__":
    main()