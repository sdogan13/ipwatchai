-- Migration: Add full visual feature columns to generated_images
-- Date: 2026-02-07
-- Purpose: Store DINOv2 embedding, OCR text, and visual breakdown alongside CLIP

ALTER TABLE generated_images
    ADD COLUMN IF NOT EXISTS dino_embedding halfvec(768),
    ADD COLUMN IF NOT EXISTS ocr_text TEXT,
    ADD COLUMN IF NOT EXISTS visual_breakdown JSONB;

-- Index on dino_embedding for future similarity searches
-- (Not strictly needed now since we only query trademarks, not generated_images)
-- CREATE INDEX IF NOT EXISTS idx_generated_images_dino ON generated_images USING hnsw (dino_embedding halfvec_cosine_ops);
