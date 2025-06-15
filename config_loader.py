import sys
sys.path.insert(0, 'libs')
from dotenv import dotenv_values
import configparser
from pathlib import Path

def load_config():
    # Load from .env
    env = dotenv_values(".env")
    # Load from config.ini
    config = configparser.ConfigParser()
    config.read("config.ini")
    # Default base path
    base_path = Path(env.get("ARRPATH", "./media/")).resolve()
    def resolve_path(section, key, fallback_relative):
        path = config.get(section, key, fallback=str(base_path / fallback_relative))
        return str(Path(path).resolve())
    merged = {
        # ENV values
        "ARRPATH": str(base_path),
        "PUID": env.get("PUID", "1000"),
        "PGID": env.get("PGID", "1000"),
        "TZ": env.get("TZ", "Europe/Helsinki"),
        "RD_API_KEY": env.get("RD_API_KEY", ""),
        "JELLYSEER_API_KEY": env.get("JELLYSEER_API_KEY", ""),
        # INI logic
        "USE_CUSTOM_STRUCTURE": config.getboolean("Settings", "use_custom_structure", fallback=False),
        "USE_JELLYSEERR": config.getboolean("Settings", "use_jellyseerr", fallback=True),
        "JELLYSEERR_URL": config.get("Settings", "jellyseerr_url", fallback="http://localhost:5055"),
        "trending": config.get("Settings", "trending", fallback=False),
        "popular_movies": config.get("Settings", "popular_movies", fallback=False),
        "aggresiveness": config.get("Settings", "aggresiveness", fallback="1"),
        # Path resolution
        "RADARR_BLACKHOLE": resolve_path("Settings", "radarr_blackhole_path", "radarr/blackhole"),
        "SONARR_BLACKHOLE": resolve_path("Settings", "sonarr_blackhole_path", "sonarr/blackhole"),
        "DOWNLOADS_PATH": resolve_path("Settings", "downloads_path", "downloads"),
        "JELLYFIN_CRAWL_PATH": resolve_path("Settings", "jellyfin_crawl_path", "jellyfin/crawl"),
        "JELLYFIN_MOVIE_PATH": resolve_path("Settings", "jellyfin_movie_path", "radarr/movies"),
        "JELLYFIN_TV_PATH": resolve_path("Settings", "jellyfin_tv_shows_path", "sonarr/tvshows"),
    }
    return merged
# For debug / verification
if __name__ == "__main__":
    config = load_config()
    for key, value in config.items():
        print(f"{key}: {value}")
