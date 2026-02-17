# Docker Backend Rebuild Results

**Date:** 2026-02-11
**Scope:** Backend container only (postgres, redis, nginx, cloudflared untouched)

---

## 1. cursor_factory Fix

**Root cause:** The Docker container had a stale copy of `database/crud.py`. The host version already had the fix at line 55-57:

```python
def cursor(self, **kwargs):
    kwargs.setdefault('cursor_factory', RealDictCursor)
    return self.conn.cursor(**kwargs)
```

This `**kwargs` forwarding allows `utils/subscription.py` (and all other callers) to pass `cursor_factory=RealDictCursor` through the `Database.cursor()` wrapper. The stale Docker image had the old signature `def cursor(self)` which ignored `cursor_factory`.

**Fix:** Rebuilt the Docker image to include the updated `database/crud.py`.

---

## 2. Additional Bugs Found & Fixed During Rebuild

### 2a. `ai/` directory shadowing `ai.py` (CRITICAL)

**Problem:** Both `ai.py` (ML models: CLIP, DINOv2, MiniLM, EasyOCR) and `ai/` directory (Gemini client package) existed. Python resolves `import ai` to the directory, shadowing `ai.py`. This broke `risk_engine.py`, `agentic_search.py`, and `watchlist/scanner.py`.

**Fix:** Renamed `ai/` to `generative_ai/`. Updated 3 import locations in `api/creative.py`:
```python
# Before:
from ai.gemini_client import get_gemini_client, GeminiError
# After:
from generative_ai.gemini_client import get_gemini_client, GeminiError
```

### 2b. Docker DB connection using host `.env` values

**Problem:** `docker-compose.yml` used `${DB_HOST:-postgres}` and `${DB_PORT:-5432}`. The host `.env` file had `DB_HOST=127.0.0.1` and `DB_PORT=5433`, which overrode the Docker defaults. Backend tried connecting to `127.0.0.1:5433` inside the container instead of `postgres:5432`.

**Fix:** Hardcoded Docker network values in `docker-compose.yml`:
```yaml
DB_HOST: "postgres"
DB_PORT: "5432"
```

### 2c. EasyOCR model cache missing

**Problem:** Fresh container had no EasyOCR model cache, causing `FileNotFoundError` on model download attempt.

**Fix:** Added volume mount in `docker-compose.yml`:
```yaml
- ${EASYOCR_HOME:-C:/Users/701693/.EasyOCR}:/root/.EasyOCR:ro
```

### 2d. NumPy 2.x incompatibility with PyTorch 2.1.2

**Problem:** `pip install numpy` resolved to 2.2.6, but PyTorch 2.1.2 was compiled against NumPy 1.x ABI. All search endpoints returned `"Numpy is not available"`.

**Fix:** Pinned in `requirements.txt`:
```
numpy>=1.26.3,<2.0
```
Also pinned opencv to avoid its numpy>=2 dependency:
```
opencv-python>=4.9.0.80,<4.11
```

### 2e. bcrypt 5.0 incompatibility with passlib 1.7.4

**Problem:** `bcrypt 5.0.0` removed `__about__` attribute and changed API, breaking `passlib 1.7.4`'s bcrypt backend. Login endpoint returned `"Invalid salt"` errors.

**Fix:** Pinned in `requirements.txt`:
```
bcrypt>=4.0.1,<4.1
```

### 2f. OrganizationResponse Pydantic schema mismatch

**Problem:** `OrganizationResponse` model expected `email`, `plan`, `max_users`, `max_watchlist_items`, `max_monthly_searches` fields, but the `organizations` DB table doesn't have these columns. `/api/v1/auth/me` returned 500 errors.

**Fix:**
- Removed required `email` from `OrganizationBase`
- Made `plan`, `max_users`, `max_watchlist_items`, `max_monthly_searches` Optional in `OrganizationResponse`
- Updated `/api/v1/auth/me` endpoint to resolve plan details via JOIN with `subscription_plans` table

### 2g. DINOv2 FP16 type mismatch in image search

**Problem:** `encode_query_image()` in `main.py` did not cast DINOv2 input tensor to `.half()` when model is FP16. Error: `"Input type (float) and bias type (c10::Half) should be the same"`.

**Fix:** Added half-precision cast in `main.py`:
```python
dino_tensor = dinov2_preprocess(pil_img).unsqueeze(0).to(ai_device)
if str(ai_device) == 'cuda':
    dino_tensor = dino_tensor.half()
```

---

## 3. Pre-Rebuild Checklist

| Check | Status |
|-------|--------|
| All new files present (api/attorneys.py, opposition-timeline.js, generative_ai/) | PASS |
| cursor_factory fix in database/crud.py | PASS |
| ai/ directory removed, generative_ai/ exists | PASS |
| .dockerignore doesn't exclude new files | PASS |
| Pipeline not running | PASS |
| No active DB queries | PASS |
| Dockerfile uses COPY . . | PASS |
| models/ bind-mounted (not baked) | PASS |
| main.py bind-mounted (not baked) | PASS |

---

## 4. Rebuild Output

```
Build command: docker-compose build backend
Build time:   ~600s (10 min)
Base image:   nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04
PyTorch:      2.1.2+cu121 (2.2GB download)
Errors:       None
```

Post-build steps:
- `docker-compose up -d backend` — recreated backend + redis
- Fixed DB connection (hardcoded postgres:5432)
- Added EasyOCR volume mount
- Downgraded numpy to 1.26.4 (in-container pip)
- Downgraded bcrypt to 4.0.1 (in-container pip)
- Downgraded opencv to 4.10.0 (in-container pip)
- Copied fixed `api/routes.py` into container
- `models/schemas.py` auto-reflected via bind mount

**Note:** The in-container pip fixes are ephemeral. The `requirements.txt` has been updated with version pins so the next `docker-compose build` will bake them in permanently.

---

## 5. Post-Rebuild Smoke Tests

| # | Test | Endpoint | Result | Details |
|---|------|----------|--------|---------|
| 1 | Auth/Me | `GET /api/v1/auth/me` | **PASS** | Returns user profile with organization (plan=professional, max_watchlist=50) |
| 2 | Quick Search | `POST /api/search` (name=NIKE) | **PASS** | Returns 10 results, first=nike (100% similarity) |
| 3 | Image Search | `POST /api/search-by-image` | **PASS** | Returns results with unified scoring, OCR detected "expo channel" |
| 4 | Leads Feed | `GET /api/v1/leads/feed` | **PASS** | Returns empty array (no leads for test org) |
| 5 | Attorney Search | `GET /api/v1/attorneys/search?query=patent` | **PASS** | Returns 10 results, top=DESTEK PATENT (33,884 trademarks) |
| 6 | Opposition Timeline JS | `GET /static/js/components/opposition-timeline.js` | **PASS** | HTTP 200 |
| 7 | Health | `GET /health` | **PASS** | DB=ok, Redis=ok, GPU=ok (RTX 4070 Ti SUPER) |

**Result: 7/7 PASS**

---

## 6. VRAM Comparison

| Metric | Value |
|--------|-------|
| Pre-rebuild baseline | 8,255 MiB |
| Post-rebuild | 8,114 MiB |
| Delta | -141 MiB (improvement) |

---

## 7. Files Modified

| File | Change |
|------|--------|
| `ai/` → `generative_ai/` | Renamed directory to fix module shadowing |
| `api/creative.py` | Updated 3 import paths from `ai.gemini_client` to `generative_ai.gemini_client` |
| `api/routes.py` | Updated `/api/v1/auth/me` to resolve plan from subscription_plans |
| `docker-compose.yml` | Hardcoded DB_HOST/DB_PORT, added EasyOCR volume mount |
| `main.py` | Fixed DINOv2 FP16 tensor cast in `encode_query_image()` |
| `models/schemas.py` | Made OrganizationResponse fields Optional, removed required email |
| `requirements.txt` | Pinned numpy<2.0, bcrypt<4.1, opencv<4.11 |

---

## 8. Container Status (Final)

```
NAMES              STATUS
ipwatch_backend    Up (healthy)
ipwatch_redis      Up (healthy)
ipwatch_nginx      Up (healthy)
ipwatch_tunnel     Up
ipwatch_postgres   Up (healthy)
```

## 9. Next Steps

- **Permanent rebuild:** Run `docker-compose build backend && docker-compose up -d backend` to bake the numpy/bcrypt/opencv pins into the image permanently (current fixes are in-container pip installs)
- **Test password hashing:** The test user `pro@test.com` password hash was regenerated using bcrypt 4.0.1. Other test users may need the same treatment if their hashes were created with bcrypt 5.x
