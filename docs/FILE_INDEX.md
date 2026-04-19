# IP Watch AI File Index

Last updated: 2026-04-19
Status: Current high-level map

## Purpose

This file is a practical map of the current codebase layout.

It is intentionally high-level.
- it does not try to list every file
- it does not include stale line counts
- it focuses on the main entrypoints and directories people actually need

## Root Entry Points

- `main.py`: supported FastAPI entrypoint and compatibility wrapper
- `legacy_main.py`: current app assembly surface
- `risk_engine.py`: shared scoring and risk evaluation entrypoint
- `agentic_search.py`: intelligent search orchestration and related runtime helpers
- `data_collection.py`: bulletin collection entrypoint
- `ingest_events.py`: event ingest entrypoint
- `compute_idf.py`: IDF recomputation utility

## Core Application Layers

- `api/`: router modules for auth, billing, reports, applications, admin, leads, holders, pipeline, and related API surfaces
- `auth/`: authentication and current-user helpers
- `config/`: settings and environment loading
- `database/`: database access and CRUD helpers
- `models/`: schema and response models
- `services/`: business logic layer
- `utils/`: shared helpers such as class utilities, scoring helpers, settings helpers, and validation

## App Composition Modules

The app still uses several root-level route and assembly modules:
- `app_factory.py`
- `app_router_registry.py`
- `app_system_routes.py`
- `app_public_search_routes.py`
- `app_public_portfolio_routes.py`
- `app_nice_class_routes.py`
- `app_enhanced_search_routes.py`
- `app_image_search_routes.py`
- `app_legacy_search_routes.py`
- `app_legacy_rollback_routes.py`

## Product And Runtime Areas

- `templates/`: server-rendered pages and dashboard partials
- `static/`: frontend JS, CSS, images, and mounted assets
- `watchlist/`: watchlist-specific scanning and monitoring helpers
- `reports/`: report generation helpers
- `notifications/`: notification support
- `workers/`: background processing support

## Data And Pipeline Areas

- `pipeline/`: embedding and ingest modules
- `bulletins/`: bulletin data root
- `custom_bulletins/`: local bulletin inputs and experiments
- `archive_bulletins/`: archived bulletin data

## Tests

- `tests/test_api_endpoints.py`: broad API contract coverage
- `tests/test_live_app_e2e.py`: aggregate live app smoke
- `tests/test_browser_e2e.py`: aggregate browser smoke
- `tests/test_nightly_e2e.py`: aggregate nightly verification
- `tests/live/`: live HTTP suites and persona coverage
- `tests/browser/`: browser E2E suites
- `tests/nightly/`: stateful/nightly flows

## Deployment And Operations

- `docker-compose.yml`: local stack and shared service definitions
- `deploy/docker-compose.prod.yml`: prod-style overlay
- `Dockerfile.backend`: backend image
- `deploy/schema.sql`: bootstrap schema
- `migrations/`: follow-on schema changes
- `nginx/`: local nginx config
- `deploy/nginx.prod.conf`: prod nginx config

## Scripts And Tooling

- `scripts/`: operational, maintenance, migration, smoke, and setup helpers
- `scripts/devtools/`: development-only tools such as the disposable test-account purge utility

## Repo Workflow Docs

- `rules.md`: repo-wide engineering workflow
- `README.md`: current setup, run, and test guide
- `test.md`: current test strategy and lane definitions
- `docs/DOCUMENTATION.md`: documentation map and reading order
- `docs/archive/`: archived historical project and cleanup trackers
