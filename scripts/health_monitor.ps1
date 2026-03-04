# ============================================
# IP Watch AI - Health Monitor & Auto-Recovery
# ============================================
# Runs every 3 minutes via Windows Task Scheduler.
# Checks: Docker daemon, all containers, backend health,
#          nginx proxy, Cloudflare tunnel, public website.
# Auto-recovers: restarts failed containers, restarts
#                Docker Desktop if daemon is down.
# Logs to: scripts/logs/health_monitor.log
# ============================================

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "scripts\logs"
$LogFile = Join-Path $LogDir "health_monitor.log"

# Ensure log directory exists
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# Rotate log if > 5MB
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 5MB) {
    $archive = Join-Path $LogDir ("health_monitor_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    Move-Item $LogFile $archive -Force
    # Keep only last 3 archived logs
    Get-ChildItem $LogDir -Filter "health_monitor_2*.log" | Sort-Object LastWriteTime -Descending | Select-Object -Skip 3 | Remove-Item -Force
}

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-Url {
    param([string]$Url, [int]$TimeoutSec = 10)
    try {
        $maxRetries = 3
        $retryWait = 2
        for ($i = 0; $i -lt $maxRetries; $i++) {
            try {
                $response = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
                if ($response.StatusCode -eq 200) { return $true }
            }
            catch {
                if ($i -lt $maxRetries - 1) {
                    Write-Log "Retry $($i + 1)/$maxRetries for $Url failed. Waiting ${retryWait}s..." "DEBUG"
                    Start-Sleep -Seconds $retryWait
                }
            }
        }
        return $false
    }
    catch {
        return $false
    }
}

# ---- Load .env.production for docker-compose ----
$envFile = Join-Path $ProjectRoot ".env.production"
if (Test-Path $envFile) {
    $envContent = Get-Content $envFile | Where-Object { $_ -match '=' -and $_ -notmatch '^#' }
    foreach ($line in $envContent) {
        $parts = $line -split '=', 2
        if ($parts.Length -eq 2) {
            $key = $parts[0].Trim()
            $value = $parts[1].Trim()
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

$issues = @()
$actions = @()

# ============================================
# CHECK 1: Docker daemon running?
# ============================================
$dockerOk = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
}
catch {}

if (-not $dockerOk) {
    $issues += "Docker daemon is not running"
    Write-Log "Docker daemon is DOWN. Attempting to start Docker Desktop..." "ERROR"

    # Start Docker Desktop
    $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) {
        Start-Process $dockerExe
        $actions += "Started Docker Desktop"
        Write-Log "Started Docker Desktop, waiting for daemon..." "ACTION"

        # Wait up to 120s for daemon
        $waited = 0
        while ($waited -lt 120) {
            Start-Sleep -Seconds 5
            $waited += 5
            try {
                $null = docker info 2>&1
                if ($LASTEXITCODE -eq 0) {
                    $dockerOk = $true
                    Write-Log "Docker daemon is ready after ${waited}s" "INFO"
                    break
                }
            }
            catch {}
        }

        if (-not $dockerOk) {
            Write-Log "Docker daemon failed to start after 120s. Aborting." "CRITICAL"
            exit 1
        }
    }
    else {
        Write-Log "Docker Desktop not found at expected path. Cannot auto-recover." "CRITICAL"
        exit 1
    }
}

# ============================================
# CHECK 2: All containers running?
# ============================================
$requiredContainers = @("ipwatch_postgres", "ipwatch_redis", "ipwatch_backend", "ipwatch_nginx", "ipwatch_tunnel")

foreach ($name in $requiredContainers) {
    $status = docker inspect --format '{{.State.Status}}' $name 2>&1
    if ($LASTEXITCODE -ne 0 -or $status -ne "running") {
        $issues += "$name is not running (status: $status)"
        Write-Log "$name is not running (status: $status). Restarting..." "ERROR"

        docker restart $name 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $actions += "Restarted $name"
            Write-Log "Restarted $name" "ACTION"
        }
        else {
            Write-Log "Failed to restart $name. Attempting docker-compose up..." "ERROR"
            Set-Location $ProjectRoot
            docker-compose up -d 2>&1 | Out-Null
            $actions += "Ran docker-compose up -d"
            Write-Log "Ran docker-compose up -d to recover all services" "ACTION"
            break
        }
    }
}

# ============================================
# CHECK 3: Container health status
# ============================================
$healthContainers = @("ipwatch_postgres", "ipwatch_redis", "ipwatch_backend", "ipwatch_nginx")

foreach ($name in $healthContainers) {
    $health = docker inspect --format '{{.State.Health.Status}}' $name 2>&1
    if ($health -eq "unhealthy") {
        $issues += "$name is unhealthy"
        Write-Log "$name is unhealthy. Restarting..." "WARN"
        docker restart $name 2>&1 | Out-Null
        $actions += "Restarted unhealthy $name"
        Write-Log "Restarted unhealthy $name" "ACTION"
    }
}

# ============================================
# CHECK 4: Backend HTTP health endpoint
# ============================================
$backendHealthy = Test-Url "http://127.0.0.1:8000/health" -TimeoutSec 30
if (-not $backendHealthy) {
    $issues += "Backend /health endpoint not responding"
    Write-Log "Backend /health not responding on port 8000" "WARN"

    # Check if it's a new startup - give it 60s grace period
    $backendUptime = docker inspect --format '{{.State.StartedAt}}' ipwatch_backend 2>&1
    try {
        $startedAt = [DateTime]::Parse($backendUptime)
        $uptimeSeconds = ((Get-Date).ToUniversalTime() - $startedAt).TotalSeconds
        if ($uptimeSeconds -lt 300) {
            Write-Log "Backend started ${uptimeSeconds}s ago (within 300s startup grace period). Skipping restart." "INFO"
        }
        else {
            Write-Log "Backend has been up for ${uptimeSeconds}s but /health fails. Restarting..." "ERROR"
            docker restart ipwatch_backend 2>&1 | Out-Null
            $actions += "Restarted backend (health check failed)"
            Write-Log "Restarted ipwatch_backend" "ACTION"
        }
    }
    catch {
        Write-Log "Could not parse backend uptime. Restarting as precaution." "WARN"
        docker restart ipwatch_backend 2>&1 | Out-Null
        $actions += "Restarted backend"
    }
}

# ============================================
# CHECK 5: Nginx proxy
# ============================================
$nginxOk = Test-Url "http://127.0.0.1:8080/health" -TimeoutSec 15
if (-not $nginxOk -and $backendHealthy) {
    $issues += "Nginx proxy not responding"
    Write-Log "Nginx not responding on port 8080 (backend is healthy). Restarting nginx..." "ERROR"
    docker restart ipwatch_nginx 2>&1 | Out-Null
    $actions += "Restarted nginx"
    Write-Log "Restarted ipwatch_nginx" "ACTION"
}

# ============================================
# CHECK 6: Public website via Cloudflare
# ============================================
$publicOk = Test-Url "https://www.ipwatchai.com/health" 15
if (-not $publicOk) {
    $issues += "Public website (ipwatchai.com) not accessible"
    Write-Log "Public website NOT accessible via Cloudflare tunnel" "ERROR"

    # Tunnel logic: restart if site is down, regardless of container status
    $tunnelInfo = docker inspect ipwatch_tunnel 2>$null | ConvertFrom-Json
    if ($tunnelInfo) {
        $startedAt = [DateTime]::Parse($tunnelInfo.State.StartedAt)
        $uptimeSeconds = ((Get-Date).ToUniversalTime() - $startedAt).TotalSeconds
        if ($uptimeSeconds -lt 300) {
            Write-Log "Public site down, but tunnel started only ${uptimeSeconds}s ago. Skipping proactive restart." "INFO"
        } else {
            Write-Log "Public site down and tunnel hasn't connected after ${uptimeSeconds}s. Proactively restarting tunnel..." "ACTION"
            docker restart ipwatch_tunnel 2>&1 | Out-Null
            $actions += "Restarted Cloudflare tunnel (proactive recovery after grace period)"
        }
    }
}

# ============================================
# CHECK 7: Disk space
# ============================================
$drive = Get-PSDrive C
$freeGB = [math]::Round($drive.Free / 1GB, 1)
if ($freeGB -lt 10) {
    $issues += "Low disk space: ${freeGB}GB free"
    Write-Log "LOW DISK SPACE: ${freeGB}GB remaining on C:" "WARN"

    # Prune unused Docker resources if critically low
    if ($freeGB -lt 5) {
        docker system prune -f 2>&1 | Out-Null
        $actions += "Docker system prune (disk critically low)"
        Write-Log "Ran docker system prune -f (only ${freeGB}GB free)" "ACTION"
    }
}

# ============================================
# SUMMARY
# ============================================
if ($issues.Count -eq 0) {
    Write-Log "All checks passed. Services healthy." "OK"
}
else {
    Write-Log ("Issues found: " + ($issues -join "; ")) "SUMMARY"
    if ($actions.Count -gt 0) {
        Write-Log ("Actions taken: " + ($actions -join "; ")) "SUMMARY"
    }
}
