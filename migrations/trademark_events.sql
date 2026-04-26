-- ============================================
-- Trademark Events Table
-- Stores structured events extracted from GZ (Gazette) and BLT (Bulletin) PDFs:
--   transfers, seizures, injunctions, cancellations, renewals, licenses, etc.
-- ============================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Add missing enum values for event-driven statuses
DO $$ BEGIN
    ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'İptal Edildi';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'Devredildi';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 2. Add new columns to trademarks table
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS last_event_type VARCHAR(50);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS has_restrictions BOOLEAN DEFAULT FALSE;

-- 3. Create trademark_events table
CREATE TABLE IF NOT EXISTS trademark_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

    -- Link to trademark (nullable — event may reference app_no not yet in DB)
    trademark_id UUID REFERENCES trademarks(id) ON DELETE SET NULL,
    application_no VARCHAR(20) NOT NULL,
    registration_no VARCHAR(20),

    -- Event classification
    event_type VARCHAR(50) NOT NULL,
    event_subtype VARCHAR(50),

    -- Source tracking
    source_type VARCHAR(3) NOT NULL,           -- 'GZ' or 'BLT'
    bulletin_no VARCHAR(10) NOT NULL,          -- gazette or bulletin number
    bulletin_date DATE,                        -- publication date
    page_number INTEGER,

    -- Event payload
    old_value TEXT,
    new_value TEXT,
    details JSONB DEFAULT '{}',
    raw_text TEXT,
    event_fingerprint VARCHAR(64),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE trademark_events
    ADD COLUMN IF NOT EXISTS event_fingerprint VARCHAR(64);

-- 4. Indexes
CREATE INDEX IF NOT EXISTS idx_te_app_no       ON trademark_events(application_no);
CREATE INDEX IF NOT EXISTS idx_te_reg_no       ON trademark_events(registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_te_type         ON trademark_events(event_type);
CREATE INDEX IF NOT EXISTS idx_te_source       ON trademark_events(source_type, bulletin_no);
CREATE INDEX IF NOT EXISTS idx_te_trademark_id ON trademark_events(trademark_id) WHERE trademark_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_te_date         ON trademark_events(bulletin_date);
CREATE INDEX IF NOT EXISTS idx_te_app_type     ON trademark_events(application_no, event_type);

-- Backfill deterministic full-payload fingerprint for old rows.
UPDATE trademark_events
SET event_fingerprint = encode(
    digest(
        concat_ws(
            E'\x1f',
            COALESCE(application_no, ''),
            COALESCE(registration_no, ''),
            COALESCE(event_type, ''),
            COALESCE(event_subtype, ''),
            COALESCE(source_type, ''),
            COALESCE(bulletin_no, ''),
            COALESCE(to_char(bulletin_date, 'YYYY-MM-DD'), ''),
            COALESCE(page_number::text, ''),
            COALESCE(old_value, ''),
            COALESCE(new_value, ''),
            COALESCE(details::text, '{}'),
            COALESCE(raw_text, '')
        ),
        'sha256'
    ),
    'hex'
)
WHERE event_fingerprint IS NULL;

-- Remove exact duplicates before creating the stronger unique index.
WITH ranked AS (
    SELECT
        ctid,
        ROW_NUMBER() OVER (
            PARTITION BY event_fingerprint
            ORDER BY created_at ASC, id ASC
        ) AS rn
    FROM trademark_events
)
DELETE FROM trademark_events te
USING ranked
WHERE te.ctid = ranked.ctid
  AND ranked.rn > 1;

ALTER TABLE trademark_events
    ALTER COLUMN event_fingerprint SET NOT NULL;

DROP INDEX IF EXISTS uq_trademark_event;

-- Dedup guard: unique full event payload, not the old weak subset.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trademark_event
    ON trademark_events(event_fingerprint);
