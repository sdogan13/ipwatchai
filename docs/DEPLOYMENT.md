# IP WATCH AI - Deployment Guide

Complete deployment instructions for IP Watch AI system.

## Prerequisites

### Hardware Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 32GB | 64GB |
| GPU | RTX 3060 (12GB) | RTX 4070 Ti Super (16GB) |
| Storage | 500GB SSD | 2TB NVMe SSD |

### Software Requirements

- Windows 11 with WSL2 or Ubuntu 22.04
- Docker Desktop with WSL2 backend
- NVIDIA Driver 535+
- NVIDIA Container Toolkit
- Python 3.10+
- PostgreSQL 16 with pgvector
- Redis 7+

---

## Quick Start (Docker)

### 1. Clone Repository

```bash
git clone https://github.com/your-org/ip-watch-ai.git
cd ip-watch-ai
```

### 2. Configure Environment

```bash
cp .env.example .env.production
```

Edit `.env.production`:

```env
# Application
ENVIRONMENT=production
DEBUG=false

# Database
DB_HOST=host.docker.internal
DB_PORT=5432
DB_NAME=trademark_db
DB_USER=turk_patent
DB_PASSWORD=your_secure_password

# Redis
REDIS_PASSWORD=your_redis_password

# Auth
AUTH_SECRET_KEY=your_256_bit_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# AI Settings
AI_DEVICE=cuda
USE_FP16=true
CLIP_BATCH_SIZE=64
```

### 3. Start Services

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f backend

# Start with development tools
docker-compose --profile dev up -d
```

### 4. Verify Deployment

```bash
# Check health
curl http://localhost:8000/health

# Expected response
{"status": "healthy", "database": "connected", "redis": "connected"}
```

---

## Manual Installation

### 1. PostgreSQL Setup

```bash
# Install PostgreSQL 16
sudo apt install postgresql-16 postgresql-16-pgvector

# Create database
sudo -u postgres createuser -P turk_patent
sudo -u postgres createdb -O turk_patent trademark_db

# Enable extensions
psql -U turk_patent -d trademark_db -f schema.sql
```

### 2. Redis Setup

```bash
# Install Redis
sudo apt install redis-server

# Configure for production
sudo nano /etc/redis/redis.conf
```

```conf
maxmemory 4gb
maxmemory-policy allkeys-lru
appendonly yes
```

### 3. Python Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux
.\venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install CUDA-enabled PyTorch (if not already)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

### 4. NVIDIA Setup (GPU)

```bash
# Verify NVIDIA driver
nvidia-smi

# Install CUDA Toolkit 12.1
# Download from: https://developer.nvidia.com/cuda-downloads

# Verify PyTorch CUDA
python -c "import torch; print(torch.cuda.is_available())"
```

### 5. Start Application

```bash
# Development
python main.py

# Production with Gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

---

## Docker Services

### Service Architecture

```
                    ┌─────────────────┐
                    │   Cloudflare    │
                    │    Tunnel       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │     Nginx       │
                    │  (Port 80)      │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼────┐  ┌──────▼─────┐  ┌─────▼─────┐
     │   Backend   │  │   Redis    │  │  Worker   │
     │  (Port 8000)│  │ (Port 6379)│  │  (Async)  │
     └──────┬──────┘  └────────────┘  └───────────┘
            │
     ┌──────▼──────┐
     │  PostgreSQL │
     │ (Port 5432) │
     └─────────────┘
```

### Container Details

| Service | Image | Ports | Purpose |
|---------|-------|-------|---------|
| backend | Custom | 8000 | FastAPI + AI models |
| redis | redis:7-alpine | 6379 | Cache + Queue |
| nginx | nginx:alpine | 80 | Reverse proxy |
| cloudflared | cloudflare/cloudflared | - | HTTPS tunnel |
| postgres | pgvector/pgvector:pg16 | 5432 | Database |
| worker | Custom | - | Background jobs |

### Docker Commands

```bash
# Start all services
docker-compose up -d

# Start with database (new installation)
docker-compose --profile with-db up -d

# Start with worker
docker-compose --profile with-worker up -d

# Start with dev tools (pgAdmin, Redis Commander)
docker-compose --profile dev up -d

# View logs
docker-compose logs -f backend
docker-compose logs -f worker

# Restart single service
docker-compose restart backend

# Stop all
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

---

## Cloudflare Tunnel Setup

### 1. Create Tunnel

```bash
# Login to Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create ipwatch

# Note the tunnel ID and credentials
```

### 2. Configure Tunnel

Create `cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json

ingress:
  - hostname: ipwatchai.com
    service: http://nginx:80
  - hostname: api.ipwatchai.com
    service: http://backend:8000
  - service: http_status:404
```

### 3. DNS Configuration

Add CNAME records in Cloudflare DNS:

```
ipwatchai.com     CNAME  <tunnel-id>.cfargotunnel.com
api.ipwatchai.com CNAME  <tunnel-id>.cfargotunnel.com
```

---

## Nginx Configuration

Create `nginx/nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    upstream backend {
        server backend:8000;
    }

    server {
        listen 80;
        server_name localhost;

        # Frontend
        location / {
            root /usr/share/nginx/html;
            try_files $uri $uri/ /index.html;
        }

        # API proxy
        location /api/ {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # WebSocket support
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }

        # Health check
        location /health {
            proxy_pass http://backend/health;
        }

        # Static images
        location /images/ {
            alias /app/bulletins/Marka/;
            expires 7d;
            add_header Cache-Control "public, immutable";
        }
    }
}
```

---

## Database Migration

### Initial Setup

```bash
# Apply schema
psql -U turk_patent -d trademark_db -f schema.sql

# For multi-tenant
psql -U turk_patent -d trademark_db -f schema_v3_multitenant.sql
```

### Migration Scripts

```bash
# Run migrations
python migrate_v3.py

# Verify migration
python -c "from database.crud import verify_schema; verify_schema()"
```

---

## Data Ingestion

### Process Bulletins

```bash
# Download bulletins
python scrapper.py --download-all

# Extract archives
python pipeline.py --extract

# Parse metadata
python metadata.py --process-all

# Generate embeddings
python ai.py --batch-process

# Ingest to database
python ingest.py --full
```

### Pipeline Orchestration

```bash
# Full pipeline
python pipeline.py --full

# Incremental update
python pipeline.py --incremental
```

---

## Monitoring

### Health Endpoints

```bash
# Basic health
curl http://localhost:8000/health

# Detailed health
curl http://localhost:8000/health/detailed
```

### Logs

```bash
# Application logs
tail -f logs/app.log

# Docker logs
docker-compose logs -f --tail=100 backend
```

### GPU Monitoring

```bash
# Real-time GPU usage
watch -n 1 nvidia-smi

# Memory usage
nvidia-smi --query-gpu=memory.used,memory.free --format=csv -l 1
```

---

## Backup & Recovery

### Database Backup

```bash
# Full backup
pg_dump -U turk_patent -d trademark_db -F c -f backup_$(date +%Y%m%d).dump

# Restore
pg_restore -U turk_patent -d trademark_db backup_20240120.dump
```

### Redis Backup

```bash
# Trigger save
redis-cli BGSAVE

# Copy RDB file
cp /var/lib/redis/dump.rdb /backup/redis_$(date +%Y%m%d).rdb
```

### Volume Backup (Docker)

```bash
# Backup volumes
docker run --rm -v ipwatch_postgres_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/postgres_data.tar.gz /data
```

---

## SSL/TLS Configuration

### With Cloudflare Tunnel

SSL is handled automatically by Cloudflare. No additional configuration needed.

### Self-Signed (Development)

```bash
# Generate certificates
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/key.pem -out nginx/ssl/cert.pem
```

### Let's Encrypt (Direct)

```bash
# Install certbot
apt install certbot python3-certbot-nginx

# Obtain certificate
certbot --nginx -d ipwatchai.com -d api.ipwatchai.com
```

---

## Scaling

### Horizontal Scaling

```yaml
# docker-compose.override.yml
services:
  backend:
    deploy:
      replicas: 4
      resources:
        limits:
          cpus: '2'
          memory: 8G
```

### Load Balancing

```nginx
upstream backend {
    least_conn;
    server backend1:8000;
    server backend2:8000;
    server backend3:8000;
}
```

---

## Troubleshooting

### Common Issues

**Port already in use:**
```bash
# Find process
netstat -ano | findstr :8000
# Kill process
taskkill /PID <pid> /F
```

**GPU not detected:**
```bash
# Check driver
nvidia-smi

# Check CUDA in container
docker exec ipwatch_backend nvidia-smi
```

**Database connection refused:**
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Check firewall
sudo ufw allow 5432
```

**Redis connection failed:**
```bash
# Check Redis status
redis-cli ping

# Check password
redis-cli -a <password> ping
```

### Log Analysis

```bash
# Search for errors
grep -i error logs/app.log | tail -50

# Watch for exceptions
tail -f logs/app.log | grep -i "exception\|error\|traceback"
```

---

## Environment Variables Reference

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Runtime environment | development |
| `DEBUG` | Debug mode | false |
| `HOST` | Server bind address | 0.0.0.0 |
| `PORT` | Server port | 8000 |
| `WORKERS` | Uvicorn workers | 4 |
| `DB_HOST` | PostgreSQL host | localhost |
| `DB_PORT` | PostgreSQL port | 5432 |
| `DB_NAME` | Database name | trademark_db |
| `DB_USER` | Database user | turk_patent |
| `DB_PASSWORD` | Database password | - |
| `REDIS_HOST` | Redis host | localhost |
| `REDIS_PORT` | Redis port | 6379 |
| `REDIS_PASSWORD` | Redis password | - |
| `AUTH_SECRET_KEY` | JWT secret key | - |
| `AI_DEVICE` | AI compute device | cuda |
| `USE_FP16` | Half precision | true |
| `CLIP_BATCH_SIZE` | CLIP batch size | 64 |
| `DATA_ROOT` | Bulletin data path | ./bulletins/Marka |
