-- Migration: Add logo_ocr_text column to watchlist_mt
-- Date: 2026-02-07
-- Purpose: Store OCR-extracted text from watchlist logo images
--          so scanner compares OCR-vs-OCR (not brand name vs OCR)

ALTER TABLE watchlist_mt
    ADD COLUMN IF NOT EXISTS logo_ocr_text TEXT;
