-- Migration: Add per-logo style column to generated_images
-- Date: 2026-05-07
-- Purpose: When the Logo Studio first-gen flow stops asking the user for a style
-- and instead generates one logo per canonical style (Modern/Classic/Bold/Playful),
-- each generated row needs to remember which style it represents — so the card
-- can label it, and so revisions can auto-lock to the parent's style across
-- page reloads / history reopens.
--
-- Nullable so existing rows stay valid; nothing needs backfilling.

ALTER TABLE generated_images
    ADD COLUMN IF NOT EXISTS style VARCHAR(20);
