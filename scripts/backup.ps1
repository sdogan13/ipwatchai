# ============================================
# IP Watch AI - Database Backup
# ============================================

param(
    [string]$OutputDir = ".\backups"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   IP Watch AI - Database Backup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Create backup directory
if (!(Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}

# Generate backup filename
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$backupFile = "$OutputDir\trademark_db_$timestamp.sql"

Write-Host "Creating backup..." -ForegroundColor Yellow
Write-Host "  Database: trademark_db" -ForegroundColor Gray
Write-Host "  Output: $backupFile" -ForegroundColor Gray
Write-Host ""

# Set password and run pg_dump
$env:PGPASSWORD = "143588"

$pgDumpArgs = @(
    "-h", "127.0.0.1",
    "-U", "turk_patent",
    "-d", "trademark_db",
    "-F", "p",  # Plain SQL format
    "-f", $backupFile,
    "--no-owner",
    "--no-privileges"
)

try {
    & pg_dump @pgDumpArgs 2>&1

    if ($LASTEXITCODE -eq 0) {
        $size = (Get-Item $backupFile).Length / 1MB
        Write-Host ""
        Write-Host "Backup completed successfully!" -ForegroundColor Green
        Write-Host "  File: $backupFile" -ForegroundColor Gray
        Write-Host "  Size: $([math]::Round($size, 2)) MB" -ForegroundColor Gray

        # Compress backup
        Write-Host ""
        Write-Host "Compressing backup..." -ForegroundColor Yellow
        $compressedFile = "$backupFile.gz"
        & gzip -k $backupFile 2>$null
        if (Test-Path $compressedFile) {
            $compressedSize = (Get-Item $compressedFile).Length / 1MB
            Write-Host "  Compressed: $compressedFile" -ForegroundColor Gray
            Write-Host "  Size: $([math]::Round($compressedSize, 2)) MB" -ForegroundColor Gray
        }
    } else {
        Write-Host "ERROR: Backup failed!" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "ERROR: pg_dump not found or failed!" -ForegroundColor Red
    Write-Host "Make sure PostgreSQL client tools are installed." -ForegroundColor Yellow
    exit 1
}

# List recent backups
Write-Host ""
Write-Host "Recent backups:" -ForegroundColor Cyan
Get-ChildItem $OutputDir -Filter "*.sql*" | Sort-Object LastWriteTime -Descending | Select-Object -First 5 | ForEach-Object {
    $size = $_.Length / 1MB
    Write-Host "  $($_.Name) - $([math]::Round($size, 2)) MB" -ForegroundColor Gray
}
