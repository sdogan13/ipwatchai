-- ============================================
-- Trademark Events Table
-- Stores structured events extracted from GZ (Gazette) and BLT (Bulletin) PDFs:
--   transfers, seizures, injunctions, cancellations, renewals, licenses, etc.
-- ============================================

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

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Indexes
CREATE INDEX IF NOT EXISTS idx_te_app_no       ON trademark_events(application_no);
CREATE INDEX IF NOT EXISTS idx_te_reg_no       ON trademark_events(registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_te_type         ON trademark_events(event_type);
CREATE INDEX IF NOT EXISTS idx_te_source       ON trademark_events(source_type, bulletin_no);
CREATE INDEX IF NOT EXISTS idx_te_trademark_id ON trademark_events(trademark_id) WHERE trademark_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_te_date         ON trademark_events(bulletin_date);
CREATE INDEX IF NOT EXISTS idx_te_app_type     ON trademark_events(application_no, event_type);

-- Dedup index: same event from same bulletin inserted only once
CREATE UNIQUE INDEX IF NOT EXISTS uq_trademark_event
    ON trademark_events(application_no, event_type, source_type, bulletin_no,
                        COALESCE(old_value, ''), COALESCE(new_value, ''));
