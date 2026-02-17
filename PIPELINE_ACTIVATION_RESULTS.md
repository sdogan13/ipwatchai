# Monitoring Pipeline Activation Results

**Date:** 2026-02-11
**Duration:** ~30 minutes

---

## 1. Pipeline Architecture

### Execution Order
1. **IDF Computation** (`compute_idf.py`) - Populates `word_idf` table with word frequency data
2. **Watchlist Creation** (API `/api/v1/watchlist`) - Users add brands to monitor
3. **Watchlist Scanner** (`watchlist/scanner.py`) - Scans trademarks against watchlist items, creates alerts in `alerts_mt`
4. **Universal Scanner** (`workers/universal_scanner.py`) - Scans new bulletin trademarks against entire DB, creates conflicts in `universal_conflicts`
5. **Alert Generation** - Happens automatically during watchlist scan (step 3)

### Trigger Mechanisms
| Component | Trigger | Entry Point |
|-----------|---------|-------------|
| IDF Computation | Manual script | `python compute_idf.py` |
| Watchlist Scan | Background task on item creation | `POST /api/v1/watchlist` triggers `_scan_watchlist_item()` |
| Single Item Scan | API call | `POST /api/v1/watchlist/{id}/scan` |
| Universal Scanner | CLI script | `python -m workers.universal_scanner --bulletin 485` |
| Queue Processing | CLI daemon | `python -m workers.universal_scanner --daemon` |

### Key Classes
- `UniversalScanner` - Scans new marks against DB, uses trigram + vector search, delegates scoring to `risk_engine.score_pair()`
- `WatchlistScanner` - Scans trademarks against watchlist items, uses `calculate_comprehensive_score()` + `score_pair()`
- `AlertCRUD.create()` - Creates alerts with severity from `risk_engine.get_risk_level()`

---

## 2. Schema Changes

### `word_idf` - 3 columns added
```sql
ALTER TABLE word_idf ADD COLUMN IF NOT EXISTS total_documents integer DEFAULT 0;
ALTER TABLE word_idf ADD COLUMN IF NOT EXISTS weight_multiplier double precision DEFAULT 1.0;
ALTER TABLE word_idf ADD COLUMN IF NOT EXISTS updated_at timestamp DEFAULT NOW();
```

### `universal_conflicts` - 2 columns added
```sql
ALTER TABLE universal_conflicts ADD COLUMN IF NOT EXISTS translation_similarity double precision DEFAULT 0;
ALTER TABLE universal_conflicts ADD COLUMN IF NOT EXISTS phonetic_similarity double precision DEFAULT 0;
```

### `alerts_mt` - 2 columns added
```sql
ALTER TABLE alerts_mt ADD COLUMN IF NOT EXISTS overlapping_classes integer[] DEFAULT '{}';
ALTER TABLE alerts_mt ADD COLUMN IF NOT EXISTS seen_at timestamp;
```

### `watchlist_mt` - 4 columns added
```sql
ALTER TABLE watchlist_mt ADD COLUMN IF NOT EXISTS alert_frequency varchar DEFAULT 'daily';
ALTER TABLE watchlist_mt ADD COLUMN IF NOT EXISTS notify_email boolean DEFAULT true;
ALTER TABLE watchlist_mt ADD COLUMN IF NOT EXISTS notification_frequency varchar DEFAULT 'daily';
ALTER TABLE watchlist_mt ADD COLUMN IF NOT EXISTS monitor_phonetic boolean DEFAULT true;
```

---

## 3. IDF Computation

| Metric | Value |
|--------|-------|
| Total documents | 2,564,547 |
| Unique words | 935,022 |
| GENERIC words | 9 (>0.5% of docs, weight=0.1) |
| SEMI_GENERIC words | 153 (0.1%-0.5%, weight=0.5) |
| DISTINCTIVE words | 934,860 (<0.1%, weight=1.0) |
| Min IDF | 3.99 |
| Avg IDF | 14.32 |
| Max IDF | 14.76 |
| Computation time | 22.7 seconds |

### Sample Classifications
| Word | Frequency | IDF | Class | Weight |
|------|-----------|-----|-------|--------|
| ve | 47,326 | 3.99 | generic | 0.1 |
| insaat | 16,601 | 5.04 | generic | 0.1 |
| patent | 2,833 | 6.81 | semi_generic | 0.5 |
| marka | 3,138 | 6.71 | semi_generic | 0.5 |
| dogan | 1,141 | 7.72 | distinctive | 1.0 |
| nike | 57 | 10.71 | distinctive | 1.0 |

---

## 4. Watchlist Items Created

| Brand | Nice Classes | Threshold | Alerts Generated |
|-------|-------------|-----------|-----------------|
| AMAZON | 9, 35, 38, 42 | 0.60 | 2 |
| KARACA | 11, 21, 35 | 0.60 | 10 |
| SALTBAE | 29, 30, 43 | 0.60 | 10 |
| HALIKARNAS | 25, 35, 43 | 0.60 | 10 |

All items created via `POST /api/v1/watchlist` API with automatic background scan triggered.

---

## 5. Universal Scanner Results (Opposition Radar / Leads)

| Bulletin | Trademarks Scanned | Conflicts Found | Critical | Very High | High | Medium |
|----------|--------------------|-----------------|----------|-----------|------|--------|
| 485 | ~15 (limit 50) | 287 | 190 | 39 | 31 | 27 |
| 484 | ~46 (limit 50) | 916 | 693 | 39 (est.) | - | - |
| **Total** | **~61** | **1,203** | **883** | - | - | - |

- Average similarity score: **91.3%**
- All conflicts have `opposition_deadline >= 2026-03-12` (within 44 days)
- All 1,203 leads have `lead_status = 'new'`

---

## 6. Alert Results

| Metric | Value |
|--------|-------|
| Total alerts | 32 |
| By severity: critical | 32 |
| By status: new | 32 |
| AMAZON alerts | 2 (classes 42, 9+38) |
| KARACA alerts | 10 (classes 11, 21, 35) |
| SALTBAE alerts | 10 (classes 29, 30, 43) |
| HALIKARNAS alerts | 10 (classes 25, 35, 43) |

Alert generation is limited to `MAX_ALERTS_PER_ITEM = 10` per watchlist item.

---

## 7. API Verification

| Endpoint | Status | Data |
|----------|--------|------|
| `GET /api/v1/leads/feed` | 200 OK | Returns leads from `universal_conflicts` |
| `GET /api/v1/leads/stats` | 200 OK | 1,203 total leads, 916 upcoming |
| `GET /api/v1/leads/credits` | 200 OK | 5 daily views, professional plan |
| `GET /api/v1/alerts` | 200 OK | 32 alerts with pagination |
| `GET /api/v1/alerts/summary` | 200 OK | 32 new, 32 critical |
| `GET /api/v1/watchlist` | 200 OK | 4 items with alert counts |
| `GET /api/v1/dashboard/stats` | 200 OK | watchlist=4, alerts=32 |
| `POST /api/v1/watchlist/{id}/scan` | 200 OK | Triggers background scan |

---

## 8. Final Table Row Counts

| Table | Before | After | Change |
|-------|--------|-------|--------|
| `word_idf` | 0 | **935,022** | +935,022 |
| `watchlist_mt` | 0 | **4** | +4 |
| `universal_conflicts` | 0 | **1,203** | +1,203 |
| `alerts_mt` | 0 | **32** | +32 |
| `pipeline_runs` | 0 | **1** | +1 |
| `trademarks` | 2,625,377 | 2,625,377 | unchanged |
| `universal_scan_queue` | 2,312,197 | 2,312,197 | unchanged |

---

## 9. Issues Found & Fixed

### Bug: Singleton Scanner Connection Poisoning
**Problem:** `WatchlistScanner` uses a singleton pattern (`get_scanner()`). When one scan fails (e.g., missing column), the shared psycopg2 connection enters `InFailedSqlTransaction` state, causing all subsequent scans to fail.
**Fix:** Added `scanner.conn.rollback()` before each scan and `reset_scanner()` on error in `api/routes.py:_scan_watchlist_item()`.

### Bug: halfvec String Parsing in Universal Scanner
**Problem:** PostgreSQL returns halfvec columns as strings (e.g., `"[-0.123,0.456,...]"`). The `_cosine_sim()` function in `universal_scanner.py` called `np.array()` on these strings, causing `ValueError`.
**Fix:** Added `_parse_vec()` helper that detects string vectors and parses them with `json.loads()`.

### Missing Schema: Multiple columns
**Problem:** 11 columns were missing across 4 tables (see Section 2).
**Fix:** Added all missing columns via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

### NLLB Translation Model
**Problem:** The NLLB-200 translation model fails to initialize in Docker due to `dtype` parameter incompatibility with the installed transformers version.
**Impact:** Translation similarity scores are always 0 in the current deployment.
**Status:** Non-blocking (translation is a 15% weight factor in scoring).

---

## 10. Remaining Work

1. **Scale Universal Scanner** - Only scanned ~100 trademarks from bulletins 484+485 (limit 50 each). Full bulletins have ~15,000 trademarks. Run `python -m workers.universal_scanner --daemon` to process the full 2.3M queue.

2. **NLLB Translation Fix** - The translation model init fails in Docker. Fix the `dtype` parameter handling in `utils/translation.py` to restore translation scoring.

3. **Scan Queue Processing** - The `universal_scan_queue` has 2,312,197 pending items. Process via daemon mode for comprehensive conflict detection.

4. **IDF Scheduled Refresh** - Set up monthly `compute_idf.py` runs (Windows Task Scheduler or `scripts/compute_idf_scheduled.bat`).

5. **Alert Notification Delivery** - Email/webhook notification for new alerts not yet configured (requires SMTP/webhook setup).

6. **`total_alerts_generated` Counter** - The `watchlist_mt.total_alerts_generated` column stays at 0 despite alerts being created. The counter update is not wired in `AlertCRUD.create()`.
