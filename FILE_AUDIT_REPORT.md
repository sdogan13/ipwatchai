# File Audit Report

**Generated**: 2026-02-07
**Auditor**: Claude Code (Opus 4.6)
**Method**: Static import tracing from `main.py` entry point + cross-reference search

---

## Summary

| Category | Count | Description |
|----------|-------|-------------|
| Total files scanned | ~160 | Excluding bulletins/, python/, pgsql/, pgvector_*, qdrant-*, uploads/ |
| 🟢 Active | 43 | In the runtime dependency chain from `main.py` |
| 🟡 Support | 35 | Dev scripts, docs, migrations, deployment configs |
| 🔴 Unused | 65+ | Not referenced by anything in the active chain |
| ⚪ Unknown | 5 | Need runtime verification |

**Note**: The `bulletins/Marka/` directory (trademark data), `python/` (venv), `pgsql/` (PostgreSQL), `pgvector_*`, `qdrant-*`, and `uploads/` directories are excluded from this audit as they are data/tooling directories.

---

## 🟢 Active Files (DO NOT TOUCH)

These files are in the direct dependency chain from `main.py -> uvicorn` and are required for the application to run.

### Entry Point
| File | Lines | Role |
|------|-------|------|
| `main.py` | 2,380 | FastAPI app, lifespan, routers, endpoints |

### API Layer (`api/`)
| File | Imported By | Role |
|------|-------------|------|
| `api/__init__.py` | Package init | Empty/minimal |
| `api/routes.py` (1,963 lines) | `main.py` | Auth, users, watchlist, alerts, reports, dashboard routers |
| `api/upload.py` | `main.py` | File upload router |
| `api/leads.py` | `main.py` | Opposition Radar lead feed |
| `api/holders.py` | `main.py` | Holder portfolio endpoints |
| `api/creative.py` | `main.py` | Creative Suite (Name Gen + Logo Studio) |

### Config & Models
| File | Imported By | Role |
|------|-------------|------|
| `config/__init__.py` | Package init | |
| `config/settings.py` | `main.py`, `api/*`, `database/crud.py`, `auth/*`, etc. | App settings + CreativeSettings |
| `models/__init__.py` | Package init | |
| `models/schemas.py` | `api/routes.py`, `api/creative.py`, `database/crud.py` | Pydantic models |

### Database
| File | Imported By | Role |
|------|-------------|------|
| `database/__init__.py` | Package init | |
| `database/crud.py` | `api/routes.py`, `api/upload.py`, `api/leads.py`, `api/holders.py`, `api/creative.py`, `notifications/service.py`, `reports/generator.py`, `watchlist/scanner.py`, `workers/monitoring_worker.py` | Database + CRUD classes |
| `pool.py` | `database/crud.py` (via `get_db_connection()`) | DatabasePool singleton |
| `db/__init__.py` | Package init | |
| `db/pool.py` | `risk_engine.py` | Re-exports from root `pool.py` |

### Auth
| File | Imported By | Role |
|------|-------------|------|
| `auth/__init__.py` | Package init | |
| `auth/authentication.py` | `api/routes.py`, `api/upload.py`, `api/leads.py`, `api/holders.py`, `api/creative.py`, `agentic_search.py`, `database/crud.py` | JWT auth, password hashing |

### Utils
| File | Imported By | Role |
|------|-------------|------|
| `utils/__init__.py` | Package init (re-exports from idf_scoring + scoring) | |
| `utils/idf_scoring.py` | `main.py`, `watchlist/scanner.py`, `risk_engine.py` | Data-driven IDF scoring |
| `utils/class_utils.py` | `main.py`, `watchlist/scanner.py`, `risk_engine.py` | Nice class utilities |
| `utils/translation.py` | `watchlist/scanner.py`, `risk_engine.py` | Translation similarity |
| `utils/scoring.py` | `utils/__init__.py` | GENERIC_WORDS_FALLBACK |
| `utils/subscription.py` | `api/leads.py`, `api/holders.py`, `api/creative.py`, `agentic_search.py` | Plan/credit management |

### AI
| File | Imported By | Role |
|------|-------------|------|
| `ai/__init__.py` | Package init | Exports GeminiClient |
| `ai/gemini_client.py` | `main.py` (lifespan), `api/creative.py` | Gemini text+image generation |
| `ai.py` (root, 781 lines) | `risk_engine.py`, `agentic_search.py`, `api/creative.py` | CLIP, DINOv2, MiniLM embeddings |

### Core Engine
| File | Imported By | Role |
|------|-------------|------|
| `risk_engine.py` (910 lines) | `agentic_search.py`, `api/creative.py` | Hybrid similarity scoring |
| `agentic_search.py` (897 lines) | `main.py` | Agentic search router |
| `scrapper.py` (692 lines) | `risk_engine.py`, `agentic_search.py` | Live Playwright scraper |
| `ingest.py` (1,013 lines) | `risk_engine.py`, `agentic_search.py` | DB upsert with pgvector |
| `idf_scoring.py` (root) | `risk_engine.py` | 3-tier IDF scoring |
| `idf_lookup.py` | `risk_engine.py` | IDFLookup class (legacy) |
| `logging_config.py` | `risk_engine.py`, `ai.py`, `worker.py` | Structured logging |

### Watchlist & Workers
| File | Imported By | Role |
|------|-------------|------|
| `watchlist/__init__.py` | Package init | |
| `watchlist/scanner.py` | `api/routes.py` (line 1533), `workers/monitoring_worker.py` | Watchlist scanning |
| `workers/__init__.py` | Package init | |
| `workers/monitoring_worker.py` | Standalone daemon | Background monitoring |
| `workers/universal_scanner.py` | Standalone daemon | Universal conflict scanner |
| `workers/credit_reset.py` | Standalone daemon | Monthly credit reset |
| `notifications/__init__.py` | Package init | |
| `notifications/service.py` | `workers/monitoring_worker.py` | Email/notification service |

### Frontend (Templates + Static)
| File | Included/Loaded By | Role |
|------|---------------------|------|
| `templates/dashboard.html` | `main.py` (TemplateResponse) | Main dashboard layout |
| `templates/partials/_navbar.html` | `dashboard.html` ({% include %}) | Navigation bar |
| `templates/partials/_search_panel.html` | `dashboard.html` | Search form |
| `templates/partials/_results_panel.html` | `dashboard.html` | Results overview tab |
| `templates/partials/_leads_panel.html` | `dashboard.html` | Opposition Radar tab |
| `templates/partials/_ai_studio_panel.html` | `dashboard.html` | AI Studio tab |
| `templates/partials/_modals.html` | `dashboard.html` | All modals |
| `static/js/utils/helpers.js` | `dashboard.html` (script tag) | Utility functions |
| `static/js/utils/auth.js` | `dashboard.html` | Auth token management |
| `static/js/utils/toast.js` | `dashboard.html` | Toast notifications |
| `static/js/components/score-badge.js` | `dashboard.html` | Score rendering |
| `static/js/components/result-card.js` | `dashboard.html` | Search result cards |
| `static/js/components/lead-card.js` | `dashboard.html` | Lead feed cards |
| `static/js/components/studio-card.js` | `dashboard.html` | AI Studio cards |
| `static/js/api.js` | `dashboard.html` | API fetch calls |
| `static/js/app.js` | `dashboard.html` | Alpine.js dashboard app |
| `static/avatars/*.jpg` (4 files) | Served via StaticFiles mount | User avatars |

---

## 🟡 Support Files (KEEP but could reorganize)

### Deployment & Configuration
| File | Purpose |
|------|---------|
| `Dockerfile.backend` | Docker image build (references `main.py`, `requirements.txt`) |
| `docker-compose.yml` | Service orchestration (redis, backend, cloudflare, pgadmin, credit-reset) |
| `.env` | Runtime environment variables |
| `.env.production` | Production environment variables |
| `.env.production.example` | Template for production env |
| `.dockerignore` | Docker build exclusions |
| `requirements.txt` | Python dependencies |
| `CLAUDE.md` | Claude Code project context |
| `README.md` | Project documentation |
| `__init__.py` (root) | Makes project importable as package |

### Migrations (run-once, kept for history)
| File | Purpose |
|------|---------|
| `migrations/creative_suite.sql` | Creative Suite DB schema |
| `migrations/run_creative_suite_migration.py` | Migration runner for creative suite |
| `migrations/add_universal_conflicts.sql` | Opposition Radar DB schema |
| `migrations/run_universal_conflicts_migration.py` | Migration runner |
| `migrations/add_translation_fields.sql` | Translation columns |
| `migrations/add_translation_similarity.sql` | Translation similarity score |
| `migrations/run_translation_similarity.py` | Migration runner |
| `migrations/add_profile_fields.sql` | User profile fields |
| `migrations/add_class_99_global_brand.sql` | Class 99 (global brand) |
| `migrations/add_lead_limits_to_plans.sql` | Lead access limits |
| `schema_v3_multitenant.sql` | Multi-tenant schema (v3, current) |
| `migration_v3_multitenant.sql` | Migration from v1 to v3 |

### Scripts (DevOps utilities)
| File | Purpose |
|------|---------|
| `scripts/setup.ps1` | Initial setup script |
| `scripts/start.ps1` | Start services |
| `scripts/stop.ps1` | Stop services |
| `scripts/backup.ps1` | Database backup |
| `scripts/logs.ps1` | View logs |
| `scripts/add_bulletin_column.py` | One-time DB column addition |
| `scripts/backfill_translations.py` | Backfill translation columns |
| `scripts/compute_idf_scheduled.bat` | Scheduled IDF computation |
| `scripts/README_SCHEDULED_TASKS.md` | Scheduled task documentation |
| `compute_idf.py` | Monthly IDF computation (run standalone, referenced in scheduled task) |

### Documentation
| File | Purpose |
|------|---------|
| `docs/DOCUMENTATION.md` | Full system docs |
| `docs/API_REFERENCE.md` | API endpoint docs |
| `docs/DATABASE_SCHEMA.md` | DB schema docs |
| `docs/DEPLOYMENT.md` | Deployment guide |
| `docs/FILE_INDEX.md` | File index (may be outdated) |

### Data Files (support)
| File | Purpose |
|------|---------|
| `nice_classes.json` (31KB) | Nice classification data |
| `nice_classes_with_embeddings.json` (525KB) | Nice classes + precomputed embeddings |
| `trademark_template.xlsx` (5KB) | Excel template for client data upload |
| `cloudflared/config.yml` | Cloudflare tunnel config |
| `cloudflared/cert.pem` | Cloudflare tunnel cert |
| `cloudflared/*.json` | Cloudflare tunnel credentials |

---

## 🔴 Unused Files (CANDIDATES FOR BACKUP & REMOVAL)

### Superseded/Legacy Python Files

| File | Lines | Evidence of Non-Use |
|------|-------|---------------------|
| `dashboard_api.py` | 682 | **Old monolithic API** before refactor to `main.py`. Not imported by anything in active chain. Contains `from risk_engine import RiskEngine` and `from risk_engine_async import AsyncRiskEngine`. Superseded by `main.py` + `api/routes.py`. |
| `risk_engine_async.py` | ~450 | **Old async risk engine**. Only imported by `dashboard_api.py` (also unused). Not imported by `main.py` or any active module. |
| `worker.py` | ~130 | **Old RQ worker**. Imports `from risk_engine import RiskEngine` and `from logging_config import...`. Superseded by `workers/monitoring_worker.py` and `workers/universal_scanner.py`. Not imported by anything. |
| `job_queue.py` | ~50 | **Old Redis job queue**. Not imported by any active module. Superseded by FastAPI BackgroundTasks. |
| `pipeline.py` | ~300 | **Old pipeline orchestrator**. Not imported by anything. Was the original `master.py` equivalent. |
| `customer_pipeline.py` | 1,619 | **Old customer data pipeline**. Not imported by any active module. Standalone script. |
| `customer_data_integration.py` | 933 | **Old customer data integration**. Not imported by anything in active chain. |
| `data_collection.py` | 703 | **Old bulk downloader** (Playwright). Not imported by any active module. |
| `zip.py` | ~800 | **Old 7-Zip extractor**. Not imported by any active module. |
| `ocr.py` | ~300 | **Old PDF text extractor** (PyMuPDF). Not imported by any active module. |
| `metadata.py` | ~400 | **Old HSQLDB SQL parser**. Not imported by any active module. |
| `CUserssdoganturk_patentmain.py` | 1 | **Garbage file** - misnamed path artifact. |
| `db/async_pool.py` | ~100 | **Unused async pool**. `asyncpg` is not used in active chain. |

### One-Off Fix/Migration Scripts (already executed)

| File | Evidence of Non-Use |
|------|---------------------|
| `fix_status.py` | Standalone DB fix. Not imported by anything. |
| `fix_schema.py` | Standalone schema fix. Not imported. |
| `fix_metadata_dates.py` | Standalone metadata fix. Not imported. |
| `fix_image_naming.py` | Standalone image naming fix. Not imported. |
| `status_fix.py` | Standalone status fix. Not imported. |
| `batch_status_fix.py` | Standalone batch fix. Not imported. |
| `fast_status_fix.py` | Standalone fast fix. Not imported. |
| `partition_fix.py` | Standalone partition fix. Not imported. |
| `migrate_step1.py` | One-time migration. Not imported. |
| `migrate_simple.py` | One-time migration. Not imported. |
| `migrate_tables.py` | One-time migration. Not imported. |
| `migrate_v3.py` | One-time migration. Not imported. |
| `run_migration.py` | One-time migration runner. Not imported. |
| `run_sql.py` | One-time SQL runner. Not imported. |
| `add_col.py` | One-time column addition. Not imported. |
| `create_test_user.py` | One-time test user creation. Not imported. |
| `create_test_users.py` | One-time test user creation. Not imported. |
| `create_word_idf.py` | One-time word_idf table creation. Not imported. |
| `verify_plans.py` | One-time plan verification. Not imported. |
| `schema.sql` | Original v1 schema. Superseded by `schema_v3_multitenant.sql`. |

### Debug/Test Scripts (development only)

| File | Evidence of Non-Use |
|------|---------------------|
| `debug_schema.py` | Debug script. Not imported. |
| `debug_ingest.py` | Debug script. Not imported. |
| `debug_pool.py` | Debug script. Not imported. |
| `debug_get_connection.py` | Debug script. Not imported. |
| `debug_import.py` | Debug script. Not imported. |
| `simple_db_test.py` | Test script. Not imported. |
| `test_python_db.py` | Test script. Not imported. |
| `test_scrapper_visible.py` | Test script. Not imported. |
| `test_idf_scoring.py` | Test script. Not imported. |
| `test_3tier_scoring.py` | Test script. Not imported. |
| `test_client_trademarks.py` | Test script. Not imported. |
| `test_routes.py` | Test script. Not imported. |
| `test_ui.py` | UI test script. Not imported. |
| `test_ui_v2.py` | UI test script. Not imported. |
| `test_ui_final.py` | UI test script. Not imported. |
| `tescil_test.py` (root) | Test script. Not imported. |
| `check_tables.py` | DB check script. Not imported. |
| `check_ext.py` | DB check script. Not imported. |
| `check_all_tables.py` | DB check script. Not imported. |
| `check_locks.py` | DB check script. Not imported. |
| `check_schema.py` | DB check script. Not imported. |
| `_check_watchlist.py` | Debug script. Not imported. |

### Standalone Analysis Scripts (run manually, not in app chain)

| File | Evidence of Non-Use |
|------|---------------------|
| `assess_client_trademarks.py` | Client assessment. Not imported by active chain. |
| `extract_client_data.py` | Client data extraction. Not imported. |
| `extract_and_merge_client_data.py` | Client data merge. Not imported. |
| `transform_client_data.py` | Client data transformation. Not imported. |
| `run_full_risk_analysis.py` | Standalone risk analysis. Not imported. |
| `run_risk_analysis_direct.py` | Standalone risk analysis. Not imported. |
| `system_health_check.py` | Health check utility. Not imported. |
| `kill_stuck.py` | Process killer. Not imported. |

### Unused PowerShell Scripts

| File | Evidence |
|------|----------|
| `check_count.ps1` | DB query. Not referenced in Docker/CI. |
| `check_db_state.ps1` | DB state check. Not referenced. |
| `check_ingestion.ps1` | Ingestion check. Not referenced. |
| `check_vector_ext.ps1` | pgvector check. Not referenced. |
| `grant_all_permissions.ps1` | DB permissions. Not referenced. |
| `grant_permissions.ps1` | DB permissions. Not referenced. |
| `install_pgvector.ps1` | pgvector install. Not referenced. |
| `overnight_step0_backup.ps1` | Backup. Not referenced. |
| `overnight_step4_postgres.ps1` | Postgres setup. Not referenced. |
| `overnight_step5_user.ps1` | User setup. Not referenced. |
| `run_debug_get_conn.ps1` | Debug launcher. Not referenced. |
| `run_debug_ingest.ps1` | Debug launcher. Not referenced. |
| `run_debug_pool.ps1` | Debug launcher. Not referenced. |
| `run_debug_schema.ps1` | Debug launcher. Not referenced. |
| `run_fix_schema.ps1` | Fix launcher. Not referenced. |
| `run_full_ingest.ps1` | Ingest launcher. Not referenced. |
| `run_ingest_direct.ps1` | Ingest launcher. Not referenced. |
| `run_ingest_unbuffered.ps1` | Ingest launcher. Not referenced. |
| `run_python_test.ps1` | Test launcher. Not referenced. |
| `run_simple_test.ps1` | Test launcher. Not referenced. |
| `start_ingestion.ps1` | Ingestion launcher. Not referenced. |
| `test_db_connection.ps1` | DB test. Not referenced. |
| `test_ingest_import.ps1` | Import test. Not referenced. |
| `try_postgres_pwd.ps1` | Password test. Not referenced. |

### Unused Data/Report Files

| File | Size | Evidence |
|------|------|----------|
| `_search_test.txt` | 0 bytes | Empty file |
| `ingest_error.txt` | 0 bytes | Empty log file |
| `ingest_output.txt` | 0 bytes | Empty log file |
| `ingest_pid.txt` | 16 bytes | Stale PID file |
| `pipeline.log` | 2.8KB | Old pipeline log |
| `patent_extraction.log` | 322 bytes | Old extraction log |
| `server.log` | 15KB | Old server log |
| `server_test.log` | 3.3MB | Large test log |
| `BACKUP_PATH.txt` | 106 bytes | Old backup path note |
| `OVERNIGHT_SUMMARY.txt` | 2KB | Old overnight run summary |
| `openapi.json` | 40KB | Generated OpenAPI spec (regenerable) |
| `openapi_temp.json` | 40KB | Duplicate of openapi.json |
| `openapi2.json` | 40KB | Duplicate of openapi.json |
| `temp_openapi.json` | 64KB | Duplicate of openapi.json |
| `temp_openapi2.json` | 64KB | Duplicate of openapi.json |
| `COMPATIBILITY_AUDIT_REPORT.md` | 11KB | Old audit report |
| `COMPLETE_FILE_LIST.md` | 13KB | Old file list (outdated) |
| `IP_WATCH_AI_DOCUMENTATION.md` | 72KB | Superseded by `docs/` directory |
| `LAUNCH_GUIDE.md` | 5KB | Potentially superseded by docs/DEPLOYMENT.md |
| `LOCAL_OPTIMIZED_ARCHITECTURE.md` | 31KB | Architecture doc (may be outdated) |
| `PRODUCTION_DEPLOYMENT_CHECKLIST.md` | 16KB | Potentially superseded by docs/DEPLOYMENT.md |
| `dashboard.html` (root) | 96KB | **OLD monolithic dashboard**. Superseded by `templates/dashboard.html` + partials. |

### `.py/` Directory (scratch/archive - ENTIRE DIR UNUSED)

| File | Evidence |
|------|----------|
| `.py/date.py` | Scratch script. Not imported by anything. |
| `.py/ai.py` | Old copy of `ai.py`. Not imported. |
| `.py/pdf.py` | PDF utility. Not imported. |
| `.py/blt_scrap.py` | Bulletin scraper. Not imported. |
| `.py/merge.py` | Data merge script. Not imported. |
| `.py/tescil_test.py` | Test script. Not imported. |
| `.py/clean.py` | Cleanup script. Not imported. |

### `trademark-system/` Directory (ENTIRE DIRECTORY UNUSED)

This entire directory is an **old/abandoned microservices architecture attempt** that was never completed. It contains copies of files from the root project reorganized into a `services/api/` and `services/worker/` structure, but the current application runs from `main.py` at the project root.

**Evidence**: `docker-compose.yml` in root references `Dockerfile.backend` (root), NOT `trademark-system/services/api/Dockerfile`. No active code imports from `trademark-system/`.

| Path | Content |
|------|---------|
| `trademark-system/docker-compose.yml` | Old compose file |
| `trademark-system/.env`, `.env.example` | Old env files |
| `trademark-system/start.sh` | Old start script |
| `trademark-system/docker/` | nginx.conf, redis.conf, postgresql.conf |
| `trademark-system/config/settings.py` | Old settings |
| `trademark-system/services/api/` | Duplicated API code |
| `trademark-system/services/worker/` | Duplicated worker code |

### Unused Binary/Installer Files

| File | Size | Evidence |
|------|------|----------|
| `Docker Desktop Installer.exe` | **614MB** | Installer - should not be in project |
| `postgresql-17.7-2-windows-x64.exe` | **371MB** | Installer - should not be in project |
| `postgresql-binaries.zip` | **316MB** | Binary archive - should not be in project |
| `7z2501.exe` | 1.3MB | 7-Zip installer |
| `winzip77-bing.exe` | 25MB | WinZip installer |
| `pgvector_pg17.zip` | 165KB | pgvector extension zip |
| `vector.v0.8.1-pg18.zip` | 165KB | pgvector extension zip |
| `get-pip.py` | 2.2MB | pip installer |
| `dogan_patent.png` | 13KB | Logo image (not referenced in active code) |

### Unused/Data Directories

| Directory | Content | Evidence |
|-----------|---------|----------|
| `C:Users701693turk_patentai/` | **Empty directory** | Artifact from path confusion |
| `frontend/dist/` | Old frontend build (429.html, 50x.html, index.html) | Superseded by templates/ |
| `clients/` | Client data (DOGAN_PATENT, WATCHLIST_TEST) | Customer data, not code |
| `dogan_patent/` | **Empty directory** | Unused |
| `dogan_patent_processed/` | Processed client data (metadata.json, .xlsx, images/) | Customer data output |
| `tescil_pdfs/` | ~90 PDF bulletins (~2.3GB total) | Downloaded data, not needed for app runtime |
| `pgvector_extracted/` | Extracted pgvector files | Installation artifact |
| `vector.v0.8.1-pg18/` | pgvector extension files | Installation artifact |

---

## ⚪ Unknown Files (NEED MANUAL VERIFICATION)

| File | Ambiguity |
|------|-----------|
| `reports/__init__.py` + `reports/generator.py` | `reports_router` is registered in `main.py` via `api/routes.py`, BUT `reports/generator.py` is NOT directly imported by `api/routes.py`. The report generation may be done inline in routes.py. Check if `reports/generator.py` is actually called at runtime. |
| `workers/monitoring_worker.py` | Standalone daemon. Imports from active modules. If it's actually deployed, it's 🟢 Active. If not deployed, it's 🟡 Support. |
| `compute_idf.py` (root) | Referenced by `scripts/compute_idf_scheduled.bat` for periodic IDF recomputation. Not a runtime dependency but may be operationally critical. Classify as 🟡 Support if still scheduled. |

---

## Other Issues Found

### Duplicates
| Original | Duplicate | Notes |
|----------|-----------|-------|
| `pool.py` (root) | `db/pool.py` | db/pool.py re-exports from root. risk_engine.py imports from db/pool.py. Both needed currently but should consolidate. |
| `idf_scoring.py` (root) | `utils/idf_scoring.py` | **Different implementations!** Root is 3-tier, utils is data-driven. Both imported by different active modules. Should consolidate. |
| `openapi.json` | 4 other copies | `openapi_temp.json`, `openapi2.json`, `temp_openapi.json`, `temp_openapi2.json` |
| `schema.sql` | `schema_v3_multitenant.sql` | v3 is current. schema.sql is v1 (superseded). |
| Root `ai.py` | `.py/ai.py` | .py/ai.py is an old copy |
| Root `tescil_test.py` | `.py/tescil_test.py` | .py/ version is an old copy |

### Misplaced Files
| File | Issue | Suggestion |
|------|-------|------------|
| `pool.py` (root) | Core module at root | Move to `database/pool.py` |
| `idf_scoring.py` (root) | Duplicates `utils/idf_scoring.py` | Consolidate into `utils/` |
| `idf_lookup.py` (root) | Should be in `utils/` | Move to `utils/idf_lookup.py` |
| `logging_config.py` (root) | Should be in `config/` | Move to `config/logging.py` |
| `ai.py` (root) | Should be in `ai/` directory | Move to `ai/embeddings.py` |
| `scrapper.py` (root) | Core module at root | Move to `services/scraper.py` |
| `ingest.py` (root) | Core module at root | Move to `database/ingest.py` |
| `risk_engine.py` (root) | Core module at root | Move to `core/risk_engine.py` |
| `agentic_search.py` (root) | Router at root level | Move to `api/agentic_search.py` |
| Installer .exe/.zip files | Should not be in project | Remove entirely |

### Oversized Files (>1000 lines)
| File | Lines | Suggestion |
|------|-------|------------|
| `main.py` | 2,380 | Split: move inline endpoints to `api/` modules |
| `api/routes.py` | 1,963 | Split: separate auth, users, watchlist, alerts into own route files |
| `customer_pipeline.py` | 1,619 | Unused - candidate for removal |
| `ingest.py` | 1,013 | Active but large - could split ingestion logic |

### Empty Directories
| Directory | Notes |
|-----------|-------|
| `C:Users701693turk_patentai/` | Garbage - empty dir from path confusion, delete immediately |
| `dogan_patent/` | Empty directory, delete |

### Potentially Unused requirements.txt Packages
| Package | Used By | Status |
|---------|---------|--------|
| `rq==1.15.1` | Only `job_queue.py` (🔴 unused) | **Remove** |
| `aiosmtplib==3.0.1` | Not found in any active import | **Remove** |
| `sentry-sdk==1.39.1` | Not imported by any project file | **Remove** |
| `asyncpg==0.29.0` | Only `db/async_pool.py` (🔴 unused) | **Remove** |
| `aiohttp==3.9.1` | Only test files (🔴 unused) | **Remove** |
| `PyMuPDF==1.23.8` | Only `ocr.py` (🔴 unused) | **Remove** |
| `httpx==0.26.0` | Listed twice (line 50 and 78) | **Deduplicate** |
| `black`, `flake8`, `isort`, `mypy` | Dev tools | Move to dev-only section |
| `pytest`, `pytest-asyncio`, `pytest-cov` | Test tools | Move to dev-only section |

---

## Recommended Backup Plan

```
backup_20260207/
├── unused_python/
│   ├── dashboard_api.py
│   ├── risk_engine_async.py
│   ├── worker.py
│   ├── job_queue.py
│   ├── pipeline.py
│   ├── customer_pipeline.py
│   ├── customer_data_integration.py
│   ├── data_collection.py
│   ├── zip.py
│   ├── ocr.py
│   ├── metadata.py
│   ├── CUserssdoganturk_patentmain.py
│   └── db/async_pool.py
├── unused_scripts/
│   ├── fix_*.py (5 files)
│   ├── migrate_*.py (4 files)
│   ├── debug_*.py (5 files)
│   ├── check_*.py (5 files)
│   ├── run_*.py (3 files)
│   ├── test_*.py (8 files)
│   ├── assess_client_trademarks.py
│   ├── extract_*.py (2 files)
│   ├── transform_client_data.py
│   ├── create_test_user.py
│   ├── create_test_users.py
│   ├── create_word_idf.py
│   ├── kill_stuck.py
│   ├── verify_plans.py
│   ├── system_health_check.py
│   ├── add_col.py
│   ├── _check_watchlist.py
│   └── run_sql.py
├── unused_ps1/
│   └── *.ps1 (24 files)
├── unused_data/
│   ├── openapi*.json (5 files)
│   ├── temp_openapi*.json (2 files)
│   ├── *.log (3 files)
│   ├── *.txt (empty/stale, 5 files)
│   ├── dashboard.html (root, old monolithic)
│   ├── schema.sql (v1, superseded)
│   └── compute_idf.py.bak, idf_lookup.py.bak
├── unused_docs/
│   ├── COMPATIBILITY_AUDIT_REPORT.md
│   ├── COMPLETE_FILE_LIST.md
│   ├── IP_WATCH_AI_DOCUMENTATION.md
│   ├── LOCAL_OPTIMIZED_ARCHITECTURE.md
│   └── PRODUCTION_DEPLOYMENT_CHECKLIST.md
├── unused_dirs/
│   ├── .py/ (entire directory)
│   ├── trademark-system/ (entire directory)
│   ├── frontend/dist/ (old frontend build)
│   └── C:Users701693turk_patentai/ (garbage dir)
├── unused_installers/  (OR just delete these)
│   ├── Docker Desktop Installer.exe (614MB!)
│   ├── postgresql-17.7-2-windows-x64.exe (371MB!)
│   ├── postgresql-binaries.zip (316MB!)
│   ├── 7z2501.exe
│   ├── winzip77-bing.exe
│   ├── pgvector_pg17.zip
│   ├── vector.v0.8.1-pg18.zip
│   └── get-pip.py
└── manifest.txt (this report - what was moved and why)
```

**Estimated space savings**: ~1.35GB from installer .exe/.zip files alone, plus ~500KB from unused Python/scripts/data files, plus ~2.3GB if tescil_pdfs/ is moved.

### Priority Actions
1. **Immediate** (safe, huge savings): Delete installer .exe/.zip files (1.3GB)
2. **Immediate** (safe): Delete empty/garbage directories
3. **Quick win**: Move `trademark-system/` to backup (entire abandoned architecture)
4. **Quick win**: Move `.py/` directory to backup (scratch files)
5. **Quick win**: Delete 5 duplicate openapi*.json files
6. **Safe**: Move legacy Python files to backup
7. **Safe**: Move old PS1 scripts to backup
8. **Later**: Consolidate `pool.py`/`db/pool.py` and `idf_scoring.py`/`utils/idf_scoring.py` duplicates
9. **Later**: Split `main.py` (2380 lines) and `api/routes.py` (1963 lines)
