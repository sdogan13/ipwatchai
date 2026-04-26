# IP Watch AI Deployment

Last updated: 2026-04-19
Status: Current

## Purpose

This file documents the current deployment paths used by this repo.

There are two main modes:
- local Docker stack for development and smoke testing
- prod-style Docker Compose overlay for server deployment

## Core Services

The current compose setup is built around:
- `postgres`
- `redis`
- `backend`
- `nginx`

Optional services:
- `cloudflared`
- `caddy`

## Environment Files

Local Docker stack:
- `.env.production`

Prod-style overlay:
- `deploy/.env.prod`

Local Python app:
- `.env`

Important:
- the base compose file mounts local host paths and is developer-machine oriented
- the prod overlay is the current canonical server deploy path

## Local Docker Stack

Use this when you want the app, PostgreSQL, Redis, and nginx on a local machine.

Setup:

```powershell
Copy-Item .env.production.example .env.production
```

Set at least:
- `DB_PASSWORD`
- `AUTH_SECRET_KEY`
- `REDIS_PASSWORD` if enabled
- `DATA_PATH`
- `CLIENTS_PATH`
- `HF_HOME`
- `TORCH_HOME`
- `WORKERS=1` unless you have explicitly revalidated multi-worker search stability

Start the stack:

```powershell
docker compose up -d postgres redis backend nginx
```

Useful endpoints:
- app health: `http://127.0.0.1:8000/health`
- nginx: `http://127.0.0.1:8080`
- postgres: `127.0.0.1:5433`
- redis: `127.0.0.1:6379`

Notes:
- Docker bootstraps the database from `deploy/schema.sql`
- the base local stack exposes PostgreSQL on host port `5433`
- the current validated backend default is `WORKERS=1`; the previous four-worker default caused intermittent dropped responses on `/api/v1/search/quick` and `/api/v1/search/intelligent`
- the local Docker backend bind-mounts `education/` and `migrations/`, so landing Education materials and the Education progress startup check stay aligned with the workspace

## Prod-Style Deploy Path

This is the current canonical deploy path for server-style environments:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d
```

Stop:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml down
```

Check merged config:

```powershell
docker compose --env-file deploy/.env.prod -f docker-compose.yml -f deploy/docker-compose.prod.yml config
```

Current prod-style behavior:
- backend reads `deploy/.env.prod`
- nginx uses `deploy/nginx.prod.conf`
- postgres is exposed on `127.0.0.1:5432`
- GPU reservation is disabled by default in the prod overlay unless explicitly reintroduced
- the backend worker default is intentionally `1` until the GPU-backed search stack is revalidated under multi-worker uvicorn

## Database Bootstrap

Fresh database bootstrap starts from:
- `deploy/schema.sql`

Additional schema evolution lives in:
- `migrations/`

Notable migration-backed areas include:
- payments
- creative suite tables
- trademark applications
- trademark events

## Health Checks

Backend:

```powershell
curl http://127.0.0.1:8000/health
```

Nginx:

```powershell
curl http://127.0.0.1:8080/health
```

## Operational Notes

- `main.py` is still the supported app entrypoint
- the backend container runs `uvicorn main:app`
- pipeline trigger routes and `workers/pipeline_scheduler.py` now spawn detached `python -m workers.pipeline_worker` child processes, so the backend or scheduler runtime must be allowed to launch child Python processes from the repo root
- detached pipeline workers survive parent web or scheduler process exits, but they do not survive a full host or container restart
- `data_collection.py` incremental mode now verifies recent issues by canonical issue-folder completeness instead of raw file presence; an issue only counts as present when its `BLT_...` or `GZ_...` folder contains both `metadata.json` and `events.json`
- raw collector downloads now use the canonical issue stem, for example `BLT_490_2026-04-13.pdf` or `GZ_500_2026-03-31.zip`
- extraction accepts those canonical raw BLT/GZ filenames alongside the older legacy raw filenames
- successful PDF extraction relocates a top-level raw PDF into its canonical issue folder as `bulletin.pdf`
- Step 2 also runs `pdf_extract_events.py` so BLT/GZ issue folders missing `events.json` are backfilled from their PDFs during extraction
- Step 3 prefers archive DB/text inputs over an existing `metadata.json`, so folders that contain both PDF output and extracted archive data are re-parsed from the archive source
- collection recency and Gazette validation are controlled by `PIPELINE_INCREMENTAL_LOOKBACK`, `PIPELINE_RECENT_WINDOW_DAYS`, and `PIPELINE_MIN_GAZETTE_ISSUE_NUMBER`
- local and prod-style compose modes use different env files; do not assume `.env` drives Docker
- if you change ports, env files, or bootstrap schema behavior, update `README.md` and this file together
