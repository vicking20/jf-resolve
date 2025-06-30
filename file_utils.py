"""
File utilities for video detection and file type checking
"""
from pathlib import Path
# File extension constants
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', 
    '.m4v', '.3gp', '.ogv', '.ts', '.m2ts', '.mts'
}
ARCHIVE_EXTENSIONS = { #im lazy, yes i could change this to something else since ive added other extensions to skip...
    '.rar', '.zip', '.7z', '.tar', '.gz', '.bz2', '.xz', '.txt', '.nfo', '.ts', '.scr'
}

UNWANTED_KEYWORDS = {'rarbg.com', 'readme'}

def should_skip_by_name(filename):
    """Skip files that contain unwanted substrings in the name"""
    name = Path(filename).name.lower()
    return any(keyword in name for keyword in UNWANTED_KEYWORDS)

def is_video_file(filename):
    """Check if file is a video file based on extension"""
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS

def is_sample_file(filename):
    """Check if filename suggests a sample file"""
    return 'sample' in Path(filename).name.lower()

def is_archive_file(filename):
    """Check if file is an archive file based on extension"""
    return Path(filename).suffix.lower() in ARCHIVE_EXTENSIONS

def filter_video_files(files):
    """
    Filter and sort video files from a list of file info dictionaries    
    Args:
        files (list): List of file info dicts with 'path', 'id', 'bytes' keys
    Returns:
        list: Sorted list of video files (largest first)
    """
    video_files = []
    for file_info in files:
        filename = file_info.get('path', '')
        file_id = file_info.get('id')
        file_size = file_info.get('bytes', 0)
        # Skip archive files
        if is_archive_file(filename):
            print(f"  Skipping other: {filename}")
            continue
        #skip sample files
        if is_sample_file(filename):
            print(f"  Skipping sample file: {filename}")
            continue
        # Add video files

        if should_skip_by_name(filename):
            print(f"  Skipping unwanted name: {filename}")
            continue

        if is_video_file(filename):
            video_files.append({
                'id': file_id,
                'filename': filename,
                'size_mb': file_size / (1024 * 1024),
                'size_bytes': file_size
            })
    # Sort by size (largest first)
    video_files.sort(key=lambda x: x['size_bytes'], reverse=True)
    # If no video files, look for large non-archive files (>50MB)
    if not video_files:
        print("  No video files found, looking for large non-archive files...")
        for file_info in files:
            filename = file_info.get('path', '')
            file_id = file_info.get('id')
            file_size = file_info.get('bytes', 0)
            if not is_archive_file(filename) and file_size > 50 * 1024 * 1024:
                video_files.append({
                    'id': file_id,
                    'filename': filename,
                    'size_mb': file_size / (1024 * 1024),
                    'size_bytes': file_size
                })
    return video_files