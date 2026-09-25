# Hybrid operation architecture

JF-Resolve runs as one application. Operation profiles select which output services are active; they do not select a second source tree or server implementation.

| Profile | Built-in player | STRM output | Jellyfin scan | Local library | Torrent playback |
| --- | --- | --- | --- | --- | --- |
| Standalone | On | Off | Off | On | On |
| Jellyfin | Off | On | On | Off | Off |
| Hybrid | On | On | On | On | On |

Profiles are presets. Administrators can change individual capability switches after applying a preset. Existing installations default to Standalone so an upgrade keeps the current web player behavior.

## Shared core

Media identity, metadata, plugin sources, source ordering, downloads, and local-file matching remain shared. Output-specific code sits behind services:

- `WebPlaybackService` uses the existing browser player, stream proxy, torrent service, and transcoder.
- `StrmLibraryService` writes Jellyfin-compatible movie and episode files from unified `MediaItem` records.
- `JellyfinService` requests library refreshes and later provides connection testing.
- `DownloadLibraryService` keeps completed server downloads available to the same unified media record.

The next migration stage ports STRM creation to the unified `MediaItem` model and adds a scoped, Jellyfin-safe resolver endpoint. The old normal-mode database model and second stream server will not be restored.
