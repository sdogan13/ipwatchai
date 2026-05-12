-- ============================================================
-- Live lifecycle status for patents.
--
-- patents.record_type is the classification of a single bulletin
-- entry (frozen at ingest). It tells you what KIND of record this
-- row is — not whether the patent is currently alive.
--
-- This migration adds:
--
--   patents.current_status     — derived live state, refreshed
--                                whenever new patent_events arrive
--   patents.last_event_type    — most recent event that affected
--                                the status (audit / sort)
--   patents.last_event_date    — date of that event
--   patents.status_computed_at — when the derivation last ran
--
-- Backfill happens in scripts/backfill_patent_current_status.py.
-- New ingest runs refresh the value per-batch.
--
-- The enum mirrors the state machine in
-- pipeline/patent_status_derivation.py — keep them in sync if you
-- add or rename values.
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'patent_lifecycle_status'
    ) THEN
        CREATE TYPE patent_lifecycle_status AS ENUM (
            'UNKNOWN',
            'PENDING',
            'ACTIVE',
            'LAPSED_APPLICATION',
            'LAPSED_GRANT',
            'REJECTED',
            'WITHDRAWN',
            'EXPIRED',
            'INVALIDATED'
        );
    END IF;
END$$;

ALTER TABLE patents
    ADD COLUMN IF NOT EXISTS current_status patent_lifecycle_status,
    ADD COLUMN IF NOT EXISTS last_event_type VARCHAR(50),
    ADD COLUMN IF NOT EXISTS last_event_date DATE,
    ADD COLUMN IF NOT EXISTS status_computed_at TIMESTAMP;

-- Filter / sort index for "active only" queries on search + watchlist.
CREATE INDEX IF NOT EXISTS idx_patents_current_status
    ON patents (current_status)
    WHERE current_status IS NOT NULL;

-- Cover the most common ordering when listing recent lifecycle changes.
CREATE INDEX IF NOT EXISTS idx_patents_last_event_date
    ON patents (last_event_date DESC NULLS LAST)
    WHERE last_event_date IS NOT NULL;
