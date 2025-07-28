from config_loader import load_config
from rd_resolver import resolve_rd_file
from datetime import datetime
import json
import shutil
import os
import re
import time
from delete_rd_torrent import delete_rd_torrent

cfg = load_config()
radarr_blackhole_path = cfg["RADARR_BLACKHOLE"]
sonarr_blackhole_path = cfg["SONARR_BLACKHOLE"]
movie_output_root = cfg.get("JELLYFIN_MOVIE_PATH", "./media/radarr/movies")
tv_output_root = cfg.get("JELLYFIN_TV_PATH", "./media/sonarr/tvshows")

# Rate limiting
MAX_REQUESTS_PER_MINUTE = 20
request_times = []

def rate_limit():
    """Implement rate limiting to stay under API limits"""
    global request_times
    current_time = time.time()
    # Remove requests older than 1 minute
    request_times = [t for t in request_times if current_time - t < 60]
    # If we're at the limit, wait
    if len(request_times) >= MAX_REQUESTS_PER_MINUTE:
        sleep_time = 60 - (current_time - request_times[0]) + 1  # Add 1 second buffer
        print(f"Rate limit reached. Sleeping for {sleep_time:.1f} seconds...")
        time.sleep(sleep_time)
        # Clean up old requests after sleeping
        current_time = time.time()
        request_times = [t for t in request_times if current_time - t < 60]
    # Record this request
    request_times.append(current_time)

def normalize_for_comparison(text):
    """Normalize text for fuzzy matching by removing punctuation, extra spaces, etc."""
    if not text:
        return ""
    # Convert to lowercase
    text = text.lower()
    # Remove common punctuation and replace with spaces
    text = re.sub(r'[:\-_.,!?()[\]{}"\']', ' ', text)
    # Remove extra whitespace
    text = ' '.join(text.split())
    return text.strip()

def extract_title_year_from_filename(filename):
    """Extract title and year from filename with better accuracy"""
    # Remove file extension
    name = re.sub(r'\.(magnet|torrent)$', '', filename, flags=re.IGNORECASE)
    
    # Look for year pattern (4 digits between 1900-2099)
    year_match = re.search(r'\b(19|20)\d{2}\b', name)
    year = None
    title_part = name
    
    if year_match:
        year = year_match.group()
        # Everything before the year is likely the title
        title_part = name[:year_match.start()].strip()
        # Also check if there's content after the year that might be part of title
        after_year = name[year_match.end():].strip()
        # If what comes after year looks like quality info, ignore it
        if not re.match(r'^[\s\-_.]*\b(720p|1080p|4K|2160p|BluRay|WEB-DL|WEBRip|HEVC|x265|x264|YTS|MeGusta|EZTV)', after_year, re.IGNORECASE):
            # Might be part of title, but be conservative
            pass
    
    # Clean up the title part
    # Remove quality indicators and other metadata
    title_part = re.sub(r'\b(720p|1080p|4K|2160p|BluRay|WEB-DL|WEBRip|HEVC|x265|x264|YTS|MeGusta|EZTV|HorribleSubs|AMZN|MAX|HDR|PROPER|REPACK)\b.*$', '', title_part, flags=re.IGNORECASE)
    
    # Replace dots, dashes, underscores with spaces
    title_part = re.sub(r'[\.\-_]+', ' ', title_part)
    
    # Remove extra whitespace and clean up
    title_part = ' '.join(title_part.split()).strip()
    
    # Remove trailing punctuation
    title_part = re.sub(r'[^\w\s]+$', '', title_part).strip()
    
    return title_part, year

def find_existing_movie_folder(title, year, movie_output_root):
    """Find existing movie folder with fuzzy matching, prioritizing better formatting"""
    if not os.path.exists(movie_output_root):
        return None
    
    # Normalize the search title for comparison
    search_title_normalized = normalize_for_comparison(title)
    
    # Get all existing folders
    existing_folders = []
    for item in os.listdir(movie_output_root):
        item_path = os.path.join(movie_output_root, item)
        if os.path.isdir(item_path):
            existing_folders.append(item)
    
    # Try different matching strategies
    matches = []
    
    for folder in existing_folders:
        # Extract title and year from existing folder
        folder_year_match = re.search(r'\((\d{4})\)', folder)
        folder_year = folder_year_match.group(1) if folder_year_match else None
        
        # Get title part (everything before year in parentheses, or whole name)
        if folder_year_match:
            folder_title = folder[:folder_year_match.start()].strip()
        else:
            folder_title = folder
        
        folder_title_normalized = normalize_for_comparison(folder_title)
        
        # Check for quality indicators for prioritization
        has_year_in_brackets = folder_year_match is not None
        has_dash_in_title = ' - ' in folder_title or '-' in folder_title
        
        # Base score calculation
        base_score = 0
        
        # Strategy 1: Exact normalized match with year consideration
        if search_title_normalized == folder_title_normalized:
            if year and folder_year:
                if year == folder_year:
                    base_score = 100  # Perfect match
                else:
                    base_score = 80   # Title match, year mismatch
            elif not year and not folder_year:
                base_score = 95   # Both have no year
            else:
                base_score = 85   # One has year, other doesn't
        
        # Strategy 2: Check if one title contains the other (for cases like "Movie" vs "Movie Title")
        elif (search_title_normalized in folder_title_normalized or 
              folder_title_normalized in search_title_normalized):
            year_score = 0
            if year and folder_year and year == folder_year:
                year_score = 20
            base_score = 60 + year_score
        
        # Apply priority bonuses if we have a valid match
        if base_score > 0:
            # Priority bonus for year in brackets (e.g., "Movie (2024)")
            if has_year_in_brackets:
                base_score += 15
                print(f"  Bonus +15 for year in brackets: '{folder}'")
            
            # Priority bonus for dash in title (e.g., "Avengers - Infinity War")
            if has_dash_in_title:
                base_score += 10
                print(f"  Bonus +10 for dash in title: '{folder}'")
            
            # Additional bonus for having both formatting features
            if has_year_in_brackets and has_dash_in_title:
                base_score += 5
                print(f"  Bonus +5 for both year brackets and dash: '{folder}'")
            
            matches.append((folder, base_score))
    
    # Sort by score and return best match if score is high enough
    if matches:
        matches.sort(key=lambda x: x[1], reverse=True)
        best_match, score = matches[0]
        if score >= 80:  # Only accept high-confidence matches
            print(f"Found existing folder match: '{best_match}' (score: {score})")
            return best_match
    
    return None

def find_existing_show_folder(title, tv_output_root):
    """Find existing show folder with case-insensitive matching"""
    if not os.path.exists(tv_output_root):
        return None
    
    normalized_title = normalize_for_comparison(title)
    
    for existing_folder in os.listdir(tv_output_root):
        existing_path = os.path.join(tv_output_root, existing_folder)
        if os.path.isdir(existing_path):
            existing_normalized = normalize_for_comparison(existing_folder)
            if existing_normalized == normalized_title:
                print(f"Found existing show folder match: '{existing_folder}'")
                return existing_folder
    
    return None

def extract_tv_show_title(filename):
    """Extract TV show title from filename with improved robustness"""
    # Remove file extension
    name = re.sub(r'\.(magnet|torrent)$', '', filename, flags=re.IGNORECASE)
    
    # More comprehensive patterns to identify where the title ends
    title_end_patterns = [
        # Season/Episode patterns
        r'\bS\d{1,2}E\d{1,2}\b',                    # S01E01
        r'\bS\d{1,2}\.E\d{1,2}\b',                  # S01.E01
        r'\bS\d{1,2}\s*-\s*E\d{1,2}\b',            # S01 - E01
        r'\bSeason\s*\d{1,2}\s*Episode\s*\d{1,2}\b', # Season 1 Episode 1
        r'\b\d{1,2}x\d{1,2}\b',                     # 1x01
        r'\bS\d{1,2}\.COMPLETE\b',                  # S01.COMPLETE
        r'\bS\d{1,2}\b',                            # S01
        r'\bSeason\s*\d{1,2}\b',                    # Season 1
        # Quality/format indicators
        r'\b(?:720p|1080p|4K|2160p|BluRay|WEB-DL|WEBRip|HEVC|x265|x264|YTS|MeGusta|EZTV|HorribleSubs|AMZN|MAX|HDR|PROPER|REPACK)\b',
        # Release group patterns
        r'\[.*?\]',   
        # Year patterns
        r'\b(?:19|20)\d{2}\b(?!\s*-)',
    ]
    
    earliest_match_pos = len(name)
    for pattern in title_end_patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match and match.start() < earliest_match_pos:
            earliest_match_pos = match.start()
    
    if earliest_match_pos < len(name):
        title_part = name[:earliest_match_pos].strip()
    else:
        title_part = name
    
    # Clean up the title
    title_part = re.sub(r'[\.\-_]+', ' ', title_part)
    title_part = ' '.join(title_part.split()).strip()
    title_part = re.sub(r'[^\w\s]+$', '', title_part).strip()
    title_part = re.sub(r'\s+\d+$', '', title_part).strip()
    
    return title_part

def extract_season_episode_info(filename):
    """Extract season and episode information from filename"""
    # Season/Episode patterns
    season_episode_patterns = [
        r'S(\d{1,2})E(\d{1,2})',  # S01E01
        r'Season[^\d]*(\d{1,2})[^\d]*Episode[^\d]*(\d{1,2})',  # Season 1 Episode 1
        r'(\d{1,2})x(\d{1,2})',  # 1x01
    ]
    for pattern in season_episode_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    
    # Season-only patterns
    season_patterns = [
        r'S(\d{1,2})\.COMPLETE',  # S01.COMPLETE
        r'S(\d{1,2})\b',          # S01
        r'Season[^\d]*(\d{1,2})',  # Season 1
    ]
    for pattern in season_patterns:
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return int(match.group(1)), None
    
    return 1, None  # Default to Season 1

def handle_file_error(error_msg, file_path):
    if "infringing_file" in error_msg:
        print("Renaming file due to infringing_file error...")
        base, _ = os.path.splitext(file_path)
        new_path = base
        try:
            os.rename(file_path, new_path)
            print(f"Renamed '{file_path}' to '{new_path}'")
        except Exception as e:
            print(f"Failed to rename: {e}")
    elif "too_many_active_downloads" in error_msg:
        print("Too many active downloads. Sleeping for 10 minutes...")
        time.sleep(600)
    elif "too_many_requests" in error_msg:
        print("Rate limit hit. Sleeping for 1 minute...")
        time.sleep(60)

# def process_movie_file(file, full_path):
#     """Process a movie file with improved folder matching"""
#     print(f"\nProcessing movie: {file}")
    
#     # Extract title and year from filename
#     title, year = extract_title_year_from_filename(file)
#     print(f"Extracted title: '{title}'" + (f", year: {year}" if year else ""))
    
#     # First, try to find an existing folder
#     existing_folder = find_existing_movie_folder(title, year, movie_output_root)
    
#     if existing_folder:
#         # Use existing folder
#         folder_name = existing_folder
#         movie_folder = os.path.join(movie_output_root, folder_name)
#         print(f"Using existing folder: '{folder_name}'")
#     else:
#         # Create new folder name
#         if year:
#             folder_name = f"{title} ({year})"
#         else:
#             folder_name = title
#         movie_folder = os.path.join(movie_output_root, folder_name)
#         print(f"Creating new folder: '{folder_name}'")
#         os.makedirs(movie_folder, exist_ok=True)
    
#     # Apply rate limiting before making API calls
#     rate_limit()
     
#     results, error_msg = resolve_rd_file(full_path)
#     if error_msg:
#         print(f"Resolve error: {error_msg}")
#         handle_file_error(error_msg, full_path)
#         return
#     if handle_invalid_magnet(results, full_path):
#         # Already handled deletion and logging, just stop processing
#         return
#     all_downloaded = all(r.get("status") == "downloaded" for r in results)
#     is_pending = not all_downloaded
     
#     # Save metadata
#     torrent_id = results[0].get("torrent_id")
#     metadata = {
#         "torrent_id": torrent_id,
#         "fetched_at": datetime.now().isoformat(),
#         "original_filename": file,
#         "extracted_title": title,
#         "extracted_year": year,
#         "folder_name": folder_name,
#         "files": results
#     }
    
#     # Add 'pending' suffix to JSON if not yet downloaded
#     date_stamp = datetime.now().strftime("%Y-%m-%d")
#     json_suffix = "-pending" if is_pending else ""
#     json_path = os.path.join(movie_folder, f"{date_stamp}{json_suffix}.json")
    
#     with open(json_path, "w", encoding="utf-8") as jf:
#         json.dump(metadata, jf, indent=2)
    
#     print(f"Saved metadata to {json_path}")
    
#     # Only create .strm files if all files are downloaded
#     if not is_pending:
#         for item in results:
#             strm_path = os.path.join(movie_folder, f"{item['filename']}.strm")
#             with open(strm_path, "w", encoding="utf-8") as sf:
#                 sf.write(item["download_url"])
#         print(f"Created {len(results)} .strm file(s) in {movie_folder}")
#     else:
#         print("Download not complete yet, .strm files not created.")
    
#     # Move original file
#     dest_file_path = os.path.join(movie_folder, file)
#     shutil.move(full_path, dest_file_path)
#     print(f"Moved source file to {dest_file_path}")

# def handle_invalid_magnet(results, full_path):
#     if results and any(r.get("status") == "magnet_error" for r in results):
#         torrent_id = results[0].get("torrent_id")
#         print(f"Invalid magnet detected for torrent_id {torrent_id}, deleting...")
#         if delete_rd_torrent(torrent_id):
#             try:
#                 os.remove(full_path)
#                 print(f"Deleted local file {full_path}")
#             except Exception as e:
#                 print(f"Error deleting local file {full_path}: {e}")
#         return True
#     return False

def process_movie_file(file, full_path):
    """Process a movie file with improved folder matching"""
    print(f"\nProcessing movie: {file}")
    
    # Extract title and year from filename
    title, year = extract_title_year_from_filename(file)
    print(f"Extracted title: '{title}'" + (f", year: {year}" if year else ""))
    
    # First, try to find an existing folder
    existing_folder = find_existing_movie_folder(title, year, movie_output_root)
    if existing_folder:
        # Use existing folder
        folder_name = existing_folder
        movie_folder = os.path.join(movie_output_root, folder_name)
        print(f"Using existing folder: '{folder_name}'")
    else:
        # Create new folder name
        if year:
            folder_name = f"{title} ({year})"
        else:
            folder_name = title
        movie_folder = os.path.join(movie_output_root, folder_name)
        print(f"Creating new folder: '{folder_name}'")
        os.makedirs(movie_folder, exist_ok=True)
    
    # Apply rate limiting before making API calls
    rate_limit()
    results, error_msg = resolve_rd_file(full_path)
    
    if error_msg:
        print(f"Resolve error: {error_msg}")
        handle_file_error(error_msg, full_path)
        return
    
    if handle_invalid_magnet(results, full_path):
        # Already handled deletion and logging, just stop processing
        return
    
    # Check for different statuses
    all_downloaded = all(r.get("status") == "downloaded" for r in results)
    has_magnet_conversion = any(r.get("status") == "magnet_conversion" for r in results)
    has_pending = any(r.get("status") not in ["downloaded", "magnet_conversion", "error"] for r in results)
    
    # Treat magnet_conversion and other non-downloaded statuses as pending
    is_pending = not all_downloaded or has_magnet_conversion or has_pending
    
    # Get torrent_id (should be available even for magnet_conversion)
    torrent_id = results[0].get("torrent_id") if results else None
    
    # For magnet_conversion, create a simplified metadata structure
    if has_magnet_conversion:
        print("Torrent is in magnet conversion phase")
        # Create metadata with minimal info available during magnet_conversion
        metadata = {
            "torrent_id": torrent_id,
            "fetched_at": datetime.now().isoformat(),
            "original_filename": file,
            "extracted_title": title,
            "extracted_year": year,
            "folder_name": folder_name,
            "status": "magnet_conversion",
            "message": "Torrent is being processed by Real-Debrid",
            "files": results  # This will contain the status info
        }
    else:
        # Normal metadata structure
        metadata = {
            "torrent_id": torrent_id,
            "fetched_at": datetime.now().isoformat(),
            "original_filename": file,
            "extracted_title": title,
            "extracted_year": year,
            "folder_name": folder_name,
            "files": results
        }
    
    # Add 'pending' suffix to JSON if not yet downloaded (including magnet_conversion)
    date_stamp = datetime.now().strftime("%Y-%m-%d")
    json_suffix = "-pending" if is_pending else ""
    json_path = os.path.join(movie_folder, f"{date_stamp}{json_suffix}.json")
    
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(metadata, jf, indent=2)
    print(f"Saved metadata to {json_path}")
    
    # Only create .strm files if all files are downloaded
    if not is_pending:
        for item in results:
            if item.get("download_url"):  # Only create .strm if download_url exists
                strm_path = os.path.join(movie_folder, f"{item['filename']}.strm")
                with open(strm_path, "w", encoding="utf-8") as sf:
                    sf.write(item["download_url"])
        print(f"Created {len([r for r in results if r.get('download_url')])} .strm file(s) in {movie_folder}")
    else:
        if has_magnet_conversion:
            print("Torrent is in magnet conversion phase, .strm files will be created later.")
        else:
            print("Download not complete yet, .strm files not created.")
    
    # Move original file
    dest_file_path = os.path.join(movie_folder, file)
    shutil.move(full_path, dest_file_path)
    print(f"Moved source file to {dest_file_path}")

def handle_invalid_magnet(results, full_path):
    """Handle invalid magnets and other error conditions"""
    if not results:
        return False
    
    # Check for various error conditions
    error_statuses = ["magnet_error", "error", "virus", "dead"]
    has_error = any(r.get("status") in error_statuses for r in results)
    
    if has_error:
        torrent_id = results[0].get("torrent_id")
        error_status = next((r.get("status") for r in results if r.get("status") in error_statuses), "unknown_error")
        
        print(f"Error detected (status: {error_status}) for torrent_id {torrent_id}, attempting cleanup...")
        
        # Try to delete the torrent from Real-Debrid if we have a torrent_id
        if torrent_id and delete_rd_torrent(torrent_id):
            print(f"Successfully deleted torrent {torrent_id} from Real-Debrid")
        
        # Delete local file
        try:
            os.remove(full_path)
            print(f"Deleted local file {full_path}")
        except Exception as e:
            print(f"Error deleting local file {full_path}: {e}")
        
        return True
    
    return False

def process_tv_file(file, full_path):
    print(f"\nProcessing TV show: {file}")
    title = extract_tv_show_title(file)
    print(f"Extracted TV show title: '{title}'")
    
    existing_folder = find_existing_show_folder(title, tv_output_root)
    if existing_folder:
        print(f"Using existing folder: '{existing_folder}'")
        title = existing_folder
    else:
        print(f"Creating new show folder: '{title}'")
    
    season_num, episode_num = extract_season_episode_info(file)
    print(f"Detected: Season {season_num}" + (f", Episode {episode_num}" if episode_num else ""))
    
    rate_limit()
    results, error_msg = resolve_rd_file(full_path)
    
    if error_msg:
        print(f"Resolve error: {error_msg}")
        handle_file_error(error_msg, full_path)
        return
    
    if handle_invalid_magnet(results, full_path):
        # Already handled deletion and logging, just stop processing
        return
    
    # Check for different statuses
    all_downloaded = all(r.get("status") == "downloaded" for r in results)
    has_magnet_conversion = any(r.get("status") == "magnet_conversion" for r in results)
    has_pending = any(r.get("status") not in ["downloaded", "magnet_conversion", "error"] for r in results)
    
    # Treat magnet_conversion and other non-downloaded statuses as pending
    is_pending = not all_downloaded or has_magnet_conversion or has_pending
    
    show_folder = os.path.join(tv_output_root, title)
    season_folder = os.path.join(show_folder, f"Season {season_num:02d}")
    os.makedirs(season_folder, exist_ok=True)
    print(f"Using show folder: '{show_folder}'")
    print(f"Using season folder: '{season_folder}'")
    
    # Get torrent_id (should be available even for magnet_conversion)
    torrent_id = results[0].get("torrent_id") if results else None
    
    # For magnet_conversion, create a simplified metadata structure
    if has_magnet_conversion:
        print("Torrent is in magnet conversion phase")
        # Create metadata with minimal info available during magnet_conversion
        metadata = {
            "torrent_id": torrent_id,
            "fetched_at": datetime.now().isoformat(),
            "original_filename": file,
            "extracted_title": title,
            "season": season_num,
            "episode": episode_num,
            "status": "magnet_conversion",
            "message": "Torrent is being processed by Real-Debrid",
            "files": results  # This will contain the status info
        }
    else:
        # Normal metadata structure
        metadata = {
            "torrent_id": torrent_id,
            "fetched_at": datetime.now().isoformat(),
            "original_filename": file,
            "extracted_title": title,
            "season": season_num,
            "episode": episode_num,
            "files": results
        }
    
    date_stamp = datetime.now().strftime("%Y-%m-%d")
    json_suffix = "-pending" if is_pending else ""
    json_path = os.path.join(season_folder, f"{date_stamp}{json_suffix}.json")
    
    # Handle existing metadata file merging (only for non-pending, non-magnet_conversion files)
    if os.path.exists(json_path) and not is_pending and not has_magnet_conversion:
        with open(json_path, "r", encoding="utf-8") as jf:
            existing_metadata = json.load(jf)
        
        existing_files = {f.get("filename", ""): f for f in existing_metadata.get("files", [])}
        
        for item in results:
            filename = item.get("filename", "")
            if filename not in existing_files:
                existing_metadata["files"].append(item)
        
        metadata = existing_metadata
        metadata["fetched_at"] = datetime.now().isoformat()
    
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(metadata, jf, indent=2)
    print(f"Saved metadata to {json_path}")
    
    # Only create .strm files if all files are downloaded
    if not is_pending:
        for item in results:
            if item.get("download_url"):  # Only create .strm if download_url exists
                strm_path = os.path.join(season_folder, f"{item['filename']}.strm")
                with open(strm_path, "w", encoding="utf-8") as sf:
                    sf.write(item["download_url"])
        print(f"Created {len([r for r in results if r.get('download_url')])} .strm file(s) in {season_folder}")
    else:
        if has_magnet_conversion:
            print("Torrent is in magnet conversion phase, .strm files will be created later.")
        else:
            print("Download not complete yet, .strm files not created.")
    
    # Move original file
    dest_file_path = os.path.join(season_folder, file)
    shutil.move(full_path, dest_file_path)
    print(f"Moved source file to {dest_file_path}")

def check_and_update_pending_tv_files(tv_output_root):
    """Check pending TV files and update their status"""
    print(f"Checking pending TV files in {tv_output_root}")
    
    for root, dirs, files in os.walk(tv_output_root):
        for file in files:
            if file.endswith("-pending.json"):
                json_path = os.path.join(root, file)
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    
                    torrent_id = metadata.get("torrent_id")
                    if not torrent_id:
                        print(f"No torrent_id found in {json_path}")
                        continue
                    
                    # Check current status
                    print(f"Checking TV show torrent {torrent_id}")
                    status_info = check_rd_torrent_status(torrent_id)
                    current_status = status_info.get("status")
                    
                    if current_status == "downloaded":
                        print(f"TV torrent {torrent_id} is now downloaded, updating...")
                        update_downloaded_tv_torrent(json_path, metadata, status_info)
                    elif current_status == "magnet_conversion":
                        print(f"TV torrent {torrent_id} still in magnet conversion")
                    elif current_status in ["error", "virus", "dead"]:
                        print(f"TV torrent {torrent_id} failed with status {current_status}")
                        handle_failed_tv_torrent(json_path, metadata, torrent_id)
                    else:
                        print(f"TV torrent {torrent_id} status: {current_status} ({status_info.get('progress', 0)}%)")
                
                except Exception as e:
                    print(f"Error processing TV file {json_path}: {e}")

def cleanup_duplicate_folders():
    """Clean up duplicate TV show folders with different cases, prioritizing better formatting"""
    if not os.path.exists(tv_output_root):
        return
        
    folders = [f for f in os.listdir(tv_output_root) if os.path.isdir(os.path.join(tv_output_root, f))]
    folder_groups = {}
    
    for folder in folders:
        normalized = normalize_for_comparison(folder)
        if normalized not in folder_groups:
            folder_groups[normalized] = []
        folder_groups[normalized].append(folder)
    
    for normalized_name, folder_list in folder_groups.items():
        if len(folder_list) > 1:
            print(f"\nFound duplicate folders for '{normalized_name}': {folder_list}")
            
            # Choose the "best" folder name with improved prioritization
            def folder_quality_score(folder_name):
                score = 0
                
                # Base score for proper case (Title Case)
                score += sum(1 for c in folder_name if c.isupper())
                score += len([w for w in folder_name.split() if w and w[0].isupper()])
                
                # Bonus for year in brackets
                if re.search(r'\(\d{4}\)', folder_name):
                    score += 20
                
                # Bonus for dash in title
                if ' - ' in folder_name or '-' in folder_name:
                    score += 15
                
                # Penalty for being too long (sometimes indicates bad formatting)
                score -= len(folder_name) * 0.1
                
                return score
            
            primary_folder = max(folder_list, key=folder_quality_score)
            print(f"Using '{primary_folder}' as primary folder (best formatting)")
            primary_path = os.path.join(tv_output_root, primary_folder)
            
            for folder in folder_list:
                if folder != primary_folder:
                    folder_path = os.path.join(tv_output_root, folder)
                    print(f"Merging '{folder}' into '{primary_folder}'")
                    for item in os.listdir(folder_path):
                        src_item = os.path.join(folder_path, item)
                        dst_item = os.path.join(primary_path, item)
                        if os.path.isdir(src_item):
                            if os.path.exists(dst_item):
                                print(f"  Merging season folder: {item}")
                                for season_item in os.listdir(src_item):
                                    src_season_item = os.path.join(src_item, season_item)
                                    dst_season_item = os.path.join(dst_item, season_item)
                                    if not os.path.exists(dst_season_item):
                                        shutil.move(src_season_item, dst_season_item)
                            else:
                                shutil.move(src_item, dst_item)
                        else:
                            if not os.path.exists(dst_item):
                                shutil.move(src_item, dst_item)
                    try:
                        os.rmdir(folder_path)
                        print(f"  Removed empty folder: '{folder}'")
                    except OSError:
                        print(f"  Could not remove folder '{folder}' (not empty)")
                        
    # Also clean up movie folders with the same logic
    if not os.path.exists(movie_output_root):
        return
        
    movie_folders = [f for f in os.listdir(movie_output_root) if os.path.isdir(os.path.join(movie_output_root, f))]
    movie_folder_groups = {}
    
    for folder in movie_folders:
        normalized = normalize_for_comparison(folder)
        if normalized not in movie_folder_groups:
            movie_folder_groups[normalized] = []
        movie_folder_groups[normalized].append(folder)
    
    for normalized_name, folder_list in movie_folder_groups.items():
        if len(folder_list) > 1:
            print(f"\nFound duplicate movie folders for '{normalized_name}': {folder_list}")
            
            # Choose the "best" folder name with movie-specific prioritization
            def movie_folder_quality_score(folder_name):
                score = 0
                
                # Base score for proper case
                score += sum(1 for c in folder_name if c.isupper())
                score += len([w for w in folder_name.split() if w and w[0].isupper()])
                
                # High bonus for year in brackets (very important for movies)
                if re.search(r'\(\d{4}\)', folder_name):
                    score += 25
                
                # Bonus for dash in title
                if ' - ' in folder_name or '-' in folder_name:
                    score += 15
                
                # Penalty for being too long
                score -= len(folder_name) * 0.1
                
                return score
            
            primary_folder = max(folder_list, key=movie_folder_quality_score)
            print(f"Using '{primary_folder}' as primary movie folder (best formatting)")
            primary_path = os.path.join(movie_output_root, primary_folder)
            
            for folder in folder_list:
                if folder != primary_folder:
                    folder_path = os.path.join(movie_output_root, folder)
                    print(f"Merging movie folder '{folder}' into '{primary_folder}'")
                    for item in os.listdir(folder_path):
                        src_item = os.path.join(folder_path, item)
                        dst_item = os.path.join(primary_path, item)
                        if not os.path.exists(dst_item):
                            shutil.move(src_item, dst_item)
                        else:
                            print(f"  Skipping existing file: {item}")
                    try:
                        os.rmdir(folder_path)
                        print(f"  Removed empty movie folder: '{folder}'")
                    except OSError:
                        print(f"  Could not remove movie folder '{folder}' (not empty)")

def scan_and_resolve():
    """Main function to scan and resolve both movies and TV shows"""
    if os.path.exists(radarr_blackhole_path):
        movie_files = [
            f for f in os.listdir(radarr_blackhole_path)
            if f.endswith('.magnet') or f.endswith('.torrent')
        ]
        print(f"\nFound {len(movie_files)} movie files in blackhole")
        for file in movie_files:
            full_path = os.path.join(radarr_blackhole_path, file)
            process_movie_file(file, full_path)
            time.sleep(5)
    
    if os.path.exists(sonarr_blackhole_path):
        tv_files = [
            f for f in os.listdir(sonarr_blackhole_path)
            if f.endswith('.magnet') or f.endswith('.torrent')
        ]
        print(f"\nFound {len(tv_files)} TV show files in blackhole")
        for file in tv_files:
            full_path = os.path.join(sonarr_blackhole_path, file)
            process_tv_file(file, full_path)
            time.sleep(10)
    
    print("\nCleaning up duplicate folders...")
    cleanup_duplicate_folders()

if __name__ == "__main__":
    scan_and_resolve()