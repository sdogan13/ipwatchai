-- ============================================
-- Translation fields for cross-language search
-- Supports: Turkish, English, Kurdish, Farsi
-- ============================================

-- Add translation columns to trademarks table
ALTER TABLE trademarks
ADD COLUMN IF NOT EXISTS name_tr VARCHAR(500),
ADD COLUMN IF NOT EXISTS name_en VARCHAR(500),
ADD COLUMN IF NOT EXISTS name_ku VARCHAR(500),
ADD COLUMN IF NOT EXISTS name_fa VARCHAR(500),
ADD COLUMN IF NOT EXISTS detected_lang VARCHAR(10);

-- B-tree indexes for exact match lookups
CREATE INDEX IF NOT EXISTS idx_trademarks_name_tr ON trademarks(name_tr);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_en ON trademarks(name_en);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_ku ON trademarks(name_ku);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_fa ON trademarks(name_fa);

-- Trigram indexes for similarity search on translations
CREATE INDEX IF NOT EXISTS idx_trademarks_name_tr_trgm ON trademarks USING gin(name_tr gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_en_trgm ON trademarks USING gin(name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_ku_trgm ON trademarks USING gin(name_ku gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_fa_trgm ON trademarks USING gin(name_fa gin_trgm_ops);
