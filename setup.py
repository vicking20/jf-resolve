import sys
sys.path.insert(0, 'libs')
import subprocess
import os
import shutil
import time
import socket
import platform
import re
from tzlocal import get_localzone
import yaml
import dotenv
import controller
import configparser
from pathlib import Path
import time

def error_not_found(filename):
    print(f"'{filename}' not found")
    print("\nPlease redownload jf-resolve and run installer again...")
        
def replace_or_append_env(key, value, env_file=".env"):
    updated = False
    lines = []
    if os.path.exists(env_file):
        with open(env_file, "r") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                updated = True
                break
    if not updated:
        lines.append(f"{key}={value}\n")
    with open(env_file, "w") as f:
        f.writelines(lines)

def configure_user_ids_and_timezone():
    print("\nConfiguring environment values (PUID, PGID, TZ)...\n")
    puid, pgid = "1000", "1000"
    if sys.platform in ["linux", "darwin"]:
        try:
            puid = str(os.getuid())
            pgid = str(os.getgid())
            print(f"Detected PUID: {puid}, PGID: {pgid}")
        except Exception:
            print("Could not detect PUID/PGID. Using default 1000.")
    user_input = input("Press Enter to use these values, or type your own (format: PUID,PGID): ").strip()
    if user_input and "," in user_input:
        parts = user_input.split(",")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            puid, pgid = parts[0], parts[1]
    replace_or_append_env("PUID", puid)
    replace_or_append_env("PGID", pgid)
    tz = time.tzname[0]
    tz_input = input(f"Detected timezone is '{tz}'. Press Enter to accept or type a different timezone (e.g., Europe/Helsinki): ").strip()
    if tz_input:
        tz = tz_input
    replace_or_append_env("TZ", tz)
    print(f"\nEnvironment configuration complete:\n- PUID={puid}\n- PGID={pgid}\n- TZ={tz}\n")

def get_local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"

def update_config_ini():
    config_file = "config.ini"
    config = configparser.ConfigParser()
    if not os.path.exists(config_file):
        print(f"{config_file} not found, creating with default values...")
        config["Settings"] = {
            "use_custom_structure": "false",
            "use_jellyseerr": "true",
            "jellyseerr_url": "http://localhost:5055",
            "radarr_blackhole_path": "./media/radarr/blackhole",
            "sonarr_blackhole_path": "./media/sonarr/blackhole",
            "downloads_path": "./media/downloads",
            "jellyfin_crawl_path": "./media/jellyfin/crawl",
            "jellyfin_movie_path": "./media/radarr/movies",
            "jellyfin_tv_shows_path": "./media/sonarr/tvshows",
            "trending": "true",
            "popular_movies": "true",
            "aggresiveness": "2",
            "clean_library_after": "365"
        }
        with open(config_file, "w") as f:
            config.write(f)
    else:
        config.read(config_file)
    print("\n Do you want to use a custom folder structure? (Advanced users only!)")
    print("Default is 'false' (Recommended)")
    use_custom = input("Use custom structure? [false/true]: ").strip().lower()
    if use_custom not in ("true", "false", ""):
        print("Invalid input, defaulting to false")
        use_custom = "false"
    if use_custom == "":
        use_custom = "false"
    config["Settings"]["use_custom_structure"] = use_custom
    arr_path = "./media"
    if os.path.exists(".env"):
        with open(".env") as envf:
            for line in envf:
                if line.startswith("ARRPATH="):
                    arr_path = line.strip().split("=", 1)[1]
                    break
    arr_path = arr_path.rstrip("/\\")
    if use_custom == "false":
        config["Settings"]["radarr_blackhole_path"] = os.path.join(arr_path, "radarr/blackhole").replace("\\", "/")
        config["Settings"]["sonarr_blackhole_path"] = os.path.join(arr_path, "sonarr/blackhole").replace("\\", "/")
        config["Settings"]["downloads_path"] = os.path.join(arr_path, "downloads").replace("\\", "/")
        config["Settings"]["jellyfin_crawl_path"] = os.path.join(arr_path, "jellyfin/crawl").replace("\\", "/")
        config["Settings"]["jellyfin_movie_path"] = os.path.join(arr_path, "radarr/movies").replace("\\", "/")
        config["Settings"]["jellyfin_tv_shows_path"] = os.path.join(arr_path, "sonarr/tvshows").replace("\\", "/")
    else:
        print("\n Enter your custom paths (press enter to keep current/default): ")
        def ask_path(key, current):
            val = input(f"{key} [{current}]: ").strip()
            return val if val else current
        config["Settings"]["radarr_blackhole_path"] = ask_path("radarr_blackhole_path", config["Settings"].get("radarr_blackhole_path", ""))
        config["Settings"]["sonarr_blackhole_path"] = ask_path("sonarr_blackhole_path", config["Settings"].get("sonarr_blackhole_path", ""))
        config["Settings"]["downloads_path"] = ask_path("downloads_path", config["Settings"].get("downloads_path", ""))
        config["Settings"]["jellyfin_crawl_path"] = ask_path("jellyfin_crawl_path", config["Settings"].get("jellyfin_crawl_path", ""))
        config["Settings"]["jellyfin_movie_path"] = ask_path("jellyfin_movie_path", config["Settings"].get("jellyfin_movie_path", ""))
        config["Settings"]["jellyfin_tv_shows_path"] = ask_path("jellyfin_tv_shows_path", config["Settings"].get("jellyfin_tv_shows_path", ""))
    def ask_bool(prompt, default="true"):
        resp = input(f"{prompt} [{default}]: ").strip().lower()
        if resp in ("true", "t", "yes", "y"):
            return "true"
        elif resp in ("false", "f", "no", "n"):
            return "false"
        elif resp == "":
            return default
        else:
            print("Invalid input, defaulting to", default)
            return default
    config["Settings"]["trending"] = ask_bool("Scrape trending? (true/false)", config["Settings"].get("trending", "true"))
    config["Settings"]["popular_movies"] = ask_bool("Scrape popular movies? (true/false)", config["Settings"].get("popular_movies", "true"))
    while True:
        print("Aggresiveness determines how many potential titles are provided from jellyseerr to update your jellyfin library. Maximum should be 500 as each increment of 1 provides potentially 40 title returns")
        aggr = input(f"Aggressiveness (integer, default {config['Settings'].get('aggresiveness','2')}): ").strip()
        if aggr == "":
            aggr = config["Settings"].get("aggresiveness", "2")
        if aggr.isdigit():
            config["Settings"]["aggresiveness"] = aggr
            break
        print("Please enter a valid integer.")
    #configure jellyseerr
    print("\n--- Jellyseerr URL Configuration ---")
    print("Let's set the base URL used to connect to Jellyseerr.")
    print("Current value:", config["Settings"].get("jellyseerr_url", "http://localhost:5055"))
    use_domain = input("Are you using an external domain for Jellyseerr? (recommended no if this is your local machine) (y/N): ").strip().lower()
    if use_domain == "y":
        domain_url = input("Enter the full domain URL (e.g., https://yourdomain.com(:port if needed)): ").strip()
        config["Settings"]["jellyseerr_url"] = domain_url
    else:
        use_ip = input("Are you using a local IP address instead? (y/N): ").strip().lower()
        if use_ip == "y":
            ip_address = get_local_ip()
            print(f"\nDetected local IP address: {ip_address}")
            confirm_ip = input(f"Do you want to use this IP? ({ip_address}) (Y/n): ").strip().lower()
            if confirm_ip == "n":
                ip_address = input("Enter the IP address you want to use: ").strip()
        else:
            ip_address = get_local_ip()
        # Port setup
        default_port = "5055"
        use_default_port = input(f"Use default Jellyseerr port {default_port}? (Y/n): ").strip().lower()
        if use_default_port == "n":
            custom_port = input("Enter the custom port Jellyseerr is running on: ").strip()
        else:
            custom_port = default_port
        config["Settings"]["jellyseerr_url"] = f"http://{ip_address}:{custom_port}"
    with open(config_file, "w") as f:
        config.write(f)
    print("\nConfiguration saved to config.ini.\n")

def set_configurations():
    env_file = ".env"
    config_file = "config.ini"
    missing = []
    if not os.path.exists(env_file):
        missing.append(env_file)
    if not os.path.exists(config_file):
        missing.append(config_file)
    if missing:
        for file in missing:
            error_not_found(file)
        return False
    print("Confguration files found, Continuing...")
    with open(env_file, "r") as f:
        lines = f.readlines()
    arrpath_line_index = None
    for i, line in enumerate(lines):
        if line.strip().startswith("ARRPATH="):
            arrpath_line_index = i
            break
    cwd = os.getcwd()
    default_media_path = os.path.join(cwd, "media")
    default_media_path_str = default_media_path.replace("\\", "\\\\") if os.name == "nt" else default_media_path
    print("Your current working directory is: ")
    print(f" {cwd}\n")
    print("By default, the media folder will be: ")
    print(f"{default_media_path}")
    print("It's recommended that you use the default path to avoid compatibility issues...")
    print("If you want to set a custom ARR path, enter it now (or just press Enter to accept default):")
    print("Examples:")
    print("  ./media/")
    print("  C:\\myfolder\\  (Windows style)\n")
    user_input = input("ARRPATH = ").strip()
    if not user_input:
        env_path = default_media_path.replace("\\", "/")
    else:
        env_path = user_input
    new_line = f"ARRPATH={env_path}\n"
    if arrpath_line_index is not None:
        lines[arrpath_line_index] = new_line
    else:
        lines.append(new_line)
    with open(env_file, "w") as f:
        f.writelines(lines)
    media_folder_path = env_path if os.path.isabs(env_path) else os.path.join(cwd, env_path)
    if not os.path.exists(media_folder_path):
        os.makedirs(media_folder_path)
        print(f"Created media folder at: {media_folder_path}")
    else:
        print(f"Media folder already exists at: {media_folder_path}")
    print("ARRPATH set successfully in .env file.")
    try:
        configure_user_ids_and_timezone()
    except Exception as e:
        print(f"Error configuring user IDs and timezone: {e}")
        return False
    try:
        update_config_ini()
    except Exception as e:
        print(f"Error updating config file: {e}")
        return False
    return True

def try_run_docker_compose():
    try:
        subprocess.run(["docker", "compose", "up", "-d"], check=True)
        print("Docker containers started successfully.")
        return True
    except subprocess.CalledProcessError:
        print("Docker compose failed. This might happen if:")
        print(" - Docker is not installed or not running")
        print(" - You’re not in the docker group (Linux)")
        print(" - You need admin rights (Windows/macOS)")
        print("\nOr may need to run manually with sudo:")
        print("   sudo docker compose up -d")
        print("   (in a new terminal window)")
        input("Once done, press Enter to continue...")
        return False

def load_docker():
    dotenv.load_dotenv(".env")
    arr_path = os.getenv("ARRPATH")
    if not arr_path:
        print("ARRFOLDER path (${ARRPATH}) not set in .env file.")
        return
    arr_path = os.path.abspath(os.path.expanduser(arr_path))
    arr_path = arr_path.rstrip("/\\")
    print(f"Base volume path (ARRPATH): {arr_path}")
    compose_file = "compose.yml"
    if not os.path.exists(compose_file):
        print("compose.yml not found.")
        return
    with open(compose_file, "r") as f:
        compose_data = yaml.safe_load(f)
    folders_to_create = set()
    for service in compose_data.get("services", {}).values():
        volumes = service.get("volumes", [])
        for vol in volumes:
            host_path, *_ = vol.split(":", 1)
            if "${ARRPATH}" in host_path:
                rel_path = host_path.replace("${ARRPATH}", "").lstrip("/")
                full_path = os.path.join(arr_path, rel_path)
                folders_to_create.add(full_path)
    for path in folders_to_create:
        Path(path).mkdir(parents=True, exist_ok=True)
        print(f"Ensured: {path}")
    try_run_docker_compose()
    print("\nDocker setup is complete. Continuing setup...\n")

def show_instructions():
    instructions_file = "instructions.txt"
    if not os.path.exists(instructions_file):
        print("Instructions file not found.")
        return
    print("\n===== SETUP INSTRUCTIONS =====\n")
    with open(instructions_file, "r", encoding="utf-8") as f:
        print(f.read())
    input("\nOnce you've completed the above steps, press Enter to continue...\n")

def paste_api_key():
    env_path = (".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
    else:
        lines = []
    def set_or_replace_env_var(key: str, value: str, lines: list[str]) -> list[str]:
        key_found = False
        new_lines = []
        for line in lines:
            if line.startswith(f"{key}="):
                new_lines.append(f"{key}={value}\n")
                key_found = True
            else:
                new_lines.append(line)
        if not key_found:
            new_lines.append(f"{key}={value}\n")
        return new_lines
    print("\nPaste your Jellyseerr api key key (copied from your jellyseerr settings page): ")
    jelly_key = input("JELLYSEER_API_KEY: ").strip()
    lines = set_or_replace_env_var("JELLYSEER_API_KEY", jelly_key, lines)
    print("\nNow go to your real-debrid account and paste your api key here, you can find it at https://real-debrid.com/devices under API private token, copy and paste the api key here: ")
    rd_key = input("RD_API_KEY: ").strip()
    lines = set_or_replace_env_var("RD_API_KEY", rd_key, lines)
    with open(env_path, "w") as f:
        f.writelines(lines)
    print("\nAPI keys updated successfully in .env file.\n")

def run_controller():
    response = input("Setup complete. Would you like to run the Media Controller now? (y/n): ").strip().lower()
    if response != 'y':
        print("\nYou can start the Media Controller later by running: python controller.py --initiate\n")
        return
    print("\nLaunching Media Controller with initial scan...\n")
    print("You can always run jf-resolve anytime by running: python controller.py --initiate\n")
    system_platform = platform.system()
    if system_platform != "Windows":
        # Ask Unix users if they want foreground or background
        run_mode = input("Run in foreground (attached) or background (detached)? A detached setup for example can keep functioning if youre interacting through an ssh session.. [fg/bg]: ").strip().lower()
        if run_mode == "bg":
            # Run with nohup in background - survives logout
            with open("media_controller.log", "a") as out, open("media_controller.err", "a") as err:
                subprocess.Popen(
                    ["nohup", sys.executable, "controller.py", "--initiate"],
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    preexec_fn=os.setpgrp,
                )
            print("Media Controller is now running in the background (nohup). It will survive logout.")
        else:
            # Run in foreground (blocking)
            subprocess.run([sys.executable, "controller.py", "--initiate"])
    else:
        # Windows: fallback runs new console window
        creationflags = subprocess.CREATE_NEW_CONSOLE
        with open("media_controller.log", "a") as out, open("media_controller.err", "a") as err:
            subprocess.Popen(
                [sys.executable, "controller.py", "--initiate"],
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags
            )
        print("Media Controller is now running in a new console window.")
    
def main(): 
    set_configurations()
    load_docker()
    show_instructions()
    paste_api_key()
    run_controller()

if __name__ == "__main__":
    main()