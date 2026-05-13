-- ============================================
-- Patent alerts webhook tracking columns
-- Adds webhook_sent + webhook_sent_at to patent_alerts_mt so the
-- webhook worker can dedupe deliveries the same way email_sent +
-- email_sent_at dedupe email digests.
-- Idempotent: safe to run multiple times.
-- ============================================

ALTER TABLE patent_alerts_mt
    ADD COLUMN IF NOT EXISTS webhook_sent     BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS webhook_sent_at  TIMESTAMP;

-- Partial index — most rows have webhook_sent=TRUE so a partial index
-- on the FALSE side keeps the worker query fast as the table grows.
CREATE INDEX IF NOT EXISTS idx_pal_webhook_unsent
    ON patent_alerts_mt (id)
    WHERE webhook_sent = FALSE;
