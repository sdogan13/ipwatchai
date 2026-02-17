# CLAUDE.md - Project Context for Claude Code

This file provides context for Claude Code when working on this project.

## Project Overview

**Trademark Risk Assessment System** - AI-powered platform for evaluating trademark conflict risk in the Turkish market.

## Hardware Specifications

```
GPU: NVIDIA RTX 4070 Ti Super (16GB VRAM, 8448 CUDA cores)
RAM: 64GB
Storage: 2TB SSD
OS: Windows 11 (with WSL2 available)
```

## Current Architecture (To Be Refactored)

```
Monolithic Python Application
├── data_collection.py    # Async Playwright bulk downloader
├── zip.py                # 7-Zip archive extractor
├── metadata.py           # HSQLDB SQL parser (tmbulletin.script/.log)
├── ocr.py                # PyMuPDF PDF text extractor
├── ai.py                 # CLIP + DINOv2 + Text embeddings
├── ingest.py             # PostgreSQL upsert with pgvector
├── scrapper.py           # Live search (Playwright sync)
├── risk_engine.py        # Hybrid similarity scoring
├── dashboard_api.py      # FastAPI server
└── master.py             # Pipeline orchestrator
```

## Target Architecture (Microservices)

```
Docker Compose Orchestration
├── services/
│   ├── api/              # Async FastAPI (non-blocking)
│   ├── ai_worker/        # GPU worker (RQ/Celery)
│   ├── scraping_worker/  # Playwright background jobs
│   ├── ingestion_worker/ # Batched DB writes
│   └── metadata_worker/  # PDF/SQL extraction
├── PostgreSQL            # 16GB shared_buffers
├── Redis                 # 4GB cache + message broker
└── Nginx                 # Reverse proxy / load balancer
```

## Key Optimization Targets

| Current | Target | How |
|---------|--------|-----|
| BATCH_SIZE=16 | BATCH_SIZE=64 | Increase in ai.py |
| FP32 precision | FP16 precision | model.half() |
| No caching | Redis embeddings cache | 24hr TTL |
| Sync blocking | Async job queue | RQ + Redis |
| 45s response | <2s response | All above combined |

## Database

- **PostgreSQL 16** with pgvector extension
- Tables: trademarks, holders, nice_classes_lookup
- Indexes: HNSW (vectors), GiST (trigram), GIN (arrays)

## AI Models

| Model | Purpose | VRAM |
|-------|---------|------|
| CLIP ViT-B-32 | Logo similarity | ~400MB |
| DINOv2 ViT-B/14 | Visual features | ~350MB |
| MiniLM-L12 | Text semantics | ~120MB |

## File Paths

- Data root: `C:\Users\701693\turk_patent\bulletins\Marka`
- Archives: `./bulletins/Marka/*.zip`
- Extracted: `./bulletins/Marka/{bulletin_id}/`
- Images: `./bulletins/Marka/{bulletin_id}/images/`

## Commands Reference

```bash
# Current startup
python dashboard_api.py

# Database
psql -U turk_patent -d trademark_db

# GPU check
nvidia-smi

# Docker
docker-compose up -d
docker-compose logs -f ai-worker
```

## Performance Benchmarks (Target)

```
Text embedding (batch=64):  ~30ms
Image embedding (batch=64): ~200ms
Vector search (1M vectors): ~50ms
Full risk analysis:         <2000ms
```

## Code Style

- Python 3.10+
- Type hints required
- Async/await for I/O operations
- Pydantic for data validation
- Structured logging (JSON format)

## Testing

Run tests with:
```bash
pytest tests/ -v
pytest tests/test_ai.py -v --gpu  # GPU tests
```

## Common Tasks

### Add Redis caching to a function
```python
import redis
import hashlib
import json

redis_client = redis.Redis(host='localhost', port=6379)

def cached_embedding(text: str) -> list[float]:
    cache_key = f"emb:{hashlib.md5(text.encode()).hexdigest()}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    
    embedding = model.encode(text).tolist()
    redis_client.setex(cache_key, 86400, json.dumps(embedding))
    return embedding
```

### Enable FP16 inference
```python
import torch

# At model load time
model = model.half().to('cuda')

# Enable TF32 for RTX 30/40 series
torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
```

### Create async background task
```python
from rq import Queue
from redis import Redis

queue = Queue(connection=Redis())

# Submit job (returns immediately)
job = queue.enqueue(process_trademark, trademark_id)
return {"job_id": job.id, "status": "queued"}
```

## DO NOT

- Don't use synchronous scraping in API endpoints
- Don't load models per-request (load once at startup)
- Don't use FP32 when FP16 works (2x slower)
- Don't ignore the 16GB VRAM (batch size can be 64+)
- Don't block API for >500ms (use background jobs)

## Priority Order

1. Increase batch sizes (immediate 4x speedup)
2. Add FP16 precision (immediate 2x speedup)
3. Add Redis caching (25x for repeated queries)
4. Async job queue (non-blocking API)
5. Dockerize services (clean deployment)
