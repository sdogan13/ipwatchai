-- Persist full V2 scoring diagnostics for watchlist alert score display.
ALTER TABLE alerts_mt
ADD COLUMN IF NOT EXISTS score_details JSONB DEFAULT '{}'::jsonb;
