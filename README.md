# IP Watch AI

IP Watch AI is a FastAPI-based trademark monitoring and search platform for Turkish trademark data.

This repo includes:
- the application backend and server-rendered frontend
- authenticated and public trademark search flows
- watchlists, alerts, reports, applications, billing, and admin tools
- holder-aware watchlist alert filtering so a holder's own later applications do not appear as similarity conflicts against their existing watched marks
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

Worker note:
- the Docker-backed backend is currently validated with `WORKERS=1`
- the previous four-worker default caused intermittent empty-response failures on quick and intelligent search routes
- only raise `WORKERS` after revalidating the live, browser, and nightly search lanes

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
- the local Docker backend bind-mounts `education/` and `migrations/`, so landing-page study materials and Education progress startup checks use the workspace files directly
- `education/moderation_overrides.json` is mounted writable into the backend container so admin/superadmin tester curation in the Education tab persists locally

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

## Scoring Engine

Canonical similarity scoring lives behind `score_pair()` in `services/scoring_service.py` and is re-exported through `risk_engine.py`.

Current version: `v2_text_visual`.
- text scoring compares the query against the original candidate name and, when present, `trademarks.name_tr`
- `translation_similarity` is the translated-name textual path score, not a separate overall signal
- text token weighting separates true descriptive generics such as `patent` and `ltd` from common trademark anchors such as `dogan`
- descriptor-like category/service/entity terms are now classified from local corpus behavior in `word_idf` and `word_idf_tr`; high-IDF descriptor terms stay generic and cannot become common anchors on their own
- low-protectability anchors are detected from descriptor statistics when a corpus-distinctive term behaves like a suffix-heavy weak modifier; shared weak-anchor-only matches are capped as limited text unless the full query core is copied
- short acronym-style anchors require exact normalized copying; non-exact fuzzy/phonetic matches such as a two-character acronym against a longer translated word are capped as weak evidence
- exact short-acronym subset matches with missing matter on either side are capped as limited text, and short collapsed `name_tr` values cannot turn a longer original candidate into a high translated match
- compact compounds with true-generic suffixes, such as `doganpatent`, are scored through their anchor plus generic components
- Retrieval V2 pre-screening searches normalized, compact, containment, token, fuzzy, OCR, semantic, visual, and phonetic candidate paths before V2 scoring
- short anchor tokens use exact token-boundary retrieval across `name` and `name_tr`; broad substring token retrieval is reserved for longer anchors so short queries are not flooded by unrelated fragments
- textual retrieval runs symmetrically across `trademarks.name` and `trademarks.name_tr`, so translated-name candidates enter the same scoring flow as original-name candidates
- retrieval diagnostics record which internal stage and field found a candidate; scoring still decides Path A (`name`) versus Path B (`name_tr`)
- dominant-core scoring keeps fully copied marks high with generic additions, while capping changed extra matter such as a different second brand term
- collapsed `name_tr` values are capped so translated-name scoring cannot turn a longer original mark into an exact match
- visual scoring normalizes across active CLIP, DINOv2, and OCR components only; color vectors may still be present in retrieval data but do not contribute to visual risk
- weak textual evidence, including generic-only, missing-anchor, dominant-anchor-missing, or semantic/phonetic-only support, prevents moderate visual similarity from creating a high conflict score
- partial multi-anchor matches with changed matter on both sides are capped as limited text evidence, so one shared token cannot be boosted into high risk by moderate visual similarity
- single-anchor matches with generic/service query matter and different target identity matter are treated as limited text evidence unless the full core is copied
- weak non-exact dominant-anchor matches, including fuzzy and phonetic anchors, are calibrated by length ratio, edit distance, and anchor coverage; full-length one-edit variants can remain meaningful while fragment-like or weak translated matches are capped as limited text evidence
- short one-token marks suppress text/visual agreement boosts when their only anchor match is non-exact and wordmark OCR disagrees, unless OCR is strong or CLIP+DINO are independently very strong
- guarded caps are calibrated continuously from coverage, match quality, and added-matter evidence; cap values remain policy ceilings rather than automatic final scores
- OCR disagreement between wordmark logos caps moderate CLIP/DINO visual evidence; weak-text cases require strong OCR agreement or very strong CLIP+DINO evidence before visual similarity can drive high risk
- `name_tr` values that normalize to the original candidate name cannot beat Path A solely because translated IDF flags classify tokens differently
- final text/visual combining is max-plus, so a strong text or logo match is not diluted when the other signal is missing
- the score remains a `0.0-1.0` similarity-risk score; legal factors such as status, class relatedness, seniority, and enforceability are handled outside this scoring slice

When unified scoring is enabled, `/api/search` maps the canonical `RiskEngine.assess_brand_risk()` result into its legacy response shape, so enhanced search, public search, and watchlist conflicts use the same candidate retrieval and `score_pair()` scoring behavior.

## Translation Refresh

- Live query translation now defaults to the MADLAD backend from `utils/translation.py`, so search-time translated queries stay aligned with the refreshed MADLAD-backed `name_tr` corpus.
- Offline `name_tr` regeneration is now driven by `scripts/regenerate_name_tr.py`, which:
  - benchmarks the candidate backend against the current NLLB baseline
  - exports a rollback snapshot before refresh
  - refreshes `trademarks.name_tr` and `detected_lang`
  - syncs matching on-disk `metadata.json` records back from the refreshed DB state
  - records `name_tr_backend`, `name_tr_model`, and `name_tr_updated_at`
  - persists resumable progress in `artifacts/translation_refresh/name_tr_refresh_progress.json`, including the active ordering mode, campaign watermark, and metadata sync state
  - processes historical backfills newest-first by `application_date DESC NULLS LAST, id DESC`, while using the MADLAD provenance watermark to skip rows already handled by the same refresh campaign
  - sends every trademark name through MADLAD on the MADLAD refresh path instead of using language detection to decide whether a row should be translated
  - cleans generic leading prompt/meta leakage structurally (for example language-prefixed `... çeviri:` / `... translation:` forms) instead of storing that leaked prefix in `name_tr`
  - preserves the original trademark when a MADLAD candidate changes, drops, adds, or reorders digits
  - uses a separate MADLAD generation microbatch size, default `16`, so DB page size and GPU generation batch size can be tuned independently without changing translation behavior
- Compatibility wrappers:
  - `scripts/backfill_translations.py`
  - `scripts/regen_null.py`
- Useful commands:
  - `python scripts/regenerate_name_tr.py --benchmark-only`
  - `python scripts/regenerate_name_tr.py --dry-run --limit 5000`
  - `python scripts/regenerate_name_tr.py`
  - `python scripts/regenerate_name_tr.py --skip-benchmark --ordering-mode application_date_desc --campaign-watermark 2026-04-24T21:34:29Z --translate-batch-size 16`
- Background launch for long-running refreshes:
  - `python scripts/launch_name_tr_refresh_background.py --backend madlad --skip-benchmark --ordering-mode application_date_desc --campaign-watermark 2026-04-24T21:34:29Z --translate-batch-size 16`
  - the launcher writes a manifest plus stdout/stderr logs under `artifacts/translation_refresh/`

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
The Docker-backed test path is currently validated against a single backend worker because the multi-worker default caused intermittent dropped search responses in the real app lanes.

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
- `/api/v1/tools/status`: AI Studio Name Lab and Logo Studio availability

## Pipeline Notes

Pipeline and data-collection code lives in:
- `data_collection.py`
- `zip.py`
- `pdf_extract.py`
- `ingest_events.py`
- `pipeline/`

Operational helpers and maintenance scripts live in `scripts/`.

Pipeline runtime notes:
- `/api/v1/pipeline/trigger` and `/api/v1/pipeline/trigger-step` create a `pipeline_runs` row and then launch `python -m workers.pipeline_worker` as a detached child process
- `workers/pipeline_scheduler.py` now launches the same detached worker path for scheduled full and daily processing runs instead of executing the pipeline inline inside the scheduler process
- this decouples an active run from the parent web or scheduler process lifetime, but a full host or container restart still terminates the worker process
- incremental collection now compares recent site issues against local issue folders and only treats an issue as complete when its `BLT_...` or `GZ_...` folder contains both `metadata.json` and `events.json`
- raw collector downloads are named with the canonical issue stem, for example `BLT_490_2026-04-13.pdf` or `GZ_500_2026-03-31.zip`
- extraction now accepts those canonical raw BLT/GZ filenames as well as legacy raw names when scanning PDFs and archives
- successful PDF extraction now relocates a top-level raw PDF into its canonical issue folder as `bulletin.pdf`
- Step 2 now also runs `pdf_extract_events.py` across BLT/GZ issue folders missing `events.json`, including repair of older top-level raw-PDF cases when the issue folder already exists
- Step 3 now prefers archive DB/text inputs over an existing `metadata.json`, so mixed PDF + archive folders are re-parsed from the archive source and `metadata.json` is overwritten
- Step 3 preserves existing AI feature fields when the trademark-name and image inputs still match, and it can restore missing AI fields from the database before `pipeline/ai.py` evaluates skip logic
- `pipeline/ingest.py` is now a thin compatibility wrapper; canonical ingest status rules live in `pipeline/ingest_rules.py`, runtime orchestration lives in `pipeline/ingest_runtime.py`, and explicit ingest-runtime setup/readiness checks live in `pipeline/ingest_bootstrap.py`
- ingest no longer self-patches schema/reference tables on every run; apply `python migrations/run_ingest_runtime_migration.py` once per environment and normal ingest will then fail fast if runtime prerequisites are missing
- APP ingest records no longer overwrite an existing BLT/GZ status with `Applied` or registration-number fallback status; APP can only overwrite an existing status when its explicit `STATUS` text maps to a recognized non-`Applied` status
- ingest normalizes placeholder trademark names by removing `sekil`/variant figure markers before writing metadata into the database
- `python -m workers.pipeline_worker --force-ingest` forces the trademark ingest step to reprocess existing metadata files, which is useful for repairing earlier ingest-state drift without changing collection or extraction inputs
- the post-ingest `repair` step now also supports batched live TURKPATENT checks: older `Yayında` rows are status-audited from live result `Durumu`, and exact-six class rows are repaired only from `DETAY` `Nice Sınıfları`; progress is stored in `repair_live_trademark_checks`
- pipeline translation for future folders now defaults to the MADLAD backend, so `pipeline/ai.py` writes `name_tr`, `detected_lang`, and translation provenance into `metadata.json` using MADLAD unless `PIPELINE_TRANSLATION_BACKEND` is overridden
- MADLAD translation runtime tuning is shared between refresh and pipeline paths through `MADLAD_TRANSLATE_BATCH_SIZE` (default `16`), so future folder translation uses the same safer generation microbatch unless explicitly overridden
- `TRANSLATION_BACKEND=nllb` remains the immediate rollback lever if live query latency or behavior needs to revert without changing the MADLAD-backed corpus
- when FastText is unavailable, language detection now reports `unknown` instead of heuristic guesses; the MADLAD refresh path still evaluates every trademark name, preserves an existing `detected_lang` when fresh detection is `unknown`, cleans generic prompt/meta leakage, and falls back to the original trademark when translated digits drift, while live MADLAD query translation preserves the original query text if the model fails
- the normal worker path now runs `event_ingest` automatically after trademark ingest and before conflict scan, so `trademark_events`, event-derived trademark state, `final_status*`, and event-based watchlist alerts stay in sync with the latest pipeline run
- `ingest_events.py` now reconciles `trademark_events` per local BLT/GZ issue scope instead of append-only inserts, so reruns replace a scope with the current `events.json` payload
- event-row uniqueness is now enforced by a full-payload `event_fingerprint`, which preserves distinct same-type events while removing exact duplicates
- `final_status`, `final_status_source`, and `final_status_at` are reconciler-owned fields derived from ingest-owned `current_status` and event-owned `effective_status`; `pipeline/ingest.py` and `ingest_events.py` now update their own source fields and then call the shared reconciler for touched application numbers
- `/api/v1/pipeline/trigger-step` now also supports `event_ingest` as a first-class worker step for manual reruns of event reconciliation/materialization
- `/api/v1/pipeline/trigger-step` now also supports a manual `final_status_repair` maintenance step for chunked full-table reconciliation when legacy drift needs repair
- collector tuning knobs: `PIPELINE_INCREMENTAL_LOOKBACK`, `PIPELINE_RECENT_WINDOW_DAYS`, and `PIPELINE_MIN_GAZETTE_ISSUE_NUMBER`

If you run archive extraction locally on Windows, make sure `PIPELINE_SEVEN_ZIP_PATH` points to a working 7-Zip executable.

## Development Rules

Before making non-trivial changes:
- read `rules.md`
- use a task branch unless the change is tiny and low risk
- run the smallest test set that proves the change
- keep created test data and runtime artifacts out of git

## License

Copyright 2026 Dogan Patent. All rights reserved.
