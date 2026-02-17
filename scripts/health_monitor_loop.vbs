' ============================================
' IP Watch AI - Health Monitor Loop (Silent)
' ============================================
' Placed in Windows Startup folder.
' Waits 2 minutes for Docker Desktop, then runs
' health_monitor.ps1 every 3 minutes in a hidden window.
' ============================================
Set WshShell = CreateObject("WScript.Shell")

' Wait 2 minutes for Docker Desktop to start
WScript.Sleep 120000

Dim scriptPath
scriptPath = "C:\Users\701693\turk_patent\scripts\health_monitor.ps1"

Do
    WshShell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & scriptPath & """", 0, True
    WScript.Sleep 180000  ' 3 minutes
Loop
