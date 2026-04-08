# Playlista Architecture

```mermaid
flowchart LR
  lib[LocalMusicLibrary] --> api[FastAPI]
  api --> db[(PostgreSQL)]
  api --> redis[(RedisQueue)]
  redis --> worker[EssentiaWorker]
  worker --> db
  ui[WebUI] --> api
```

- `FastAPI` exposes ingest, analysis, and playlist endpoints.
- `Redis + RQ` executes asynchronous analysis jobs.
- `EssentiaWorker` extracts features and writes raw + normalized outputs.
- `PostgreSQL` stores tracks, jobs, features, playlists, and explanations.
