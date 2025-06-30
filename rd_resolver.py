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
        if not file_ids:
            print("No file IDs provided for selection")
            return
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
            status = info.get('status', 'unknown')
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
        
        # Handle empty files array (e.g., during magnet_conversion)
        if not files:
            status = torrent_info.get('status', 'unknown')
            if status == 'magnet_conversion':
                print("Torrent is in magnet conversion phase - no files available yet")
                return []
            else:
                print("No files found in torrent!")
                return []
        
        print("Analyzing files...")
        video_files = filter_video_files(files)
        if not video_files:
            print("No suitable video files found!")
            return []
        
        print(f"Found {len(video_files)} video file(s):")
        for i, file_info in enumerate(video_files[:10]):  # Show up to 10 for logging
            print(f"  {i+1}. {file_info['filename']} ({file_info['size_mb']:.1f} MB)")
        
        # Select ALL filtered video files
        file_ids = [f['id'] for f in video_files]
        print(f"Selecting {len(video_files)} file(s) for download...")
        self.select_files(torrent_info['id'], file_ids)
        return video_files

    def get_direct_links(self, completed_info):
        """Convert Real-Debrid links to direct download links"""
        links = completed_info.get('links', [])
        
        # Handle empty links array
        if not links:
            status = completed_info.get('status', 'unknown')
            print(f"No download links found (status: {status})")
            return []
        
        print("Getting direct download links...")
        direct_links = []
        for link in links:
            try:
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
            except Exception as e:
                print(f"Failed to unrestrict link {link}: {e}")
                continue
        
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
            status = info.get('status', 'unknown')
            
            # Handle different statuses
            if status == 'magnet_conversion':
                print("Torrent is in magnet conversion phase")
                return [{
                    "status": "magnet_conversion",
                    "torrent_id": torrent_id,
                    "message": "Torrent is being processed, check back later"
                }]
            
            selected_files = self.process_torrent_files(info)
            
            # If no files were selected (empty or magnet_conversion), return status info
            if not selected_files:
                return [{
                    "status": status,
                    "torrent_id": torrent_id,
                    "message": f"No files available (status: {status})"
                }]
            
            # Check download status (non-blocking)
            print("Checking download status...")
            status_result = self.wait_for_download(torrent_id, timeout=5)
            current_status = status_result["status"]
            
            if current_status == "downloaded":
                links = self.get_direct_links(status_result["info"])
                if links:
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
                    # Downloaded but no links available
                    return [{
                        "status": "downloaded_no_links",
                        "torrent_id": torrent_id,
                        "message": "Download completed but no links available"
                    }]
            else:
                print(f"Not ready yet (status: {current_status}), returning file metadata for tracking.")
                return [
                    {
                        "status": current_status,
                        "filename": f["filename"],
                        "torrent_id": torrent_id,
                        "filesize": f.get("size", 0)
                    }
                    for f in selected_files
                ]
                
        except Exception as e:
            error_msg = str(e)
            print(f"Error resolving file: {error_msg}")
            # Return error info with torrent_id if available
            error_result = {
                "status": "error",
                "message": error_msg
            }
            if 'torrent_id' in locals():
                error_result["torrent_id"] = torrent_id
            return [error_result]

    def check_torrent_status(self, torrent_id):
        """Check the status of a specific torrent by ID"""
        try:
            info = self.get_torrent_info(torrent_id)
            status = info.get('status', 'unknown')
            progress = info.get('progress', 0)
            
            result = {
                "torrent_id": torrent_id,
                "status": status,
                "progress": progress,
                "filename": info.get('filename', 'Unknown'),
                "bytes": info.get('bytes', 0)
            }
            
            if status == "downloaded":
                links = self.get_direct_links(info)
                if links:
                    result["download_links"] = links
                else:
                    result["message"] = "Downloaded but no links available"
            elif status == "magnet_conversion":
                result["message"] = "Still converting magnet link"
            elif status in ["error", "virus", "dead"]:
                result["message"] = f"Torrent failed with status: {status}"
            else:
                result["message"] = f"Torrent is {status} ({progress}%)"
            
            return result
        except Exception as e:
            return {
                "torrent_id": torrent_id,
                "status": "error",
                "message": str(e)
            }

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

def check_rd_torrent_status(torrent_id):
    """Check status of a specific torrent by ID"""
    resolver = RealDebridResolver()
    return resolver.check_torrent_status(torrent_id)

def main():
    """Command-line interface"""
    if len(sys.argv) < 2:
        print("Usage: python rd_resolver.py <torrent_file_or_magnet_url>")
        print("       python rd_resolver.py --check <torrent_id>")
        print("Example: python rd_resolver.py example.torrent")
        print("Example: python rd_resolver.py 'magnet:?xt=urn:btih:...'")
        print("Example: python rd_resolver.py --check JCYURNPMTREEY")
        return
    
    if sys.argv[1] == "--check" and len(sys.argv) >= 3:
        torrent_id = sys.argv[2]
        print(f"Checking status for torrent ID: {torrent_id}")
        status_info = check_rd_torrent_status(torrent_id)
        print(f"\nTorrent Status:")
        print(f"ID: {status_info['torrent_id']}")
        print(f"Status: {status_info['status']}")
        print(f"Progress: {status_info['progress']}%")
        print(f"Filename: {status_info['filename']}")
        if 'message' in status_info:
            print(f"Message: {status_info['message']}")
        if 'download_links' in status_info:
            print(f"Download links available: {len(status_info['download_links'])}")
        return
    
    input_arg = sys.argv[1]
    try:
        links, error = resolve_rd_file(input_arg)
        if error:
            print(f"Error: {error}")
            return
            
        if links:
            print(f"\nResolved {len(links)} result(s):")
            for i, link_info in enumerate(links, 1):
                print(f"\n{i}. Status: {link_info['status']}")
                if 'filename' in link_info:
                    print(f"   Filename: {link_info['filename']}")
                if 'torrent_id' in link_info:
                    print(f"   Torrent ID: {link_info['torrent_id']}")
                if 'filesize' in link_info and link_info['filesize']:
                    filesize_mb = link_info['filesize'] / (1024 * 1024)
                    print(f"   Size: {filesize_mb:.1f} MB")
                if 'download_url' in link_info:
                    print(f"   Direct Download: {link_info['download_url']}")
                if 'message' in link_info:
                    print(f"   Message: {link_info['message']}")
        else:
            print("\nNo results returned")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()