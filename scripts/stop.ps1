# ============================================
# IP Watch AI - Stop Services
# ============================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   IP Watch AI - Stopping Services" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# Check running containers
$running = docker ps --filter "name=ipwatch" --format "{{.Names}}" 2>$null
if (!$running) {
    Write-Host "No IP Watch AI services are running." -ForegroundColor Yellow
    exit 0
}

Write-Host "Stopping services:" -ForegroundColor Yellow
$running | ForEach-Object { Write-Host "  - $_" -ForegroundColor Gray }
Write-Host ""

# Stop services
docker-compose down

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "All services stopped successfully." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Some services may not have stopped cleanly." -ForegroundColor Yellow
    Write-Host "Run 'docker ps' to check for remaining containers." -ForegroundColor Yellow
}
