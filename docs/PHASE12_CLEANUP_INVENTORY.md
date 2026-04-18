# Phase 12 Cleanup Inventory

Last updated: 2026-04-13

## Purpose

This document is the working inventory for Phase 12 root cleanup and compatibility removal.

Use it to decide whether a compatibility surface should be:
- `delete`
- `deleted`
- `keep`
- `move`
- `decide later`

Do not delete from the Phase 12 candidate list without:
- a local backup archive under `phase12_backups/`
- a batch manifest
- the required tests for that batch

## Classification Rules

- `delete`:
  The file is only a compatibility wrapper, the canonical replacement is active, and runtime callers can be switched safely.
- `deleted`:
  The file was archived under `phase12_backups/` and removed in a completed Phase 12 batch.
- `keep`:
  The file is still the stable public entrypoint or deployment contract.
- `move`:
  The file should survive, but not in its current root location.
- `decide later`:
  The file looks removable, but current runtime, script, or external usage still needs review.

## Candidate Inventory

| Candidate | Current role | Canonical replacement | Status | Notes |
| --- | --- | --- | --- | --- |
| `templates/admin.html` | Thin template include wrapper | `templates/admin/page.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/pricing.html` | Thin template include wrapper | `templates/billing/pricing.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/checkout.html` | Thin template include wrapper | `templates/billing/checkout.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/dashboard.html` | Thin template include wrapper | `templates/dashboard/page.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/landing.html` | Thin template include wrapper | `templates/marketing/landing.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_navbar.html` | Thin include wrapper | `templates/dashboard/partials/_navbar.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_search_panel.html` | Thin include wrapper | `templates/dashboard/partials/_search_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_results_panel.html` | Thin include wrapper | `templates/dashboard/partials/_results_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_watchlist_panel.html` | Thin include wrapper | `templates/dashboard/partials/_watchlist_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_leads_panel.html` | Thin include wrapper | `templates/dashboard/partials/_leads_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_ai_studio_panel.html` | Thin include wrapper | `templates/dashboard/partials/_ai_studio_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_reports_panel.html` | Thin include wrapper | `templates/dashboard/partials/_reports_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_applications_panel.html` | Thin include wrapper | `templates/dashboard/partials/_applications_panel.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `templates/partials/_modals.html` | Thin include wrapper | `templates/dashboard/partials/_modals.html` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `static/js/admin.js` | Thin script loader wrapper | `static/js/admin/panel.js` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `static/js/app.js` | Thin script loader wrapper | `static/js/dashboard/app.js` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `static/js/landing.js` | Thin script loader wrapper | `static/js/marketing/landing.js` | `deleted` | Archived and removed in `batch01_frontend_wrappers`. |
| `ingest.py` | Root compatibility wrapper | `pipeline/ingest.py` | `deleted` | Archived and removed in `batch03_ingest_wrapper` after retargeting runtime imports, tests, and the ingest audit surface. Residual `ingest.py` hits are doc-only tree/comment references. |
| `pipeline_parallel.py` | Root compatibility wrapper | `pipeline/parallel.py` | `deleted` | Archived and removed in `batch02_pipeline_parallel_wrapper` after retargeting the remaining tests. |
| `ai.py` | Root compatibility wrapper | `pipeline/ai.py` | `deleted` | Archived and removed in `batch04_ai_wrapper` after retargeting runtime callers, scripts, docs, tests, and the dev compose mount to `pipeline.ai`. |
| `idf_scoring.py` | Root scoring wrapper | `services/scoring_service.py` | `deleted` | Archived and removed in `batch05_idf_scoring_wrapper` after retargeting tests, scripts, docs, and the local dev compose mount. |
| `main.py` | Stable app entrypoint | `legacy_main.py` implementation behind `main:app` | `keep` | Still the app boot contract and should not be deleted early. |
| `database/crud.py` | Compatibility facade plus DB wrapper | `database/repositories/*` | `keep` | Still carries the shared database wrapper; not a pure delete candidate yet. |
| `check_flag.py` | One-off root utility | `scripts/devtools/check_flag.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `check_gazette.py` | One-off root utility | `scripts/devtools/check_gazette.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `check_status.py` | One-off root utility | `scripts/devtools/check_status.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `debug_score.py` | One-off root utility | `scripts/devtools/debug_score.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `debug_score2.py` | One-off root utility | `scripts/devtools/debug_score2.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `fix_filters.py` | One-off root utility | `scripts/devtools/fix_filters.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `migrate_enum_cleanup.py` | One-off root utility | `scripts/devtools/migrate_enum_cleanup.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `update_status.py` | One-off root utility | `scripts/devtools/update_status.py` | `move` | Moved off the repo root in `batch06_root_devtools_move`. |
| `check_missing.py` | One-off root utility | `scripts/devtools/check_missing.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `check_missing_container.py` | One-off root utility | `scripts/devtools/check_missing_container.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `check_sim.py` | One-off root utility | `scripts/devtools/check_sim.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `audit_data_quality.py` | One-off root audit utility | `scripts/devtools/audit_data_quality.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `audit_pdf_formats.py` | One-off root audit utility | `scripts/devtools/audit_pdf_formats.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_api.py` | Root scratch test utility | `scripts/devtools/test_api.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_db_setting.py` | Root scratch test utility | `scripts/devtools/test_db_setting.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_db_setting2.py` | Root scratch test utility | `scripts/devtools/test_db_setting2.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_db_status.py` | Root scratch test utility | `scripts/devtools/test_db_status.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_idf.py` | Root scratch test utility | `scripts/devtools/test_idf.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_isolated.py` | Root scratch test utility | `scripts/devtools/test_isolated.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_isolated2.py` | Root scratch test utility | `scripts/devtools/test_isolated2.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_isolated3.py` | Root scratch test utility | `scripts/devtools/test_isolated3.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_isolated4.py` | Root scratch test utility | `scripts/devtools/test_isolated4.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_legacy.py` | Root scratch test utility | `scripts/devtools/test_legacy.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_legacy2.py` | Root scratch test utility | `scripts/devtools/test_legacy2.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_query.py` | Root scratch test utility | `scripts/devtools/test_query.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_query2.py` | Root scratch test utility | `scripts/devtools/test_query2.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_search_error.py` | Root scratch test utility | `scripts/devtools/test_search_error.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_sim.py` | Root scratch test utility | `scripts/devtools/test_sim.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_sql.py` | Root scratch test utility | `scripts/devtools/test_sql.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `test_yenilendi.py` | Root scratch test utility | `scripts/devtools/test_yenilendi.py` | `move` | Moved off the repo root in `batch07_remaining_root_cleanup`. |
| `take_screenshot.py` | One-off root browser helper | `scripts/devtools/take_screenshot.py` | `move` | Moved off the repo root in `batch08_remaining_root_review`. |
| `search_metadata.py` | One-off root metadata search helper | `scripts/devtools/search_metadata.py` | `move` | Moved off the repo root in `batch09_remaining_root_review`. |
| `start-docker.ps1` | One-off root Docker startup helper | `scripts/devtools/start-docker.ps1` | `move` | Moved off the repo root in `batch09_remaining_root_review`. |
| `legacy_main.py` | Legacy app implementation module behind stable wrapper | `main.py` compatibility entrypoint | `keep` | Still mounted in local compose and remains the actual implementation behind `main:app`. |
| `logging_config.py` | Shared runtime logging module | N/A | `keep` | Still imported directly by runtime modules and tests. |
| `compute_idf.py` | Operational IDF maintenance script | N/A | `keep` | Still documented and referenced by admin/test/docs flows for maintaining `word_idf` tables. |
| `idf_lookup.py` | Runtime IDF lookup module | N/A | `keep` | Still imported directly by runtime code, tests, and local compose. |
| `foreign_generics.py` | Standalone scoring constants module | N/A | `keep` | Still imported by `idf_lookup.py`, `utils.idf_scoring`, and local compose. |

## Remaining root-code conclusion

The remaining tracked root code files now fall into one of two categories:
- active runtime or deployment contracts that should stay in place
- canonical operational modules that are no longer compatibility cleanup candidates

Generated local artifacts still sitting in the root directory, such as logs, temp outputs, dumps, and ad hoc reports, are out of scope for Phase 12 because they are environment-local files rather than tracked compatibility surfaces.

## Batch 1 Definition

### Batch name

`batch01_frontend_wrappers`

### Goal

Remove the Phase 11 template and frontend bundle wrappers after switching runtime callers to canonical paths.

### Pre-delete updates required

1. Update `app_assets.py` to render:
- `admin/page.html`
- `billing/pricing.html`
- `billing/checkout.html`
- `dashboard/page.html`
- `marketing/landing.html`

2. Update canonical templates to load canonical bundle URLs directly:
- `templates/admin/page.html` -> `/static/js/admin/panel.js`
- `templates/dashboard/page.html` -> `/static/js/dashboard/app.js`
- `templates/marketing/landing.html` -> `/static/js/marketing/landing.js`

3. Update wrapper-oriented tests to inspect canonical paths instead of deleted wrapper files.

### Files to archive and delete in Batch 1

- `templates/admin.html`
- `templates/pricing.html`
- `templates/checkout.html`
- `templates/dashboard.html`
- `templates/landing.html`
- `templates/partials/_navbar.html`
- `templates/partials/_search_panel.html`
- `templates/partials/_results_panel.html`
- `templates/partials/_watchlist_panel.html`
- `templates/partials/_leads_panel.html`
- `templates/partials/_ai_studio_panel.html`
- `templates/partials/_reports_panel.html`
- `templates/partials/_applications_panel.html`
- `templates/partials/_modals.html`
- `static/js/admin.js`
- `static/js/app.js`
- `static/js/landing.js`

### Required validation for Batch 1

- `python -m py_compile tests/test_page_smoke.py tests/test_dashboard_layout.py app_assets.py`
- `python -m pytest tests/test_page_smoke.py -q`
- `python -m pytest tests/test_dashboard_layout.py -q`
- Gate A
- Gate C

Gate D is not required unless the batch ends up changing upload or watchlist UI flow.

### Batch 1 execution status

- Status: completed
- Archive: `phase12_backups/20260413T191509_batch01_frontend_wrappers`
- Validation result:
  - `python -m py_compile tests/test_page_smoke.py tests/test_dashboard_layout.py app_assets.py`
  - `python -m pytest tests/test_page_smoke.py -q` -> `9 passed`
  - `python -m pytest tests/test_dashboard_layout.py -q` -> `49 passed`
  - Gate A -> `410 passed`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 2 Definition

### Batch name

`batch02_pipeline_parallel_wrapper`

### Goal

Remove `pipeline_parallel.py` after retargeting the remaining in-repo tests to the canonical packaged module.

### Pre-delete updates required

1. Update `tests/test_ingest.py` to import folder-sorting helpers from `pipeline.parallel`.
2. Replace the `pipeline_parallel` wrapper smoke in `tests/test_phase0_smoke.py` with a canonical `pipeline.parallel` surface check.

### Files to archive and delete in Batch 2

- `pipeline_parallel.py`

### Required validation for Batch 2

- `python -m py_compile pipeline/parallel.py tests/test_ingest.py tests/test_phase0_smoke.py`
- `python -m pytest tests/test_ingest.py -q`
- `python -m pytest tests/test_phase0_smoke.py -q`
- Gate C

### Batch 2 execution status

- Status: completed
- Archive: `phase12_backups/20260413T192340_batch02_pipeline_parallel_wrapper`
- Validation result:
  - `python -m py_compile pipeline/parallel.py tests/test_ingest.py tests/test_phase0_smoke.py`
  - `python -m pytest tests/test_ingest.py -q` -> `135 passed`
  - `python -m pytest tests/test_phase0_smoke.py -q` -> `67 passed`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 3 Definition

### Batch name

`batch03_ingest_wrapper`

### Goal

Remove `ingest.py` after retargeting the runtime caller, ingest-focused tests, and the ingest security-audit surface to `pipeline.ingest`.

### Pre-delete updates required

1. Update `agentic_search.py` to import `process_file_batch` from `pipeline.ingest`.
2. Update `tests/test_ingest.py` to import the canonical packaged ingest module and helpers directly.
3. Replace the `ingest` wrapper smoke in `tests/test_phase0_smoke.py` with a canonical `pipeline.ingest` surface check.
4. Update `tests/test_security_audit.py` to inspect only `pipeline/ingest.py`.

### Files to archive and delete in Batch 3

- `ingest.py`

### Required validation for Batch 3

- `python -m py_compile pipeline/ingest.py agentic_search.py tests/test_ingest.py tests/test_phase0_smoke.py tests/test_security_audit.py`
- `python -m pytest tests/test_ingest.py -q`
- `python -m pytest tests/test_phase0_smoke.py -q`
- `python -m pytest tests/test_security_audit.py -q -k ingest`
- Gate C

### Batch 3 execution status

- Status: completed
- Archive: `phase12_backups/20260413T193514_batch03_ingest_wrapper`
- Validation result:
  - `python -m py_compile pipeline/ingest.py agentic_search.py tests/test_ingest.py tests/test_phase0_smoke.py tests/test_security_audit.py`
  - `python -m pytest tests/test_ingest.py -q` -> `135 passed`
  - `python -m pytest tests/test_phase0_smoke.py -q` -> `67 passed`
  - `python -m pytest tests/test_security_audit.py -q -k ingest` -> `2 passed`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 4 Definition

### Batch name

`batch04_ai_wrapper`

### Goal

Remove `ai.py` after retargeting the remaining runtime callers, supporting scripts, docs, and the local dev compose mount to `pipeline.ai`.

### Pre-delete updates required

1. Update `risk_engine.py`, `watchlist/scanner.py`, `agentic_search.py`, and `app_enhanced_search_routes.py` to import from `pipeline.ai`.
2. Update AI-facing scripts and sample runners to resolve `pipeline/ai.py` directly.
3. Replace the `ai` wrapper smoke and import stubs in `tests/test_phase0_smoke.py` and `tests/conftest.py` with `pipeline.ai`.
4. Update plain-text docs and the compose bind mount so they no longer assume a root `ai.py` file.

### Files to archive and delete in Batch 4

- `ai.py`

### Required validation for Batch 4

- `python -m py_compile pipeline/ai.py app_enhanced_search_routes.py risk_engine.py watchlist/scanner.py agentic_search.py scripts/fix_class_embeddings.py scripts/test_ai_pipeline.py scripts/test_ai_pipeline_v2.py scripts/run_embeddings.py scripts/run_sample_test.py tests/conftest.py tests/test_phase0_smoke.py`
- `python -m pytest tests/test_phase0_smoke.py -q`
- `python -m pytest tests/test_api_endpoints.py -s -k "search or portfolio or admin_test_scoring"`
- Gate C

Note: the equivalent `-q` API-subset invocation currently trips a pytest capture tempfile-close bug on this Python 3.13 runner, so the non-capturing `-s` variant is the reliable functional check for this batch.

### Batch 4 execution status

- Status: completed
- Archive: `phase12_backups/20260413T220948_batch04_ai_wrapper`
- Validation result:
  - `python -m py_compile pipeline/ai.py app_enhanced_search_routes.py risk_engine.py watchlist/scanner.py agentic_search.py scripts/fix_class_embeddings.py scripts/test_ai_pipeline.py scripts/test_ai_pipeline_v2.py scripts/run_embeddings.py scripts/run_sample_test.py tests/conftest.py tests/test_phase0_smoke.py`
  - `python -m pytest tests/test_phase0_smoke.py -q` -> `67 passed`
  - `python -m pytest tests/test_api_endpoints.py -s -k "search or portfolio or admin_test_scoring"` -> `48 selected passed`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 5 Definition

### Batch name

`batch05_idf_scoring_wrapper`

### Goal

Remove `idf_scoring.py` after retargeting the remaining test callers, helper scripts, docs, and the local dev compose mount to `services.scoring_service`.

### Pre-delete updates required

1. Update scoring-focused tests and helper scripts to import from `services.scoring_service`.
2. Replace the `idf_scoring` wrapper smoke in `tests/test_phase0_smoke.py` with canonical `services.scoring_service` surface checks.
3. Update plain-text docs and the compose bind mount so they no longer assume a root `idf_scoring.py` file.

### Files to archive and delete in Batch 5

- `idf_scoring.py`

### Required validation for Batch 5

- `python -m py_compile services/scoring_service.py tests/test_scoring_engine.py tests/test_edge_cases.py tests/test_phase0_smoke.py scripts/test_scoring_live.py scripts/test_jewelry_fix.py`
- `python -m pytest tests/test_scoring_engine.py -q`
- `python -m pytest tests/test_edge_cases.py -q`
- `python -m pytest tests/test_phase0_smoke.py -q`
- `python -m pytest tests/test_api_endpoints.py -s -k "search or portfolio or admin_test_scoring"`
- Gate C

### Batch 5 execution status

- Status: completed
- Archive: `phase12_backups/20260413T233200_batch05_idf_scoring_wrapper`
- Validation result:
  - `python -m py_compile services/scoring_service.py tests/test_scoring_engine.py tests/test_edge_cases.py tests/test_phase0_smoke.py scripts/test_scoring_live.py scripts/test_jewelry_fix.py`
  - `python -m pytest tests/test_scoring_engine.py -q` -> `105 passed`
  - `python -m pytest tests/test_edge_cases.py -q` -> `42 passed`
  - `python -m pytest tests/test_phase0_smoke.py -q` -> `67 passed`
  - `python -m pytest tests/test_api_endpoints.py -s -k "search or portfolio or admin_test_scoring"` -> `48 selected passed`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 6 Definition

### Batch name

`batch06_root_devtools_move`

### Goal

Move low-risk one-off root utilities into `scripts/devtools/` so the repo root gets cleaner without touching runtime entrypoints or compatibility contracts.

### Pre-move review required

1. Confirm the candidate scripts have no in-repo callers or documented runtime contracts.
2. Keep stable runtime contracts such as `main.py` and `database/crud.py` in place.

### Files moved in Batch 6

- `check_flag.py` -> `scripts/devtools/check_flag.py`
- `check_gazette.py` -> `scripts/devtools/check_gazette.py`
- `check_status.py` -> `scripts/devtools/check_status.py`
- `debug_score.py` -> `scripts/devtools/debug_score.py`
- `debug_score2.py` -> `scripts/devtools/debug_score2.py`
- `fix_filters.py` -> `scripts/devtools/fix_filters.py`
- `migrate_enum_cleanup.py` -> `scripts/devtools/migrate_enum_cleanup.py`
- `update_status.py` -> `scripts/devtools/update_status.py`

### Required validation for Batch 6

- `python -m py_compile scripts/devtools/check_flag.py scripts/devtools/check_gazette.py scripts/devtools/check_status.py scripts/devtools/debug_score.py scripts/devtools/debug_score2.py scripts/devtools/fix_filters.py scripts/devtools/migrate_enum_cleanup.py scripts/devtools/update_status.py`
- Gate C

### Batch 6 execution status

- Status: completed
- Archive: none (move-only batch)
- Validation result:
  - `python -m py_compile scripts/devtools/check_flag.py scripts/devtools/check_gazette.py scripts/devtools/check_status.py scripts/devtools/debug_score.py scripts/devtools/debug_score2.py scripts/devtools/fix_filters.py scripts/devtools/migrate_enum_cleanup.py scripts/devtools/update_status.py`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 7 Definition

### Batch name

`batch07_remaining_root_cleanup`

### Goal

Move the remaining low-risk root audit, check, and scratch scripts into `scripts/devtools/` so the repository root keeps shrinking without touching runtime entrypoints.

### Pre-move review required

1. Confirm each candidate has no tracked in-repo callers or deployment/runtime contract.
2. Keep active runtime contracts such as `main.py` and `database/crud.py` in place.

### Files moved in Batch 7

- `check_missing.py` -> `scripts/devtools/check_missing.py`
- `check_missing_container.py` -> `scripts/devtools/check_missing_container.py`
- `check_sim.py` -> `scripts/devtools/check_sim.py`
- `audit_data_quality.py` -> `scripts/devtools/audit_data_quality.py`
- `audit_pdf_formats.py` -> `scripts/devtools/audit_pdf_formats.py`
- `test_api.py` -> `scripts/devtools/test_api.py`
- `test_db_setting.py` -> `scripts/devtools/test_db_setting.py`
- `test_db_setting2.py` -> `scripts/devtools/test_db_setting2.py`
- `test_db_status.py` -> `scripts/devtools/test_db_status.py`
- `test_idf.py` -> `scripts/devtools/test_idf.py`
- `test_isolated.py` -> `scripts/devtools/test_isolated.py`
- `test_isolated2.py` -> `scripts/devtools/test_isolated2.py`
- `test_isolated3.py` -> `scripts/devtools/test_isolated3.py`
- `test_isolated4.py` -> `scripts/devtools/test_isolated4.py`
- `test_legacy.py` -> `scripts/devtools/test_legacy.py`
- `test_legacy2.py` -> `scripts/devtools/test_legacy2.py`
- `test_query.py` -> `scripts/devtools/test_query.py`
- `test_query2.py` -> `scripts/devtools/test_query2.py`
- `test_search_error.py` -> `scripts/devtools/test_search_error.py`
- `test_sim.py` -> `scripts/devtools/test_sim.py`
- `test_sql.py` -> `scripts/devtools/test_sql.py`
- `test_yenilendi.py` -> `scripts/devtools/test_yenilendi.py`

### Required validation for Batch 7

- `python -m py_compile scripts/devtools/check_missing.py scripts/devtools/check_missing_container.py scripts/devtools/check_sim.py scripts/devtools/audit_data_quality.py scripts/devtools/audit_pdf_formats.py scripts/devtools/test_api.py scripts/devtools/test_db_setting.py scripts/devtools/test_db_setting2.py scripts/devtools/test_db_status.py scripts/devtools/test_idf.py scripts/devtools/test_isolated.py scripts/devtools/test_isolated2.py scripts/devtools/test_isolated3.py scripts/devtools/test_isolated4.py scripts/devtools/test_legacy.py scripts/devtools/test_legacy2.py scripts/devtools/test_query.py scripts/devtools/test_query2.py scripts/devtools/test_search_error.py scripts/devtools/test_sim.py scripts/devtools/test_sql.py scripts/devtools/test_yenilendi.py`
- Gate C

### Batch 7 execution status

- Status: completed
- Archive: none (move-only batch)
- Validation result:
  - `python -m py_compile scripts/devtools/check_missing.py scripts/devtools/check_missing_container.py scripts/devtools/check_sim.py scripts/devtools/audit_data_quality.py scripts/devtools/audit_pdf_formats.py scripts/devtools/test_api.py scripts/devtools/test_db_setting.py scripts/devtools/test_db_setting2.py scripts/devtools/test_db_status.py scripts/devtools/test_idf.py scripts/devtools/test_isolated.py scripts/devtools/test_isolated2.py scripts/devtools/test_isolated3.py scripts/devtools/test_isolated4.py scripts/devtools/test_legacy.py scripts/devtools/test_legacy2.py scripts/devtools/test_query.py scripts/devtools/test_query2.py scripts/devtools/test_search_error.py scripts/devtools/test_sim.py scripts/devtools/test_sql.py scripts/devtools/test_yenilendi.py`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 8 Definition

### Batch name

`batch08_remaining_root_review`

### Goal

Move the remaining isolated root browser-helper script into `scripts/devtools/` after confirming it has no tracked callers or runtime contract.

### Pre-move review required

1. Confirm `take_screenshot.py` has no tracked in-repo callers or documented runtime contract.
2. Keep the remaining root scripts with live runtime or compose contracts in place.

### Files moved in Batch 8

- `take_screenshot.py` -> `scripts/devtools/take_screenshot.py`

### Required validation for Batch 8

- `python -m py_compile scripts/devtools/take_screenshot.py`
- Gate C

### Batch 8 execution status

- Status: completed
- Archive: none (move-only batch)
- Validation result:
  - `python -m py_compile scripts/devtools/take_screenshot.py`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Batch 9 Definition

### Batch name

`batch09_remaining_root_review`

### Goal

Move the last remaining low-risk standalone root helpers into `scripts/devtools/` and close the remaining root-review gap before Phase 12 close-out.

### Pre-move review required

1. Confirm `search_metadata.py` and `start-docker.ps1` have no tracked in-repo callers or deployment/runtime contract.
2. Reclassify the remaining live root code surfaces as `keep` if they still have active runtime, compose, or documented contracts.

### Files moved in Batch 9

- `search_metadata.py` -> `scripts/devtools/search_metadata.py`
- `start-docker.ps1` -> `scripts/devtools/start-docker.ps1`

### Required validation for Batch 9

- `python -m py_compile scripts/devtools/search_metadata.py`
- PowerShell parse of `scripts/devtools/start-docker.ps1`
- Gate C

### Batch 9 execution status

- Status: completed
- Archive: none (move-only batch)
- Validation result:
  - `python -m py_compile scripts/devtools/search_metadata.py`
  - PowerShell parse of `scripts/devtools/start-docker.ps1`
  - Gate C -> `1358 collected`, `1325 passed`, `33 skipped`

## Planned Batch Order

1. `batch01_frontend_wrappers` - completed
2. `batch02_pipeline_parallel_wrapper` - completed
3. `batch03_ingest_wrapper` - completed
4. `batch04_ai_wrapper` - completed
5. `batch05_scoring_wrappers` - completed
6. `batch06_root_devtools_move` - completed
7. `batch07_remaining_root_cleanup` - completed
8. `batch08_remaining_root_review` - completed
9. `batch09_remaining_root_review` - completed
