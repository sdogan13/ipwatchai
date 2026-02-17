#!/bin/bash
set -e

echo "=== Initializing IP Watch AI Database ==="

# Create extensions
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    CREATE EXTENSION IF NOT EXISTS "vector";
    CREATE EXTENSION IF NOT EXISTS "pg_trgm";
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";
    CREATE EXTENSION IF NOT EXISTS "fuzzystrmatch";
EOSQL
echo "[OK] Extensions created"

# Apply consolidated schema
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/schema.sql
echo "[OK] Schema applied"

# Import word_idf seed data if available
if [ -f /docker-entrypoint-initdb.d/word_idf_seed.csv ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "\COPY word_idf FROM '/docker-entrypoint-initdb.d/word_idf_seed.csv' WITH CSV HEADER"
    echo "[OK] word_idf seed data imported"
else
    echo "[INFO] No word_idf seed data found — app will start without IDF (graceful fallback)"
fi

# Import nice_classes seed data if available
if [ -f /docker-entrypoint-initdb.d/nice_classes_seed.csv ]; then
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "\COPY nice_classes_lookup FROM '/docker-entrypoint-initdb.d/nice_classes_seed.csv' WITH CSV HEADER"
    echo "[OK] nice_classes_lookup seed data imported"
fi

echo "=== Database initialized successfully ==="
