-- Drop deprecated MiniLM + color histogram columns from the trademark category.
--
-- Pre-condition: PR 1 (commits 58f62790..52d6072f, merged in 5ffc6181) must be
-- deployed and verified — readers and writers for these columns have been
-- removed from application code. Running this migration before deploying PR 1
-- will cause ingest / search / scanner failures.
--
-- This migration is irreversible: data in the dropped columns cannot be
-- recovered without a backup. Capture a logical dump of the four columns
-- (e.g., COPY (SELECT id, color_histogram FROM trademarks) TO ...) before
-- applying if you may need rollback.

-- Drop dependent indexes first.
DROP INDEX IF EXISTS idx_tm_color_vec;

-- trademarks.color_histogram (HSV 8x8x8 histogram, 512-dim) — no longer
-- computed by the ingest pipeline; visual scoring uses CLIP + DINOv2 + OCR.
ALTER TABLE trademarks DROP COLUMN IF EXISTS color_histogram;

-- watchlist_mt.text_embedding (MiniLM brand-name embedding, 384-dim) — no
-- longer cloned from trademarks.text_embedding (already dropped) at
-- create_with_embeddings time.
ALTER TABLE watchlist_mt DROP COLUMN IF EXISTS text_embedding;

-- watchlist_mt.logo_color_histogram (HSV 8x8x8 histogram, 512-dim) — no
-- longer cloned from trademarks.color_histogram nor computed for
-- user-uploaded logos by watchlist/scanner.py:generate_logo_embeddings.
ALTER TABLE watchlist_mt DROP COLUMN IF EXISTS logo_color_histogram;

-- nice_classes_lookup.description_embedding (MiniLM NICE description
-- embedding, 384-dim) — no longer populated by ingest_bootstrap and no
-- longer queried by risk_engine.suggest_classes (which now returns []).
ALTER TABLE nice_classes_lookup DROP COLUMN IF EXISTS description_embedding;
