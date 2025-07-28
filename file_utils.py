"""
File utilities for video detection and file type checking
"""
from pathlib import Path

# File extension constants
VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.3gp', '.ogv', '.ts', '.m2ts', '.mts'
}

ARCHIVE_EXTENSIONS = {  # im lazy, yes i could change this to something else since ive added other extensions to skip...
    '.rar', '.zip', '.7z', '.tar', '.gz', '.bz2', '.xz', '.txt', '.nfo', '.ts', '.scr'
}

SUBTITLE_EXTENSIONS = {'.srt', '.vtt', '.ass', '.ssa', '.sub', '.idx'}

UNWANTED_KEYWORDS = {'rarbg.com', 'readme'}
BONUS_CONTENT_KEYWORDS = {'making', 'behind', 'bonus', 'extra', 'featurette', 'interview', 'trailer', 'deleted', 'blooper', 'commentary'}

# Size limits in bytes
MIN_VIDEO_SIZE = 110 * 1024 * 1024  # 200MB minimum for video files
MIN_FALLBACK_SIZE = 200 * 1024 * 1024  # 200MB for fallback large files

def should_skip_by_name(filename):
    """Skip files that contain unwanted substrings in the name"""
    name = Path(filename).name.lower()
    return any(keyword in name for keyword in UNWANTED_KEYWORDS)

def is_video_file(filename):
    """Check if file is a video file based on extension"""
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS

def is_subtitle_file(filename):
    """Check if file is a subtitle file based on extension"""
    return Path(filename).suffix.lower() in SUBTITLE_EXTENSIONS

def is_sample_file(filename):
    """Check if filename suggests a sample file"""
    return 'sample' in Path(filename).name.lower()

def is_archive_file(filename):
    """Check if file is an archive file based on extension"""
    return Path(filename).suffix.lower() in ARCHIVE_EXTENSIONS

def is_bonus_content(filename):
    """Check if filename suggests bonus content"""
    name = Path(filename).name.lower()
    return any(keyword in name for keyword in BONUS_CONTENT_KEYWORDS)

def is_video_too_small(file_size, min_size=MIN_VIDEO_SIZE):
    """Check if video file is below minimum size limit"""
    return file_size < min_size

def filter_video_files(files):
    """
    Filter files to get all valid video files
    Args:
        files (list): List of file info dicts with 'path', 'id', 'bytes' keys
    Returns:
        list: List containing all valid video files, or empty list if none found
    """
    video_candidates = []
    
    # First pass: collect all valid video candidates
    for file_info in files:
        filename = file_info.get('path', '')
        file_id = file_info.get('id')
        file_size = file_info.get('bytes', 0)
        
        # Skip archive files
        if is_archive_file(filename):
            print(f"  Skipping other: {filename}")
            continue
            
        # Skip sample files
        if is_sample_file(filename):
            print(f"  Skipping sample file: {filename}")
            continue
            
        # Skip unwanted names
        if should_skip_by_name(filename):
            print(f"  Skipping unwanted name: {filename}")
            continue
            
        # Skip bonus content
        if is_bonus_content(filename):
            print(f"  Skipping bonus content: {filename}")
            continue
        
        # Skip non-video files (including subtitles)
        if not is_video_file(filename):
            print(f"  Skipping non-video file: {filename}")
            continue
            
        # Skip video files that are too small (now 200MB)
        if is_video_too_small(file_size):
            print(f"  Skipping small video file ({file_size / (1024 * 1024):.1f}MB): {filename}")
            continue
            
        video_candidates.append({
            'id': file_id,
            'filename': filename,
            'size_mb': file_size / (1024 * 1024),
            'size_bytes': file_size
        })
    
    # Return all valid video files
    if video_candidates:
        # Sort by size (largest first) for consistent ordering
        video_candidates.sort(key=lambda x: x['size_bytes'], reverse=True)
        print(f"  Found {len(video_candidates)} valid video files:")
        for video in video_candidates:
            print(f"    - {video['filename']} ({video['size_mb']:.1f}MB)")
        video_files = video_candidates
        return video_files
    
    # If no video files found, look for largest non-archive file as fallback
    print("  No video files found, looking for large non-archive files...")
    fallback_candidates = []
    
    for file_info in files:
        filename = file_info.get('path', '')
        file_id = file_info.get('id')
        file_size = file_info.get('bytes', 0)
        
        if (not is_archive_file(filename) and 
            not is_subtitle_file(filename) and
            not is_bonus_content(filename) and
            not should_skip_by_name(filename) and
            not is_sample_file(filename) and
            file_size > MIN_FALLBACK_SIZE):
            fallback_candidates.append({
                'id': file_id,
                'filename': filename,
                'size_mb': file_size / (1024 * 1024),
                'size_bytes': file_size
            })
    
    if fallback_candidates:
        largest_fallback = max(fallback_candidates, key=lambda x: x['size_bytes'])
        print(f"  Selected largest fallback file ({largest_fallback['size_mb']:.1f}MB): {largest_fallback['filename']}")
        video_files = [largest_fallback]
        return video_files
    
    # Return empty video_files list if no valid files found
    print("  No valid files found - returning invalid")
    video_files = []
    return video_files