DROP INDEX IF EXISTS idx_trademarks_text_embedding;
DROP INDEX IF EXISTS idx_tm_text_embedding;
DROP INDEX IF EXISTS idx_tm_text_vec;
ALTER TABLE trademarks DROP COLUMN IF EXISTS text_embedding;
