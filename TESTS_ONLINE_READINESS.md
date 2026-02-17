# Tests Online Readiness Report

**Date:** 2026-02-09
**Author:** Automated investigation
**Python:** 3.13.12 | **OS:** Windows 11 | **GPU:** RTX 4070 Ti Super (16GB)

---

## Executive Summary

We have **480 passing unit tests** (all mocked, no DB/network/GPU). Getting these running in CI is **straightforward (Phase 1, ~4 hours)**. The biggest blocker is that **no git remote exists** -- the repo is local-only on `master` with no GitHub/GitLab connection. Once a remote is configured, a GitHub Actions workflow can run unit tests immediately. Integration tests (Phase 2) require a PostgreSQL+pgvector Docker service and IDF seed data, which is a larger effort (~8-12 hours).

---

## Current State

| Metric | Value |
|--------|-------|
| Unit tests passing | **480** (13 test files) |
| Pre-existing failures | **19** in `test_security_audit.py` (see Section 7) |
| Overall code coverage | **19%** (2,392 of 12,305 statements) |
| Test framework | pytest 7.4.4 + pytest-asyncio 0.23.3 + pytest-cov 4.1.0 |
| Config files | `pytest.ini`, `.coveragerc` -- both exist and work |
| CI/CD pipelines | **None** -- no GitHub Actions, GitLab CI, or any CI config |
| Git remote | **None** -- local-only repo, `master` branch only |
| Docker | `Dockerfile.backend` (CUDA-based), `docker-compose.yml` (10 services) |

### Coverage Highlights

| Module | Coverage | Notes |
|--------|----------|-------|
| `utils/class_utils.py` | **100%** | Fully tested |
| `config/settings.py` | **99%** | Fully tested |
| `models/schemas.py` | **95%** | Fully tested |
| `utils/deadline.py` | **94%** | Fully tested |
| `idf_scoring.py` | **87%** | Well tested |
| `auth/authentication.py` | **66%** | Good coverage |
| `utils/subscription.py` | **66%** | Good coverage |
| `risk_engine.py` | **36%** | Standalone funcs tested, RiskEngine class at 0% |
| `api/routes.py` | **21%** | Only validation/routing tested |
| `database/crud.py` | **23%** | CRUD ops need real DB |
| `ai.py`, `ingest.py`, `data_collection.py` | **0%** | Pipeline modules -- need GPU/DB/network |
| All `workers/*` | **0%** | Background workers -- need DB |

---

## Blocker Analysis

### Tier 1: Must-Have for CI (unit tests only)

These items are needed to run the existing 480 mocked tests in CI.

| Item | Status | Effort | Notes |
|------|--------|--------|-------|
| Git remote (GitHub/GitLab) | Not configured | 15 min | `git remote add origin <url>` + `git push` |
| CI config file (`.github/workflows/test.yml`) | Does not exist | 1 hour | See proposed config below |
| `requirements-dev.txt` | Exists | 0 | Already has pytest, pytest-asyncio, pytest-cov |
| `pytest.ini` | Exists | 0 | Properly configured |
| `.coveragerc` | Exists | 0 | Properly configured |
| `DB_PASSWORD` env var | Required by Pydantic Settings | 5 min | Must be set even for mocked tests (Settings loaded at import) |
| Heavy module mocking | Already in `conftest.py` | 0 | torch, open_clip, transformers, easyocr, PIL, cv2, playwright, db.pool all mocked |
| Python 3.13 CI runner | Available on GitHub Actions | 0 | `actions/setup-python@v5` supports 3.13 |
| System packages for pip install | Needed in CI | 30 min | `libpq-dev` (psycopg2), build tools for torch stub |
| `.env` for CI | Does not exist | 15 min | Create `.env.test` with safe defaults |
| Fix/skip security tests | 19 failures | 15 min | `--ignore=tests/test_security_audit.py` or delete the file |

**Total Tier 1 effort: ~3-4 hours**

### Tier 2: Needed for Integration Tests (test DB)

| Item | Status | Effort | Notes |
|------|--------|--------|-------|
| Test PostgreSQL + pgvector | Not configured | 2 hours | Docker service in CI: `pgvector/pgvector:pg16` |
| Schema migration runner | Ad-hoc SQL files (no Alembic) | 3 hours | Need ordered migration script or single schema.sql |
| `word_idf` seed data | No seed file exists | 2 hours | Export from prod: `COPY word_idf TO '/tmp/word_idf.csv'` |
| `nice_classes_lookup` seed | Embeddings needed | 1 hour | Export or generate minimal set |
| Test fixtures (DB-backed) | Only mock fixtures exist | 3 hours | Create factory functions that INSERT real rows |
| `conftest.py` DB setup/teardown | Does not exist | 2 hours | Create test DB, run migrations, seed, cleanup |
| Integration test files | Do not exist | 4-8 hours | Write tests for CRUD, search, watchlist scan |

**Total Tier 2 effort: ~16-20 hours**

### Tier 3: Nice-to-Have (full pipeline)

| Item | Status | Effort | Notes |
|------|--------|--------|-------|
| ML models in CI | ~2.5GB download | 4 hours | Cache in CI, or use tiny model stubs |
| GPU CI runner | Not available on free GitHub | N/A | Use self-hosted runner or skip GPU tests |
| Visual embedding tests | Not tested | 4 hours | Requires CLIP+DINOv2 models loaded |
| End-to-end pipeline test | Not tested | 8 hours | data_collection -> ingest -> search |
| Playwright browser tests | Not tested | 4 hours | Requires Playwright browsers installed |
| Gemini API integration | Not tested | 2 hours | Requires API key, mock or use test key |
| Email integration | Not tested | 1 hour | Use `aiosmtpd` test server or mock |

**Total Tier 3 effort: ~24+ hours**

---

## Environment Variables Needed

### Required for Unit Tests (Tier 1)

| Variable | Required For | Secret? | CI Value |
|----------|-------------|---------|----------|
| `DB_PASSWORD` | Pydantic Settings validation | Yes | `test_password_ci` |
| `AUTH_SECRET_KEY` | JWT token creation in tests | Yes | `test-secret-key-for-ci-only-32chars!!` |
| `ENVIRONMENT` | Prevent production guard | No | `testing` |
| `AI_DEVICE` | Model config (mocked) | No | `cpu` |

### Required for Integration Tests (Tier 2)

| Variable | Required For | Secret? | CI Value |
|----------|-------------|---------|----------|
| `DB_HOST` | Test database connection | No | `localhost` (Docker service) |
| `DB_PORT` | Test database connection | No | `5432` |
| `DB_NAME` | Test database name | No | `trademark_db_test` |
| `DB_USER` | Test database user | No | `turk_patent` |
| `DB_PASSWORD` | Test database auth | Yes | `test_password_ci` |
| `REDIS_HOST` | Redis cache (optional) | No | `localhost` |

### Not Needed for CI (optional features)

| Variable | Feature | Notes |
|----------|---------|-------|
| `CREATIVE_GOOGLE_API_KEY` | Gemini AI Studio | Graceful degradation if missing |
| `SMTP_USER` / `SMTP_PASSWORD` | Email notifications | Graceful degradation if missing |
| `SUPERADMIN_EMAIL` | Superadmin seeding | Optional startup action |
| `DATA_ROOT` | Trademark image files | Not accessed in unit tests |
| `PIPELINE_*` | Data pipeline config | All have safe defaults |

### Startup Validation Gates

1. **`DB_PASSWORD`** -- Pydantic `Field(alias="DB_PASSWORD")` with no default. Settings class **will not instantiate** without it. This is the single hard blocker for running any code that imports `config.settings`.
2. **`AUTH_SECRET_KEY`** in production -- If `ENVIRONMENT=production` and key is default, raises `ValueError("FATAL: You must set a unique AUTH_SECRET_KEY in production")`. Safe in CI if `ENVIRONMENT=testing`.

---

## PostgreSQL Extensions Required

| Extension | Purpose | Used In | Critical? |
|-----------|---------|---------|-----------|
| `pgvector` (vector) | Embedding similarity search (`halfvec`, `<=>` operator) | `trademarks`, `watchlist`, `generated_images`, `nice_classes_lookup` | **Yes** -- core search |
| `pg_trgm` | Trigram text similarity (`similarity()`, GiST indexes) | `trademarks` name columns, translation fields | **Yes** -- text search |
| `fuzzystrmatch` | Phonetic matching (`dmetaphone()`) | Referenced but not actively used | No -- legacy |
| `uuid-ossp` | UUID generation (`uuid_generate_v4()`) | All table primary keys | **Yes** -- schema |
| `pgcrypto` | Password hashing (`crypt()`, `gen_salt()`) | User authentication | **Yes** -- auth |

**SQLite cannot substitute** -- pgvector alone makes this impossible. CI test DB **must be PostgreSQL 16 with pgvector**.

---

## Coverage Quick Wins (No DB Needed)

Functions that could be tested with better mocking -- no real DB required.

| Module | Function | Current Status | Fix |
|--------|----------|---------------|-----|
| `risk_engine.py` | `score_pair()` | Partially tested | Add edge case tests (empty strings, None embeddings) |
| `risk_engine.py` | `_dynamic_combine()` | Tested via score_pair | Add direct tests for weight boundary conditions |
| `risk_engine.py` | `calculate_visual_similarity()` | Not directly tested | Mock numpy arrays, test composite formula |
| `utils/translation.py` | `score_translated_pair()` | 49% covered | Add tests for edge cases (same language, empty input) |
| `utils/settings_manager.py` | `get_rate_limit_value()` | 47% covered | Mock DB returns, test cache expiry |
| `idf_lookup.py` | `IDFLookup.analyze_query()` | 40% covered | Already seeded in conftest, add query analysis tests |
| `api/routes.py` | Input validation paths | 21% covered | Add more 422 error tests for each endpoint |
| `auth/authentication.py` | `require_superadmin()` | 66% covered | Test decorator behavior with non-superadmin |
| `agentic_search.py` | `search()` orchestration | 17% covered | Mock RiskEngine, test search flow logic |

**Estimated effort: 8-12 hours for ~15-20% coverage improvement**

---

## Coverage Requires Test DB

Functions that genuinely need a real PostgreSQL to test properly.

| Module | Function | Why |
|--------|----------|-----|
| `risk_engine.py` | `RiskEngine.assess_brand_risk()` | Raw SQL with `<=>` pgvector operator, HNSW index scans |
| `risk_engine.py` | `RiskEngine.calculate_hybrid_risk()` | Joins trademarks + embeddings with vector distance |
| `risk_engine.py` | `RiskEngine.pre_screen_candidates()` | pg_trgm `similarity()` + pgvector distance |
| `database/crud.py` | All CRUD classes (23% covered) | INSERT/UPDATE/DELETE with RETURNING, jsonb, array ops |
| `api/routes.py` | All endpoint handlers (21%) | Depend on CRUD + RiskEngine with real data |
| `api/admin.py` | Settings CRUD, org management (15%) | Complex queries with ILIKE, GREATEST, jsonb |
| `api/creative.py` | Logo generation + safety audit (10%) | Inserts to `generated_images` with halfvec columns |
| `api/leads.py` | Opposition Radar queries (40%) | `universal_conflicts` table with array overlap (`&&`) |
| `watchlist/scanner.py` | `scan_single_watchlist()` (0%) | Full pipeline: fetch watchlist -> assess risk -> create alerts |
| `ingest.py` | `run_ingest()` (0%) | Bulk upsert with `ON CONFLICT`, halfvec columns |
| `idf_lookup.py` | `IDFLookup.load()` (40%) | `SELECT * FROM word_idf` (100K+ rows) |
| `utils/idf_scoring.py` | Data-driven IDF (9%) | Reads from `word_idf` table at runtime |

---

## Pre-existing Security Test Failures

**File:** `tests/test_security_audit.py` -- **19 tests, ALL failing**

### Root Causes

1. **Async/sync mismatch:** Tests use `async def` with synchronous `TestClient` -- causes I/O errors
2. **Missing fixtures:** `regular_user_headers`, `free_user_headers`, `refresh_token`, `access_token`, `deactivated_user_token`, `deactivated_org_user_token` -- none defined in `conftest.py`
3. **Stub implementations:** 15 of 19 tests have `pass` as their body (TODO placeholders)

### Test-by-Test Analysis

| Test Class | Tests | Failure Reason | Overlap With | Action |
|------------|-------|---------------|--------------|--------|
| `TestDebugEndpoints` | 2 | Async mismatch; endpoints don't exist | None | **Delete** |
| `TestAuthentication` | 3 | Missing fixtures; body is `pass` | `test_auth.py` (31 tests) | **Delete** -- duplicated |
| `TestRefreshToken` | 2 | Async mismatch; missing fixtures | None (unique coverage) | **Rewrite** -- real endpoint `/api/v1/auth/refresh` |
| `TestRateLimiting` | 2 | Body is `pass`; rate limiting not implemented | None | **Delete** -- feature doesn't exist |
| `TestFreePlanLimits` | 6 | Missing `free_user_headers`; body is `pass` | `test_plan_features.py` (94 tests) | **Delete** -- duplicated |
| `TestAdminEndpoints` | 2 | Missing `regular_user_headers`; body is `pass` | `test_api_endpoints.py` (20 tests) | **Delete** -- duplicated |
| `TestUsageSummary` | 2 | Missing fixtures; body is `pass` | None | **Delete** -- endpoint doesn't exist |

### Recommended Action

- **Delete 17 tests** (stubs with `pass` body or duplicate coverage)
- **Rewrite 2 tests** (`TestRefreshToken`) -- these test a real endpoint not covered elsewhere
- **Or simplest:** Add `--ignore=tests/test_security_audit.py` to CI and address later

---

## Recommended CI Pipeline

### Phase 1: Unit Tests (immediate -- run the 480 passing tests in CI)

**What's needed:**
1. Push repo to GitHub (currently local-only, no remote)
2. Create `.github/workflows/test.yml`
3. Set `DB_PASSWORD` and `AUTH_SECRET_KEY` as GitHub Secrets
4. Install Python dependencies (requirements-dev.txt)
5. Skip security tests (`--ignore=tests/test_security_audit.py`)

**Key challenge:** `requirements.txt` includes `torch==2.1.2` and CUDA packages (~2GB download). For unit tests where torch is fully mocked, we need a **CPU-only torch** or a **lightweight install strategy**.

**Strategy:** Install `torch` CPU-only in CI (`--index-url https://download.pytorch.org/whl/cpu`) to save ~1.5GB download and avoid CUDA dependency.

### Phase 2: Integration Tests (next sprint -- add DB-backed tests)

**What's needed:**
1. PostgreSQL 16 + pgvector Docker service in CI
2. Schema creation script (consolidate the 16 migration SQL files into one ordered script)
3. `word_idf` seed data (export ~100K rows from production, or generate minimal test set)
4. `nice_classes_lookup` seed data (45 rows with embeddings)
5. Integration test fixtures in `conftest.py` (DB setup/teardown with transaction rollback)
6. New test files: `test_crud_integration.py`, `test_search_integration.py`, `test_watchlist_integration.py`

### Phase 3: Full E2E (future -- requires self-hosted runner or large CI)

**What's needed:**
1. Self-hosted GPU runner (for CLIP/DINOv2/NLLB model loading)
2. Model caching in CI (HuggingFace cache: `~/.cache/huggingface`, Torch Hub: `~/.cache/torch`)
3. Playwright browsers installed (`playwright install chromium`)
4. Full pipeline test: `data_collection -> zip -> metadata -> ai -> ingest -> search`
5. Gemini API test key (for Creative Suite integration)

---

## Proposed GitHub Actions Config

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]

env:
  ENVIRONMENT: testing
  DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
  AUTH_SECRET_KEY: ${{ secrets.AUTH_SECRET_KEY }}
  AI_DEVICE: cpu
  USE_FP16: "false"
  USE_TF32: "false"

jobs:
  unit-tests:
    name: Unit Tests (480 mocked)
    runs-on: ubuntu-latest
    timeout-minutes: 15

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.13
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip

      - name: Install system dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y libpq-dev

      - name: Install Python dependencies
        run: |
          pip install --upgrade pip
          # Install torch CPU-only first (saves ~1.5GB vs CUDA)
          pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
          # Then install the rest (torch already satisfied, won't re-download CUDA version)
          pip install -r requirements-dev.txt

      - name: Run unit tests
        run: |
          pytest tests/ \
            --ignore=tests/test_security_audit.py \
            -v --tb=short \
            --cov=. --cov-config=.coveragerc \
            --cov-report=term-missing \
            --cov-report=xml:coverage.xml

      - name: Upload coverage
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml

  # ---------- Phase 2 (uncomment when ready) ----------
  # integration-tests:
  #   name: Integration Tests (DB)
  #   runs-on: ubuntu-latest
  #   timeout-minutes: 30
  #
  #   services:
  #     postgres:
  #       image: pgvector/pgvector:pg16
  #       env:
  #         POSTGRES_USER: turk_patent
  #         POSTGRES_PASSWORD: ${{ secrets.DB_PASSWORD }}
  #         POSTGRES_DB: trademark_db_test
  #       ports:
  #         - 5432:5432
  #       options: >-
  #         --health-cmd "pg_isready -U turk_patent"
  #         --health-interval 10s
  #         --health-timeout 5s
  #         --health-retries 5
  #
  #     redis:
  #       image: redis:7-alpine
  #       ports:
  #         - 6379:6379
  #       options: >-
  #         --health-cmd "redis-cli ping"
  #         --health-interval 10s
  #         --health-timeout 5s
  #         --health-retries 5
  #
  #   env:
  #     DB_HOST: localhost
  #     DB_PORT: 5432
  #     DB_NAME: trademark_db_test
  #     DB_USER: turk_patent
  #     DB_PASSWORD: ${{ secrets.DB_PASSWORD }}
  #     REDIS_HOST: localhost
  #
  #   steps:
  #     - uses: actions/checkout@v4
  #
  #     - name: Set up Python 3.13
  #       uses: actions/setup-python@v5
  #       with:
  #         python-version: "3.13"
  #         cache: pip
  #
  #     - name: Install dependencies
  #       run: |
  #         sudo apt-get update && sudo apt-get install -y libpq-dev
  #         pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  #         pip install -r requirements-dev.txt
  #
  #     - name: Set up test database
  #       run: |
  #         # Create extensions
  #         PGPASSWORD=$DB_PASSWORD psql -h localhost -U turk_patent -d trademark_db_test -c "
  #           CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";
  #           CREATE EXTENSION IF NOT EXISTS pg_trgm;
  #           CREATE EXTENSION IF NOT EXISTS vector;
  #           CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
  #           CREATE EXTENSION IF NOT EXISTS pgcrypto;
  #         "
  #         # Run schema (TODO: create consolidated schema.sql)
  #         # PGPASSWORD=$DB_PASSWORD psql -h localhost -U turk_patent -d trademark_db_test -f migrations/schema.sql
  #         # Seed IDF data (TODO: create seed file)
  #         # PGPASSWORD=$DB_PASSWORD psql -h localhost -U turk_patent -d trademark_db_test -f tests/fixtures/word_idf_seed.sql
  #
  #     - name: Run integration tests
  #       run: |
  #         pytest tests/integration/ -v --tb=short
```

---

## Database Schema for Integration Tests

### Current State: No Consolidated Schema

Migrations are ad-hoc SQL files with no ordering system:
- 16 `.sql` files in `migrations/`
- 11 Python runner scripts (each runs one SQL file)
- No Alembic, no version tracking
- Base schema in `backup_20260207/data/schema_v3_multitenant.sql`

### What's Needed

1. **Consolidated `schema.sql`** -- single file that creates all 30 tables in dependency order
2. **`word_idf` seed data** -- the `compute_idf.py` script exists in `backup_20260207/scripts/` but references `python compute_idf.py` which is **missing from the main project directory**. The scheduled task `scripts/compute_idf_scheduled.bat` also calls `python compute_idf.py`. For CI, we need either:
   - A CSV export of the word_idf table (`COPY word_idf TO STDOUT WITH CSV HEADER`)
   - A minimal seed with ~500 representative words (distinctive, semi-generic, generic)
3. **`nice_classes_lookup` seed** -- 45 rows with class descriptions (embeddings can be dummy vectors for integration tests)

### word_idf Table Details

| Column | Type | Purpose |
|--------|------|---------|
| `word` | `VARCHAR(255) PRIMARY KEY` | Normalized Turkish word |
| `idf_score` | `FLOAT` | Log inverse document frequency |
| `document_frequency` | `INTEGER` | Number of trademarks containing this word |
| `is_generic` | `BOOLEAN` | True if IDF < 5.3 |

- **Size:** ~100,000+ rows (computed monthly from 2.3M trademarks)
- **Populated by:** `compute_idf.py` (in backup, not in main project)
- **Loaded by:** `idf_lookup.py` `IDFLookup.load()` -- single `SELECT * FROM word_idf`
- **Test mock:** `conftest.py` `seed_idf_lookup()` populates in-memory cache directly (bypasses DB)
- **For integration tests:** Need real rows in the table, or at minimum ~500 representative entries

---

## ML Model Handling in CI

| Model | Size | Download | CI Strategy |
|-------|------|----------|-------------|
| PyTorch (CPU) | ~300MB | pip install | Install CPU-only variant (`--index-url .../cpu`) |
| CLIP ViT-B-32 | ~350MB | First use (lazy) | **Mock in unit tests** (already done in conftest.py) |
| DINOv2 ViT-B/14 | ~330MB | First use (torch.hub) | **Mock in unit tests** (already done) |
| MiniLM-L12 | ~120MB | First use (lazy) | **Mock in unit tests** (already done) |
| NLLB-200-600M | ~1.4GB | First use (lazy) | **Mock in unit tests** (already done) |
| EasyOCR | ~200MB | First use (lazy) | **Mock in unit tests** (already done) |

**Unit tests (Phase 1):** All models are already mocked in `conftest.py`. No model downloads needed.

**Integration tests (Phase 2):** Models still mocked -- DB tests don't need real ML inference.

**E2E tests (Phase 3):** Would need real models. Options:
- GitHub Actions cache (`actions/cache@v4`) with `~/.cache/huggingface` key (~2.5GB, persists across runs)
- Self-hosted runner with pre-downloaded models
- Use tiny/dummy models for CI (e.g., `hf-internal-testing/tiny-random-*`)

---

## Estimated Effort

| Phase | Effort | Dependencies | Outcome |
|-------|--------|-------------|---------|
| **Phase 1: Unit Tests in CI** | **3-4 hours** | GitHub repo + secrets | 480 tests green in CI on every push |
| **Phase 2: Integration Tests** | **16-20 hours** | Phase 1 + pgvector Docker + schema + seed data | CRUD, search, watchlist tested against real DB |
| **Phase 3: Full E2E** | **24+ hours** | Phase 2 + GPU runner + model cache + Playwright | Full pipeline from scraping to search |

### Phase 1 Breakdown

| Task | Time |
|------|------|
| Create GitHub repo + push code | 30 min |
| Set up GitHub Secrets (DB_PASSWORD, AUTH_SECRET_KEY) | 15 min |
| Write `.github/workflows/test.yml` | 1 hour |
| Test and debug CI pipeline (torch install, system deps) | 1-2 hours |
| Delete/fix `test_security_audit.py` | 30 min |
| Verify 480 tests pass in CI | 30 min |

### Phase 2 Breakdown

| Task | Time |
|------|------|
| Consolidate schema into single `schema.sql` | 3 hours |
| Export and create `word_idf` seed data | 2 hours |
| Create `nice_classes_lookup` seed data | 1 hour |
| Write `conftest.py` DB setup/teardown fixtures | 3 hours |
| Write `test_crud_integration.py` (CRUD operations) | 3 hours |
| Write `test_search_integration.py` (search pipeline) | 3 hours |
| Write `test_watchlist_integration.py` (scan flow) | 2 hours |
| Update CI workflow with postgres+redis services | 1 hour |
| Test and debug | 2 hours |

---

## Appendix A: All Test Files

| File | Tests | Lines | Coverage Target |
|------|-------|-------|-----------------|
| `test_scoring_engine.py` | 79 | 606 | idf_scoring, risk_engine |
| `test_translation_scoring.py` | 46 | 362 | utils/translation |
| `test_edge_cases.py` | 43 | 331 | risk_engine, idf_scoring edge cases |
| `test_turkish_similarity.py` | 40 | 200 | risk_engine Turkish text |
| `test_validation.py` | 34 | 243 | models/schemas |
| `test_auth.py` | 31 | 289 | auth/authentication |
| `test_subscription.py` | 29 | 359 | utils/subscription |
| `test_class_utils.py` | 27 | 272 | utils/class_utils |
| `test_translation.py` | 25 | 301 | utils/translation |
| `test_deadline.py` | 22 | 221 | utils/deadline |
| `test_api_endpoints.py` | 20 | 161 | API routing, auth gates |
| `test_settings_manager.py` | 18 | 158 | utils/settings_manager |
| `test_plan_features.py` | 14 | 94 | utils/subscription plan limits |
| `test_security_audit.py` | 19 (FAILING) | 90 | Security checks (stubs) |
| **conftest.py** | -- | 383 | Shared fixtures |

## Appendix B: Zero-Coverage Modules (0%)

These modules have **no test coverage at all** and represent the largest testing gaps:

| Module | Statements | Why Untested | Tier |
|--------|-----------|-------------|------|
| `zip.py` | 703 | 7-Zip extraction, filesystem ops | Tier 3 |
| `ai.py` | 493 | GPU model loading, embedding generation | Tier 3 |
| `data_collection.py` | 485 | Playwright browser scraping | Tier 3 |
| `ingest.py` | 445 | PostgreSQL bulk upsert with pgvector | Tier 2 |
| `scrapper.py` | 388 | Live Playwright scraping | Tier 3 |
| `metadata.py` | 329 | HSQLDB SQL parser | Tier 2 |
| `workers/pipeline_worker.py` | 328 | Pipeline orchestrator | Tier 2 |
| `watchlist/scanner.py` | 293 | Watchlist conflict scanning | Tier 2 |
| `logging_config.py` | 238 | Structured logging setup | Tier 1 (quick win) |
| `workers/universal_scanner.py` | 220 | Opposition Radar scanner | Tier 2 |
| `reports/generator.py` | 213 | PDF report generation | Tier 2 |
| `workers/monitoring_worker.py` | 196 | Alert monitoring | Tier 2 |
| `pool.py` / `db/pool.py` | 187 | DB connection pool | Tier 2 |
| `ai/gemini_client.py` | 156 | Gemini API client | Tier 2 |
| `notifications/service.py` | 144 | Email notifications | Tier 3 |
| `workers/credit_reset.py` | 95 | Credit reset worker | Tier 3 |
| `workers/pipeline_scheduler.py` | 75 | Pipeline scheduler | Tier 3 |
| `utils/seed_settings.py` | 40 | Settings seeder | Tier 1 (quick win) |
| `utils/superadmin.py` | 26 | Superadmin seeder | Tier 1 (quick win) |

## Appendix C: PostgreSQL-Specific Features Count

**Total PostgreSQL-specific SQL patterns found: 155+ `execute()` calls**

| Feature | Count | Used In |
|---------|-------|---------|
| `RETURNING` clause | 19 | database/crud.py, api/admin.py, api/creative.py, api/leads.py |
| `ON CONFLICT` (upsert) | 8+ | ingest.py, database/crud.py |
| `::halfvec` / `::vector` cast | 10+ | ingest.py, creative_suite.sql, enhance_logo_visual.sql |
| `ILIKE` | 6 | api/admin.py |
| `jsonb` / `::jsonb` | 8+ | database/crud.py, api/leads.py |
| `&&` (array overlap) | 2 | api/creative.py, api/leads.py |
| `GREATEST()` | 2 | api/admin.py |
| `%s` parameterized | All | Every raw SQL query uses parameterized queries |

**Conclusion:** SQLite is completely unsuitable. PostgreSQL 16 with pgvector is the only viable test DB option.
