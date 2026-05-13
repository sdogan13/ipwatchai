-- ============================================
-- Applications: polymorphic registry support (Phase 1)
-- ============================================
-- Adds registry_kind discriminator, classification_codes (replaces
-- trademark-only nice_class_numbers in new code), and a JSONB
-- `details` blob for registry-specific extras. Existing trademark
-- rows are backfilled to registry_kind='trademark' and have their
-- nice_class_numbers copied into classification_codes (as text).
--
-- Idempotent. Safe to re-run.
-- ============================================

-- 1. Add the three new columns
ALTER TABLE trademark_applications_mt
    ADD COLUMN IF NOT EXISTS registry_kind TEXT NOT NULL DEFAULT 'trademark',
    ADD COLUMN IF NOT EXISTS classification_codes TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;

-- 2. Constrain registry_kind to the supported registries
DO $$ BEGIN
    ALTER TABLE trademark_applications_mt
        ADD CONSTRAINT chk_applications_registry_kind
        CHECK (registry_kind IN ('trademark', 'design', 'patent', 'cografi'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 3. Relax brand_name NOT NULL — it now holds the generic primary display
-- title (brand name for TM, design title for design, etc.). Existing TM
-- rows already have brand_name populated; new design rows will use it as
-- the design title.
ALTER TABLE trademark_applications_mt
    ALTER COLUMN brand_name DROP NOT NULL;

-- 4. Backfill classification_codes from nice_class_numbers for existing
-- trademark rows so list rendering can read a single canonical column.
UPDATE trademark_applications_mt
SET classification_codes = ARRAY(SELECT n::text FROM unnest(nice_class_numbers) AS n)
WHERE registry_kind = 'trademark'
  AND (classification_codes IS NULL OR array_length(classification_codes, 1) IS NULL)
  AND array_length(nice_class_numbers, 1) IS NOT NULL;

-- 5. Index optimized for the most common list query (org + registry + status)
CREATE INDEX IF NOT EXISTS idx_tma_org_kind_status
    ON trademark_applications_mt (organization_id, registry_kind, status);
