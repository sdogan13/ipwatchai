# IP WATCH AI - Complete Technical Documentation

**Version:** 3.0.0
**Last Updated:** 2026-01-21
**Platform:** Windows 11 with RTX 4070 Ti Super (16GB VRAM)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Complete File Reference](#3-complete-file-reference)
4. [Database Schema](#4-database-schema)
5. [API Reference](#5-api-reference)
6. [Core Features](#6-core-features)
7. [Frontend Documentation](#7-frontend-documentation)
8. [Configuration](#8-configuration)
9. [Deployment](#9-deployment)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. System Overview

**IP Watch AI** is an AI-powered trademark risk assessment platform for the Turkish market. It evaluates trademark conflict risk using:

- **2.3M+ trademark records** from Turkish Patent Office
- **Multi-modal AI analysis**: Text embeddings (MiniLM), visual embeddings (CLIP, DINOv2)
- **Real-time monitoring**: Watchlist scanning with automatic alerts
- **Live investigation**: Web scraping for fresh data

### Key Capabilities

| Feature | Description |
|---------|-------------|
| Trademark Search | Hybrid text + semantic + visual search |
| Risk Assessment | IDF-weighted scoring with Turkish normalization |
| Watchlist Monitoring | Continuous conflict detection |
| Auto Class Suggestion | AI-powered Nice class recommendations |
| Multi-tenant Support | Organization-based data isolation |
| Alert Management | Severity-based notifications |

### Hardware Requirements

```
GPU: NVIDIA RTX 4070 Ti Super (16GB VRAM, 8448 CUDA cores)
RAM: 64GB
Storage: 2TB SSD
OS: Windows 11 (with WSL2 available)
```

---

## 2. Architecture

### Current Architecture

```
Monolithic Python Application
├── main.py                    # FastAPI entry point
├── api/
│   ├── routes.py              # All REST endpoints
│   └── upload.py              # File upload handling
├── auth/
│   └── authentication.py      # JWT & API key auth
├── config/
│   └── settings.py            # Pydantic configuration
├── database/
│   └── crud.py                # Database operations
├── models/
│   └── schemas.py             # Pydantic models
├── utils/
│   └── scoring.py             # Similarity algorithms
├── watchlist/
│   └── scanner.py             # Conflict detection
├── ai.py                      # CLIP + DINOv2 + MiniLM
├── risk_engine.py             # Risk scoring
├── scrapper.py                # TurkPatent web scraper
├── ingest.py                  # Data ingestion
├── metadata.py                # SQL dump parser
└── agentic_search.py          # Intelligent search orchestrator
```

### Data Flow

```
User Query
    │
    ▼
┌─────────────────────┐
│   main.py (FastAPI) │
└─────────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌───────┐  ┌──────────┐
│Search │  │Watchlist │
│Engine │  │ Scanner  │
└───┬───┘  └────┬─────┘
    │           │
    ▼           ▼
┌─────────────────────┐
│   PostgreSQL + pgvector  │
│   (2.3M trademarks)      │
└─────────────────────┘
          │
          ▼
┌─────────────────────┐
│   Redis Cache       │
│   (Embeddings TTL)  │
└─────────────────────┘
```

### AI Models

| Model | Purpose | VRAM (FP16) | Output Dims |
|-------|---------|-------------|-------------|
| CLIP ViT-B-32 | Logo similarity | ~400MB | 512 |
| DINOv2 ViT-B/14 | Visual features | ~350MB | 768 |
| MiniLM-L12 | Text semantics | ~120MB | 384 |
| EasyOCR | Logo text extraction | Variable | Text |
| CrossEncoder | Re-ranking | ~200MB | Score |

---

## 3. Complete File Reference

### 3.1 Root Directory Files

#### main.py
**Path:** `C:\Users\701693\turk_patent\main.py`
**Lines:** 2060
**Purpose:** Main FastAPI application entry point

**Key Components:**
- Application lifecycle management (startup/shutdown)
- CORS middleware configuration
- Router registrations (auth, users, org, watchlist, alerts, reports, dashboard)
- Global exception handling
- Image serving endpoints
- Search endpoints (simple, unified, enhanced)
- Nice class management

**Routers Included:**
- `auth_router` - Authentication (prefix: /api/v1)
- `users_router` - User management (prefix: /api/v1)
- `org_router` - Organization (prefix: /api/v1)
- `watchlist_router` - Watchlist (prefix: /api/v1)
- `alerts_router` - Alerts (prefix: /api/v1)
- `reports_router` - Reports (prefix: /api/v1)
- `dashboard_router` - Dashboard (prefix: /api/v1)
- `upload_router` - File uploads
- `agentic_router` - Agentic search

---

#### ai.py
**Path:** `C:\Users\701693\turk_patent\ai.py`
**Lines:** 635
**Purpose:** GPU-accelerated AI pipeline for embeddings

**Models Loaded:**
- OpenCLIP ViT-B-32 (laion2b_s34b_b79k)
- DINOv2 ViT-B/14 (Facebook Research)
- Sentence-Transformers MiniLM-L12-v2
- EasyOCR (optional)

**Key Functions:**
- `get_text_embedding_cached(text)` - Cached text embeddings
- `get_clip_embedding_cached(image_path)` - Cached CLIP embeddings
- `get_dino_embedding_cached(image_path)` - Cached DINOv2 embeddings
- `process_batch(records)` - Batch embedding generation

**Optimizations:**
- FP16 precision (2x speedup)
- TF32 on Ampere GPUs
- Redis caching (24h TTL)
- Batch processing (size=64)

---

#### risk_engine.py
**Path:** `C:\Users\701693\turk_patent\risk_engine.py`
**Lines:** 843
**Purpose:** Core risk assessment engine

**Key Methods:**
- `assess_brand_risk(name, image_path, target_classes)` - Main assessment
- `calculate_enhanced_score()` - IDF-weighted scoring
- `suggest_classes(description)` - Auto class suggestions
- `run_live_investigation()` - Scrape + analyze new data

**Scoring Components:**
- Text similarity (Turkish normalized)
- Semantic similarity (embeddings)
- Visual similarity (CLIP/DINOv2)
- Phonetic matching (Double Metaphone)

---

#### scrapper.py
**Path:** `C:\Users\701693\turk_patent\scrapper.py`
**Lines:** 693
**Purpose:** TurkPatent web scraper via Playwright

**Key Methods:**
- `search_and_ingest(trademark_name, limit)` - Main scraping
- `_detect_grid()` - Auto-detect page structure
- `_scrape_current_view()` - Extract visible rows
- `_jiggle_recovery()` - Scroll recovery

**Features:**
- Headless Chromium automation
- Intelligent scroll handling
- DevExtreme/CDK grid support
- Skip list for placeholder terms

---

#### ingest.py
**Path:** `C:\Users\701693\turk_patent\ingest.py`
**Lines:** 800
**Purpose:** Data ingestion pipeline

**Key Functions:**
- `process_file_batch(conn, file_path)` - Main processor
- `determine_status(folder_name, status_raw)` - Status mapping
- `check_and_migrate_schema(conn)` - Schema management

**Conflict Resolution:**
- APP_ folders: Always overwrite
- BLT_ folders: Fill gaps only
- GZ_ folders: Fill gaps only
- Status ranking: Renewed > Registered > Published > Applied

---

#### metadata.py
**Path:** `C:\Users\701693\turk_patent\metadata.py`
**Lines:** 562
**Purpose:** HSQLDB SQL dump parser

**Key Functions:**
- `parse_tmbulletin_files()` - Main parser
- `parse_sql_values()` - SQL value extraction
- `clean_table_name()` - Table normalization

**Supported Formats:**
- `.script` files
- `.log` files
- `.txt` files
- Multi-encoding (UTF-8, CP1254, Latin-1)

---

#### agentic_search.py
**Path:** `C:\Users\701693\turk_patent\agentic_search.py`
**Lines:** 760
**Purpose:** Intelligent search orchestration

**Pipeline:**
1. Search local database
2. Check confidence threshold
3. Trigger live scrape if needed
4. Generate embeddings
5. Ingest to database
6. Recalculate score

---

### 3.2 API Module

#### api/routes.py
**Path:** `C:\Users\701693\turk_patent\api\routes.py`
**Lines:** 1100+
**Purpose:** All REST API endpoints

**Routers Defined:**
- `auth_router` - Authentication (/auth)
- `users_router` - User management (/users)
- `org_router` - Organization (/organization)
- `watchlist_router` - Watchlist (/watchlist)
- `alerts_router` - Alerts (/alerts)
- `reports_router` - Reports (/reports)
- `dashboard_router` - Dashboard (/dashboard)

**Key Endpoints:**
- POST /auth/register - User registration
- POST /auth/login - User authentication
- GET /watchlist - List watchlist items
- POST /watchlist/upload - Bulk upload
- POST /watchlist/upload/detect-columns - Column detection
- POST /watchlist/upload/with-mapping - Upload with mapping
- GET /alerts - List alerts with filtering
- POST /alerts/{id}/acknowledge - Acknowledge alert

---

#### api/upload.py
**Path:** `C:\Users\701693\turk_patent\api\upload.py`
**Lines:** 300
**Purpose:** Alternative upload endpoint

---

### 3.3 Authentication Module

#### auth/authentication.py
**Path:** `C:\Users\701693\turk_patent\auth\authentication.py`
**Lines:** 333
**Purpose:** JWT authentication and RBAC

**Functions:**
- `hash_password()` - Bcrypt hashing
- `verify_password()` - Password verification
- `create_token_pair()` - JWT generation
- `get_current_user()` - FastAPI dependency

**Token Configuration:**
- Algorithm: HS256
- Access token: 30 minutes
- Refresh token: 7 days

---

### 3.4 Database Module

#### database/crud.py
**Path:** `C:\Users\701693\turk_patent\database\crud.py`
**Lines:** 840
**Purpose:** Database CRUD operations

**Classes:**
- `Database` - Connection context manager
- `OrganizationCRUD` - Organization operations
- `UserCRUD` - User operations
- `WatchlistCRUD` - Watchlist operations
- `AlertCRUD` - Alert operations
- `ScanLogCRUD` - Scan tracking

---

### 3.5 Models Module

#### models/schemas.py
**Path:** `C:\Users\701693\turk_patent\models\schemas.py`
**Lines:** 597
**Purpose:** Pydantic request/response models

**Enums:**
- `PlanType` - FREE, STARTER, PROFESSIONAL, ENTERPRISE
- `UserRole` - OWNER, ADMIN, MEMBER, VIEWER
- `AlertSeverity` - CRITICAL, HIGH, MEDIUM, LOW
- `AlertStatus` - NEW, SEEN, ACKNOWLEDGED, RESOLVED, DISMISSED
- `TrademarkStatus` - APPLIED, PUBLISHED, REGISTERED, etc.

**Key Models:**
- `OrganizationCreate`, `OrganizationResponse`
- `UserCreate`, `UserResponse`, `UserProfile`
- `WatchlistItemCreate`, `WatchlistItemResponse`
- `AlertResponse`, `AlertScores`
- `FileUploadResult`, `ColumnDetectionResponse`

---

### 3.6 Config Module

#### config/settings.py
**Path:** `C:\Users\701693\turk_patent\config\settings.py`
**Lines:** 181
**Purpose:** Centralized configuration

**Settings Classes:**
- `DatabaseSettings` - PostgreSQL config
- `RedisSettings` - Redis config
- `AuthSettings` - JWT config
- `AISettings` - Model config
- `MonitoringSettings` - Scanning thresholds
- `EmailSettings` - SMTP config
- `PathSettings` - File paths
- `Settings` - Main aggregator

---

### 3.7 Utils Module

#### utils/scoring.py
**Path:** `C:\Users\701693\turk_patent\utils\scoring.py`
**Lines:** 305
**Purpose:** Similarity scoring algorithms

**Functions:**
- `normalize_turkish(text)` - Turkish character normalization
- `calculate_text_similarity(query, target)` - IDF-weighted scoring
- `calculate_combined_score()` - Multi-modal aggregation
- `get_risk_level(score)` - Risk classification

**Generic Words (Low IDF):**
patent, marka, ticaret, limited, ltd, sirket, holding, company, gida, tekstil

---

### 3.8 Watchlist Module

#### watchlist/scanner.py
**Path:** `C:\Users\701693\turk_patent\watchlist\scanner.py`
**Lines:** 654
**Purpose:** Watchlist conflict detection

**Key Methods:**
- `scan_new_trademarks()` - Scan new TMs against all watchlists
- `scan_single_watchlist()` - Scan single watchlist item
- `_check_conflict()` - Multi-modal conflict detection
- `_calculate_textual_score()` - Text + semantic scoring
- `_calculate_visual_score()` - CLIP + DINO + color + OCR

---

### 3.9 Frontend

#### frontend/dist/index.html
**Path:** `C:\Users\701693\turk_patent\frontend\dist\index.html`
**Lines:** 7800+
**Purpose:** Single-page application

**Sections:**
- Public landing page
- Dashboard (authenticated)
- Watchlist management
- Alert management
- File upload with column mapping
- Search interface

**Frameworks:**
- Tailwind CSS
- Vanilla JavaScript
- No React/Vue/Angular

---

## 4. Database Schema

### PostgreSQL Configuration

```
Host: 127.0.0.1
Port: 5432
Database: trademark_db
User: turk_patent
Extensions: pgvector, pg_trgm, fuzzystrmatch
```

### Tables

#### trademarks
```sql
CREATE TABLE trademarks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_no VARCHAR(255) UNIQUE NOT NULL,
    name TEXT NOT NULL,
    current_status tm_status DEFAULT 'Unknown',
    nice_class_numbers INTEGER[],
    application_date DATE,
    registration_date DATE,
    bulletin_no VARCHAR(255),
    bulletin_date DATE,
    gazette_no VARCHAR(255),
    gazette_date DATE,
    expiry_date DATE,
    appeal_deadline DATE,
    image_path TEXT,
    image_embedding halfvec(512),       -- CLIP
    dinov2_embedding halfvec(768),      -- DINOv2
    text_embedding halfvec(384),        -- MiniLM
    color_histogram halfvec(32),
    logo_ocr_text TEXT,
    extracted_goods JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

#### organizations
```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug VARCHAR(100) UNIQUE,
    email TEXT,
    phone TEXT,
    address TEXT,
    settings JSONB,
    subscription_plan_id UUID,
    default_alert_threshold FLOAT DEFAULT 0.7,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### users
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES organizations(id),
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    role user_role DEFAULT 'member',
    is_active BOOLEAN DEFAULT TRUE,
    is_email_verified BOOLEAN DEFAULT FALSE,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### watchlist_mt
```sql
CREATE TABLE watchlist_mt (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES organizations(id),
    user_id UUID REFERENCES users(id),
    brand_name TEXT NOT NULL,
    nice_class_numbers INTEGER[],
    description TEXT,
    alert_threshold FLOAT DEFAULT 0.7,
    customer_application_no TEXT,
    customer_bulletin_no TEXT,
    text_embedding halfvec(384),
    logo_embedding halfvec(512),
    is_active BOOLEAN DEFAULT TRUE,
    last_scan_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### alerts_mt
```sql
CREATE TABLE alerts_mt (
    id UUID PRIMARY KEY,
    organization_id UUID REFERENCES organizations(id),
    watchlist_item_id UUID REFERENCES watchlist_mt(id),
    conflicting_trademark_id UUID REFERENCES trademarks(id),
    conflicting_name TEXT,
    conflicting_application_no TEXT,
    conflicting_classes INTEGER[],
    overall_risk_score FLOAT,
    text_similarity_score FLOAT,
    visual_similarity_score FLOAT,
    phonetic_match BOOLEAN,
    severity alert_severity,
    status alert_status DEFAULT 'new',
    resolution_notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Indexes

```sql
-- Trigram index for text search
CREATE INDEX idx_tm_name_trgm ON trademarks USING GiST (name gist_trgm_ops);

-- Vector indexes (HNSW)
CREATE INDEX idx_tm_text_emb ON trademarks USING hnsw (text_embedding halfvec_cosine_ops);
CREATE INDEX idx_tm_image_emb ON trademarks USING hnsw (image_embedding halfvec_cosine_ops);

-- Standard indexes
CREATE INDEX idx_tm_app_no ON trademarks (application_no);
CREATE INDEX idx_tm_status ON trademarks (current_status);
CREATE INDEX idx_alerts_org ON alerts_mt (organization_id);
CREATE INDEX idx_alerts_status ON alerts_mt (status);
```

---

## 5. API Reference

### Authentication Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /api/v1/auth/register | Register new user |
| POST | /api/v1/auth/login | User login |
| POST | /api/v1/auth/refresh | Refresh tokens |
| POST | /api/v1/auth/change-password | Change password |
| GET | /api/v1/auth/me | Get current user profile |

### Watchlist Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | /api/v1/watchlist | List watchlist items |
| POST | /api/v1/watchlist | Create watchlist item |
| GET | /api/v1/watchlist/upload/template | Download template |
| POST | /api/v1/watchlist/upload/detect-columns | Detect file columns |
| POST | /api/v1/watchlist/upload/with-mapping | Upload with mapping |
| POST | /api/v1/watchlist/upload | Upload with auto-detection |
| POST | /api/v1/watchlist/scan-all | Scan all items |
| DELETE | /api/v1/watchlist/all | Delete all items |
| GET | /api/v1/watchlist/{id} | Get item details |
| PUT | /api/v1/watchlist/{id} | Update item |
| DELETE | /api/v1/watchlist/{id} | Delete item |
| POST | /api/v1/watchlist/{id}/scan | Trigger scan |

### Alert Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | /api/v1/alerts | List alerts with filtering |
| GET | /api/v1/alerts/summary | Get alert summary |
| GET | /api/v1/alerts/{id} | Get alert details |
| POST | /api/v1/alerts/{id}/acknowledge | Acknowledge alert |
| POST | /api/v1/alerts/{id}/resolve | Resolve alert |
| POST | /api/v1/alerts/{id}/dismiss | Dismiss alert |

### Search Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | /api/search/simple | Simple text search |
| POST | /api/search-by-image | Image-based search |
| POST | /api/search/unified | Combined search |
| POST | /api/search | Enhanced search |

---

## 6. Core Features

### 6.1 Trademark Search

**Multi-Modal Search:**
1. Text similarity (Turkish normalized)
2. Semantic similarity (MiniLM embeddings)
3. Visual similarity (CLIP + DINOv2)
4. Phonetic matching (Double Metaphone)

**Scoring Weights:**
- All available: Text 50%, Image 30%, Semantic 20%
- No image: Text 60%, Semantic 40%
- Text only: Text 100%

### 6.2 Risk Assessment

**Risk Levels:**
| Score | Level | Color |
|-------|-------|-------|
| >= 0.80 | Critical | Red |
| >= 0.65 | High | Orange |
| >= 0.50 | Medium | Yellow |
| < 0.50 | Low | Green |

### 6.3 Watchlist Monitoring

**Scan Process:**
1. Load watchlist item
2. Find similar trademarks
3. Calculate multi-modal scores
4. Check class overlap
5. Generate alert if threshold exceeded
6. Update scan timestamp

### 6.4 File Upload with Column Mapping

**Flow:**
1. User uploads file
2. System detects columns
3. Auto-mapping attempted
4. If columns missing, show mapping UI
5. User maps columns manually
6. Upload with custom mapping

---

## 7. Frontend Documentation

### Single Page Application

**Sections:**
- Landing page (public)
- Login/Register modals
- Dashboard (authenticated)
- Watchlist tab
- Alerts tab
- Upload tab
- Search interface

### Key Components

**Dashboard Object:**
```javascript
const Dashboard = {
    token: null,
    user: null,

    // Initialization
    init(),
    loadStats(),
    loadWatchlist(),
    loadAlerts(),

    // File Upload
    handleFileUpload(file),
    showColumnMappingModal(data),
    submitWithMapping(mappings),

    // Alert Management
    acknowledgeAlert(id),
    resolveAlert(id),
    dismissAlert(id)
}
```

### CSS Framework

- Tailwind CSS (CDN)
- Custom glass morphism effects
- Risk level color coding
- Responsive design

---

## 8. Configuration

### Environment Variables

```bash
# Database
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=trademark_db
DB_USER=turk_patent
DB_PASSWORD=***

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=

# Auth
AUTH_SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# AI
AI_DEVICE=cuda
USE_FP16=true
USE_TF32=true
CLIP_BATCH_SIZE=64

# Monitoring
DEFAULT_SIMILARITY_THRESHOLD=0.70
CRITICAL_THRESHOLD=0.90
HIGH_THRESHOLD=0.75
```

### File Paths

```
DATA_ROOT=C:\Users\701693\turk_patent\bulletins\Marka
UPLOAD_DIR=C:\Users\701693\turk_patent\uploads
LOG_DIR=C:\Users\701693\turk_patent\logs
FRONTEND_DIR=C:\Users\701693\turk_patent\frontend\dist
```

---

## 9. Deployment

### Development

```bash
# Start server
python main.py

# Or with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Production

```bash
# Without --reload
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Docker (Optional)

```bash
docker-compose up -d
```

---

## 10. Troubleshooting

### Common Issues

**1. Port 8000 already in use**
```bash
# Find process
netstat -ano | findstr :8000

# Kill process
taskkill /PID <pid> /F
```

**2. GPU not detected**
```bash
# Check CUDA
python -c "import torch; print(torch.cuda.is_available())"
```

**3. Redis connection failed**
```bash
# Check Redis
redis-cli ping
```

**4. Database connection failed**
```bash
# Check PostgreSQL
psql -U turk_patent -d trademark_db -c "SELECT 1"
```

**5. Column mapping modal not showing**
- Clear browser cache (Ctrl+Shift+R)
- Restart server (python main.py)
- Check browser console for errors

---

## Appendix A: File Tree

```
C:\Users\701693\turk_patent\
├── main.py                     # FastAPI entry point (2060 lines)
├── ai.py                       # AI pipeline (635 lines)
├── risk_engine.py              # Risk scoring (843 lines)
├── scrapper.py                 # Web scraper (693 lines)
├── ingest.py                   # Data ingestion (800 lines)
├── metadata.py                 # SQL parser (562 lines)
├── agentic_search.py           # Search orchestrator (760 lines)
├── api/
│   ├── __init__.py
│   ├── routes.py               # REST endpoints (1100+ lines)
│   └── upload.py               # Upload handling (300 lines)
├── auth/
│   ├── __init__.py
│   └── authentication.py       # JWT auth (333 lines)
├── config/
│   ├── __init__.py
│   └── settings.py             # Configuration (181 lines)
├── database/
│   ├── __init__.py
│   └── crud.py                 # CRUD operations (840 lines)
├── models/
│   ├── __init__.py
│   └── schemas.py              # Pydantic models (597 lines)
├── utils/
│   ├── __init__.py
│   └── scoring.py              # Scoring algorithms (305 lines)
├── watchlist/
│   ├── __init__.py
│   └── scanner.py              # Conflict detection (654 lines)
├── frontend/
│   └── dist/
│       └── index.html          # SPA frontend (7800+ lines)
├── bulletins/
│   └── Marka/                  # Trademark data
├── requirements.txt            # Python dependencies
├── docker-compose.yml          # Docker config
└── .env                        # Environment variables
```

---

## Appendix B: Dependency Graph

```
main.py
├── api/routes.py
│   ├── auth/authentication.py
│   ├── database/crud.py
│   └── models/schemas.py
├── risk_engine.py
│   ├── ai.py
│   └── utils/scoring.py
├── agentic_search.py
│   ├── risk_engine.py
│   ├── scrapper.py
│   └── ingest.py
└── watchlist/scanner.py
    ├── database/crud.py
    └── utils/scoring.py
```

---

**End of Documentation**
