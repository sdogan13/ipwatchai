-- Drop the legacy Quick Search counter column.
-- Search is now unified under the daily live_searches counter (Agentic Search).
ALTER TABLE api_usage DROP COLUMN IF EXISTS quick_searches;
