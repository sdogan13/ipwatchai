#!/bin/bash
set -e
BACKUP_DIR="/opt/backups/trademark"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"
cd /opt/trademark-app
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec -T postgres pg_dump -U turk_patent -Fc trademark_db > "$BACKUP_DIR/db_$TIMESTAMP.dump"
find "$BACKUP_DIR" -name "db_*.dump" -mtime +7 -delete
echo "Backup complete: db_$TIMESTAMP.dump ($(du -h "$BACKUP_DIR/db_$TIMESTAMP.dump" | cut -f1))"
