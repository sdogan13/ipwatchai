-- Fix days_until_deadline: remove broken GENERATED ALWAYS AS STORED column
-- PostgreSQL rejects CURRENT_DATE in generated columns (not immutable).
-- All queries now compute (opposition_deadline - CURRENT_DATE) dynamically.
--
-- Run: psql -h 127.0.0.1 -p 5433 -U turk_patent -d trademark_db -f migrations/fix_days_until_deadline.sql

-- Drop the generated column if it exists (it may have prevented table creation entirely)
ALTER TABLE universal_conflicts DROP COLUMN IF EXISTS days_until_deadline;

-- Drop the stale index that referenced it
DROP INDEX IF EXISTS idx_uc_days_until;

-- Add a functional index on opposition_deadline for urgency filtering
CREATE INDEX IF NOT EXISTS idx_uc_opposition_deadline
    ON universal_conflicts(opposition_deadline);

-- Recreate views with dynamic computation
CREATE OR REPLACE VIEW active_leads AS
SELECT
    uc.*,
    (uc.opposition_deadline - CURRENT_DATE) as days_until_deadline,
    CASE
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 7 THEN 'critical'
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 14 THEN 'urgent'
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 30 THEN 'soon'
        ELSE 'normal'
    END as urgency_level
FROM universal_conflicts uc
WHERE uc.opposition_deadline >= CURRENT_DATE
  AND uc.lead_status NOT IN ('dismissed', 'converted')
ORDER BY uc.opposition_deadline ASC, uc.similarity_score DESC;

CREATE OR REPLACE VIEW lead_statistics AS
SELECT
    COUNT(*) as total_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 7) as critical_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 14) as urgent_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 30) as upcoming_leads,
    COUNT(*) FILTER (WHERE lead_status = 'new') as new_leads,
    COUNT(*) FILTER (WHERE lead_status = 'viewed') as viewed_leads,
    COUNT(*) FILTER (WHERE lead_status = 'contacted') as contacted_leads,
    COUNT(*) FILTER (WHERE lead_status = 'converted') as converted_leads,
    AVG(similarity_score) as avg_similarity,
    MAX(created_at) as last_scan_at
FROM universal_conflicts
WHERE opposition_deadline >= CURRENT_DATE;
