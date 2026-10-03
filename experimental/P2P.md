# Magnet playback

The experimental web player supports magnets through a local TorrServer helper. It supplies a seekable HTTP stream to the existing FFmpeg/HLS player; MKV playback, resume, seeking and embedded text subtitles use the same playback path.

Install the official helper inside the project:

```sh
venv/bin/python scripts/install_torrserver.py
```

Nothing is installed systemwide. Restart JF-Resolve after updating the code. The helper starts on demand at `127.0.0.1:8091` and stops when the app shuts down. Its binary, cache and logs live under `data/torrserver/`. Closing playback stops FFmpeg and drops the torrent when no other playback uses it.

On a media details page, paste a magnet into **Paste a magnet link** and click **Play magnet**. Without a selected file, the largest video excluding samples is used. A magnet's `so=0` selects the first torrent file (zero-based); Stremio `fileIdx` and tracker URLs are preserved automatically.

Stream Manager adds **P2P** fallback sources for configured Torrentio debrid manifests. Debrid sources remain first, followed by P2P sources if playback fails. Its plugin configuration supports `p2p_fallback: false` to disable this and `p2p_manifest_url` for another Stremio source that provides torrent hashes. A configured provider must actually return magnet/hash sources; an arbitrary failed HTTP URL cannot be converted back into a torrent.

For an existing TorrServer, set `JF_TORRSERVER_URL=http://host:port` before starting the app. JF-Resolve does not stop an externally managed server.

P2P playback downloads directly from torrent peers using the machine running TorrServer. Startup and seeking depend on available peers and download speed. Metadata discovery times out after 90 seconds with a visible error if peers cannot supply it.

## Subtitles and original-file downloads

Text subtitle tracks are extracted alongside video in the same FFmpeg process. The player reads the resulting WebVTT cues every three seconds, including after a seek, so selecting a track does not start a second torrent reader. Image subtitle tracks still require a separate SRT/VTT file.

Open **Download** in the video player's top bar:

- **Download to device** streams the original file to the browser for any signed-in user.
- **Save on server** is admin-only. It queues an original-file download under `data/downloads/<job-id>/`.
- **Server downloads** opens `/downloads`, showing progress, cancellation and links to completed files.

Server downloads persist as files and keep running when the browser closes; unfinished jobs are marked interrupted after an app restart and must be started again. Two downloads run at once. A download keeps its torrent connection independently of playback. Downloads started from a media page retain the exact media UUID (and season/episode). On completion they are registered as local sources and can be played from the media details page or the Downloads page. Each file has a `.jf-resolve.json` identity sidecar, which active scans honor without guessing from the filename. Library links are checked again at startup. Folder watching remains optional for files added outside the app; configure a watched library path if you need it.
