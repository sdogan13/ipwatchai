# IP Watch AI File Index

Last updated: 2026-04-25
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
- `risk_engine.py`: shared scoring facade and search risk evaluation entrypoint
- `agentic_search.py`: intelligent search orchestration and related runtime helpers
- `data_collection.py`: bulletin collection entrypoint (Marka)
- `data_collection_patent.py`: bulletin collection entrypoint (Patent / Faydalı Model)
- `data_collection_tasarim.py`: bulletin collection entrypoint (Tasarım)
- `data_collection_cografi.py`: bulletin collection entrypoint (Coğrafi İşaret ve Geleneksel Ürün Adı)
- `ingest_events.py`: event ingest entrypoint
- `compute_idf.py`: IDF and corpus-derived descriptor-classification recomputation utility

## Core Application Layers

- `api/`: router modules for auth, billing, reports, applications, admin, leads, holders, education, pipeline, and related API surfaces
- `auth/`: authentication and current-user helpers
- `config/`: settings and environment loading
- `database/`: database access and CRUD helpers
- `models/`: schema and response models
- `services/`: business logic layer, including canonical V2 text/visual scoring in `services/scoring_service.py`, education content/progress loading, and Education tester moderation overlay handling for category, explanation, summary, and soft-delete overrides
- `utils/`: shared helpers such as class utilities, scoring helpers, settings helpers, and validation
- `idf_lookup.py`: in-memory IDF and descriptor-evidence lookup used by V2 scoring and retrieval

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
- `education/`: repo-owned vekillik study materials such as PDFs, categorized flashcard CSV/JSON data, quiz JSON, and `moderation_overrides.json` for tester-only Education category/explanation curation
- `watchlist/`: watchlist-specific scanning and monitoring helpers
- `reports/`: report generation helpers
- `notifications/`: notification support
- `workers/`: background processing support

## Data And Pipeline Areas

- `pipeline/`: embedding and ingest modules
  canonical ingest modules are `pipeline/ingest.py` (compat wrapper), `pipeline/ingest_rules.py`, `pipeline/ingest_runtime.py`, `pipeline/ingest_bootstrap.py`, and `pipeline/ingest_helpers.py`
- Tasarım (industrial design) pipeline: `data_collection_tasarim.py`, `pdf_extract_tasarim.py`, `pdf_extract_tasarim_events.py`, `cd_extract_tasarim.py`, `embeddings_tasarim.py`, `pipeline/reconcile_tasarim.py`, `pipeline/ingest_designs.py`, plus the one-shot folder-hygiene `scripts/fix_tasarim_folder_dates.py`. Each issue is materialized into `bulletins/Tasarim/TS_{N}_{date}/` containing `bulletin.pdf`, `metadata.json`, `events.json`, `images/`, `cd_metadata.json`, `cd_images/`, and the reconciler's `merged_metadata.json`. PDF and CD outputs share a canonical `image_path` key shape `{appno_norm}/{d}_{v}.jpg`; the reconciler pairs records by `application_no` (TR) or normalised `registration_no` (Hague) and merges with CD-wins precedence.
- Coğrafi İşaret ve Geleneksel Ürün Adı pipeline (Phase F — collector + extractor + embeddings + DB ingest + search/detail routes + watchlist/alerts, full archive): `data_collection_cografi.py`, `pdf_extract_cografi.py`, `embeddings_cografi.py`, `pipeline/ingest_cografi.py`, `migrations/cografi.sql`, `migrations/cografi_watchlist.sql`, `services/cografi_search_service.py`, `services/cografi_detail_service.py`, `services/cografi_watchlist_service.py`, `services/cografi_scanner_service.py`, `app_cografi_search_routes.py`, `app_cografi_watchlist_routes.py`, plus the one-shot `scripts/migrate_cografi_layout.py`. Each issue is materialized into `bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/CI_{card_id}_{date}/` containing `bulletin.pdf`, the extractor's `metadata.json`, and a `figures/{record_slug}/{idx}.{ext}` tree of embedded record images. The collector takes a direct-href fast path against the TÜRKPATENT cografi category and writes magic-byte-detected RAR bundles separately as `{card_id}_bundle.rar` for the migration helper to expand. The extractor uses Section 2's `Sıralı Liste` as its parsing oracle and emits records across 8 semantic types (examined / registered / article 40 modified / article 42 change requests / article 42 finalized / article 43 modified / corrections / gazette-only announcements); section dispatch is by title classification (so transitional bulletins with both KHK 555 and SMK 6769 sub-sections route correctly). Built-in per-PDF quality verifier cross-checks index counts against parsed body counts. B2 adds per-record figures (smart-filtered against per-page header logos via image-prevalence threshold) and body_sections (free-text subsections for product description / production method / boundary processing / inspection). C1 adds text embeddings (`intfloat/multilingual-e5-large`, 1024-dim) and per-figure DINOv2 ViT-L/14 (1024-dim) + CLIP ViT-B/32 (512-dim) image embeddings with mean-pooled per-record `primary_figure_embedding`. D adds DB ingest into four `cografi_*` tables (records / holders / change_requests / figures) with HNSW vector indexes + trigram on names; reuses the global `holders` table for applicants. E adds the FastAPI route layer (`app_cografi_search_routes.py`) + service modules: hybrid text+image search at `POST /api/v1/cografi-search/quick` (auth) and `GET\|POST /api/v1/cografi-search/public` (anonymous, capped at 10), typeahead at `GET /api/v1/cografi-search/autocomplete`, full-detail hydrate at `GET /api/v1/cografi/{record_id}`, and figure thumbnail serving at `GET /api/v1/cografi-image/{path}`. Filters: section_keys, record_types, gi_type, region (trigram), bulletin_date range, application_no, registration_no. Exact-ID shortcut for `C{YYYY}/{NNNNNN}` and bare integer registration numbers. F adds watchlist + alerts: two new tables (`cografi_watchlist_mt` + `cografi_alerts_mt`), four watch types (holder / reference / region / lifecycle — region + lifecycle are NEW vs patent's two), a per-item scanner with 4 match modes, and a post-ingest hook in `pipeline/ingest_cografi.py` that fans out scans against newly upserted record_ids only. Quota is shared across all four registries via `combined_watchlist_count`. Endpoints under `/api/v1/cografi-watchlist` (CRUD + on-demand /scan) and `/api/v1/cografi-alerts` (list + acknowledge/dismiss/resolve workflow). F3 adds notification delivery: `EmailService.send_cografi_digest` (bilingual TR/EN, per-watch_type Eşleşme column), `WebhookService.send_cografi_alert_webhook` (cografi.alert.new event with discriminating watched.watch_type for all 4 types), and `NotificationWorker` daily/weekly digest + immediate webhook loops scheduled at the existing 09:10 / Monday-09:10 stagger. CLI shortcuts: `python -m notifications.service [cografi-daily-digest | cografi-weekly-digest | cografi-webhooks]`. Covers cards 1-220 (KHK 555 + SMK 6769 eras) — empirical 220/220 bulletins, 3,527 records, 5,393 figures, 99.26% record-level success, ≈5 min full embedding pass on an RTX 4070, ≈3.5 min full DB ingest on local Postgres 16 + pgvector 0.8.1.
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
  ingest runtime bootstrap now lives in `migrations/ingest_runtime.sql` and `migrations/run_ingest_runtime_migration.py`
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
