#!/bin/bash
# Run extract+ingest cycle for newly downloaded GZ PDFs
# Can be run periodically while download_gz_targeted.py is running
set -e

cd "$(dirname "$0")/.."

echo "=== $(date) ==="
echo "Step 1: Extract events from GZ folders with PDFs but no events.json..."
PYTHONUNBUFFERED=1 python scripts/batch_extract_events.py --source GZ 2>&1 | tail -5

echo ""
echo "Step 2: Ingest events into DB..."
PYTHONUNBUFFERED=1 python ingest_events.py --source GZ 2>&1 | tail -5

echo ""
echo "=== Cycle complete $(date) ==="
