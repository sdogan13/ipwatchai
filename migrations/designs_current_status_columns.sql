-- ============================================================
-- Live lifecycle audit columns for designs.
--
-- designs.current_status (design_status enum) already exists, but
-- today it's set from the publication section at ingest time
-- (tr_native / hague / republished / deferred_lifted -> Yayında,
-- deferred -> Yayım Ertelendi) and never moves once events arrive.
-- This migration adds the audit columns that drive the live
-- derivation in pipeline/design_status_derivation.py.
--
-- Backfill: scripts/backfill_design_current_status.py
-- Ingest hook: pipeline/ingest_designs.py
-- ============================================================

ALTER TABLE designs
    ADD COLUMN IF NOT EXISTS last_event_type VARCHAR(50),
    ADD COLUMN IF NOT EXISTS last_event_date DATE,
    ADD COLUMN IF NOT EXISTS status_computed_at TIMESTAMP;

-- Cover the most common ordering when listing recent lifecycle
-- changes. Mirrors idx_patents_last_event_date.
CREATE INDEX IF NOT EXISTS idx_designs_last_event_date
    ON designs (last_event_date DESC NULLS LAST)
    WHERE last_event_date IS NOT NULL;

-- current_status filter index — partial because most rows are
-- "Yayında" and we want to keep the index small for cancellations /
-- expirations / renewals lookups.
CREATE INDEX IF NOT EXISTS idx_designs_current_status_active
    ON designs (current_status)
    WHERE current_status NOT IN ('Yayında', 'Bilinmiyor');
