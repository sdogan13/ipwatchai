-- ============================================
-- Registry type discriminator on the trademark table
-- Adds the same `registry_type` column shape that designs already has,
-- so unified queries can branch on the registry without table-name
-- inspection. Idempotent: safe to re-run.
-- ============================================

-- Add column with DEFAULT so existing rows backfill instantly (no rewrite).
ALTER TABLE trademarks
    ADD COLUMN IF NOT EXISTS registry_type VARCHAR(20) NOT NULL DEFAULT 'trademark';

-- CHECK constraint pins values to the known registry vocabulary.
DO $$ BEGIN
    ALTER TABLE trademarks
        ADD CONSTRAINT trademarks_registry_type_check
        CHECK (registry_type IN ('trademark', 'design'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Convenience btree (filtered queries against this column will be exact-match)
CREATE INDEX IF NOT EXISTS idx_tm_registry_type ON trademarks(registry_type);
