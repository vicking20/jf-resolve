<p align="center">
  <img src="https://raw.githubusercontent.com/vicking20/jfresolve/main/jfresolve.png" alt="Jfresolve Logo" width="128" height="128">
</p>

# JF-Resolve

JF-Resolve is a web-based application that bridges Jellyfin media server with Real-Debrid and AllDebrid services. It allows you to build and manage a virtual media library by discovering content from TMDB and generating STRM files that dynamically resolve to debrid-cached streams.

<p align="center">
  <a href="https://discord.gg/hPz3qn72Ue" target="_blank">
    <img src="https://img.shields.io/badge/Chat%20on%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

For an integrated experience, you can instead use the plugin directly with Jellyfin: [**jfresolve**](https://github.com/vicking20/jfresolve).

### JF-Resolve
![JF-Resolve](images/jfresolvesetup.png)
### Discover
![JF-Resolve Discover](images/discover.png)

## What It Does

JF-Resolve acts as a middleware between your Jellyfin server and debrid services. Instead of storing large media files, it creates lightweight STRM files that Jellyfin can play. When you start playback, these files redirect to premium cached streams from your debrid service through Stremio addons.

This approach provides access to a vast content library without requiring significant storage space or maintaining physical media files. You also no longer need to wait for downloads.

## Features

### Content Discovery
- Search for movies and TV shows using The Movie Database
- Browse trending content updated daily
- Explore popular movies and TV shows by category
- Check if content is already in your library

### Library Management
- Add movies and TV shows with a single click
- Generate multiple quality versions per item (4K, 1080p, 720p)
- Automatically organize content in Jellyfin-compatible folder structures
- Track new episodes for TV series and update automatically
- Remove items and clean up STRM files when no longer needed

### Stream Resolution
- Automatically query Stremio addons for available streams
- Intelligent quality matching based on your preferences
- Automatic failover to alternative streams if playback fails
- Grace period handling to allow buffering without switching streams
- Support for movies and individual TV show episodes

### Automation
- Configure automatic library population from trending and popular lists
- Schedule periodic updates to detect new TV show episodes
- Trigger Jellyfin library scans after adding content (Experimental)
- Manage multiple quality versions simultaneously

### User Management
- Secure authentication with password hashing
- Admin controls for system configuration

## Requirements

Before using JF-Resolve, you need:

1. A running Jellyfin server instance
2. A TMDB API key for content discovery and metadata
3. A Debrid provider acount / subscription
4. A Stremio addon manifest URL configured for your debrid service
5. Python 3.10 or higher installed on your system

## Installation

### Setting Up the Application

1. Clone or download the project to your preferred location

2. Create and activate a Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Linux/Mac
   venv\Scripts\activate     # On Windows
   ```

3. Install required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Start both servers using the included script:
   ```bash
   python scripts/run.py
   ```

5. Access the web interface at `http://<jf-resolve's ip>:8765`

### Initial Configuration

When you first access the application, you will need to:

1. Create an administrator account with username and password
2. Enter your TMDB API key
3. Configure your Jellyfin server URL
4. Set the library path where STRM files will be created
5. Add your Stremio addon manifest URL

All configuration can be done through the Settings page in the web interface.

## How to Use

### Adding Content to Your Library

1. Navigate to the home page or search page
2. Browse trending content or search for specific titles
3. Click on any item to view information
4. Select which quality versions you want (you can choose multiple)
5. Click "Add to Library" to create the STRM files
6. Wait for Jellyfin to scan the library (about 30 seconds)
7. Start watching in Jellyfin

### Managing TV Shows

When you add a TV show:
- All current episodes are added automatically
- User can configure the system to periodically check for new episodes
- New episodes are added to your library when detected
- You can manually refresh a show to check for updates

### Removing Content

To remove items from your library:
1. Go to the Library page
2. Find the item you want to remove
3. Click the delete button
4. Confirm the deletion
5. STRM files are automatically removed from disk

### Configuring Stream Resolution

The application includes intelligent failover logic:
- When you start playback, it fetches available streams
- If a stream fails, it waits 45 seconds to allow buffering (configurable)
- After the grace period, it automatically tries the next stream
- If playback succeeds, it resets to the preferred stream after 2 minutes


### Automatic Library Population

You can configure the application to automatically add content:
1. Go to Settings and enable automatic population
2. Select sources (trending movies, popular TV shows, etc.)
3. Set how often to check for new content
4. The system will add new items based on your preferences

## Understanding STRM Files

STRM files are simple text files containing a URL. When Jellyfin plays an STRM file, it requests the URL and plays the resulting stream.

JF-Resolve organizes these files in standard Jellyfin folder structures:

For movies:
```
Movies/
  Movie Title (2024)/
    Movie Title (2024) - 1080p.strm
    Movie Title (2024) - 4K.strm
```

For TV shows:
```
TV Shows/
  Show Title (2024)/
    Season 01/
      S01E01 - 1080p.strm
      S01E02 - 1080p.strm
    Season 02/
      S02E01 - 1080p.strm
```

Each STRM file points to the stream resolution server, which handles finding and redirecting to the actual playable stream.

## Stremio Addon Configuration

The application works with Stremio addons that support your debrid service. Common examples include Torrentio, which integrates with Real-Debrid and AllDebrid.

An example addon manifest URL looks like:
```
stremio://torrentio.strem.fun/providers=yts,eztv,rarbg,1337x,thepiratebay,kickasstorrents,torrentgalaxy,magnetdl,horriblesubs,nyaasi,tokyotosho,anidex|qualityfilter=brremux,scr,cam|limit=1|debridoptions=nodownloadlinks,nocatalog|realdebrid=(input your real debrid key here with no brackets)/manifest.json
```

The addon must be configured with your debrid service API key. When JF-Resolve requests streams, the addon queries your debrid service for content and returns available streams.


## Logs and Troubleshooting

The application maintains detailed logs to help diagnose issues:

- Info logs track normal operations and successful actions
- Error logs capture failures and exceptions
- Stream logs specifically track stream resolution for debugging playback issues

You can view logs through the web interface or access them directly in the logs directory.

### Common Issues

**STRM files not appearing in Jellyfin:**
- Verify the library path is correct in Settings
- Ensure Jellyfin has read access to the path
- Trigger a manual library scan in Jellyfin
- Check that automatic scanning is enabled

**No streams available:**
- Verify your Stremio addon URL is correct
- Ensure your debrid service account is active
- Check that the content is cached on your debrid service
- Try different quality options

**Streams not playing:**
- Check stream logs for detailed error messages
- Verify Jellyfin can reach the stream server
- Ensure the stream server is running on port 8766
- Test the stream URL directly in a browser

**Content not found:**
- Verify the item has an IMDB ID in TMDB
- Some content may not be available through your addon
- Try searching for alternative releases or versions


## System Architecture

JF-Resolve runs two separate servers:

1. The main server on port 8765 handles the web interface, library management, and configuration
2. The stream server on port 8766 handles stream resolution requests from Jellyfin


## Disclaimer

This project is intended for educational purposes to explore programmatic media playback integration with Jellyfin and debrid services. It is provided as-is for personal use and learning.

The application was designed primarily for Linux systems. While it may work on other platforms, compatibility is not guaranteed. Users should ensure they comply with all applicable terms of service for TMDB, Jellyfin, Stremio, and their debrid service provider.

Version 2.0.0
