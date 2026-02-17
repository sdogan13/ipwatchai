# DEAD FIELDS WIRING — IMPLEMENTATION PLAN

> Investigation completed 2026-02-10. All findings verified against live database (1,765,181 records) and source code.

---

## DATABASE POPULATION RATES

| Field | Count | Rate | Verdict |
|-------|-------|------|---------|
| `attorney_name` | 1,137,263 | 64.4% | **Wire to UI** |
| `attorney_no` | 740,631 | 42.0% | **Wire to UI** |
| `registration_no` | 174,291 | 9.9% | **Wire to UI** (valuable when present) |
| `vienna_class_numbers` | 1,093,346 | 61.9% | **Wire to UI** |
| `bulletin_no` | 1,547,609 | 87.7% | Already partially wired |
| `bulletin_date` | 1,374,146 | 77.8% | **Wire to UI** |
| `appeal_deadline` | 1,374,146 | 77.8% | **Wire to UI** |
| `application_date` | 1,731,875 | 98.1% | Already wired (naming inconsistency only) |
| `registration_date` | 0 | 0% | **SKIP** — completely empty |
| `current_status` | 1,765,181 | 100% | Already wired (missing as search filter) |
| `logo_ocr_text` | 1,191,474 | 67.5% | Already wired in risk engine |
| `detected_lang` | 1,707,671 | 96.7% | **Wire to UI** (low priority, informational) |
| `name_tr` | 1,707,656 | 96.7% | Already wired in risk engine |
| `holder_name` | 1,728,625 | 97.9% | Already wired |
| `holder_tpe_client_id` | 1,005,920 | 57.0% | Already wired |

**Decision**: Skip `registration_date` (0% populated). All other fields have sufficient data.

### Attorney Data Quality Notes

- 1,408 unique attorney IDs, 2,167 unique attorney names
- Only 5 attorney names map to 2 different IDs (low duplication)
- Some `attorney_name` values contain garbage (bulletin text fragments) — display-only, no action needed now
- **Duplicate holder names are very common** (MEHMET YILMAZ → 84 different IDs) — confirms ID-based lookup is essential

### Status Distribution

| Status | Count | Pct |
|--------|-------|-----|
| Published | 1,597,012 | 90.5% |
| Registered | 165,865 | 9.4% |
| Withdrawn | 1,631 | 0.09% |
| Applied | 646 | 0.04% |
| Refused | 27 | 0.002% |

---

## FEATURE GROUP 1: Holder & Attorney Display + Portfolio

### 1.1 Holder Card Display — Current State Assessment

**Current state**: FULLY WORKING. The holder feature is complete end-to-end:

| Layer | Status | Details |
|-------|--------|---------|
| DB columns | `holder_name VARCHAR(500)`, `holder_tpe_client_id VARCHAR(50)` | On `trademarks` table |
| DB indexes | `idx_tm_holder_name` (B-tree), `idx_tm_holder_tpe_id` (B-tree) | Both exist. No trigram on trademarks table (only on unused `holders` table) |
| API endpoint | `GET /api/v1/holders/{tpe_client_id}/trademarks` | `api/holders.py:22-118` — paginated, PRO-gated |
| API endpoint | `GET /api/v1/holders/search?query=...` | `api/holders.py:121-163` — returns name + ID + count |
| Pydantic model | None (returns raw dicts) | Portfolio + search responses are ad-hoc |
| SQL queries | Risk engine (`risk_engine.py:708-722`) selects both fields | Enhanced search (`main.py:1505-1506`) also selects both |
| Result card | `renderHolderLink(holderName, holderTpeId)` in `result-card.js:7-34` | Format: "Applicant: Name (ID)" — clickable for PRO, locked for free |
| Click handler | `showHolderPortfolio(tpeId, name)` → `loadHolderTrademarks(tpeId, page)` | Uses `holder_tpe_client_id` — correct ID-based lookup |
| Portfolio modal | `#holderPortfolioModal` in `_modals.html:338-427` | Full modal with search, stats, pagination |
| App logic | `app.js:737-901` — 8 functions | Complete: show/render/paginate/search/select/clear |
| API calls | `api.js:357-395` (load) + `api.js:581-594` (search) | Both use correct ID-based endpoints |
| i18n | 20+ keys under `holder.*` namespace | Complete in en.json, tr.json, ar.json |

**Changes needed**: NONE for basic holder display. It already uses `Name (ID)` format and ID-based portfolio lookup. One improvement opportunity:

- **Missing trigram index on `trademarks.holder_name`** — The ILIKE search in `holders/search` can't use the B-tree index efficiently for mid-string matches. Add GIN trigram index.

### 1.2 Attorney — Wire from DB to Frontend (NEW)

**Current state**: COMPLETELY DEAD — `attorney_name` and `attorney_no` exist in DB (64%/42% populated) but are never selected in any search query, never returned in any API response, never displayed in the frontend.

**One partial exception**: `TrademarkResult` model in `main.py:1248` has `attorney: Optional[str]` field, but it's hardcoded to `None` at `main.py:1666`.

#### 1.2.1 Backend — SQL Queries to Update

Each query below needs `t.attorney_name, t.attorney_no` added to its SELECT:

| # | File | Function | Lines | Current SELECT |
|---|------|----------|-------|----------------|
| Q5 | `risk_engine.py` | `calculate_hybrid_risk()` | 704-722 | Has holder but NOT attorney |
| Q7 | `main.py` | Image search — CLIP candidates | 774-783 | Missing both |
| Q8 | `main.py` | Image search — DINOv2 candidates | 794-808 | Missing both |
| Q9 | `main.py` | Legacy text search | 1151-1163 | Missing both |
| Q10 | `main.py` | Enhanced search candidates | 1494-1521 | Missing both |
| Q16 | `api/holders.py` | Holder trademarks | 78-90 | Missing both |

**Also need to update result dict construction:**

| File | Lines | What to change |
|------|-------|---------------|
| `risk_engine.py` | 775-787 | Add `attorney_name`, `attorney_no` to result dict |
| `main.py` | 1656-1672 | Change `attorney=None` to `attorney=row.get('attorney_name')`, add `attorney_no` |
| `main.py` | 1007-1030 | Legacy search result construction — add attorney fields |

#### 1.2.2 API Models — Pydantic Models to Update

| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1248 | Already has `attorney: Optional[str]`. Add `attorney_no: Optional[str]` |
| `ConflictingTrademark` | `models/schemas.py` | 401-411 | Add `attorney_name: Optional[str]`, `attorney_no: Optional[str]` |

#### 1.2.3 New Endpoint: `GET /api/v1/attorneys/{attorney_no}/trademarks`

**File**: Create `api/attorneys.py` (mirrors `api/holders.py` exactly)

- **Route**: `GET /attorneys/{attorney_no}/trademarks`
- **Auth**: Requires authenticated user
- **Access control**: Same PRO gate (`can_view_holder_portfolio`)
- **Query params**: `page` (int, default 1), `page_size` (int, default 20, max 100)
- **SQL**: Lookup by `attorney_no` (NOT by name):
  ```sql
  SELECT DISTINCT attorney_name, attorney_no
  FROM trademarks WHERE attorney_no = %s LIMIT 1
  ```
  Then:
  ```sql
  SELECT id, application_no, name, current_status,
         nice_class_numbers, application_date, registration_date,
         image_path, bulletin_no, holder_name, holder_tpe_client_id,
         (extracted_goods IS NOT NULL AND extracted_goods != '[]'::jsonb
          AND extracted_goods != 'null'::jsonb) AS has_extracted_goods
  FROM trademarks WHERE attorney_no = %s
  ORDER BY application_date DESC NULLS LAST, application_no DESC
  LIMIT %s OFFSET %s
  ```
- **Response shape**:
  ```json
  {
    "attorney_name": "Mehmet YILMAZ",
    "attorney_no": "12345",
    "total_count": 42,
    "page": 1,
    "page_size": 20,
    "total_pages": 3,
    "trademarks": [...]
  }
  ```

#### 1.2.4 New Endpoint: `GET /api/v1/attorneys/search?q=...`

**File**: Same `api/attorneys.py`

- **Route**: `GET /attorneys/search`
- **Auth**: Requires authenticated user, PRO-gated
- **Query params**: `query` (string, min 2 chars), `limit` (int, default 10, max 50)
- **SQL**: Returns BOTH name AND attorney_no:
  ```sql
  SELECT attorney_name, attorney_no, COUNT(*) as trademark_count
  FROM trademarks
  WHERE (attorney_name ILIKE %s OR attorney_no ILIKE %s)
    AND attorney_no IS NOT NULL
  GROUP BY attorney_name, attorney_no
  ORDER BY trademark_count DESC
  LIMIT %s
  ```
- **Response shape**:
  ```json
  {
    "query": "mehmet",
    "results": [
      { "attorney_name": "Mehmet YILMAZ", "attorney_no": "12345", "trademark_count": 42 }
    ]
  }
  ```

#### 1.2.5 Register Router

**File**: `main.py`

Add alongside holder router registration (~line 259):
```python
from api.attorneys import router as attorneys_router
app.include_router(attorneys_router, prefix="/api/v1")
```

#### 1.2.6 Frontend — Result Card

**File**: `static/js/components/result-card.js`

Add `renderAttorneyLink(attorneyName, attorneyNo)` function (lines ~35-60), mirroring `renderHolderLink`:
- If `attorneyName` is falsy: return empty string
- If `attorneyNo` is falsy: show plain text "Attorney: name"
- PRO plan: clickable `showAttorneyPortfolio(attorneyNo, attorneyName)`
- Free plan: name with lock icon → `showUpgradeModal()`
- Format: `"Attorney: Name (ID)"`

Add to `renderResultCard()` at line ~118, immediately after the `renderHolderLink` call:
```javascript
+ window.AppComponents.renderAttorneyLink(r.attorney_name, r.attorney_no)
```

#### 1.2.7 Frontend — Attorney Portfolio Modal

**File**: `templates/partials/_modals.html`

Add `#attorneyPortfolioModal` after `#holderPortfolioModal` (after line 427). Identical structure with `attorney`-prefixed IDs:
- `attorneyModalTitle`, `attorneyModalSubtitle`
- `attorneySearchInput`, `attorneySearchResults`
- `attorneyPortfolioBody`, `attorneyPortfolioLoading`, `attorneyPortfolioResults`
- `attorneyTotalCount`, `attorneyRegisteredCount`, `attorneyPendingCount`
- `attorneyTrademarksList`, `attorneyPagination`
- `attorneyPortfolioError`

#### 1.2.8 Frontend — App Logic

**File**: `static/js/app.js`

Add attorney portfolio functions (~after line 901), mirroring the holder pattern:
- `showAttorneyPortfolio(attorneyNo, attorneyName)`
- `renderAttorneyTrademarks(trademarks)`
- `renderAttorneyPagination(currentPage, totalPages, attorneyNo)`
- `closeAttorneyPortfolio()`
- `performAttorneySearch()`
- `renderAttorneySearchResults(results)`
- `selectAttorneyFromSearch(attorneyNo, attorneyName)`
- `clearAttorneySearch()`

#### 1.2.9 Frontend — API Calls

**File**: `static/js/api.js`

Add two functions mirroring holder API calls:
- `loadAttorneyTrademarks(attorneyNo, page)` → `GET /api/v1/attorneys/{attorney_no}/trademarks`
- `searchAttorneys(query, limit)` → `GET /api/v1/attorneys/search?query=...`

#### 1.2.10 Frontend — i18n

**Files**: `static/locales/en.json`, `static/locales/tr.json`, `static/locales/ar.json`

Add `attorney.*` namespace mirroring `holder.*`:

```json
"attorney": {
  "title": "Attorney Portfolio",
  "loading": "Loading portfolio...",
  "subtitle": "Attorney No: {attorneyNo} — {count} trademarks",
  "loading_subtitle": "Attorney No: {attorneyNo} — Loading...",
  "total_trademarks": "Total Trademarks",
  "registered": "Registered",
  "pending": "Pending",
  "no_trademarks": "No trademarks found for this attorney.",
  "label": "Attorney:",
  "search_placeholder": "Search attorney (min 2 characters)...",
  "search": "Search",
  "back": "Back",
  "search_min_chars": "Enter at least 2 characters",
  "searching": "Searching...",
  "no_results": "No results found",
  "results_found": "{count} results found",
  "trademarks_count": "{count} trademarks",
  "load_error": "An error occurred while loading the attorney portfolio.",
  "search_error": "An error occurred during search.",
  "close": "Close"
}
```

#### 1.2.11 Database Indexes

**Existing indexes** (already created by `ingest.py`):
- `idx_tm_attorney_name` — B-tree on `attorney_name`
- `idx_tm_attorney_no` — B-tree on `attorney_no`

**Missing — need to add**:
```sql
-- Fuzzy search for attorney autocomplete
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_attorney_name_trgm
    ON trademarks USING gin(attorney_name gin_trgm_ops);

-- Fuzzy search for holder autocomplete (missing from trademarks table)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_holder_name_trgm
    ON trademarks USING gin(holder_name gin_trgm_ops);
```

**File**: New migration `migrations/add_attorney_trigram_indexes.sql`

---

## FEATURE GROUP 2: Dead Fields → API Responses

### 2.1 `registration_no` (VARCHAR) — 9.9% populated

**Add to SQL queries**: Q5, Q7, Q8, Q9, Q10 (see table in §1.2.1)

**Add to models**:
| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1236 | Add `registration_no: Optional[str] = None` |
| `ConflictingTrademark` | `models/schemas.py` | 401 | Add `registration_no: Optional[str] = None` |

**Add to result construction**:
| File | Lines | Change |
|------|-------|--------|
| `risk_engine.py` | 775-787 | Add `registration_no` to result dict |
| `main.py` | 1656-1672 | Add `registration_no=row.get('registration_no')` |
| `main.py` | 1007-1030 | Add to legacy search construction |

**Frontend display** (`result-card.js`):
- Show next to application_no in the TURKPATENT button area (line 116)
- Only when non-null: small gray text `"Reg: 12345"`
- Or add to the `renderTurkpatentButton` function as a secondary line

### 2.2 `application_date` Naming Fix (`filing_date` → `application_date`)

**Files to change** (4 occurrences in 2 Python files):

| File | Line | Current | New |
|------|------|---------|-----|
| `models/schemas.py` | 196 | `filing_date: Optional[date] = None` | `application_date: Optional[date] = None` |
| `models/schemas.py` | 258 | `filing_date: Optional[date] = Field(None, validation_alias='customer_registration_date')` | `application_date: Optional[date] = Field(None, validation_alias='customer_registration_date')` |
| `models/schemas.py` | 410 | `filing_date: Optional[date]` | `application_date: Optional[date] = None` |
| `api/routes.py` | 1979 | `filing_date=None,` | `application_date=None,` |

**Impact check**: Frontend JS and main.py already use `application_date`. The only consumers of `filing_date` are the watchlist and alert models — need to verify no frontend code reads `filing_date` from API responses.

**Verification needed**: Check `api/routes.py` for where `ConflictingTrademark` is constructed — every instance must change `filing_date=` to `application_date=`.

### 2.3 `vienna_class_numbers` (INTEGER[]) — 61.9% populated

**Add to SQL queries**: Q5, Q7, Q8, Q9, Q10

**Add to models**:
| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1236 | Add `vienna_classes: Optional[List[int]] = None` |
| `ConflictingTrademark` | `models/schemas.py` | 401 | Add `vienna_classes: Optional[List[int]] = None` |

**Frontend display** (`result-card.js`):
- Show below Nice class badges using similar badge styling but in a different color (e.g., teal/emerald)
- Only when non-null and non-empty
- Can reuse `renderNiceClassBadges` pattern from `score-badge.js` with a different color scheme

### 2.4 `bulletin_date` (DATE) — 77.8% populated

**Already in**: Alert CRUD queries (via JOIN on trademarks). NOT in search results.

**Add to SQL queries**: Q5, Q10

**Add to models**:
| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1236 | Add `bulletin_date: Optional[str] = None` |

**Frontend display**: Show on result card near `bulletin_no` (already displayed). Format: `"BLT 2024/5 — 2024-03-15"`

### 2.5 `appeal_deadline` (DATE) — 77.8% populated

**Already in**: Alert CRUD queries. NOT in search results.

**Add to SQL queries**: Q5, Q10

**Add to models**:
| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1236 | Add `appeal_deadline: Optional[str] = None` |

**Frontend display**: Show on result card with urgency coloring:
- Past deadline: gray strikethrough
- < 7 days: red text
- < 30 days: amber text
- > 30 days: green text
- Only when non-null

### 2.6 `detected_lang` (VARCHAR) — 96.7% populated

**Add to SQL queries**: Q5, Q10

**Add to models**:
| Model | File | Line | Change |
|-------|------|------|--------|
| `TrademarkResult` | `main.py` | 1236 | Add `detected_lang: Optional[str] = None` |

**Frontend display**: Low priority. Show as a tiny language badge on the result card (e.g., "TR", "EN", "AR") — only if not Turkish (since most are Turkish, showing it for all would be noise).

### 2.7 `registration_date` — SKIP

0% populated in the database. No UI work needed. Leave field in models but don't add to new SQL queries.

---

## FEATURE GROUP 3: Status Filter on Main Search

### 3.1 Current State

- `current_status` is 100% populated
- 5 distinct values: Published, Registered, Withdrawn, Applied, Refused
- Status is already displayed on result cards (via `r.status`)
- **No status filter exists on the search panel** (`_search_panel.html`)

### 3.2 Backend Changes

**File**: `main.py`

Add optional `status` query param to both search endpoints:

Enhanced search endpoint (~line 1480):
```python
status: Optional[str] = Query(None, description="Filter by trademark status")
```

Add to WHERE clause of Q10 (~line 1520):
```sql
AND ($status IS NULL OR t.current_status = $status)
```

Legacy search endpoint (~line 1140):
Same pattern.

**File**: `risk_engine.py`

Add optional `status_filter` parameter to `calculate_hybrid_risk()`:
- Pre-filter candidates by status before scoring
- Or post-filter after scoring (simpler, since candidate set is already limited)

### 3.3 Frontend Changes

**File**: `templates/partials/_search_panel.html`

Add status filter dropdown between Nice Class select and Search button (after line 32):
```html
<select id="status-filter" class="...">
  <option value="">All Statuses</option>
  <option value="Published">Published</option>
  <option value="Registered">Registered</option>
  <option value="Applied">Applied</option>
  <option value="Withdrawn">Withdrawn</option>
  <option value="Refused">Refused</option>
</select>
```

**File**: `static/js/api.js`

Update `handleQuickSearch()` and `handleAgenticSearch()` to include status param:
```javascript
var status = document.getElementById('status-filter').value;
if (status) url += '&status=' + encodeURIComponent(status);
```

---

## FEATURE GROUP 4: Attorney Filter on Main Search

### 4.1 Frontend — Search Panel

**File**: `templates/partials/_search_panel.html`

Add attorney text input with autocomplete (after status filter):
```html
<input id="attorney-filter" type="text" placeholder="Attorney name or ID..."
       class="..." autocomplete="off" />
<div id="attorney-autocomplete" class="hidden absolute ..."></div>
```

### 4.2 Frontend — Autocomplete Logic

**File**: `static/js/app.js`

Add debounced autocomplete on `#attorney-filter`:
- On input (debounced 300ms): call `searchAttorneys(query)`
- Show dropdown with `Name (ID)` format
- On select: store `attorney_no` in hidden field, show selected name in input
- On search submit: include `attorney_no` as query param

### 4.3 Backend — Search Param

**File**: `main.py`

Add optional `attorney_no` query param to search endpoints:
```python
attorney_no: Optional[str] = Query(None, description="Filter by attorney number")
```

Add to WHERE clause:
```sql
AND ($attorney_no IS NULL OR t.attorney_no = $attorney_no)
```

---

## EXECUTION ORDER

| Step | What | Files | Depends On | Risk |
|------|------|-------|-----------|------|
| 1 | Trigram indexes for attorney + holder | New migration SQL | — | Low (CONCURRENTLY) |
| 2 | `filing_date` → `application_date` rename | `models/schemas.py`, `api/routes.py` | — | Low |
| 3 | Add dead fields to Pydantic models | `main.py`, `models/schemas.py` | — | Low |
| 4 | Add dead fields to SQL queries + result dicts | `risk_engine.py`, `main.py`, `api/holders.py` | Step 3 | Medium |
| 5 | Create `api/attorneys.py` (portfolio + search) | New file | Step 1 | Medium |
| 6 | Register attorney router in `main.py` | `main.py` | Step 5 | Low |
| 7 | Attorney display on result cards | `result-card.js` | Step 4 | Low |
| 8 | Attorney portfolio modal | `_modals.html` | — | Low |
| 9 | Attorney app logic + API calls | `app.js`, `api.js` | Steps 5, 7, 8 | Medium |
| 10 | Attorney i18n keys | `en.json`, `tr.json`, `ar.json` | Step 7 | Low |
| 11 | Registration_no, vienna_classes, bulletin_date, appeal_deadline on result cards | `result-card.js`, `score-badge.js` | Step 4 | Low |
| 12 | Status filter (backend + frontend) | `main.py`, `_search_panel.html`, `api.js` | — | Low |
| 13 | Attorney filter (backend + frontend) | `main.py`, `_search_panel.html`, `app.js`, `api.js` | Steps 5, 9 | Medium |
| 14 | Lead card enhancements (attorney display) | `lead-card.js` | Step 4 | Low |
| 15 | Tests | New test file(s) | All above | — |

---

## FILES TO CREATE

| File | Purpose |
|------|---------|
| `api/attorneys.py` | Attorney portfolio + search endpoints |
| `migrations/add_attorney_trigram_indexes.sql` | GIN trigram indexes |

## FILES TO MODIFY

| File | Changes |
|------|---------|
| `main.py` | Register attorney router, add fields to SQL queries + models, add status/attorney filter params |
| `risk_engine.py` | Add dead fields to Q5 SELECT + result dict |
| `models/schemas.py` | Rename `filing_date` → `application_date`, add attorney/registration/vienna fields |
| `api/routes.py` | Rename `filing_date` → `application_date` in alert construction |
| `api/holders.py` | Add attorney fields to holder portfolio SQL |
| `static/js/components/result-card.js` | Add `renderAttorneyLink()`, registration_no, vienna classes, bulletin_date, appeal_deadline display |
| `static/js/components/score-badge.js` | Add `renderViennaClassBadges()` if needed |
| `static/js/app.js` | Attorney portfolio functions, attorney filter autocomplete |
| `static/js/api.js` | `loadAttorneyTrademarks()`, `searchAttorneys()`, status/attorney params in search |
| `static/js/components/lead-card.js` | Add attorney display in lead cards |
| `templates/partials/_modals.html` | Add attorney portfolio modal |
| `templates/partials/_search_panel.html` | Add status dropdown + attorney input |
| `static/locales/en.json` | Add `attorney.*` namespace |
| `static/locales/tr.json` | Add `attorney.*` namespace |
| `static/locales/ar.json` | Add `attorney.*` namespace |

---

## DESIGN DECISIONS NEEDING OWNER INPUT

1. **Attorney access control**: Should attorney portfolio be PRO-gated (same as holder), or available to all plans?
   - **Recommendation**: Same PRO gate as holder (`can_view_holder_portfolio`)

2. **Attorney display on lead cards**: Should attorney be clickable on lead cards, or stay plain text like holder?
   - **Recommendation**: Plain text (same pattern as holder on leads) — leads have their own detail modal

3. **Vienna classes display**: Should Vienna codes be shown as badges (like Nice classes) or as a simple text list?
   - **Recommendation**: Badges in a different color (teal/emerald) to distinguish from Nice classes (indigo)

4. **detected_lang display**: Should it show for all trademarks, or only non-Turkish?
   - **Recommendation**: Only non-Turkish (most are Turkish, showing it for all adds noise)

5. **Result card information density**: Adding attorney, registration_no, vienna_classes, bulletin_date, and appeal_deadline makes cards significantly taller. Should some fields be collapsed/expandable?
   - **Recommendation**: Show attorney + appeal_deadline always (high value). Show registration_no, vienna_classes, bulletin_date in a collapsible "More details" section.

6. **Generic vs separate portfolio modal**: Should we create a separate attorney modal, or make the holder modal generic/reusable for both?
   - **Recommendation**: Separate modal (simpler to implement, clear separation, easier to maintain). The holder modal has specific IDs and state management that would be complex to genericize.
