-- Migration: Add name_generations and quick_searches counters to api_usage
-- Date: 2026-02-08
-- Purpose: Track monthly name generation calls and daily quick searches for plan limit enforcement

ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS name_generations INTEGER DEFAULT 0;
ALTER TABLE api_usage ADD COLUMN IF NOT EXISTS quick_searches INTEGER DEFAULT 0;
