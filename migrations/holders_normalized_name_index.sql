-- ============================================================
-- Functional partial index that backs the conservative
-- name-normalization dedup in the ingest pipelines.
--
-- The matching SELECT is in
--   pipeline/holder_helpers.find_holder_id_by_normalized_name
-- and is called by the design / patent / cografi ingest paths
-- before they insert a new holders row whose source has no TPE
-- client id. Without this index the SELECT is a seq scan over
-- holders (~143k rows) — bearable for a one-off ingest run but
-- punishing during bulletin replays.
--
-- The WHERE filter (``tpe_client_id IS NULL``) keeps the index
-- tiny: only the no-TPE-ID rows participate in this dedup path.
--
-- CONCURRENTLY: build off the AccessExclusiveLock so live ingest
-- keeps writing during the build. Must run outside a transaction.
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_holders_normalized_name_no_tpe
    ON holders (
        LOWER(
            REGEXP_REPLACE(
                REGEXP_REPLACE(name, '[[:space:]]+', ' ', 'g'),
                '[[:punct:]]', '', 'g'
            )
        )
    )
    WHERE tpe_client_id IS NULL;
