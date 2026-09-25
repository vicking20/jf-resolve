import os
import requests
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

VENDOR_ASSETS = {
    "js": {
        "tailwind.js": "https://cdn.tailwindcss.com",
        "alpine.min.js": "https://cdn.jsdelivr.net/npm/alpinejs@3.13.3/dist/cdn.min.js",
        "hls.js": "https://cdn.jsdelivr.net/npm/hls.js@1.5.8/dist/hls.min.js",
        "artplayer.js": "https://cdn.jsdelivr.net/npm/artplayer@5.1.7/dist/artplayer.js",
    },
    "css": {
        "daisyui.min.css": "https://cdn.jsdelivr.net/npm/daisyui@4.4.19/dist/full.min.css",
    }
}

def ensure_vendor_assets():
    """Download missing vendor assets during startup"""
    # Get base directory (project root)
    base_dir = Path(__file__).resolve().parent.parent.parent
    vendor_dir = base_dir / "frontend" / "static" / "vendor"
    
    # Ensure directories exist
    os.makedirs(vendor_dir / "js", exist_ok=True)
    os.makedirs(vendor_dir / "css", exist_ok=True)
    
    missing_count = 0
    
    for type_dir, assets in VENDOR_ASSETS.items():
        for filename, url in assets.items():
            dest_path = vendor_dir / type_dir / filename
            if not dest_path.exists():
                print(f"Downloading vendor asset: {filename}...")
                try:
                    response = requests.get(url, timeout=30)
                    response.raise_for_status()
                    content = response.content
                    
                    # Patch tailwind to remove production warning
                    if filename == "tailwind.js":
                        warning_msg = b'console.warn("cdn.tailwindcss.com should not be used in production. To use Tailwind CSS in production, install it as a PostCSS plugin or use the Tailwind CLI: https://tailwindcss.com/docs/installation");'
                        content = content.replace(warning_msg, b'')
                    
                    dest_path.write_bytes(content)
                    missing_count += 1
                except Exception as e:
                    logger.error(f"Failed to download {filename}: {e}")
                    print(f"Failed to download {filename}: {e}")
    
    if missing_count > 0:
        logger.info(f"Successfully downloaded {missing_count} vendor assets.")
        print(f"Downloaded {missing_count} vendor assets.")
    else:
        logger.debug("All vendor assets already present.")
