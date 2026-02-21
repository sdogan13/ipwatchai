# ============================================
# Install the health monitor as a Windows Scheduled Task
# Run this script once as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\install_monitor.ps1
# ============================================

# Remove existing task if any
Unregister-ScheduledTask -TaskName 'IPWatchAI_HealthMonitor' -Confirm:$false -ErrorAction SilentlyContinue

# Create the scheduled task action
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\701693\turk_patent\scripts\health_monitor.ps1"'

# Trigger 1: Every 3 minutes, indefinitely (ongoing monitoring)
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 3)

# Trigger 2: At system startup with 2-minute delay (immediate recovery after reboot)
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$bootTrigger.Delay = 'PT2M'

# Settings: battery-safe, restart on failure, run even if missed
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

# Register task with both triggers
Register-ScheduledTask `
    -TaskName 'IPWatchAI_HealthMonitor' `
    -Action $action `
    -Trigger @($repeatTrigger, $bootTrigger) `
    -Settings $settings `
    -Description 'Monitors IP Watch AI services health and auto-recovers failed containers. Runs every 3 minutes + at system startup.' `
    -RunLevel Highest `
    -Force

Write-Host "`nScheduled task created successfully!" -ForegroundColor Green
Write-Host "Triggers: Every 3 minutes + At system startup (2-min delay)" -ForegroundColor Cyan
Get-ScheduledTask -TaskName 'IPWatchAI_HealthMonitor' | Format-Table TaskName, State -AutoSize
