-- ============================================
-- Translation Similarity scoring component
-- Adds translation-based conflict detection fields
-- ============================================

-- Add translation similarity score to alerts table
ALTER TABLE alerts_mt
ADD COLUMN IF NOT EXISTS translation_similarity_score REAL DEFAULT 0;

-- Add semantic similarity score (was missing from original schema)
ALTER TABLE alerts_mt
ADD COLUMN IF NOT EXISTS semantic_similarity_score REAL;

-- Add translation similarity to universal_conflicts table (Opposition Radar)
ALTER TABLE universal_conflicts
ADD COLUMN IF NOT EXISTS translation_similarity REAL DEFAULT 0;
