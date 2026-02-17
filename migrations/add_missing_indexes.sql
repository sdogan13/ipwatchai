-- ============================================
-- Add Missing HNSW Indexes for DINOv2 and Color Histogram
-- Also add GIN trigram index on logo_ocr_text
--
-- IMPORTANT: Run during low-activity window.
-- CONCURRENTLY prevents table locks but consumes CPU+memory.
-- Monitor progress:
--   SELECT phase, blocks_done, blocks_total,
--          round(100.0 * blocks_done / NULLIF(blocks_total, 0), 1) AS pct
--   FROM pg_stat_progress_create_index;
-- ============================================

-- DINOv2 embedding HNSW index (768-dim, ~1-2 hours build)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_dinov2_vec
    ON trademarks USING hnsw (dinov2_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE dinov2_embedding IS NOT NULL;

-- Color histogram HNSW index (512-dim, ~30-60 minutes build)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_color_vec
    ON trademarks USING hnsw (color_histogram halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE color_histogram IS NOT NULL;

-- OCR text trigram index for text search (~5-10 minutes build)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_ocr_trgm
    ON trademarks USING gin (logo_ocr_text gin_trgm_ops);
