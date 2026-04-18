-- ============================================
-- Event-Derived Columns on trademarks table
-- These columns are computed by ingest_events.py's chronological materialization.
-- They sit ALONGSIDE ingest.py's current_status (which reflects source priority).
-- ============================================

-- 1. Add 'İptal Edildi' to tm_status enum if not already present
--    (also in trademark_events.sql, but safe to re-run)
DO $$ BEGIN
    ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'İptal Edildi';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'Devredildi';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'Süresi Doldu';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 2. Event-derived columns
--    effective_status: status derived from walking events chronologically
--    (may differ from current_status which is set by ingest.py source priority)
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS effective_status tm_status;

--    active_restriction_count: seizures minus lifts (0 = no active restrictions)
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS active_restriction_count INTEGER DEFAULT 0;

--    current_holder_name: latest holder from transfer/merger events
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS current_holder_name TEXT;

--    holder_changed_at: date of the most recent transfer/merger event
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS holder_changed_at DATE;

--    renewal_expiry: expiry date computed from the latest renewal event (+10 years)
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS renewal_expiry DATE;

--    last_event_type: type of the most recent event (by bulletin_date)
--    (also in trademark_events.sql, safe to re-run)
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS last_event_type VARCHAR(50);

--    last_event_date: date of the most recent event
--    (already exists in schema.sql as last_event_date DATE)

--    has_restrictions: boolean flag (also in trademark_events.sql, safe to re-run)
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS has_restrictions BOOLEAN DEFAULT FALSE;

--    event_flags: JSONB for additional computed flags
--    e.g. {"has_license": true, "has_court_order": true, "madrid_protected": true}
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS event_flags JSONB DEFAULT '{}';

--    total_event_count: how many events exist for this trademark
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS total_event_count INTEGER DEFAULT 0;

-- 3. Indexes for event-derived columns
CREATE INDEX IF NOT EXISTS idx_tm_effective_status ON trademarks(effective_status)
    WHERE effective_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_restrictions ON trademarks(active_restriction_count)
    WHERE active_restriction_count > 0;
CREATE INDEX IF NOT EXISTS idx_tm_holder_changed ON trademarks(holder_changed_at)
    WHERE holder_changed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_event_flags ON trademarks USING GIN (event_flags)
    WHERE event_flags != '{}';
