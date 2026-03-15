# Lyrarr

**Lidarr Companion App for Music Metadata** — manages cover art and lyrics for your music library, similar to how [Bazarr](https://github.com/morpheus65535/bazarr) manages subtitles.

## Features

- 🔗 **Lidarr Integration** — Syncs artists, albums, and tracks from Lidarr with real-time SignalR updates
- 🎨 **Cover Art** — Downloads album art from MusicBrainz Cover Art Archive and fanart.tv
- 📝 **Lyrics** — Downloads synced (.lrc) and plain lyrics from LRCLIB and Genius
- 🎵 **Dashboard** — Overview of your library with missing metadata stats
- ⏱️ **Scheduled Tasks** — Background jobs for auto-syncing and metadata scanning
- ⚙️ **Settings UI** — Configure everything from the web interface

## Quick Start

### Backend

```bash
# Install Python dependencies
pip install -r requirements.txt

# Start Lyrarr
python lyrarr.py
```

Lyrarr will start on **http://localhost:6868** by default.

### Frontend (Development)

```bash
cd frontend
npm install
npm run dev
```

Frontend dev server starts on **http://localhost:3000** with API proxy to the backend.

### Frontend (Production Build)

```bash
cd frontend
npm run build
```

Built files are output to `lyrarr/frontend/` and served by the backend.

## Configuration

Configuration is stored in `data/config/config.yaml` (auto-created on first run).

### Command Line Options

| Option | Description |
|---|---|
| `--config DIR` | Config/data directory (default: `./data`) |
| `--port PORT` | Override listen port |
| `--no-update` | Disable auto-update |
| `--no-signalr` | Disable Lidarr SignalR client |
| `--debug` | Enable debug logging |

## Architecture

- **Backend**: Python, Flask, Waitress, SQLAlchemy (SQLite), Dynaconf, APScheduler, flask-restx
- **Frontend**: React 19, TypeScript, Vite, Mantine 8, TanStack Query

## License

GPL-3.0
