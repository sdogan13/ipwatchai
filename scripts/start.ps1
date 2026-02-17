# ============================================
# IP Watch AI - Start Services
# ============================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   IP Watch AI - Starting Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# Load environment
if (Test-Path ".env.production") {
    Write-Host "Loading .env.production..." -ForegroundColor Yellow
    $envContent = Get-Content ".env.production" | Where-Object { $_ -match '=' -and $_ -notmatch '^#' }
    foreach ($line in $envContent) {
        $parts = $line -split '=', 2
        if ($parts.Length -eq 2) {
            $key = $parts[0].Trim()
            $value = $parts[1].Trim()
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

# Check if already running
$running = docker ps --filter "name=ipwatch" --format "{{.Names}}" 2>$null
if ($running) {
    Write-Host "Services already running:" -ForegroundColor Yellow
    $running | ForEach-Object { Write-Host "  - $_" -ForegroundColor Gray }
    Write-Host ""
    $restart = Read-Host "Restart services? (y/N)"
    if ($restart -eq 'y' -or $restart -eq 'Y') {
        Write-Host "Stopping existing services..." -ForegroundColor Yellow
        docker-compose down
    } else {
        Write-Host "Aborted." -ForegroundColor Yellow
        exit 0
    }
}

# Build and start
Write-Host ""
Write-Host "[1/4] Building Docker images..." -ForegroundColor Yellow
docker-compose build --parallel
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Build failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: Build complete" -ForegroundColor Green

Write-Host ""
Write-Host "[2/4] Starting services..." -ForegroundColor Yellow
docker-compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to start services!" -ForegroundColor Red
    exit 1
}

# Wait for health checks
Write-Host ""
Write-Host "[3/4] Waiting for services to be healthy..." -ForegroundColor Yellow
$maxWait = 180  # 3 minutes
$waited = 0
$interval = 5

while ($waited -lt $maxWait) {
    Start-Sleep -Seconds $interval
    $waited += $interval

    # Check backend health
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 5 -ErrorAction Stop
        if ($health.status -eq "healthy") {
            Write-Host "  OK: Backend is healthy" -ForegroundColor Green
            break
        }
    } catch {
        Write-Host "  Waiting... ($waited/$maxWait seconds)" -ForegroundColor Gray
    }
}

if ($waited -ge $maxWait) {
    Write-Host "  WARNING: Health check timed out" -ForegroundColor Yellow
    Write-Host "  Services may still be starting. Check logs with: .\scripts\logs.ps1" -ForegroundColor Yellow
}

# Show status
Write-Host ""
Write-Host "[4/4] Service Status:" -ForegroundColor Yellow
docker-compose ps

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   IP Watch AI is Running!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Local Access:" -ForegroundColor Cyan
Write-Host "  Frontend:  http://localhost" -ForegroundColor White
Write-Host "  API:       http://localhost:8000" -ForegroundColor White
Write-Host "  Health:    http://localhost:8000/health" -ForegroundColor White
Write-Host ""
Write-Host "Public Access (via Cloudflare Tunnel):" -ForegroundColor Cyan
Write-Host "  Website:   https://ipwatchai.com" -ForegroundColor White
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  View logs:  .\scripts\logs.ps1" -ForegroundColor White
Write-Host "  Stop:       .\scripts\stop.ps1" -ForegroundColor White
Write-Host ""
