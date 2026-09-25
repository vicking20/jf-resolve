<p align="center">
  <img src="https://raw.githubusercontent.com/vicking20/jfresolve/main/jfresolve.png" alt="JF-Resolve logo" width="128" height="128">
</p>

# JF-Resolve

Browse movies and TV from TMDB and play them from Stremio addons, in the browser or through Jellyfin.

<p align="center">
  <a href="https://discord.gg/hPz3qn72Ue" target="_blank">
    <img src="https://img.shields.io/badge/Chat%20on%20Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  </a>
</p>

Prefer something that lives inside Jellyfin? Use the plugin instead: [jfresolve](https://github.com/vicking20/jfresolve).

![Setup](images/jfresolvesetup.png)
![Discover](images/discover.png)

## Features

- **Discover and search** movies and shows from TMDB.
- **Web player** with a source picker, audio track and subtitle selection, and on-the-fly transcoding with FFmpeg when the browser can't play a file.
- **Addon streams** from any Stremio addon manifest (Torrentio, AIOStreams, Comet, …). Results keep the addon's own ranking. Debrid services are configured in the addon, not here.
- **Torrent playback** through a bundled TorrServer, with a P2P fallback for Torrentio.
- **Local library**: scan your own folders and play those files alongside addon streams.
- **Jellyfin output**: writes `.strm` files for movies and every episode, and triggers a Jellyfin library scan.
- **Requests**: in Jellyfin mode, users request titles and an admin approves them.
- **Users and parental controls**: per-user content rating limits apply to browsing and playback.
- **Watchlist, progress and continue watching.**
- **Plugins** installable from the built-in marketplace.

### Modes

Pick one during setup; change it later in Settings.

| Mode | Web player | Jellyfin `.strm` output |
|---|---|---|
| Standalone | ✓ | |
| Jellyfin | | ✓ |
| Hybrid | ✓ | ✓ |

## Requirements

- Python 3.12 (newer versions may not have wheels for every dependency)
- FFmpeg on your `PATH` for transcoding and subtitles
- A TMDB API key
- A Stremio addon manifest URL, for example from Torrentio or AIOStreams

## Install

### Docker

The image is built locally; no image is published.

```bash
docker compose up -d --build
```

Or without Compose:

```bash
./docker-run.sh
```

Open `http://<server-ip>:8765`. Data and plugins are kept in `./data`, `./experimental/data` and `./experimental/plugins`.

**For Jellyfin or Hybrid mode**, create a `.env` next to `docker-compose.yml` that points at folders Jellyfin reads:

```bash
JELLYFIN_MOVIES_DIR=/srv/jellyfin/strm/movies
JELLYFIN_TV_DIR=/srv/jellyfin/strm/tv
```

Then in JF-Resolve Settings:

- Movie directory: `/strm/movies`
- Series directory: `/strm/tv`
- JF-Resolve URL for Jellyfin: `http://<server-ip>:8765` (an address Jellyfin can reach, not `127.0.0.1`)

Add the same host folders as libraries in Jellyfin.

### Linux / macOS

```bash
bash setup.sh
source venv/bin/activate
python start.py
```

To run it in the background instead: `./run.sh start`, `./run.sh stop`, `./run.sh status`, `./run.sh logs`.

### Windows (PowerShell)

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
$env:PYTHONUTF8 = "1"
python -m pip install -r requirements.txt
python start.py
```

## First run

1. Open `http://127.0.0.1:8765`.
2. Create the admin account and choose a mode.
3. Add your TMDB key.
4. Paste your addon manifest URL into the Stream Manager plugin settings.
5. For Jellyfin or Hybrid, fill in the Jellyfin URL, API key and STRM folders.

## Jellyfin output

Adding a title to the library (as admin) or approving a request writes:

```
movies/
  Movie Title (2024)/
    Movie Title (2024).strm
tv/
  Show Title (2024)/
    Season 01/
      Show Title (2024) - S01E01 - Episode Name.strm
```

Each file holds a link back to JF-Resolve. When Jellyfin plays it, JF-Resolve asks your addons for streams and redirects to the first playable one, in the addon's order. To control which release plays, set up sorting and filters in the addon (AIOStreams is good at this). Magnet links and streams that need custom headers are skipped, since Jellyfin can't follow them.

Removing a title deletes only folders JF-Resolve created (they contain a `.jfresolve` marker).

## Troubleshooting

- **Nothing shows up in Jellyfin:** check the STRM folders are mounted and added as Jellyfin libraries, then run a library scan.
- **Jellyfin can't play a title:** make sure the JF-Resolve URL in Settings is reachable from the Jellyfin server, and check the logs page.
- **No streams:** check the addon URL and that your debrid account is active.
- **Browser can't play a stream:** pick another source from the player's source menu, or make sure FFmpeg is installed.

Logs are on the Logs page (admin only) and in `experimental/data/logs/`.

## Disclaimer

For personal and educational use. You are responsible for complying with the terms of TMDB, Jellyfin, Stremio addons and any debrid service you use.
