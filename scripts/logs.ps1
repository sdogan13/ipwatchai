# ============================================
# IP Watch AI - View Logs
# ============================================

param(
    [string]$Service = "all",
    [int]$Lines = 100,
    [switch]$Follow
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   IP Watch AI - Logs" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Show available services
if ($Service -eq "all" -and !$Follow) {
    Write-Host "Available services:" -ForegroundColor Yellow
    Write-Host "  backend     - FastAPI backend (main logs)" -ForegroundColor Gray
    Write-Host "  nginx       - Nginx reverse proxy" -ForegroundColor Gray
    Write-Host "  redis       - Redis cache" -ForegroundColor Gray
    Write-Host "  cloudflared - Cloudflare tunnel" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\logs.ps1                    # Show all logs (last 100 lines)" -ForegroundColor Gray
    Write-Host "  .\logs.ps1 -Service backend   # Show backend logs only" -ForegroundColor Gray
    Write-Host "  .\logs.ps1 -Follow            # Follow all logs in real-time" -ForegroundColor Gray
    Write-Host "  .\logs.ps1 -Service backend -Follow  # Follow backend logs" -ForegroundColor Gray
    Write-Host "  .\logs.ps1 -Lines 500         # Show last 500 lines" -ForegroundColor Gray
    Write-Host ""
}

# Build docker-compose command
$cmd = "docker-compose logs"

if ($Service -ne "all") {
    $cmd += " $Service"
}

$cmd += " --tail=$Lines"

if ($Follow) {
    $cmd += " -f"
    Write-Host "Following logs... (Press Ctrl+C to stop)" -ForegroundColor Yellow
    Write-Host ""
}

# Execute
Invoke-Expression $cmd
