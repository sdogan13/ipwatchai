# ============================================
# IP Watch AI - Docker Startup Script
# Run after Docker Desktop is started
# ============================================

Write-Host "=== IP Watch AI Docker Startup ===" -ForegroundColor Cyan

# Set docker path
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;$env:PATH"

# Check Docker is running
$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker is not running. Please start Docker Desktop first." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Docker is running" -ForegroundColor Green

# Check GPU availability in Docker
$gpuCheck = docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] GPU available in Docker" -ForegroundColor Green
} else {
    Write-Host "[WARN] GPU not available in Docker, will use CPU mode" -ForegroundColor Yellow
}

# Navigate to project directory
Set-Location "C:\Users\701693\turk_patent"

# Build and start services
Write-Host "`n[+] Building backend image..." -ForegroundColor Yellow
docker compose --env-file .env.production build backend

Write-Host "`n[+] Starting all services..." -ForegroundColor Yellow
docker compose --env-file .env.production up -d redis backend nginx cloudflared

Write-Host "`n[+] Waiting for services to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 30

# Check service health
Write-Host "`n=== Service Status ===" -ForegroundColor Cyan
docker compose ps

# Test health endpoint
Write-Host "`n=== Health Check ===" -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "http://localhost:8000/health" -ErrorAction SilentlyContinue
if ($health) {
    Write-Host "[OK] Backend healthy" -ForegroundColor Green
} else {
    Write-Host "[WARN] Backend not yet responding, check logs with: docker compose logs backend" -ForegroundColor Yellow
}

Write-Host "`n=== Quick Links ===" -ForegroundColor Cyan
Write-Host "  Local:    http://localhost/dashboard"
Write-Host "  API Docs: http://localhost/docs"
Write-Host "  Public:   https://ipwatchai.com/dashboard"
Write-Host ""
Write-Host "  Logs:     docker compose logs -f backend"
Write-Host "  Status:   docker compose ps"
Write-Host "  Stop:     docker compose down"
