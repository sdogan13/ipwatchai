# Install the health monitor as a Windows Scheduled Task
# Run this script once as Administrator

# Remove existing task if any
Unregister-ScheduledTask -TaskName 'IPWatchAI_HealthMonitor' -Confirm:$false -ErrorAction SilentlyContinue

# Create the scheduled task
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\701693\turk_patent\scripts\health_monitor.ps1"'

# Trigger: every 3 minutes, indefinitely
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 3)

# Settings
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

# Register task
Register-ScheduledTask -TaskName 'IPWatchAI_HealthMonitor' -Action $action -Trigger $trigger -Settings $settings -Description 'Monitors IP Watch AI services health and auto-recovers failed containers' -RunLevel Highest -Force

Write-Host "Scheduled task created successfully!" -ForegroundColor Green
Get-ScheduledTask -TaskName 'IPWatchAI_HealthMonitor' | Format-Table TaskName, State -AutoSize
