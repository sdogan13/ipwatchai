-- ============================================================
-- Add the missing index on trademarks.holder_id.
--
-- Why: trademarks.holder_id had no index, which (a) made the
-- FK validation step on every holders DELETE perform a full
-- seq scan over the 2.7M-row trademarks table — discovered during
-- the holders_consolidate_dups_no_tpe migration rehearsal, which
-- timed out on a 2,359-row DELETE — and (b) the column was a
-- candidate for queries that join holders → trademarks on
-- holder_id but always had to fall back to the denormalized
-- trademarks.holder_tpe_client_id index.
--
-- CONCURRENTLY: keeps the build off the AccessExclusiveLock so
-- the live ingest pipeline keeps writing during the build. Must
-- run outside a transaction.
--
-- The WHERE filter keeps the index small — designs that lack a
-- holder_id are excluded (most rows have holder_id set; the
-- partial index still satisfies the FK validation use case).
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_trademarks_holder_id
    ON trademarks(holder_id)
    WHERE holder_id IS NOT NULL;
