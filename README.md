# Playlista

Self-hosted music analyzer and playlist generator for local libraries.

Playlista scans your folders, extracts audio features (Essentia), and builds playlists with explainable scoring, energy-curve control, and manual editing.

## What You Get

- Local-file workflow (no cloud dependency required)
- Incremental scan/analysis pipeline
- Feature extraction: BPM, key/scale, energy, flux, dynamic complexity, etc.
- Smart generation with profiles (`balanced`, `focus`, `workout`, `chill`, `drive`)
- M3U/M3U8/JSON playlist export
- Manual playlist builder (add/remove/reorder tracks)
- Optional Navidrome import endpoint

## Architecture

Services in `infra/docker-compose.yml`:

- `api` (FastAPI) -> HTTP API at `http://localhost:8000`
- `worker` (RQ worker) -> background feature analysis
- `postgres` -> metadata/features/playlists storage
- `redis` -> analysis queue backend
- `ui` (nginx static) -> web UI at `http://localhost:8080`

## Requirements

- Linux/macOS with Docker + Docker Compose plugin
- A local music directory on host
- Read access to your music files
- Enough CPU for analysis workload (Essentia is CPU-intensive on large libraries)

## Configure Environment

Create `.env` at project root (same level as this README).

Minimum example:

```env
POSTGRES_DB=playlista
POSTGRES_USER=playlista
POSTGRES_PASSWORD=playlista
DATABASE_URL=postgresql+psycopg://playlista:playlista@postgres:5432/playlista
REDIS_URL=redis://redis:6379/0
MUSIC_ROOT=/absolute/path/to/your/music
ANALYSIS_VERSION=v1
```

Optional settings (supported by app):

```env
# Comma-separated roots shown in UI preset dropdown
MUSIC_ROOTS=/music/A,/music/B
SCAN_PRESETS=/music/A,/music/B

# If using Essentia MusiCNN TF model
ESSENTIA_TF_MODEL_PATH=/models/msd-musicnn-1.pb
```

Notes:

- Use absolute host paths for `MUSIC_ROOT`.
- Compose mounts `${MUSIC_ROOT}` read-only into API/worker.
- If you change analysis logic significantly, bump `ANALYSIS_VERSION` and enqueue analysis again.

## Deploy (Docker Compose)

From project root:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
```

Check status:

```bash
docker compose -f infra/docker-compose.yml ps
```

Follow logs:

```bash
docker compose -f infra/docker-compose.yml logs -f api worker
```

## First Run Workflow

1. Open UI: `http://localhost:8080`
2. In **Library**:
   - set/select scan path
   - click **Scan**
3. Click **Enqueue Analysis**
4. Wait until jobs process (`/analysis/jobs` or logs)
5. Go to **Generate Playlist**, choose profile/preset, click **Generate**
6. Export via **M3U**, **M3U8**, or **JSON**

## UI Guide

Recent UI changes included in this version:

- Preset system restored and synced with profile defaults (not flat-only)
- Advanced controls moved under **Custom** toggle
- Saved custom generation presets in browser storage
- Tracks table upgraded to server-side pagination/sort/filter
- Table shows normalized + raw metrics
- Tracks table and JSON output can be shown/hidden with buttons
- Manual playlist builder supports create/load/update + reordering

### Library

- Scan local root folders
- Use scan presets from env (`MUSIC_ROOTS` / `SCAN_PRESETS`)

### Tracks

- Server-side pagination (30/page), sorting, search, analyzed filter
- Shows normalized metrics and raw metrics
- Hide/show table toggle

### Manual Playlist Builder

- Add tracks from table
- Reorder with up/down
- Save new manual playlist or update existing by ID

### Generate Playlist

- Profiles:
  - `balanced` -> flat curve baseline
  - `focus` -> stricter transitions, lower variance
  - `workout` -> warmup-like energy rise
  - `chill` -> cooldown-like descent
  - `drive` -> peak profile
- Custom panel includes advanced controls:
  - `seed_track_id`, BPM range, curve, diversity
  - cooldowns, transition threshold, temperature
  - history window/penalty, strict constraints
- Real-time mode can auto-regenerate on control changes

## Workers and Throughput

Current default:

- `worker` service runs **1 RQ worker process** (single container, single process)
- In code: `Worker(["analysis"], ...)` in `backend/worker/run_worker.py`

How to increase workers:

- Scale the worker service horizontally with Compose:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --scale worker=4
```

- This starts 4 worker containers consuming from the same `analysis` queue.

How many workers to use:

- Small library / low CPU: `1-2`
- Medium library / modern desktop CPU: `3-6`
- Large library / server-grade CPU: `6+` (watch thermals, IO, DB load)

Operational note:

- More workers increase analysis speed, but also increase CPU and disk pressure.
- Keep Postgres/Redis healthy; if DB becomes bottlenecked, reduce workers.

## API Reference (Core)

Health/metrics:

- `GET /health`
- `GET /metrics`

Library/tracks:

- `POST /library/scan`
- `GET /library/presets`
- `GET /tracks`
- `GET /tracks/status` (server-side `page`, `page_size`, `q`, `analyzed`, `sort_by`, `sort_dir`)

Analysis:

- `POST /analysis/enqueue` (incremental by default if `track_ids` omitted)
- `POST /analysis/retry-failed`
- `GET /analysis/jobs`

Playlist generation:

- `POST /playlists/generate`
- `GET /playlists/{playlist_id}`
- `GET /playlists/{playlist_id}/quality`

Manual playlists:

- `POST /playlists/manual`
- `PUT /playlists/{playlist_id}/manual`

Exports:

- `GET /playlists/{playlist_id}/export.m3u`
- `GET /playlists/{playlist_id}/export.m3u8`
- `GET /playlists/{playlist_id}/export.json`

Navidrome integration:

- `POST /integrations/navidrome/import`

## Navidrome Import Usage

Endpoint: `POST /integrations/navidrome/import`

Request example:

```json
{
  "playlist_id": "PUT-PLAYLIST-UUID-HERE",
  "navidrome_url": "http://localhost:4533",
  "username": "admin",
  "password": "your-password",
  "playlist_format": "m3u8"
}
```

What it does:

- Logs into Navidrome (`/auth/login`)
- Uploads generated M3U payload to Navidrome (`/api/playlist`)

## Data and Persistence

- Postgres volume: `postgres_data`
- Music files are read-only mounted from host
- Playlists, features, jobs are in database

## Troubleshooting

- **No tracks found after scan**
  - Check `MUSIC_ROOT` path exists on host and contains supported files
  - Supported extensions include: `.mp3`, `.flac`, `.wav`, `.m4a`, `.ogg`, `.aiff`, `.aac`

- **Analysis not progressing**
  - Check worker logs
  - Ensure Redis and Postgres are healthy
  - Retry failed jobs via `POST /analysis/retry-failed`

- **UI can open but actions fail**
  - Verify API is reachable at `http://localhost:8000`
  - Check API logs for traceback details

- **Playlist quality seems off**
  - Re-run analysis after major changes
  - Tune profile/custom controls (transition score, cooldowns, BPM jump, strict mode)

- **M3U path playback issues**
  - Ensure exported paths are valid on your player host
  - Confirm consistent host-visible music root mapping

## Upgrade Notes

- Pull changes and rebuild:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up --build -d
```

- If schema changed and not auto-applied, verify migration SQL files in `backend/migrations`.
- If feature logic changed materially, consider bumping `ANALYSIS_VERSION` and re-enqueueing analysis.

## Repo Hygiene

`.gitignore` excludes runtime/artifact data such as:

- `music/`
- `__pycache__/`, `.pyc`, caches/build outputs
- local env files (`.env*`)
- editor/OS junk files
