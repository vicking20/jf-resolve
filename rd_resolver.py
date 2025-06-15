import os
import sys
sys.path.insert(0, 'libs')
import requests
import time
from pathlib import Path
from config_loader import load_config
from torrent_converter import torrent_to_magnet
from file_utils import filter_video_files

class RealDebridResolver:
    def __init__(self):
        self.config = load_config()
        self.api_key = self.config.get('RD_API_KEY')
        if not self.api_key:
            raise ValueError("RD_API_KEY not found in configuration")
        self.base_url = "https://api.real-debrid.com/rest/1.0"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    def _api_request(self, method, endpoint, data=None, expected_status=200):
        """Make API request with error handling"""
        url = f"{self.base_url}/{endpoint}"
        response = requests.request(method, url, headers=self.headers, data=data)
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

    def add_magnet(self, magnet_url):
        """Add magnet link to Real-Debrid"""
        return self._api_request('POST', 'torrents/addMagnet', 
                               data={'magnet': magnet_url}, expected_status=201)

    def get_torrent_info(self, torrent_id):
        """Get torrent information"""
        return self._api_request('GET', f'torrents/info/{torrent_id}')

    def select_files(self, torrent_id, file_ids):
        """Select specific files from torrent"""
        self._api_request('POST', f'torrents/selectFiles/{torrent_id}',
                         data={'files': ','.join(map(str, file_ids))}, 
                         expected_status=204)

    def unrestrict_link(self, link):
        """Unrestrict a Real-Debrid link to get direct download"""
        return self._api_request('POST', 'unrestrict/link', data={'link': link})

    def wait_for_download(self, torrent_id, timeout=300):
        """Wait for torrent download status and return it"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            info = self.get_torrent_info(torrent_id)
            status = info.get('status')
            progress = info.get('progress', 0)
            print(f"Status: {status}, Progress: {progress}%")
            if status == "downloaded":
                return {"status": "downloaded", "info": info}
            elif status in ["error", "virus", "dead"]:
                return {"status": status, "info": info}
            time.sleep(1)
        # Timed out but still not downloaded
        return {"status": status, "info": info}

    def process_torrent_files(self, torrent_info):
        """Process torrent files and select all video files"""
        files = torrent_info.get('files', [])
        if not files:
            return []
        print("Analyzing files...")
        video_files = filter_video_files(files)
        if not video_files:
            print("No suitable video files found!")
            return []
        print(f"Found {len(video_files)} video file(s):")
        for i, file_info in enumerate(video_files[:10]):  # Show up to 10 for logging
            print(f"  {i+1}. {file_info['filename']} ({file_info['size_mb']:.1f} MB)")
        #Select ALL filtered video files
        file_ids = [f['id'] for f in video_files]
        print(f"Selecting {len(video_files)} file(s) for download...")
        self.select_files(torrent_info['id'], file_ids)
        return video_files

    def get_direct_links(self, completed_info):
        """Convert Real-Debrid links to direct download links"""
        links = completed_info.get('links', [])
        if not links:
            raise Exception("No download links found")
        print("Getting direct download links...")
        direct_links = []
        for link in links:
            unrestricted = self.unrestrict_link(link)
            download_url = unrestricted.get('download')
            filename = unrestricted.get('filename')
            filesize = unrestricted.get('filesize', 0)
            if download_url:
                direct_links.append({
                    'filename': filename,
                    'download_url': download_url,
                    'filesize': filesize
                })
        return direct_links

    def resolve_file(self, file_path=None, magnet_url=None):
        """Main method to resolve torrent/magnet to direct download links"""
        try:
            # Convert .torrent to magnet if needed
            if file_path and not magnet_url:
                magnet_url = torrent_to_magnet(file_path)
                print("Generated magnet link")
                print(f"{magnet_url[:80]}...")
            # Add magnet to RD
            print("Adding magnet to Real-Debrid...")
            add_result = self.add_magnet(magnet_url)
            torrent_id = add_result.get('id')
            print(f"Torrent added with ID: {torrent_id}")
            # Fetch info and process
            info = self.get_torrent_info(torrent_id)
            info['id'] = torrent_id
            selected_files = self.process_torrent_files(info)
            if not selected_files:
                return []
            # Check download status (non-blocking)
            print("Checking download status...")
            status_result = self.wait_for_download(torrent_id, timeout=5)
            status = status_result["status"]
            if status == "downloaded":
                links = self.get_direct_links(status_result["info"])
                return [
                    {
                        "status": "downloaded",
                        "filename": l["filename"],
                        "download_url": l["download_url"],
                        "filesize": l["filesize"],
                        "torrent_id": torrent_id
                    }
                    for l in links
                ]
            else:
                print(f"Not ready yet (status: {status}), returning file metadata for tracking.")
                return [
                    {
                        "status": status,
                        "filename": f["filename"],
                        "torrent_id": torrent_id,
                        "filesize": f.get("size", 0)
                    }
                    for f in selected_files
                ]
        except Exception as e:
            error_msg = str(e)
            print(f"Error resolving file: {error_msg}")
            raise Exception(error_msg)

def resolve_rd_file(input_arg):
    resolver = RealDebridResolver()
    try:
        if input_arg.startswith('magnet:'):
            print("Processing direct magnet link...")
            results = resolver.resolve_file(magnet_url=input_arg)
        elif input_arg.endswith('.magnet'):
            print("Reading magnet link from .magnet file...")
            with open(input_arg, 'r', encoding='utf-8') as f:
                magnet_link = f.read().strip()
            print(f"Read magnet link: {magnet_link[:80]}...")
            results = resolver.resolve_file(magnet_url=magnet_link, file_path=input_arg)
        elif input_arg.endswith('.torrent'):
            print("Processing .torrent file...")
            results = resolver.resolve_file(file_path=input_arg)
        else:
            raise ValueError("Unsupported file type or input.")
        return results, None
    except Exception as e:
        return [], str(e)

def main():
    """Command-line interface"""
    if len(sys.argv) < 2:
        print("Usage: python rd_resolver.py <torrent_file_or_magnet_url>")
        print("Example: python rd_resolver.py example.torrent")
        print("Example: python rd_resolver.py 'magnet:?xt=urn:btih:...'")
        return
    input_arg = sys.argv[1]
    try:
        links = resolve_rd_file(input_arg)
        if links:
            print(f"\nSuccessfully resolved {len(links)} download link(s):")
            for i, link_info in enumerate(links, 1):
                filesize_mb = link_info.get('filesize', 0) / (1024 * 1024) if link_info.get('filesize') else 0
                print(f"\n{i}. {link_info['filename']}")
                if filesize_mb > 0:
                    print(f" Size: {filesize_mb:.1f} MB")
                print(f" Direct Download: {link_info['download_url']}")
        else:
            print("\nNo download links could be resolved")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()