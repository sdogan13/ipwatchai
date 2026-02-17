# E2E Test Results — Trademark Risk Assessment System

**Date:** 2026-02-10
**Environment:** Docker (ipwatch_backend:8000, ipwatch_nginx:8080, ipwatch_postgres:5433, ipwatch_redis:6379)
**Auth User:** pro@test.com (Pro plan, owner role)
**Test Type:** Read-only, non-destructive

---

## Summary Table

| # | Category | Tests | Pass | Fail | Skip/Warn | Pass Rate |
|---|----------|-------|------|------|-----------|-----------|
| 1 | Infrastructure Health | 4 | 4 | 0 | 0 | **100%** |
| 2 | Text Search (`/api/search`) | 7 | 6 | 1 | 0 | **86%** |
| 3 | Image Search (POST `/api/v1/search/intelligent`) | 3 | 0 | 3 | 0 | **0%** |
| 4 | Agentic Search (`/api/v1/search/*`) | 6 | 6 | 0 | 0 | **100%** |
| 5 | Holder Portfolio | 4 | 3 | 1 | 0 | **75%** |
| 6 | Attorney Portfolio | 2 | 2 | 0 | 0 | **100%** |
| 7 | Leads / Opposition Radar | 5 | 0 | 4 | 1 | **0%** |
| 8 | Watchlist / Alerts | 5 | 5 | 0 | 0 | **100%** |
| 9 | Deprecated / Legacy Endpoints | 4 | 3 | 0 | 1 | **75%** |
| 10 | Feature Flags / Config | 4 | 0 | 4 | 0 | **0%** |
| 11 | Frontend Smoke Tests | 13 | 11 | 1 | 1 | **85%** |
| 12 | Database Sanity | 8 | 5 | 3 | 0 | **63%** |
| | **TOTAL** | **65** | **45** | **17** | **3** | **69%** |

---

## Category 1 — Infrastructure Health

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T1.1 | `GET /health` | **PASS** | `{"status":"healthy","checks":{"database":"ok","redis":"ok","gpu":"ok (NVIDIA GeForce RTX 4070 Ti SUPER)"}}` |
| T1.2 | Database connectivity | **PASS** | PostgreSQL 16 responding on port 5433 |
| T1.3 | Redis connectivity | **PASS** | Redis responding on port 6379 |
| T1.4 | GPU availability | **PASS** | RTX 4070 Ti Super — 2803 MiB used, 13261 MiB free |

---

## Category 2 — Text Search (`POST /api/search`)

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T2.1 | Basic search "NIKE" | **PASS** | 10 results, top similarity 97.9%, 3.6s |
| T2.2 | "APPLE" + class filter [9,42] | **PASS** | 10 results, all with class_overlap_count >= 1, 1.0s |
| T2.3 | "SAMSUNG" + class [9] | **PASS** | 10 results, all contain class 9, 0.7s |
| T2.4 | Turkish chars "TÜRK" | **PASS*** | Works with Unicode escapes; raw UTF-8 body parsing fails |
| T2.5 | Status filter "Tescil" | **FAIL** | Status parameter silently ignored — results unfiltered |
| T2.6 | Empty query | **PASS** | Proper 422 validation error returned |
| T2.7 | 100-char query | **PASS** | Graceful handling, low-similarity results, 5.2s |

**Response fields:** `application_date, application_no, attorney, bulletin_no, class_overlap_count, classes, id, image_url, name, name_similarity, nice_classes, owner, registration_date, similarity, status, status_code`

**Key findings:**
- The `status` filter on `/api/search` is **not working** — parameter accepted but ignored
- Field names differ from agentic search: `attorney` (not `attorney_name`), `owner` (not `holder_name`)
- Missing from response: `attorney_no`, `holder_tpe_client_id`, `registration_no`
- UTF-8 Turkish characters require Unicode escapes in JSON body

---

## Category 3 — Image Search (POST with file upload)

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T3.1 | Real JPEG upload | **FAIL** | HTTP 500 — `cursor_factory` TypeError in `subscription.py:131` |
| T3.2 | Text-only POST (no image) | **FAIL** | HTTP 500 — same root cause |
| T3.3 | Invalid file upload | **FAIL** | HTTP 500 — crashes before image validation |

**Root cause:** `utils/subscription.py` line 131 calls `db.cursor(cursor_factory=RealDictCursor)` but the Docker container's `Database.cursor()` method doesn't accept keyword arguments. The subscription/eligibility check runs before any search logic, blocking all POST requests to `/api/v1/search/intelligent`.

---

## Category 4 — Agentic Search (`/api/v1/search/*`)

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T4.1 | Quick search "NIKE" | **PASS** | Results with full risk scoring |
| T4.2 | Intelligent search "APPLE" classes=[9,42] | **PASS** | Results with credit tracking |
| T4.3 | Quick search + status filter | **PASS** | Status filter works (uses English enum: `Registered`, `Published`) |
| T4.4 | Quick search + attorney_no filter | **PASS** | Returns 0 results for non-existent attorney (correct) |
| T4.5 | Search without auth | **PASS** | Returns 401 Unauthorized |
| T4.6 | Empty query | **PASS** | Graceful handling |

**Response fields per result:** `application_no, attorney_name, attorney_no, bulletin_no, classes, exact_match, has_extracted_goods, holder_name, holder_tpe_client_id, image_path, name, registration_no, scores, status`

**Score object fields:** `containment, distinctive_match, distinctive_weight_matched, dynamic_weights, exact_match, generic_match, generic_weight_matched, matched_words, phonetic_similarity, scoring_path, semantic_similarity, semi_generic_match, semi_generic_weight_matched, text_idf_score, text_similarity, token_overlap, total, translation_similarity, visual_similarity, weighted_overlap`

**Key finding:** Status filter on agentic search uses **English** enum values (`Registered`, `Published`, `Applied`, etc.), NOT Turkish (`Tescil`). Using Turkish values causes HTTP 500 with `invalid input value for enum tm_status`.

---

## Category 5 — Holder Portfolio

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T5.1 | Search holders "SAMSUNG" | **PASS** | 10 results, top: SAMSUNG ELECTRONICS CO., LTD. (70 trademarks) |
| T5.2-prep | `/api/search` to find holder | **FAIL** | HTTP 500 — `ai` module attribute error |
| T5.2 | Holder trademarks by tpe_client_id | **PASS** | 70 trademarks, paginated (20/page) |
| T5.3 | Holder search "SAMSUNG ELECTRONICS" | **PASS** | Multi-word search works |

**Working endpoints:**
- `GET /api/v1/holders/search?query=<name>&per_page=N`
- `GET /api/v1/holders/{tpe_client_id}/trademarks?per_page=N`

---

## Category 6 — Attorney Portfolio

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T6.1 | Attorney search "patent" | **PASS** | 5 results, top: YUSUF ERSOY DESTEK PATENT A.S. (33,883 trademarks) |
| T6.2 | Attorney portfolio (attorney_no=595) | **PASS** | 43,167 trademarks, paginated |

**Working endpoints:**
- `GET /api/v1/attorneys/search?query=<name>&limit=N`
- `GET /api/v1/attorneys/{attorney_no}/trademarks?per_page=N`

---

## Category 7 — Leads / Opposition Radar

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T7.1 | List leads | **FAIL** | HTTP 500 — `cursor_factory` TypeError |
| T7.2 | Leads urgency filter | **FAIL** | HTTP 500 — same root cause |
| T7.3 | Leads status filter | **FAIL** | HTTP 500 — same root cause |
| T7.4 | Lead detail by ID | **SKIP** | T7.1 failed, no lead IDs available |
| T7.5 | Lead stats/summary | **FAIL** | HTTP 500 — same root cause |

**Root cause:** Same `cursor_factory` bug as Category 3. The `_require_lead_access()` → `get_user_plan()` chain in `utils/subscription.py:131` crashes before any leads logic runs.

**Additional note:** The `universal_conflicts` table has 0 rows, so even after fixing the bug, the leads feed would return empty results until conflict detection is run.

---

## Category 8 — Watchlist / Alerts

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T8.1 | List watchlist | **PASS** | 200 OK, 0 items (empty, expected) |
| T8.2 | List alerts | **PASS** | 200 OK, 0 items (empty, expected) |
| T8.3 | Watchlist without auth | **PASS** | 401 Unauthorized (correct) |
| T8.4 | Alerts summary | **PASS** | `{by_status, by_severity, total_new}` |
| T8.5 | Watchlist scan-status | **PASS** | `{auto_scan_enabled, schedule, next_scan_at}` |

---

## Category 9 — Deprecated / Legacy Endpoints

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T9.1a | `GET /api/trademark/search?q=NIKE` | **PASS** | 404 — old endpoint correctly removed |
| T9.1b | `GET /trademark/search?q=NIKE` | **PASS** | 404 — old endpoint correctly removed |
| T9.2a | `GET /docs` (Swagger UI) | **WARN** | 404 — docs intentionally disabled |
| T9.2b | `GET /openapi.json` | **PASS** | 200 — OpenAPI schema accessible |

---

## Category 10 — Feature Flags / Config

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T10.1 | `GET /api/v1/auth/me` | **FAIL** | HTTP 500 — server error on user profile |
| T10.2a | `GET /api/v1/subscription` | **FAIL** | 404 — not found |
| T10.2b | `GET /api/v1/auth/subscription` | **FAIL** | 404 — not found |
| T10.2c | `GET /api/v1/plans` | **FAIL** | 404 — not found |

**Note:** Plan info is partially available nested inside `GET /api/v1/dashboard/stats` under `plan_usage`, but no dedicated subscription management endpoints exist.

---

## Category 11 — Frontend Smoke Tests

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T11.1a | `GET /` | **PASS** | Returns API status JSON |
| T11.1b | `GET /dashboard` | **PASS** | Dashboard HTML loads (97KB) |
| T11.2a | `static/js/app.js` | **PASS** | 200 |
| T11.2b | `static/js/api.js` | **PASS** | 200 |
| T11.2c | `static/js/components/score-badge.js` | **PASS** | 200 |
| T11.2d | `static/js/components/opposition-timeline.js` | **FAIL** | 404 — file not in Docker image |
| T11.2e | `static/js/components/result-card.js` | **PASS** | 200 |
| T11.2f | `static/js/components/lead-card.js` | **PASS** | 200 |
| T11.2g | `static/js/components/studio-card.js` | **PASS** | 200 |
| T11.3a | `static/locales/en.json` | **PASS** | 200 |
| T11.3b | `static/locales/tr.json` | **PASS** | 200 |
| T11.3c | `static/locales/ar.json` | **PASS** | 200 |
| T11.4 | Dashboard HTML has key script tags | **WARN** | 6/7 found, `opposition-timeline.js` missing |

**Note:** `opposition-timeline.js` was created on the host but the Docker image was not rebuilt, so it's not available in the container.

---

## Category 12 — Database Sanity

| Test | Description | Result | Details |
|------|-------------|--------|---------|
| T12.1a | `GET /api/v1/stats` | **FAIL** | 404 |
| T12.1b | `GET /api/stats` | **FAIL** | 404 |
| T12.1c | `GET /api/v1/dashboard/stats` | **PASS** | Returns watchlist/plan usage stats |
| T12.2a | `GET /api/v1/pipeline/status` | **PASS** | `is_running: false`, next: 2026-02-16 |
| T12.2b | `GET /api/pipeline/status` | **FAIL** | 404 |
| T12.3 | DB trademark counts (direct query) | **PASS** | See table below |
| T12.4 | Source distribution | **PASS** | BLT: 1,847,530 / APP: 1,767 |
| T12.5 | Status distribution | **PASS** | Published: 1,681,128 / Registered: 165,865 |

### Database Population Stats (T12.3)

| Metric | Count | Coverage |
|--------|-------|----------|
| **Total trademarks** | 1,841,102 | — |
| With `bulletin_date` | 1,450,127 | 78.8% |
| With `appeal_deadline` | 1,450,127 | 78.8% |
| With `image_embedding` | 1,283,120 | 69.7% |
| With `text_embedding` | 1,823,473 | 99.0% |
| With `name_tr` (translation) | 1,782,646 | 96.8% |

### Status Distribution (T12.5)

| Status | Count |
|--------|-------|
| Published | 1,681,128 |
| Registered | 165,865 |
| Withdrawn | 1,631 |
| Applied | 646 |
| Refused | 27 |

### Source Distribution (T12.4)

| Source | Count |
|--------|-------|
| BLT | 1,847,530 |
| APP | 1,767 |

---

## Critical Failures

### 1. `cursor_factory` TypeError (Blocks Categories 3, 7, 10)

**File:** `utils/subscription.py:131`
**Error:** `TypeError: Database.cursor() got an unexpected keyword argument 'cursor_factory'`
**Impact:** All endpoints that call `get_user_plan()`, `check_live_search_eligibility()`, or `_require_lead_access()` return HTTP 500.
**Affected endpoints:** POST `/api/v1/search/intelligent`, all `/api/v1/leads/*`, `GET /api/v1/auth/me`
**Fix:** Update `database/crud.py` `cursor()` method to accept `**kwargs`, then rebuild Docker image.

### 2. Status Filter Not Working on Enhanced Search (Category 2)

**Endpoint:** `POST /api/search`
**Issue:** The `status` parameter is accepted but silently ignored — results are not filtered.
**Note:** Status filter DOES work on agentic search endpoints (`/api/v1/search/quick`, `/api/v1/search/intelligent`) but requires English enum values (`Registered`, `Published`), not Turkish (`Tescil`).

### 3. Docker Image Stale — Missing New Files

**Files missing from container:**
- `static/js/components/opposition-timeline.js` (new component)
- `api/attorneys.py` (new router — causes import crash if `main.py` references it)
- Updated `database/crud.py` (has the `cursor_factory` fix on host)

**Fix:** `docker-compose build backend && docker-compose up -d backend`

### 4. `universal_conflicts` Table Empty

The leads/opposition radar system has 0 rows in its data table. Even after fixing the `cursor_factory` bug, the leads feed will return empty results until the conflict detection pipeline is run.

---

## Missing Features

| Feature | Status | Notes |
|---------|--------|-------|
| Subscription management endpoints | **NOT IMPLEMENTED** | No `/api/v1/subscription` or `/api/v1/plans` |
| User profile endpoint | **BROKEN** | `/api/v1/auth/me` returns 500 |
| Swagger UI docs | **DISABLED** | `/docs` returns 404 (intentional) |
| Dedicated stats endpoint | **NOT IMPLEMENTED** | Stats only via `/api/v1/dashboard/stats` |
| Enhanced search status filter | **NOT WORKING** | Parameter accepted but ignored |
| Opposition timeline component | **NOT DEPLOYED** | File exists on host but not in Docker image |

---

## Data Observations

1. **Bulletin/deadline data is well-populated:** 78.8% of trademarks have both `bulletin_date` and `appeal_deadline` (always co-present, never mismatched)
2. **Text embeddings near-complete:** 99.0% coverage (1,823,473 / 1,841,102)
3. **Image embeddings at 69.7%:** 1,283,120 trademarks have image embeddings — the gap is expected for text-only marks
4. **Translation coverage at 96.8%:** 1,782,646 trademarks have `name_tr` translations
5. **No GZ_ source records:** Only BLT (1,847,530) and APP (1,767) sources present. GZ records were likely merged into BLT during ingestion via source priority system
6. **Status distribution heavily skewed:** 91.3% are "Published", only 9.0% "Registered"

---

## Ingestion Status

| Pipeline | Status |
|----------|--------|
| Running | No |
| Next scheduled | 2026-02-16 |
| Total records | 1,841,102 |
| Sources ingested | BLT (356 bulletins), APP (1 folder) |
| Universal conflicts | 0 rows (not yet computed) |

---

## Recommendations

1. **Rebuild Docker image** — Many host-side fixes and new files are not in the container
2. **Fix `subscription.py` cursor** — Single-line fix unblocks 3 entire categories
3. **Run conflict detection** — Populate `universal_conflicts` to enable leads/opposition radar
4. **Fix enhanced search status filter** — Either wire it to the SQL query or remove the parameter
5. **Standardize status filter values** — Decide between Turkish (`Tescil`) and English (`Registered`) enum values; document which is expected
6. **Add `/api/v1/auth/me` fix** — User profile endpoint is important for frontend auth flow
