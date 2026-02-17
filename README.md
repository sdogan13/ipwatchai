# AI Powered Trademark Monitoring System

An AI-powered trademark monitoring platform that detects potential conflicts across 2.3M+ official Turkish trademarks using multi-modal similarity search and IDF-weighted scoring.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-Modal Search** | Text + Image + Color similarity analysis |
| **IDF-Weighted Scoring** | Smart weighting of distinctive vs generic words |
| **3-Tier Classification** | Generic (0.1), Semi-generic (0.5), Distinctive (1.0) |
| **Nice Class Filtering** | Filter by trademark classification (1-45) |
| **Agentic Search** | Auto-fetches fresh data when confidence < 75% |
| **Automated Collection** | Daily scraping from TurkPatent gazette |
| **REST API** | FastAPI endpoints for integration |
| **Email Alerts** | Automated notifications for conflicts |

## System Metrics

```
Total Trademarks in Database     | 2,298,000+
IDF Vocabulary Size              | 861,254 words
Image Embedding (CLIP)           | 512 dimensions
Image Embedding (DINOv2)         | 768 dimensions
Text Embedding (MiniLM)          | 384 dimensions
Average Search Time              | < 100ms
Agentic Threshold                | 75% confidence
```

## Tech Stack

- **Language:** Python 3.10+
- **Database:** PostgreSQL 15+ with pgvector
- **Cache:** Redis 7+
- **API:** FastAPI
- **ML Models:** CLIP ViT-B-32, DINOv2 ViT-B/14, MiniLM-L12-v2
- **Browser Automation:** Playwright

## Project Structure

```
turk_patent/
├── bulletins/Marka/             # Downloaded & processed trademark data
├── clients/                     # Customer portfolios
├── models/                      # AI model cache
├── logs/                        # Application logs
├── db/                          # Database utilities
│   └── pool.py                  # Connection pooling
├── api/                         # API routes
│   └── routes.py                # Dashboard & search endpoints
│
├── data_collection.py           # Bulk download from TurkPatent
├── zip.py                       # Extract ZIP/RAR/7z archives
├── metadata.py                  # Parse SQL to JSON
├── ai.py                        # Generate AI embeddings
├── ingest.py                    # Load to PostgreSQL
├── scrapper.py                  # Live on-demand scraping
├── risk_engine.py               # Risk analysis + agentic search
├── idf_scoring.py               # IDF-weighted scoring
├── idf_lookup.py                # Fast IDF word lookup
├── agentic_search.py            # API router for search
├── customer_pipeline.py         # End-to-end customer processing
├── customer_data_integration.py # Data extraction utilities
├── main.py                      # FastAPI application
│
├── .env                         # Environment variables
└── requirements.txt             # Python dependencies
```

## Installation

### Prerequisites

- Python 3.10+
- PostgreSQL 15+ with pgvector extension
- Redis 7+
- 7-Zip (for archive extraction)
- NVIDIA GPU with CUDA (optional)

### Setup

```bash
# Clone/navigate to project
cd C:\Users\701693\turk_patent

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env with your database credentials
```

### Database Setup

```sql
CREATE DATABASE trademark_db;
\c trademark_db
CREATE EXTENSION vector;
```

## Usage

### Start API Server

```bash
python main.py
```

The API will be available at `http://localhost:8000`

### Run Daily Data Pipeline

```bash
# Step 1: Download new bulletins
python data_collection.py

# Step 2: Extract archives
python zip.py --root "bulletins/Marka"

# Step 3: Parse SQL to JSON
python metadata.py

# Step 4: Generate embeddings
python ai.py "bulletins/Marka"

# Step 5: Ingest to database
python ingest.py
```

### Customer Portfolio Analysis

```bash
python customer_pipeline.py "customer_data.xlsx" --customer "CLIENT_NAME"
```

Output: `clients/CLIENT_NAME/RISK_ANALYSIS_REPORT.xlsx`

### Programmatic Search

```python
from risk_engine import RiskEngine

engine = RiskEngine()

# Quick search (database only)
result, needs_live = engine.assess_brand_risk("Nike", target_classes=[25])
print(f"Score: {result['final_risk_score']:.2%}")

# Full search (with live investigation if needed)
result = engine.assess_brand_risk_full("Nike", target_classes=[25])
```

## Workflows

### 1. Scheduled Data Pipeline (Daily)

```
data_collection.py -> zip.py -> metadata.py -> ai.py -> ingest.py
     |                 |           |          |          |
     v                 v           v          v          v
  Download         Extract      Parse      Generate   Insert to
  ZIP/RAR          archives     SQL->JSON  embeddings PostgreSQL
```

### 2. Agentic Search (On-Demand)

```
User Query -> risk_engine.py -> Database Search -> Score >= 75%?
                                                    |
                              +---------------------+---------------------+
                              v                                           v
                         YES: Return                              NO: Live Investigation
                         Result                                   (scrapper.py -> ai.py -> ingest.py)
```

### 3. Customer Portfolio Analysis

```
Excel Input -> Extract -> Embed -> Search DB -> Generate Risk Report
                                    |
                           (NO database ingestion)
```

## Risk Categories

| Score | Level | Action |
|-------|-------|--------|
| >= 85% | CRITICAL | Immediate review required |
| 70-85% | HIGH | Investigate |
| 50-70% | MEDIUM | Monitor |
| < 50% | LOW | Minimal concern |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/search/status` | GET | Health check |
| `/api/v1/search/quick` | GET | Fast database search |
| `/api/v1/search/intelligent` | GET | Full agentic search |
| `/api/v1/dashboard/*` | GET | Dashboard data endpoints |

### Example: Quick Search

```bash
curl "http://localhost:8000/api/v1/search/quick?q=nike&limit=10&classes=25,35"
```

## Configuration

Key environment variables in `.env`:

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=trademark_db
DB_USER=postgres
DB_PASSWORD=your_password

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# API
API_HOST=0.0.0.0
API_PORT=8000

# AI Models
USE_GPU=true
BATCH_SIZE=32
```

## Security

- JWT authentication for API endpoints
- bcrypt password hashing
- Rate limiting (100 req/min)
- Parameterized SQL queries
- KVKK (Turkish GDPR) compliance

## Documentation

Full technical documentation available in `docs/SYSTEM_DOCUMENTATION.md`

## License

Copyright 2026 Dogan Patent. All rights reserved.

## Contact

- **Technical Support:** tech@doganpatent.com
