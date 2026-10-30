# Lyrarr

**Lidarr Companion App for Music Metadata** — manages cover art and lyrics for your music library, similar to how [Bazarr](https://github.com/morpheus65535/bazarr) manages subtitles.

## Features

- 🔗 **Lidarr Integration** — Syncs artists, albums, and tracks from Lidarr with real-time SignalR updates (or a Lidarr webhook)
- 🎨 **Cover Art** — MusicBrainz Cover Art Archive, Deezer, iTunes, TheAudioDB and fanart.tv; optional embedding into audio files
- 📝 **Lyrics** — Synced (`.lrc`) and plain lyrics from LRCLIB, Musixmatch, NetEase and Genius, with match scoring, provider agreement, blacklisting, version history and plain → synced upgrades
- 🌍 **LRCLIB** — Use a self-hosted LRCLIB instance, honour its instrumental flag, and publish your own synced lyrics back
- 🎙️ **Whisper Sync Generation** — Turn plain lyrics into synced `.lrc` by aligning them to the audio with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- 🈯 **Translation & Language Detection** — Detect lyric languages and translate (Google or DeepL), per profile or per artist
- 🧰 **Mass Edit** — Select artists/albums/genres and bulk fetch, upgrade, translate or delete lyrics with live progress
- 🧾 **Profiles** — Per-profile providers, score thresholds, cover format and translation rules
- 🎵 **Dashboard & Wanted** — Library completion stats and lists of missing covers, lyrics, untimed and undetected tracks
- 🔒 **Authentication** — Optional form or HTTP Basic login; API key for integrations
- 🔔 **Notifications** — Discord and Telegram
- ⏱️ **Scheduled Tasks** — Background jobs for syncing, scanning and retrying

## Docker (recommended)

Images for `linux/amd64` and `linux/arm64` are published to **`ghcr.io/solarbaron/lyrarr`**:

| Tag | Meaning |
|---|---|
| `latest` | Newest stable release |
| `1.6.0`, `1.6` | A specific release / the newest patch of a release line |

```yaml
services:
  lyrarr:
    image: ghcr.io/solarbaron/lyrarr:latest
    container_name: lyrarr
    restart: unless-stopped
    ports:
      - "6868:6868"
    volumes:
      - ./data:/config            # settings, database, logs, backups, Whisper models
      - /path/to/music:/music     # same path Lidarr uses for your library
    environment:
      - TZ=Europe/London
      - PUID=1000
      - PGID=1000
```

Then open **http://localhost:6868**. A ready-to-edit [`docker-compose.yml`](docker-compose.yml) is included (`build: .` builds from source instead).

**Volumes**

- `/config` — everything Lyrarr stores: `config/config.yaml`, the SQLite database, logs, backups and downloaded Whisper models (`/config/cache`).
- `/music` — your music library. Lyrarr writes `.lrc` files and cover art next to the audio, so it needs write access, and the path inside the container must match the paths Lidarr reports (map it to the same container path Lidarr uses).

**Environment**

| Variable | Description |
|---|---|
| `PUID` / `PGID` | Run as this user/group id instead of root. `/config` is chowned to it on start; `/music` is not touched, so make sure that uid/gid can write there. Unset = run as root (the default, so existing installs keep their file ownership). |
| `UMASK` | File-creation mask for files Lyrarr writes, e.g. `002`. |
| `TZ` | Time zone for schedules and logs. |
| `LYRARR_HTTPS=1` | Mark the session cookie `Secure`. Set it when Lyrarr is only reached over HTTPS (e.g. behind a TLS reverse proxy); leave unset for plain-HTTP access or logins will fail. |

The container has a health check (`/api/auth/status`), so `docker ps` shows whether the web server is up.

## Security

Authentication is **off** by default — anyone who can reach port 6868 can change settings and rewrite lyrics files. Enable it under **Settings → Security** (form login or HTTP Basic) unless Lyrarr is only reachable from a trusted network; the System page warns while it is disabled. Integrations (e.g. the Lidarr webhook at `/api/webhook/lidarr`) authenticate with the API key shown in Settings via the `X-API-KEY` header.

Login sessions created by older versions are not carried over by the upgrade that hardened session handling — just sign in again once.

## Running from source

Requires **Python 3.13+** (and Node.js 22+ only if you change the frontend).

```bash
pip install -r requirements.txt
python lyrarr.py
```

Lyrarr starts on **http://localhost:6868** by default. The production frontend build is committed under `lyrarr/frontend/`, so no Node.js is needed just to run it.

`requirements.txt` lists supported version ranges; `requirements.lock` is the exact, hash-pinned set the Docker image installs (`pip install --require-hashes -r requirements.lock`).

### Frontend development

```bash
cd frontend
npm ci
npm run dev      # dev server on http://localhost:3000, proxies /api to :6868
npm run build    # type-check + production build into ../lyrarr/frontend
```

Commit the rebuilt `lyrarr/frontend/` together with frontend changes — CI fails if it is out of date.

## Configuration

Configuration is stored in `<config dir>/config/config.yaml` (auto-created on first run; `./data` from source, `/config` in Docker) and is editable from the Settings page.

To serve Lyrarr under a subpath behind a reverse proxy (e.g. `https://example.com/lyrarr`), set **Settings → General → URL Base** to `/lyrarr` and restart.

### Command line options

| Option | Description |
|---|---|
| `--config DIR` | Config/data directory (default: `./data`) |
| `--port PORT` | Override the listen port |
| `--no-signalr` | Disable the Lidarr SignalR client |
| `--no-tasks` | Disable all scheduled tasks |
| `--debug` | Enable debug logging |
| `--no-update` | No-op (Lyrarr has no auto-updater); kept so existing launch scripts keep working |
| `--create-db-revision` | Currently a no-op; kept for compatibility. Create migrations with Alembic directly |

## Architecture

- **Backend**: Python 3.13, Flask, Waitress, SQLAlchemy (SQLite), Alembic, Dynaconf, APScheduler, flask-restx
- **Frontend**: React 19, TypeScript, Vite, Mantine 8, TanStack Query

## License

GPL-3.0
