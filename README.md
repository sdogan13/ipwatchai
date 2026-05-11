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
  - logo-only `SEKIL/ŞEKİL` records are cleaned as textless marks; name-derived text embeddings/translations are cleared while OCR and visual embeddings are preserved
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
- `CREATIVE_DEEPSEEK_API_KEY` if you want DeepSeek fallback for text-only risk reports
- `CREATIVE_DEEPSEEK_TIMEOUT` for full fallback search risk reports; use `120` seconds for the 20-result dashboard report path
- `CREATIVE_QWEN_API_KEY` or `DASHSCOPE_API_KEY` if you want Qwen risk reports enabled
- `CREATIVE_QWEN_TEXT_MODEL` for text-only risk reports; default `qwen-max`
- `CREATIVE_QWEN_CLASS_MODEL` for Nice class suggestions; default `qwen-flash`
- `CREATIVE_QWEN_VL_MODEL` for logo-based risk reports; default `qwen3-vl-plus`
- `CREATIVE_QWEN_TIMEOUT` for full logo-based risk reports; use `120` seconds for the 20-result dashboard report path
- `CREATIVE_OPENAI_API_KEY` or `OPENAI_API_KEY` for Logo Studio image generation; default image model is `gpt-image-2`
- `CREATIVE_OPENAI_IMAGE_SIZE`, `CREATIVE_OPENAI_IMAGE_QUALITY`, `CREATIVE_OPENAI_IMAGE_BACKGROUND`, and `CREATIVE_OPENAI_IMAGE_OUTPUT_FORMAT` to tune GPT Image logo output; in-code defaults are `1024x1024`, `high`, `auto`, and `png`. Production overrides first-generation quality to `medium` to control spend (high ≈ $0.17/image, medium ≈ $0.04/image, low ≈ $0.01/image at 1024×1024)
- `CREATIVE_OPENAI_IMAGE_REVISION_QUALITY` to set the quality used by the edit endpoint when revising a chosen logo (defaults to `high`). Kept high because revisions refine a logo the user has already committed to, where fidelity matters more than cost
- `CREATIVE_LOGO_IMAGES_PER_RUN` to control how many logo candidates each first-generation request produces; in-code default is `4` (also used in production for meaningful exploration). Cost scales linearly with this value
- `CREATIVE_LOGO_REVISION_IMAGES_PER_RUN` to control how many candidates a revision (logo edit) request produces; in-code default is `1`. Revisions refine an already-chosen logo, so a single high-quality output usually matches user intent better than multiple variants
- `CREATIVE_GOOGLE_API_KEY` if you want AI Studio Name Lab enabled, Gemini fallback for search risk reports, or Logo Studio backup image generation
- `CREATIVE_GEMINI_CLASS_FALLBACK_MODEL` for Nice class suggestion fallback; default `gemini-2.5-flash-lite`
- `CREATIVE_GEMINI_IMAGE_MODEL` for Logo Studio backup image generation; default `gemini-3-pro-image-preview` (Nano Banana Pro)
- local host paths such as `DATA_PATH`, `CLIENTS_PATH`, `HF_HOME`, and `TORCH_HOME` if the defaults do not match your machine

Worker note:
- the Docker-backed backend is currently validated with `WORKERS=1`
- the previous four-worker default caused intermittent empty-response failures on quick and intelligent search routes
- only raise `WORKERS` after revalidating the live, browser, and nightly search lanes

Start the core local stack:

```powershell
docker compose up -d postgres redis backend nginx
```

If you change `.env.production` after the backend container has already been created, recreate the backend so Docker applies the new environment:

```powershell
docker compose up -d --force-recreate backend
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
- visual scoring normalizes across active CLIP, DINOv2, and OCR components only; OCR is compared logo-to-logo with conservative exact/character evidence, can drive plain text wordmark visual matches when both images are text-on-blank, and cannot cap or drag down neural CLIP/DINO evidence; color vectors may still be present in retrieval data but do not contribute to visual risk
- blank-background plain-text wordmark images are profiled from image geometry and OCR presence; when those logo texts or names do not agree, their CLIP/DINO visual score is capped so typography-on-white false positives do not dominate
- image-only searches do not promote uploaded-logo OCR into the trademark-name text query; OCR stays inside `visual_breakdown`, is compared only against candidate `logo_ocr_text`, and has low weight in the image-only visual quality guard because EasyOCR can be noisy on logo crops; graphic/mixed logo layout variants can escape the moderate cap when CLIP and DINOv2 corroborate the same visual identity through strict high-component evidence or balanced close-component evidence
- weak textual evidence, including generic-only, missing-anchor, dominant-anchor-missing, or semantic/phonetic-only support, prevents moderate visual similarity from creating a high conflict score
- partial multi-anchor matches with changed matter on both sides are capped as limited text evidence, so one shared token cannot be boosted into high risk by moderate visual similarity
- single-anchor matches with generic/service query matter and different target identity matter are treated as limited text evidence unless the full core is copied
- weak non-exact dominant-anchor matches, including fuzzy and phonetic anchors, are calibrated by length ratio, edit distance, and anchor coverage; full-length one-edit variants can remain meaningful while fragment-like or weak translated matches are capped as limited text evidence
- guarded caps are calibrated continuously from coverage, match quality, and added-matter evidence; cap values remain policy ceilings rather than automatic final scores
- OCR disagreement remains diagnostic only; it no longer caps visual evidence or suppresses text/visual agreement boosts
- `name_tr` values that normalize to the original candidate name cannot beat Path A solely because translated IDF flags classify tokens differently
- final text/visual combining is max-plus, so a strong text or logo match is not diluted when the other signal is missing
- search and watchlist score cards display original-name text from `path_a_score` and translated-name text from `translation_similarity`; `text_idf_score` remains the selected textual path used by the overall combiner
- new watchlist similarity alerts persist the full V2 score diagnostics in `alerts_mt.score_details`, so conflict cards can show original-name, translated-name, semantic, and visual components without collapsing translated Path B into the direct text card; existing alert rows remain score snapshots until rescanned
- watchlist conflict lists, counters, scanner pools, and alert feeds treat a similarity conflict as active only when the conflicting mark is published and its opposition deadline has not passed; older registered, renewed, refused, or withdrawn rows are not shown as active conflicts even if stale deadline data exists
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
- `data_collection.py` (Marka)
- `data_collection_patent.py` (Patent / Faydalı Model)
- `data_collection_tasarim.py` (Tasarım)
- `data_collection_cografi.py` (Coğrafi İşaret ve Geleneksel Ürün Adı)
- `zip.py`
- `pdf_extract.py`
- `pdf_extract_tasarim.py`, `pdf_extract_tasarim_events.py`, `cd_extract_tasarim.py`, `embeddings_tasarim.py` (Tasarım extractors)
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

### Patent / Faydalı Model collector

`data_collection_patent.py` is the sister collector to `data_collection.py` (Marka) and `data_collection_tasarim.py` (Tasarım). It targets the TÜRKPATENT bulletin page for the **Patent / Faydalı Model** category. Output lands flat under `bulletins/Patent__Faydali_Model/`, matching the existing on-disk convention:

- `{YYYY_M}.pdf` — INID-coded bulletin with embedded figures
- `{YYYY_M}_CD.rar` — HSQLDB-backed CD bundle (see UI note below)

```powershell
python data_collection_patent.py                     # incremental, headless, default tracks
python data_collection_patent.py --full              # walk full archive
python data_collection_patent.py --pdf-only          # only the PDF track
python data_collection_patent.py --cd-only           # only the CD track (see note)
python data_collection_patent.py --limit 1           # stop after 1 download
python data_collection_patent.py --headless=false    # show browser
python data_collection_patent.py --bulletins-root C:\path\to\elsewhere
```

**UI reality (verified 2026-05-08):** the live TÜRKPATENT page exposes each Patent bulletin as a **direct-href `<a>` anchor to the PDF only** — there is no dropdown menu and no CD `.rar` download exposed by this UI. The collector takes a fast path on these anchors (stream-downloads via the href with the browser's cookies). When run with the default tracks, missing CDs are reported as skipped (not failed) since the UI does not currently provide them. Existing `_CD.rar` files in the folder predate the current UI shape; their acquisition path is not part of this collector. `--cd-only` is preserved for forward compatibility but will produce all-skips against today's UI.

A legacy menu fallback path is retained in case TÜRKPATENT reintroduces a dropdown UI for some bulletins (Marka and Tasarım both rely on that path), but it is not exercised by the current Patent flow.

Pure-helper unit tests live in `tests/test_data_collection_patent.py` and cover card-id normalization, recency window, per-track filename construction, completeness check (including the legacy multi-month bundle false-match guard), menu-item CD/PDF classification, the direct-href validator, and CLI argv parsing.

### Coğrafi İşaret ve Geleneksel Ürün Adı pipeline

The cografi pipeline materializes every issue into a single canonical folder, mirroring the tasarım layout so downstream stages have one predictable place to look:

```
bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/
├── CI_{card_id}_{YYYY-MM-DD}/
│   ├── bulletin.pdf                ← PDF source (data_collection_cografi)
│   └── metadata.json               ← extracted records (pdf_extract_cografi)
└── {card_id}_bundle.rar            ← legacy multi-bulletin RAR archives
                                      (cards 1-99 era; expanded by the
                                      one-shot migration helper)
```

**Collector — `data_collection_cografi.py`** targets the TÜRKPATENT bulletin page for the **Coğrafi İşaret ve Geleneksel Ürün Adı** (geographical indication / traditional product name) category. Each downloaded card lands directly in `CI_{card_id}_{date}/bulletin.pdf`. Files whose magic bytes are `Rar!` (legacy multi-bulletin bundles served with a `.pdf` content-disposition) are renamed to `{card_id}_bundle.rar` for the migration helper to expand.

```powershell
python data_collection_cografi.py                     # incremental, headless
python data_collection_cografi.py --full              # walk full archive
python data_collection_cografi.py --limit 1           # stop after 1 download
python data_collection_cografi.py --headless=false    # show browser
python data_collection_cografi.py --force             # ignore on-disk freshness
python data_collection_cografi.py --bulletins-root C:\path\to\elsewhere
```

**UI reality (verified 2026-05-10):** every cografi card on the live UI surfaces a single direct-href `<a>` (`webim.turkpatent.gov.tr/file/{uuid}?name={ID}&download`) pointing at the issue PDF (or, for cards 1-99, a RAR archive). No CD bundle and no İndir dropdown menu. Cards rendered without a usable href are reported as failures (not silently skipped) so a future UI change is loud.

Card IDs are sequential issue numbers (`220, 219, 218 ...`) rather than the patent collector's `YYYY_M` shape; cadence is roughly biweekly. The card-date regex is intentionally wider than the patent collector's (`\d{1,2}` vs `\d{2}` for the day) — the cografi UI emits dates with single-digit days (e.g. `4.05.2026`).

**Migration helper — `scripts/migrate_cografi_layout.py`** is the one-shot conversion from the legacy flat layout (`{N}.pdf` + RAR bundles named `{N1}-{N2}.pdf`) into the subfolder layout. Idempotent.

```powershell
python scripts/migrate_cografi_layout.py --dry-run     # preview
python scripts/migrate_cografi_layout.py               # apply
```

**Extractor — `pdf_extract_cografi.py`** reads each `CI_*/bulletin.pdf` and emits the sibling `metadata.json` containing one record per published application across 8 record types (examined / registered / article 40 modified / article 42 change requests / article 42 finalized / article 43 modified / corrections / gazette-only announcements). Section 2's `Sıralı Liste` is the parsing oracle. As of B2 the extractor also captures **figures** (embedded images written to `figures/{record_slug}/{idx}.ext`, smart-filtered against per-page header logos) and **body_sections** (the four free-text subsections found in every applied/registered record: product_description, production_method, boundary_processing, inspection).

```powershell
python pdf_extract_cografi.py --pdf path/to/bulletin.pdf
python pdf_extract_cografi.py --issue 220
python pdf_extract_cografi.py --all                    # every CI_*/bulletin.pdf
python pdf_extract_cografi.py --all --force            # overwrite metadata.json
```

**Full archive supported (B1.5).** Cards 1-220 (KHK 555 + SMK 6769 eras) extract via a label-mapping refactor that handles both legal regimes' field labels and section types. Section dispatch is by **semantic key** classified from the TOC title (so transitional bulletins with both KHK and SMK examined sub-sections get routed correctly), and per-record slicing supports multiple body extents per semantic key. **Empirical: 220/220 bulletins, 3,527 records, ≈99.26% record-level success** — see the extractor's docstring for the residual edge-case categories (mostly source-data omissions).

**Embeddings — `embeddings_cografi.py` (B2+C1).** Reads each `CI_*/metadata.json` and writes embeddings back in place:
- **Text** (`intfloat/multilingual-e5-large`, 1024-dim, L2-normalised): per-record passage built from name + gi_type + product_group + geographical_boundary + usage_description + body_sections; stored under `record.text_embedding`.
- **DINOv2 ViT-L/14** (1024-dim) per figure under `figure.embeddings.dinov2_vitl14`; mean-pooled per record into `record.primary_figure_embedding`.
- **CLIP ViT-B/32** (512-dim, L2-normalised) per figure under `figure.embeddings.clip_vitb32`.

```powershell
python embeddings_cografi.py                    # all bulletins missing aggregates
python embeddings_cografi.py --issue 220
python embeddings_cografi.py --device cuda      # default: auto-detect
python embeddings_cografi.py --force            # re-embed everything
```

Vision branch is auto-skipped when no figures exist across the selected metadata.json files (saves ~1.8 GB GPU memory). Idempotent — already-embedded records pass through untouched unless `--force`. Empirical: 5.3 min on an RTX 4070 for the full 220-bulletin set (3,527 text + 5,393 image embeddings + 1,458 primary aggregates).

Per-PDF quality verifier built into the extractor cross-checks Section 2 index counts against the parsed body for every bulletin during `--all`; structural problems are surfaced as `[?]` warnings so a regression is loud.

Pure-helper unit tests live in `tests/test_data_collection_cografi.py` (collector + subfolder layout + RAR detection) and `tests/test_pdf_extract_cografi.py` (extractor helpers + section-key classification + record header parsing + change-request / correction parsers).

### Tasarım (industrial design) pipeline

The Tasarım pipeline materializes every issue into a single canonical folder so downstream stages have one predictable place to look for sources, metadata, and images:

```
bulletins/Tasarim/TS_{bulletin_no}_{bulletin_date}/
├── bulletin.pdf                  ← PDF source (data_collection_tasarim)
├── metadata.json                 ← PDF-extracted (pdf_extract_tasarim)
├── events.json                   ← PDF events (pdf_extract_tasarim_events)
├── images/                       ← PDF figures
│   └── {appno_norm}/
│       └── {d}_{v}.jpg
├── cd_metadata.json              ← HSQLDB-CD-extracted (cd_extract_tasarim)
└── cd_images/                    ← CD-extracted JPEGs
    └── {appno_norm}/
        └── {d}_{v}.jpg
```

Pipeline modules:
- `data_collection_tasarim.py` — bulletin collector (TÜRKPATENT Tasarım category, PDF only via the legacy menu fallback)
- `pdf_extract_tasarim.py` — PDF metadata + per-design view image extraction; saves figures to `images/{appno_norm}/{d}_{v}.jpg`
- `pdf_extract_tasarim_events.py` — events on existing registrations (12 event types: transfer, seizure, renewal, cancellation variants, etc.)
- `cd_extract_tasarim.py` — HSQLDB CD bundle extractor for legacy issues 230..466. Reads `idbulletin.{script,log,inf}` plus per-application JPEG folders, persists images to `cd_images/{appno_norm}/{d}_{v}.jpg`, emits `cd_metadata.json` with all dossiers, holders, designers, designs, and IDANNOTATION rows. Handles both modern `{N}_CD.rar` and the verbose-named `* cd içeri *.rar` layouts. Hague (`DM/...`) dossiers are emitted with `views: []` (no images on CD)
- `embeddings_tasarim.py` — DINOv2 + CLIP + HSV per-view embeddings, mean-pooled per design; written back into `metadata.json`
- `pipeline/reconcile_tasarim.py` — Stage 3 reconciler. Merges per-issue `cd_metadata.json` and `metadata.json` into `merged_metadata.json`. Locked precedence: **CD wins on every overlapping field**, PDF fills gaps. Pairs TR records by `application_no` and Hague by normalised `registration_no`. CD images stay in `cd_images/`; PDF dups at the canonical key are dropped (proactively at extraction time by D.1 + D.2 in pdf_extract_tasarim and cd_extract_tasarim, plus an idempotent `dedupe_images_on_disk` mop-up callable via `--dedupe-images`). Embeddings stay in source `metadata.json`; CD `annotations[]` and PDF `events.json` pass through unchanged
- `scripts/fix_tasarim_folder_dates.py` — one-shot folder-hygiene script: extracts each `_CD.rar`'s `idbulletin.inf`, finds existing `TS_{N}_*/` folders whose date suffix drifts from the canonical inf DATE (TÜRKPATENT page intermittently reports archive-ingestion dates instead of publication dates), and renames them so subsequent CD output lands in the same folder
- `pipeline/ingest_designs.py` — DB ingest into `designs`, `design_views`, `design_events` (idempotent)

**Canonical image key**: both `metadata.json` and `cd_metadata.json` use the same string shape for `image_path` — `{appno_norm}/{d}_{v}.jpg`, no leading folder prefix. The folder prefix (`images/` vs `cd_images/`) is provided by the consumer when resolving the key against disk. This lets a future stage-3 reconciler match PDF and CD images by a single string.

CD CLI examples:
```powershell
python cd_extract_tasarim.py --rar bulletins/Tasarim/240_CD.rar     # one archive
python cd_extract_tasarim.py --all                                  # every HSQLDB-shape rar in bulletins/Tasarim/
python cd_extract_tasarim.py --rar ... --force                      # overwrite an existing cd_metadata.json
```

PDF CLI:
```powershell
python pdf_extract_tasarim.py --issue TS_483_2026-04-24             # single issue
python pdf_extract_tasarim.py --force                               # re-extract; --force wipes images/ for clean slate
```

Collector CLI:
```powershell
python data_collection_tasarim.py                                   # incremental, headless
python data_collection_tasarim.py --full                            # walk full archive
python data_collection_tasarim.py --issue 240                       # targeted single-bulletin recovery (implies --full)
```

Reconciler CLI:
```powershell
python -m pipeline.reconcile_tasarim --issue TS_240_2016-03-09      # one folder
python -m pipeline.reconcile_tasarim --all                          # every TS_*/ folder
python -m pipeline.reconcile_tasarim --issue ... --force            # overwrite an existing merged_metadata.json
python -m pipeline.reconcile_tasarim --all --dedupe-images          # also remove pre-existing PDF image dups
```

## Development Rules

Before making non-trivial changes:
- read `rules.md`
- use a task branch unless the change is tiny and low risk
- run the smallest test set that proves the change
- keep created test data and runtime artifacts out of git

## License

Copyright 2026 Dogan Patent. All rights reserved.
