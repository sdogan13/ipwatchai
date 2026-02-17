# TIER 3: Expose Hidden Database Fields in API Responses — Results

## Summary

All 6 backend fixes implemented and verified. Data that was already in the database (or computable from `api_usage`) is now exposed in API responses. No schema migrations needed — all fields already existed in PostgreSQL.

## Fix Details

| Fix # | Field(s) | File:Line | What was added | Status |
|-------|----------|-----------|---------------|--------|
| 1 | `holder_name`, `holder_tpe_client_id` | api/attorneys.py:108-109 | Added to attorney portfolio trademark response dict (SQL already SELECTs them) | Done |
| 2 | `attorney_name`, `attorney_no`, `registration_no`, `bulletin_no` | api/holders.py:107-110 | Added to holder portfolio trademark response dict (SQL already SELECTs them) | Done |
| 3 | `application_date` | risk_engine.py:731, 767, 812 | Added to SQL SELECT, extracted from row, included in result dict | Done |
| 4 | `expiry_date` | risk_engine.py:731, 768, 813 | Added to SQL SELECT, extracted from row, included in result dict | Done |
| 5 | `name_tr` | risk_engine.py:808 | Already in SQL SELECT — added to result dict (was extracted but never returned) | Done |
| 6 | `searches_this_month` | api/routes.py:2065-2073, 2091, 2095 | Query `api_usage` table for org's monthly search total; replaced hardcoded `0` | Done |

## Files Modified

| File | Changes |
|------|---------|
| `api/attorneys.py` | +2 fields (`holder_name`, `holder_tpe_client_id`) in response dict |
| `api/holders.py` | +4 fields (`attorney_name`, `attorney_no`, `registration_no`, `bulletin_no`) in response dict |
| `risk_engine.py` | +2 columns in SQL SELECT (`application_date`, `expiry_date`), updated all 14 index-based field reads (+2 offset), +3 fields in result dict (`name_tr`, `application_date`, `expiry_date`) |
| `main.py` | +1 line in CLIP SQL SELECT (`application_date`, `expiry_date`), +1 line in DINOv2 SQL SELECT, +3 fields in unified result dict, +3 fields in legacy result dict |
| `api/routes.py` | New SQL query joining `api_usage`→`users` for org monthly searches; replaced `searches_this_month=0` with real count; also updated `plan_usage.searches.used`; fixed both dashboard stats AND organization stats endpoints |

## Verification Results

### Fix 1: Attorney Portfolio
```
GET /api/v1/attorneys/1045/trademarks
→ holder_name: None (null in DB for this record)
→ holder_tpe_client_id: 7975018
→ Keys include: holder_name, holder_tpe_client_id ✓
```

### Fix 2: Holder Portfolio
```
GET /api/v1/holders/6967542/trademarks
→ attorney_name: DUYGU DEMİRAL YILDIZ HOLDİNG A.Ş.
→ attorney_no: 3036
→ registration_no: None
→ bulletin_no: 485
→ Keys include: attorney_name, attorney_no, registration_no, bulletin_no ✓
```

### Fix 3-5: Search Results
```
GET /api/v1/search/quick?query=Nike&classes=25
→ name: nike
→ name_tr: nike
→ application_date: 1989-12-03
→ expiry_date: 1999-12-03
→ Keys include: name_tr, application_date, expiry_date ✓
```

### Fix 6: Dashboard Stats
```
GET /api/v1/dashboard/stats
→ searches_this_month: 20
→ plan_usage.searches: {"used": 20, "limit": 20}
→ No longer hardcoded to 0 ✓
```

## Deployment

- **Syntax check**: All 5 files pass `py_compile`
- **Ingestion check**: No pipeline running at deploy time
- **Files deployed**: `docker cp` for attorneys.py, holders.py, risk_engine.py, routes.py; main.py is bind-mounted
- **Container restart**: `docker restart ipwatch_backend`
- **Health check**: All services healthy (database OK, redis OK, GPU OK)
