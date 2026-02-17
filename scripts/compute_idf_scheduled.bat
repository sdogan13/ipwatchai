@echo off
REM Scheduled IDF computation script
REM Runs monthly to update word_idf table with latest trademark frequencies

cd /d C:\Users\701693\turk_patent
echo [%date% %time%] Starting IDF computation... >> logs\idf_scheduled.log

python compute_idf.py >> logs\idf_scheduled.log 2>&1

echo [%date% %time%] IDF computation complete >> logs\idf_scheduled.log
