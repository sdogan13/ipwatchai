@echo off
REM ============================================
REM IP Watch AI - Database Export Script
REM Run this on your local PC to export the database
REM ============================================

echo ============================================
echo IP Watch AI - Database Export
echo ============================================

echo [1/3] Exporting database with compression...
docker exec ipwatch_postgres pg_dump -U turk_patent -d trademark_db --no-owner --no-acl -F custom -Z 6 -f /tmp/trademark_db.dump

echo [2/3] Copying dump from container...
docker cp ipwatch_postgres:/tmp/trademark_db.dump .\trademark_db.dump

echo [3/3] Checking file size...
for %%I in (trademark_db.dump) do echo Export size: %%~zI bytes

echo.
echo ============================================
echo Export complete: trademark_db.dump
echo ============================================
echo.
echo Next steps:
echo   1. Upload to VPS:  scp trademark_db.dump root@YOUR_VPS_IP:/opt/ipwatch/
echo   2. On VPS, import:
echo      docker exec -i ipwatch_postgres pg_restore -U turk_patent -d trademark_db --no-owner --clean --if-exists /tmp/trademark_db.dump
echo      (first: docker cp /opt/ipwatch/trademark_db.dump ipwatch_postgres:/tmp/)
echo.
