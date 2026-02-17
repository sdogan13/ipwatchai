# ============================================
# IP Watch AI - Initial Setup Script
# Run this once before first deployment
# ============================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   IP Watch AI - Setup Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Check Docker
Write-Host "[1/6] Checking Docker..." -ForegroundColor Yellow
if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Docker is not installed!" -ForegroundColor Red
    Write-Host "Please install Docker Desktop from: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
    exit 1
}
$dockerVersion = docker --version
Write-Host "  OK: $dockerVersion" -ForegroundColor Green

# Check Docker is running
$dockerRunning = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Docker is not running!" -ForegroundColor Red
    Write-Host "Please start Docker Desktop and try again." -ForegroundColor Yellow
    exit 1
}
Write-Host "  OK: Docker is running" -ForegroundColor Green

# Check NVIDIA Docker
Write-Host ""
Write-Host "[2/6] Checking NVIDIA GPU support..." -ForegroundColor Yellow
$nvidiaCheck = docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: NVIDIA GPU detected" -ForegroundColor Green
} else {
    Write-Host "  WARNING: NVIDIA GPU not available in Docker" -ForegroundColor Yellow
    Write-Host "  The system will run on CPU (slower)" -ForegroundColor Yellow
}

# Check PostgreSQL
Write-Host ""
Write-Host "[3/6] Checking PostgreSQL connection..." -ForegroundColor Yellow
$env:PGPASSWORD = "143588"
$pgCheck = & psql -h 127.0.0.1 -U turk_patent -d trademark_db -c "SELECT COUNT(*) FROM trademarks;" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: PostgreSQL is accessible" -ForegroundColor Green
    $count = ($pgCheck | Select-String -Pattern "\d+" | Select-Object -First 1).Matches.Value
    Write-Host "  Trademark count: $count" -ForegroundColor Cyan
} else {
    Write-Host "  INFO: PostgreSQL not accessible from host (will use Docker internal)" -ForegroundColor Yellow
}

# Create directories
Write-Host ""
Write-Host "[4/6] Creating directories..." -ForegroundColor Yellow
$dirs = @(
    "$ProjectRoot\frontend\dist",
    "$ProjectRoot\nginx",
    "$ProjectRoot\cloudflared",
    "$ProjectRoot\scripts",
    "$ProjectRoot\logs",
    "$ProjectRoot\uploads",
    "$ProjectRoot\reports"
)
foreach ($dir in $dirs) {
    if (!(Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        Write-Host "  Created: $dir" -ForegroundColor Gray
    }
}
Write-Host "  OK: All directories exist" -ForegroundColor Green

# Generate secure secret key if not exists
Write-Host ""
Write-Host "[5/6] Checking environment file..." -ForegroundColor Yellow
$envFile = "$ProjectRoot\.env.production"
if (Test-Path $envFile) {
    Write-Host "  OK: .env.production exists" -ForegroundColor Green
} else {
    Write-Host "  Creating .env.production from template..." -ForegroundColor Yellow
    Copy-Item "$ProjectRoot\.env.production.example" $envFile -ErrorAction SilentlyContinue

    # Generate a secure secret key
    $secretKey = -join ((65..90) + (97..122) + (48..57) | Get-Random -Count 64 | ForEach-Object {[char]$_})
    (Get-Content $envFile) -replace 'ipwatchai-prod-secret-key-change-me-to-something-secure-64chars', $secretKey | Set-Content $envFile
    Write-Host "  Generated secure AUTH_SECRET_KEY" -ForegroundColor Green
}

# Check Cloudflare tunnel credentials
Write-Host ""
Write-Host "[6/6] Checking Cloudflare tunnel..." -ForegroundColor Yellow
$tunnelCreds = Get-ChildItem "$ProjectRoot\cloudflared\*.json" -ErrorAction SilentlyContinue
if ($tunnelCreds) {
    Write-Host "  OK: Tunnel credentials found" -ForegroundColor Green
    Write-Host "  File: $($tunnelCreds.Name)" -ForegroundColor Gray
} else {
    Write-Host "  WARNING: No tunnel credentials found" -ForegroundColor Yellow
    Write-Host "  Run: cloudflared tunnel login" -ForegroundColor Yellow
    Write-Host "  Then: cloudflared tunnel create ipwatchai" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Review .env.production settings" -ForegroundColor White
Write-Host "  2. Run: .\scripts\start.ps1" -ForegroundColor White
Write-Host ""
