# Migrating from JF-Resolve 1.x

1.x STRM files point at a stream server on port 8766, which no longer exists, so Jellyfin can't play them after upgrading. This script moves your settings over and rewrites the STRM files. It's best-effort: back up first.

## What it does

- Copies your TMDB key, Jellyfin URL, API key, and movie/TV folders from `data/jfresolve.db`.
- Puts your old Stremio addon URL into the Stream Manager plugin.
- Switches the app to Jellyfin mode.
- Rewrites the STRM files for every title in your old library. If a title fails, its old files are left as they were.

## What it doesn't do

- Separate anime or search folders from 1.x are not supported. Those titles are written to your main movie/TV folders.
- Old users and the automatic population settings are not carried over.
- Titles don't appear in the web app's library list. They only exist in Jellyfin.

## Steps

1. Stop the old version and back up `data/` and your STRM folders.
2. Update the code and install requirements (`bash setup.sh`).
3. Preview the changes:
   ```bash
   python scripts/migrate_from_v1.py --dry-run
   ```
4. Run it with an address Jellyfin can reach JF-Resolve on:
   ```bash
   python scripts/migrate_from_v1.py --public-url http://192.168.1.10:8765
   ```
5. Start the app (`python start.py`), create your admin account, and rescan your Jellyfin libraries.

Use `--old-db /path/to/jfresolve.db` if the old database lives somewhere else.

### Docker

Old installs kept the database in `./data`, which the container mounts at `/app/data`, so you can run:

```bash
docker compose run --rm jf-resolve python scripts/migrate_from_v1.py --public-url http://192.168.1.10:8765
```

The script writes to the movie and TV paths saved by 1.x, so those exact paths must exist inside the container. Mount your old STRM folders at the same paths (add them to `volumes:` in `docker-compose.yml`) before running it.
