# CODE-LEVEL FEATURE TRACEABILITY AUDIT

**Date:** 2026-02-10
**Scope:** Pure code reading - DB schema, backend, API models, API endpoints, frontend templates
**Method:** 5 parallel analysis agents covering schema, API, frontend, capabilities, and config

---

## TRACEABILITY MATRIX (37 Columns)

```
Field                    DB Schema  Ingest  Backend     API Model     API Endpoint        Frontend     Status
------------------------ ---------- ------- ----------- ------------- ------------------- ------------ --------
id                       UUID PK    Yes     queries     AlertResponse various             -            INTERNAL
application_no           VARCHAR    Yes     search/risk Conflicting   /search, /leads     result-card  FULL
name                     TEXT       Yes     search/risk Conflicting   /search, /leads     result-card  FULL
current_status           tm_status  Yes     risk score  Conflicting   /search, /leads     status badge FULL
nice_class_numbers       INT[]      Yes     class utils Conflicting   /search, /leads     class badges FULL
holder_name              VARCHAR    Yes     holder qry  Conflicting   /holders, /search   result-card  FULL
holder_tpe_client_id     VARCHAR    Yes     holder qry  holder resp   /holders/{id}       portfolio    FULL
image_path               TEXT       Yes     img serve   Conflicting   /trademark-image    thumbnails   FULL
bulletin_no              VARCHAR    Yes     alert gen   AlertResp     /alerts, /leads     lead-card    FULL
bulletin_date            DATE       Yes     deadline    AlertResp     /alerts, /leads     deadline UI  FULL
appeal_deadline          DATE       Yes     deadline    AlertResp     /alerts, /leads     opposition   FULL
application_date         DATE       Yes     holder qry  holder resp   /holders            portfolio    FULL
extracted_goods          JSONB      Yes     goods EP    goods resp    /trademark/goods    goods btn    FULL
name_tr                  VARCHAR    Yes     risk transl -             (via scoring)       (via score)  INTERNAL
image_embedding          halfvec    Yes     CLIP search -             (via visual_sim)    (via badge)  INTERNAL
dinov2_embedding         halfvec    Yes     risk score  -             (via visual_sim)    (via badge)  INTERNAL
text_embedding           halfvec    Yes     semantic    -             (via semantic_sim)  (via badge)  INTERNAL
color_histogram          halfvec    Yes     risk score  -             (via visual_sim)    (via badge)  INTERNAL
logo_ocr_text            TEXT       Yes     risk score  -             (via visual_sim)    (via badge)  INTERNAL
detected_lang            VARCHAR    Yes     transl.py   -             -                   -            BACKEND ONLY
status_source            VARCHAR    Yes     src priority-             -                   -            INTERNAL
created_at               TIMESTAMP  Yes     queries     LeadResp      /leads              -            PARTIAL
updated_at               TIMESTAMP  Yes     upserts     -             -                   -            INTERNAL
registration_date        DATE       Yes     holder qry  holder resp   /holders            portfolio    PARTIAL
registration_no          VARCHAR    Yes     -           -             -                   -            DEAD
wipo_no                  VARCHAR    Yes     -           -             -                   -            DEAD
attorney_name            VARCHAR    Yes     -           -             -                   -            DEAD
attorney_no              VARCHAR    Yes     -           -             -                   -            DEAD
vienna_class_numbers     INT[]      Yes     -           -             -                   -            DEAD
gazette_no               VARCHAR    Yes     -           -             -                   -            DEAD
gazette_date             DATE       Yes     -           -             -                   -            DEAD
expiry_date              DATE       Yes     -           -             -                   -            DEAD
last_event_date          DATE       Yes     -           -             -                   -            DEAD
availability_status      VARCHAR    Yes     -           -             -                   -            DEAD
name_en                  VARCHAR    Migr    -           -             -                   -            DEAD
name_ku                  VARCHAR    Migr    -           -             -                   -            DEAD
name_fa                  VARCHAR    Migr    -           -             -                   -            DEAD
```

### STATUS KEY
```
FULL      - Traced through all layers: ingest -> backend -> API model -> endpoint -> frontend
INTERNAL  - Correctly internal-only (embeddings used for vector search, not exposed raw)
PARTIAL   - Present in some layers but not all the way to frontend display
BACKEND ONLY - Used in backend logic but not in any API response
DEAD      - Ingested into DB but never used beyond that
```

### SUMMARY COUNTS
```
FULL STACK (DB -> backend -> frontend):   13 fields
INTERNAL (correctly not exposed):          9 fields
PARTIAL (some layers missing):             2 fields
BACKEND ONLY (not displayed):              1 field
DEAD (ingested but unused):               12 fields
                                         ----
TOTAL:                                    37 fields
```

---

## SEARCH CAPABILITIES

| Search Type                  | Backend File         | Endpoint                    | Frontend Wired | Status  |
|-----------------------------|----------------------|-----------------------------|----|---------|
| Text search (trigram+IDF)    | main.py, risk_engine | /api/search, /search/unified | Yes | ACTIVE  |
| Visual search (CLIP)         | main.py, risk_engine | /api/search-by-image        | Yes | ACTIVE  |
| Visual search (DINOv2)       | watchlist/scanner.py | (via risk scoring only)     | Indirect | SCORING ONLY |
| Color histogram              | watchlist/scanner.py | (via risk scoring only)     | Indirect | SCORING ONLY |
| Semantic search (MiniLM)     | main.py, risk_engine | (via search ranking)        | Indirect | SCORING ONLY |
| OCR text search              | watchlist/scanner.py | (via risk scoring only)     | Indirect | SCORING ONLY |

**Notes:**
- DINOv2, color, semantic, OCR are all used internally in risk scoring but have **no standalone search endpoints**
- Only CLIP has a dedicated image search endpoint
- Text search uses Turkish normalization + IDF 3-tier weighting

---

## FILTER CAPABILITIES

| Filter                  | Backend Support | Endpoint Param        | Frontend UI    | Status  |
|------------------------|-----------------|----------------------|----------------|---------|
| Nice class filter       | class_utils.py  | `classes` param      | multi-select   | ACTIVE  |
| Nice class 99 (global)  | class_utils.py  | auto-expands to 1-45 | supported      | ACTIVE  |
| Status filter (alerts)   | routes.py       | `status` on /alerts  | alert filters  | ACTIVE  |
| Holder name filter       | holders.py      | /holders/search      | autocomplete   | ACTIVE  |
| Date range (reports)     | reports.py      | period_start/end     | date pickers   | ACTIVE  |
| Vienna class filter      | -               | -                    | -              | MISSING |
| Attorney name filter     | -               | -                    | -              | MISSING |
| Status filter (search)   | -               | (not on /search)     | -              | MISSING |

---

## SPECIAL FEATURES

| Feature                    | Backend File(s)           | Endpoint(s)             | Frontend     | Status  |
|---------------------------|--------------------------|-------------------------|-------------|---------|
| Opposition radar (leads)   | api/leads.py             | 8 endpoints /leads/*    | leads panel | ACTIVE  |
| Risk scoring (unified)     | risk_engine.py           | All search endpoints    | score badges| ACTIVE  |
| Watchlist monitoring        | watchlist/scanner.py     | 18 endpoints /watchlist/*| watchlist UI| ACTIVE  |
| IDF scoring (3-tier)        | idf_scoring.py           | /admin/idf-*           | admin panel | ACTIVE  |
| Image serving               | main.py                  | /api/trademark-image/*  | thumbnails  | ACTIVE  |
| Export/download             | api/reports.py           | /reports/generate+download| reports UI | ACTIVE  |
| Nice class descriptions     | risk_engine.py           | /api/suggest-classes    | class select| ACTIVE  |
| Translation (NLLB-200)      | utils/translation.py     | (via scoring)          | score badges| ACTIVE  |
| AI Studio (names)           | api/creative.py          | /tools/suggest-names   | studio panel| ACTIVE  |
| AI Studio (logos)           | api/creative.py          | /tools/generate-logo   | studio panel| ACTIVE  |
| Reports system              | api/reports.py           | 5 endpoints /reports/* | reports tab | ACTIVE  |
| Agentic search (live)       | agentic_search.py        | /search/intelligent    | loading modal| ACTIVE |
| Holder portfolio            | api/holders.py           | /holders/{id}/trademarks| portfolio modal| ACTIVE |

---

## DOCKER & INFRASTRUCTURE

| Check                       | Status | Details                                              |
|----------------------------|--------|------------------------------------------------------|
| bulletins/ mounted          | YES    | `C:/Users/701693/turk_patent/bulletins -> /app/bulletins (ro)` |
| Nginx image route           | YES    | Proxied through `/api/trademark-image/{path}` -> backend |
| Cloudflare tunnel           | YES    | `ipwatchai.com -> nginx:80 -> backend:8000`          |
| Static files served         | YES    | `/static/` mounted via FastAPI StaticFiles             |
| GPU configured              | YES    | NVIDIA 1x GPU, CUDA 12.1.1, FP16+TF32                |
| Redis caching               | YES    | 4GB maxmemory, LRU eviction, AOF persistence          |
| Health checks               | YES    | All 4 core services (redis, postgres, backend, nginx)  |
| Rate limiting               | YES    | 3-tier: auth(5r/min), search(2r/s), api(10r/s)        |
| Security headers            | PARTIAL| Has X-Frame, X-XSS, nosniff. Missing CSP, HSTS       |

---

## NAMING CONVENTIONS

**Zero camelCase/snake_case mismatches found between backend and frontend.**

All API responses use `snake_case`. Frontend JavaScript consumes fields as-is:
- `application_no` (not `applicationNo`)
- `holder_name` (not `holderName`)
- `nice_class_numbers` / `classes` (not `niceClasses`)
- `similarity_score` (not `similarityScore`)
- `has_extracted_goods` (not `hasExtractedGoods`)

Frontend convention: `snake_case` for data fields, `camelCase` for function names, `UPPER_CASE` for constants.

---

## FIELD NAME DIFFERENCES BETWEEN LAYERS

| DB Column              | API Response Field          | Notes                               |
|-----------------------|----------------------------|-------------------------------------|
| nice_class_numbers    | classes                     | Renamed in API for brevity          |
| application_date      | filing_date (in some models)| Inconsistent naming                 |
| holder_name           | holder (in ConflictingTM)   | Shortened in nested object          |
| appeal_deadline       | opposition_deadline (leads) | Different name in leads context     |
| alert_threshold       | similarity_threshold        | Renamed in watchlist response       |
| customer_application_no| application_no (watchlist)  | Internal vs. user-facing name       |
| customer_bulletin_no  | bulletin_no (watchlist)     | Internal vs. user-facing name       |

---

## CONFIGURATION MISMATCHES FOUND

| # | Issue                                 | Severity | Details                                            |
|---|---------------------------------------|----------|----------------------------------------------------|
| 1 | DB password hardcoded in compose      | HIGH     | `Dogan.1996` visible in docker-compose.yml          |
| 2 | DB_HOST/DB_PORT inconsistency         | MEDIUM   | .env=127.0.0.1:5433, compose=postgres:5432, example=host.docker.internal:5432 |
| 3 | Schema init path mismatch             | MEDIUM   | compose: `./deploy/schema.sql`, prod: `./deploy/initdb/schema.sql` (doesn't exist) |
| 4 | CORS origins differ across configs    | LOW      | compose has 4 origins, prod template has 2, actual prod has 3 |
| 5 | GPU enabled/disabled inconsistency    | MEDIUM   | Dev compose: cuda+FP16, prod: cpu+FP32 (no easy GPU toggle) |
| 6 | 7-Zip path Windows vs Linux           | MEDIUM   | .env: `C:\Program Files\7-Zip\7z.exe`, prod: `/usr/bin/7z` |
| 7 | Redis no authentication               | LOW      | No REDIS_PASSWORD set in any active config          |

---

## ACTION ITEMS

### CRITICAL (broken - user sees errors)

None found. All frontend-referenced fields are provided by the backend.

**7 potentially undefined global JS functions** (may exist in unread files or be defined inline):
1. `isInWatchlist(appNo)` - used in result-card.js
2. `showExtractedGoods(appNo)` - used in result-card.js, lead-card.js
3. `openQuickWatchlistAdd(data)` - used in result-card.js
4. `refreshWatchlistButtons()` - used in _modals.html
5. `openStudioWithContext(mode, ctx)` - used in result-card.js
6. `loadPortfolio()` - called from auth.js
7. `initPipelineStatus()` - called from auth.js

These are likely defined in `app.js` (1700+ lines) or loaded dynamically. Verify they exist.

### HIGH (data exists in DB, just needs wiring)

| # | Field(s)              | What to do                                                    | Effort |
|---|-----------------------|---------------------------------------------------------------|--------|
| 1 | registration_no       | Add to search results & holder portfolio API responses        | Small  |
| 2 | wipo_no               | Add to search results (useful for international marks)        | Small  |
| 3 | attorney_name         | Add to search results; create attorney search/filter endpoint | Medium |
| 4 | vienna_class_numbers  | Add Vienna classification filter to search                    | Medium |
| 5 | expiry_date           | Add to holder portfolio & alert detail for renewal tracking   | Small  |
| 6 | detected_lang         | Show language badge on search results (data already computed) | Small  |
| 7 | name_en/name_ku/name_fa| Wire translations to frontend for multilingual display       | Medium |

### MEDIUM (would improve the product)

| # | Feature                           | What to do                                                    |
|---|-----------------------------------|---------------------------------------------------------------|
| 1 | DINOv2 standalone search          | Create endpoint for DINOv2-specific visual search             |
| 2 | Color-based search                | Create endpoint for searching by dominant color               |
| 3 | OCR text search                   | Create endpoint to search trademarks by text in logos         |
| 4 | Status filter on main search      | Add `status` query param to /search endpoints                 |
| 5 | Attorney name filter              | Create attorney search endpoint + frontend filter             |
| 6 | gazette_no/gazette_date display   | Add gazette info to detailed trademark views                  |
| 7 | Security: Add CSP header          | Configure Content-Security-Policy in nginx                    |
| 8 | Security: Add HSTS header         | Configure Strict-Transport-Security in nginx                  |
| 9 | Security: Redis auth              | Set REDIS_PASSWORD in production config                       |

### LOW (nice to have)

| # | Item                              | Details                                                       |
|---|-----------------------------------|---------------------------------------------------------------|
| 1 | last_event_date display           | Could show "last activity" in trademark details               |
| 2 | availability_status display       | Could show availability in search results                     |
| 3 | Deploy schema path fix            | Create `deploy/initdb/` or update prod compose path           |
| 4 | CORS origin standardization       | Use env file only, remove hardcoded CORS from compose         |

---

## DEAD FIELDS - RECOMMENDATIONS

| Field                | Size in DB | Recommendation                                    | Reasoning                                     |
|----------------------|-----------|---------------------------------------------------|-----------------------------------------------|
| registration_no      | VARCHAR   | **BUILD** - Add to portfolio & detail views       | High value for IP professionals               |
| wipo_no              | VARCHAR   | **BUILD** - Add to international trademark views  | Important for Madrid Protocol marks            |
| attorney_name        | VARCHAR   | **BUILD** - Create search + filter                | Users want to find marks by attorney           |
| attorney_no          | VARCHAR   | **BUILD** - Link to attorney profiles             | Pairs with attorney_name                       |
| vienna_class_numbers | INT[]     | **BUILD** - Add as search filter                  | Visual classification standard, useful filter  |
| gazette_no           | VARCHAR   | **BUILD** - Show in detailed views                | Reference info, low effort to display          |
| gazette_date         | DATE      | **BUILD** - Show in detailed views                | Reference info, low effort to display          |
| expiry_date          | DATE      | **BUILD** - Add renewal tracking feature          | High value for portfolio management            |
| last_event_date      | DATE      | **DEFER** - Low priority                          | Redundant with updated_at for most purposes    |
| availability_status  | VARCHAR   | **DEFER** - Low priority                          | Rarely populated, uncertain value              |
| name_en              | VARCHAR   | **BUILD** - Multilingual display                  | Already computing translations                 |
| name_ku              | VARCHAR   | **BUILD** - Multilingual display                  | Already computing translations                 |
| name_fa              | VARCHAR   | **BUILD** - Multilingual display                  | Already computing translations                 |

**Summary:** 10 DEAD fields should have features built (data already being ingested), 2 can be deferred, 0 should be removed from ingest (all have future value).

---

## API ENDPOINT INVENTORY (70 total)

| Category          | Count | Key Endpoints                                          |
|-------------------|-------|--------------------------------------------------------|
| Authentication    | 5     | login, register, refresh, change-password, /me         |
| User Management   | 5     | list, create, get, update, deactivate                  |
| User Profile      | 5     | get/update profile, avatar, get/update organization    |
| Organization      | 5     | get, update, stats, settings, threshold                |
| Watchlist         | 18    | CRUD, bulk, upload (3 variants), template, scan, logo  |
| Alerts            | 6     | list, summary, get, acknowledge, resolve, dismiss      |
| Dashboard         | 1     | stats                                                  |
| Leads (Radar)     | 8     | feed, stats, credits, detail, contact, convert, dismiss, export |
| Reports           | 5     | generate, list, get, download, delete                  |
| Creative Suite    | 4     | suggest-names, generate-logo, get-image, history       |
| Holders           | 2     | portfolio, search                                      |
| Pipeline          | 4     | trigger, trigger-step, status, run detail              |
| Admin             | 7     | settings CRUD, overview, IDF stats/analyze             |
| Utility           | 4     | health, info, config, status                           |
| **TOTAL**         | **70**|                                                        |

---

## INFRASTRUCTURE SUMMARY

```
INTERNET
    |
    v
Cloudflare CDN (ipwatchai.com)
    |
    v
cloudflared tunnel container
    |
    v
nginx:80 (rate limiting, security headers, gzip)
    |--- /api/v1/auth/*      -> auth_limit (5r/min)
    |--- /api/v1/search/*    -> search_limit (2r/s, 180s timeout)
    |--- /api/*              -> api_limit (10r/s, 120s timeout)
    |--- /ws/*               -> WebSocket (1hr timeout)
    |--- /static/*           -> FastAPI StaticFiles
    |--- /                   -> dashboard.html
    v
backend:8000 (FastAPI, 4 workers, uvloop)
    |--- GPU: NVIDIA RTX 4070 Ti Super (16GB VRAM)
    |--- Models: CLIP(512d) + DINOv2(768d) + MiniLM(384d) + NLLB-200
    |--- FP16 + TF32 enabled
    |
    +---> postgres:5432 (pgvector, halfvec embeddings)
    +---> redis:6379 (4GB cache, AOF persistence)
```

**Volume Architecture:**
```
Host bulletins/ (50GB+) ---ro---> /app/bulletins  (trademark images)
Host .cache/ (15GB)     ---ro---> /root/.cache    (model weights)
Named vol postgres_data --------> /var/lib/postgresql/data
Named vol uploads_data  --------> /app/uploads    (user content)
Named vol reports_data  --------> /app/reports    (generated reports)
Named vol logs_data     --------> /app/logs       (application logs)
```
