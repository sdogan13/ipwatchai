-- ============================================
-- Unified final_status column on trademarks table
-- Combines current_status (ingest.py) and effective_status (ingest_events.py)
-- using the most recent data date as tiebreaker.
-- ============================================

-- 1. Add new columns
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS final_status tm_status;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS final_status_at DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS final_status_source VARCHAR(10);

-- 2. Backfill from existing data
UPDATE trademarks SET
    final_status = CASE
        WHEN effective_status IS NULL THEN current_status
        WHEN current_status IS NULL THEN effective_status
        WHEN last_event_date >= COALESCE(
            CASE status_source
                WHEN 'BLT' THEN bulletin_date
                WHEN 'GZ'  THEN gazette_date
                ELSE updated_at::date
            END,
            updated_at::date
        ) THEN effective_status
        WHEN last_event_date < COALESCE(
            CASE status_source
                WHEN 'BLT' THEN bulletin_date
                WHEN 'GZ'  THEN gazette_date
                ELSE updated_at::date
            END,
            updated_at::date
        ) THEN current_status
        ELSE COALESCE(effective_status, current_status)
    END,
    final_status_source = CASE
        WHEN effective_status IS NULL THEN 'ingest'
        WHEN current_status IS NULL THEN 'event'
        WHEN last_event_date >= COALESCE(
            CASE status_source
                WHEN 'BLT' THEN bulletin_date
                WHEN 'GZ'  THEN gazette_date
                ELSE updated_at::date
            END,
            updated_at::date
        ) THEN 'event'
        WHEN last_event_date < COALESCE(
            CASE status_source
                WHEN 'BLT' THEN bulletin_date
                WHEN 'GZ'  THEN gazette_date
                ELSE updated_at::date
            END,
            updated_at::date
        ) THEN 'ingest'
        ELSE 'event'
    END,
    final_status_at = GREATEST(
        last_event_date,
        COALESCE(
            CASE status_source
                WHEN 'BLT' THEN bulletin_date
                WHEN 'GZ'  THEN gazette_date
                ELSE updated_at::date
            END,
            updated_at::date
        )
    );

-- 3. Index for final_status
CREATE INDEX IF NOT EXISTS idx_tm_final_status ON trademarks(final_status);
