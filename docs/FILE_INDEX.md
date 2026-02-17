# IP WATCH AI - File Index

Quick reference of all files in the project.

## Core Application Files

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| main.py | /turk_patent/main.py | FastAPI entry point | ~2060 |
| ai.py | /turk_patent/ai.py | AI embedding pipeline | ~635 |
| risk_engine.py | /turk_patent/risk_engine.py | Risk assessment engine | ~843 |
| scrapper.py | /turk_patent/scrapper.py | TurkPatent web scraper | ~693 |
| ingest.py | /turk_patent/ingest.py | Data ingestion pipeline | ~800 |
| metadata.py | /turk_patent/metadata.py | SQL dump parser | ~562 |
| agentic_search.py | /turk_patent/agentic_search.py | Intelligent search orchestrator | ~760 |

## API Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/api/__init__.py | Module init | 5 |
| routes.py | /turk_patent/api/routes.py | REST API endpoints | ~1100 |
| upload.py | /turk_patent/api/upload.py | File upload handling | ~300 |

## Authentication Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/auth/__init__.py | Module init | 5 |
| authentication.py | /turk_patent/auth/authentication.py | JWT authentication | ~333 |

## Configuration Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/config/__init__.py | Module init | 5 |
| settings.py | /turk_patent/config/settings.py | Pydantic settings | ~181 |

## Database Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/database/__init__.py | Module init | 5 |
| crud.py | /turk_patent/database/crud.py | CRUD operations | ~840 |

## Models Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/models/__init__.py | Module init | 5 |
| schemas.py | /turk_patent/models/schemas.py | Pydantic models | ~597 |

## Utils Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/utils/__init__.py | Module init | ~171 |
| scoring.py | /turk_patent/utils/scoring.py | Scoring algorithms | ~305 |

## Watchlist Module

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| __init__.py | /turk_patent/watchlist/__init__.py | Module init | 5 |
| scanner.py | /turk_patent/watchlist/scanner.py | Conflict detection | ~654 |

## Frontend

| File | Path | Purpose | Lines |
|------|------|---------|-------|
| index.html | /turk_patent/frontend/dist/index.html | SPA frontend | ~7800 |

## Configuration Files

| File | Path | Purpose |
|------|------|---------|
| .env | /turk_patent/.env | Environment variables |
| .env.production | /turk_patent/.env.production | Production env |
| requirements.txt | /turk_patent/requirements.txt | Python dependencies |
| docker-compose.yml | /turk_patent/docker-compose.yml | Docker configuration |
| Dockerfile.backend | /turk_patent/Dockerfile.backend | Backend Docker image |

## SQL Files

| File | Path | Purpose |
|------|------|---------|
| schema.sql | /turk_patent/schema.sql | Database schema |
| schema_v3_multitenant.sql | /turk_patent/schema_v3_multitenant.sql | Multi-tenant schema |
| migration_v3_multitenant.sql | /turk_patent/migration_v3_multitenant.sql | Migration script |

## Documentation Files

| File | Path | Purpose |
|------|------|---------|
| README.md | /turk_patent/README.md | Project readme |
| CLAUDE.md | /turk_patent/Claude.md | Claude Code instructions |
| IP_WATCH_AI_DOCUMENTATION.md | /turk_patent/IP_WATCH_AI_DOCUMENTATION.md | System documentation |
| COMPLETE_FILE_LIST.md | /turk_patent/COMPLETE_FILE_LIST.md | File listing |

## Test Files

| File | Path | Purpose |
|------|------|---------|
| test_routes.py | /turk_patent/test_routes.py | Route tests |
| test_ui.py | /turk_patent/test_ui.py | UI tests |
| test_ui_final.py | /turk_patent/test_ui_final.py | Final UI tests |
| test_idf_scoring.py | /turk_patent/test_idf_scoring.py | Scoring tests |
| test_3tier_scoring.py | /turk_patent/test_3tier_scoring.py | 3-tier scoring tests |

## Utility Scripts

| File | Path | Purpose |
|------|------|---------|
| compute_idf.py | /turk_patent/compute_idf.py | IDF computation |
| idf_lookup.py | /turk_patent/idf_lookup.py | IDF lookup |
| idf_scoring.py | /turk_patent/idf_scoring.py | IDF scoring |
| pipeline.py | /turk_patent/pipeline.py | Pipeline orchestrator |
| worker.py | /turk_patent/worker.py | Background worker |
| logging_config.py | /turk_patent/logging_config.py | Logging configuration |

## Migration Scripts

| File | Path | Purpose |
|------|------|---------|
| migrate_simple.py | /turk_patent/migrate_simple.py | Simple migration |
| migrate_tables.py | /turk_patent/migrate_tables.py | Table migration |
| migrate_v3.py | /turk_patent/migrate_v3.py | V3 migration |
| migrate_bulletin.py | /turk_patent/migrate_bulletin.py | Bulletin migration |
| run_migration.py | /turk_patent/run_migration.py | Migration runner |

## Data Processing Scripts

| File | Path | Purpose |
|------|------|---------|
| customer_data_integration.py | /turk_patent/customer_data_integration.py | Customer data integration |
| customer_pipeline.py | /turk_patent/customer_pipeline.py | Customer pipeline |
| extract_client_data.py | /turk_patent/extract_client_data.py | Client data extraction |
| transform_client_data.py | /turk_patent/transform_client_data.py | Client data transformation |

## Total File Count

| Category | Count |
|----------|-------|
| Core Application | 7 |
| API Module | 3 |
| Auth Module | 2 |
| Config Module | 2 |
| Database Module | 2 |
| Models Module | 2 |
| Utils Module | 2 |
| Watchlist Module | 2 |
| Frontend | 1 |
| Config Files | 5 |
| SQL Files | 3 |
| Documentation | 4+ |
| Test Files | 5+ |
| Utility Scripts | 6+ |
| Migration Scripts | 5+ |
| Data Processing | 4+ |
| **Total** | **~55+ files** |
