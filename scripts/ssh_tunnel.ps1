# ============================================
# IP Watch AI - SSH Reverse Tunnel to VPS
# ============================================
# This script creates a persistent SSH reverse tunnel
# from your PC to the Vultr VPS, forwarding port 8080.
#
# Architecture:
#   Cloudflare Edge -> VPS cloudflared -> VPS:8080 -> SSH tunnel -> PC:8080 (nginx)
#
# Run this script to start the tunnel. It auto-reconnects on failure.
# ============================================

$VPS_IP = "78.141.238.79"
$VPS_USER = "root"
$SSH_KEY = "$HOME\.ssh\vultr_key"
$LOCAL_PORT = 8080    # nginx on your PC
$REMOTE_PORT = 8080   # cloudflared on VPS expects traffic here

$LogFile = Join-Path $PSScriptRoot "logs\ssh_tunnel.log"
$LogDir = Split-Path $LogFile
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-TunnelLog {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

Write-TunnelLog "Starting SSH reverse tunnel to $VPS_IP..."
Write-TunnelLog "Forwarding VPS:$REMOTE_PORT -> localhost:$LOCAL_PORT"

while ($true) {
    Write-TunnelLog "Connecting..."

    # -R binds VPS:8080 to PC:8080 through the SSH tunnel
    # -N = no remote command, -T = no TTY, -o options for keepalive
    ssh -i $SSH_KEY `
        -R ${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT} `
        -o ServerAliveInterval=30 `
        -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes `
        -o StrictHostKeyChecking=no `
        -N -T `
        "${VPS_USER}@${VPS_IP}" 2>&1 | ForEach-Object { Write-TunnelLog $_ }

    $exitCode = $LASTEXITCODE
    Write-TunnelLog "SSH disconnected (exit code: $exitCode). Reconnecting in 10s..."
    Start-Sleep -Seconds 10
}
