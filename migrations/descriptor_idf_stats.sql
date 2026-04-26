-- Descriptor-like token evidence for IDF tables.
-- Populated by compute_idf.py from the local trademark corpus.

ALTER TABLE word_idf
    ADD COLUMN IF NOT EXISTS total_documents INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS weight_multiplier FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS descriptor_like BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS descriptor_score DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS descriptor_stats JSONB DEFAULT '{}'::jsonb;

ALTER TABLE word_idf_tr
    ADD COLUMN IF NOT EXISTS total_documents INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS weight_multiplier FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS descriptor_like BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS descriptor_score DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS descriptor_stats JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_word_idf_descriptor_like
    ON word_idf(word)
    WHERE descriptor_like = TRUE;

CREATE INDEX IF NOT EXISTS idx_word_idf_tr_descriptor_like
    ON word_idf_tr(word)
    WHERE descriptor_like = TRUE;
