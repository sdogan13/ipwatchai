# IP Watch AI

IP Watch AI is a FastAPI-based trademark monitoring and search platform for Turkish trademark data.

This repo includes:
- the application backend and server-rendered frontend
- authenticated and public trademark search flows
- watchlists, alerts, reports, applications, billing, and admin tools
- bulletin collection and ingest pipeline code
- unit, API, live, browser, and nightly verification suites

Engineering workflow and change rules live in `rules.md`.

## Key Docs

- `rules.md`: repo-wide engineering workflow
- `test.md`: test strategy, coverage map, and verification lanes
- `docs/DOCUMENTATION.md`: current documentation map
- `docs/DEPLOYMENT.md`: deployment guidance
- `docs/DATABASE_SCHEMA.md`: schema notes

## Repo Layout

- `main.py`: compatibility entrypoint for the FastAPI app
- `legacy_main.py`: current app assembly and route registration surface
- `api/`, `auth/`, `config/`, `database/`: core app layers
- `services/`: business logic for auth, search, watchlist, billing, reports, usage, and admin flows
- `pipeline/`: embedding and ingest pipeline modules
- `templates/`, `static/`: mounted UI assets and server-rendered pages
- `tests/`: unit, API, live, browser, and nightly suites
- `deploy/`: bootstrap schema and deployment overlays
- `scripts/`: operational and maintenance helpers

## Quick Start

### Option A: Docker Stack

Recommended when you want the full local stack with PostgreSQL, Redis, backend, and nginx.

Prerequisites:
- Docker Desktop
- Python 3.10+ if you also want to run local scripts or tests

Setup:

```powershell
Copy-Item .env.production.example .env.production
```

Edit `.env.production` and set at least:
- `DB_PASSWORD`
- `AUTH_SECRET_KEY`
- `REDIS_PASSWORD` if you want Redis auth enabled
- local host paths such as `DATA_PATH`, `CLIENTS_PATH`, `HF_HOME`, and `TORCH_HOME` if the defaults do not match your machine

Start the core local stack:

```powershell
docker compose up -d postgres redis backend nginx
```

Useful endpoints:
- backend health: `http://127.0.0.1:8000/health`
- nginx entrypoint: `http://127.0.0.1:8080`
- PostgreSQL host port: `127.0.0.1:5433`
- Redis host port: `127.0.0.1:6379`

Notes:
- `cloudflared` is optional and not needed for local development
- Docker bootstraps the database from `deploy/schema.sql`

### Option B: Local Python App Against Local Or Docker Services

Recommended when you want to edit Python code directly and run the app outside Docker.

Prerequisites:
- Python 3.10+
- PostgreSQL with pgvector
- Redis
- Playwright browsers if you plan to run browser tests
- 7-Zip if you plan to run archive extraction locally

Setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m playwright install chromium
Copy-Item .env.production.example .env
```

Edit `.env` for your local setup.

Common local values when PostgreSQL and Redis are running through Docker Compose:
- `DB_HOST=127.0.0.1`
- `DB_PORT=5433`
- `REDIS_HOST=127.0.0.1`
- `REDIS_PORT=6379`
- `AI_DEVICE=cpu` if you are not running with CUDA

Start the backing services if needed:

```powershell
docker compose up -d postgres redis
```

Run the app:

```powershell
python main.py
```

For live reload during development:

```powershell
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Notes:
- `/docs` is only available when debug mode is enabled
- `main.py` remains the supported entrypoint even though it is now a compatibility wrapper

## Testing

The repo has several verification layers. Start narrow and widen only when the change affects a broader surface.

Core API regression:

```powershell
python -m pytest tests/test_api_endpoints.py -s
```

Full mocked regression suite:

```powershell
python -m pytest tests -s
```

Live app aggregate:

```powershell
python tests/test_live_app_e2e.py
```

Browser aggregate:

```powershell
python tests/test_browser_e2e.py
```

Nightly aggregate:

```powershell
python tests/test_nightly_e2e.py
```

Live, browser, and nightly suites expect a running app and read:
- `TEST_BASE_URL`
- `TEST_EMAIL`
- `TEST_PASSWORD`

The smoke harness now reuses managed free, starter, and professional test personas instead of creating large numbers of disposable accounts on every run.

Browser notes:
- default browser channel is `msedge`
- if Edge is not available locally, set `TEST_BROWSER_CHANNEL=chromium`

See `test.md` for the current test lanes and coverage expectations.

## Stable Endpoints

- `/health`: app, database, and Redis health
- `/api/info`: basic service metadata
- `/api/v1/status`: service status and headline database stats
- `/api/v1/search/public`: public landing-page search
- `/api/v1/search/quick`: authenticated quick search
- `/api/v1/search/intelligent`: authenticated deeper search flow

## Pipeline Notes

Pipeline and data-collection code lives in:
- `data_collection.py`
- `zip.py`
- `pdf_extract.py`
- `ingest_events.py`
- `pipeline/`

Operational helpers and maintenance scripts live in `scripts/`.

If you run archive extraction locally on Windows, make sure `PIPELINE_SEVEN_ZIP_PATH` points to a working 7-Zip executable.

## Development Rules

Before making non-trivial changes:
- read `rules.md`
- use a task branch unless the change is tiny and low risk
- run the smallest test set that proves the change
- keep created test data and runtime artifacts out of git

## License

Copyright 2026 Dogan Patent. All rights reserved.
