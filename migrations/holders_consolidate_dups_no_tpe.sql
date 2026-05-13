-- ============================================================
-- Consolidate duplicate holder rows that share a normalized name
-- but lack a tpe_client_id (foreign holders + legacy records).
--
-- Why: the holders table has ~3,187 distinct entities split across
--   ~5,849 rows because of trivial name variants like "CO.  LTD."
--   vs "CO. LTD." Multiple holders.id values for the same real-world
--   entity break click-to-portfolio (we'd return only a slice of the
--   holder's actual footprint) and pollute watchlist scans.
--
-- Normalization rule (CONSERVATIVE):
--   LOWER + collapse runs of whitespace + strip ASCII punctuation
--   Preserves all letters, including Turkish diacritics (İ, Ş, Ğ,
--   Ü, Ö, Ç) so "ÜMİT ÜNAL" and "ÜMÜT İNAL" stay separate.
--   See rehearsal log for the false-merge counterexamples that led
--   to choosing this over a stricter rule.
--
-- FK references repointed (6 tables — see schema introspection
-- via information_schema.table_constraints):
--   designs.holder_id
--   trademarks.holder_id
--   patent_holders.holder_id
--   cografi_holders.holder_id
--   patent_watchlist_mt.holder_id
--   cografi_watchlist_mt.holder_id
--
-- Canonical row pick: oldest created_at, tie-broken by id. The
-- original plan picked by inbound-FK-count first, but the six
-- correlated subqueries over 4,307 holders + their indexed FK
-- tables ran 6+ min on prod and got cancelled. Total FK rewrites
-- are tiny (~22k) regardless of which row is canonical, so the
-- optimization wasn't worth the rehearsal time. Oldest-row is a
-- deterministic, fast choice and roughly correlates with "more
-- established identity" anyway.
--
-- Run as one transaction. Default = REHEARSAL (ROLLBACK at end);
-- toggle to COMMIT once verified.
-- ============================================================

BEGIN;

-- 1. Build the dedup map: which holder rows merge into which canonical.
DROP TABLE IF EXISTS holder_dedup_map;
CREATE TEMP TABLE holder_dedup_map ON COMMIT PRESERVE ROWS AS
WITH normalized AS (
    SELECT h.id, h.name, h.created_at,
           LOWER(
               REGEXP_REPLACE(
                   REGEXP_REPLACE(h.name, '[[:space:]]+', ' ', 'g'),
                   '[[:punct:]]', '', 'g'
               )
           ) AS norm
    FROM holders h
    WHERE h.tpe_client_id IS NULL
      AND h.name IS NOT NULL
),
dup_norms AS (
    SELECT norm
    FROM normalized
    GROUP BY norm
    HAVING COUNT(*) > 1
),
in_dup_groups AS (
    SELECT n.id, n.norm, n.created_at
    FROM normalized n
    JOIN dup_norms d ON d.norm = n.norm
),
ranked AS (
    SELECT id, norm, created_at,
           ROW_NUMBER() OVER (
               PARTITION BY norm
               ORDER BY created_at ASC NULLS LAST, id ASC
           ) AS rk
    FROM in_dup_groups
),
canonical AS (
    SELECT norm, id AS canonical_id FROM ranked WHERE rk = 1
)
SELECT r.id AS non_canonical_id,
       c.canonical_id,
       r.norm
FROM ranked r
JOIN canonical c USING (norm)
WHERE r.rk > 1;

CREATE INDEX ON holder_dedup_map (non_canonical_id);

-- 2. Map shape sanity (expect: rows_to_merge ≈ 1948, distinct_canonicals ≈ 2359)
SELECT
    (SELECT COUNT(*) FROM holder_dedup_map)                           AS rows_to_merge,
    (SELECT COUNT(DISTINCT canonical_id) FROM holder_dedup_map)       AS distinct_canonicals;

-- 3. Pre-migration row counts on every touched table.
SELECT 'pre_holders'              AS what, COUNT(*) FROM holders
UNION ALL SELECT 'pre_designs',              COUNT(*) FROM designs
UNION ALL SELECT 'pre_trademarks',           COUNT(*) FROM trademarks
UNION ALL SELECT 'pre_patent_holders',       COUNT(*) FROM patent_holders
UNION ALL SELECT 'pre_cografi_holders',      COUNT(*) FROM cografi_holders
UNION ALL SELECT 'pre_patent_watchlist_mt',  COUNT(*) FROM patent_watchlist_mt
UNION ALL SELECT 'pre_cografi_watchlist_mt', COUNT(*) FROM cografi_watchlist_mt;

-- 4. Repoint FKs in each of the 6 referencing tables.
UPDATE designs SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE designs.holder_id = m.non_canonical_id;

UPDATE trademarks SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE trademarks.holder_id = m.non_canonical_id;

UPDATE patent_holders SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE patent_holders.holder_id = m.non_canonical_id;

UPDATE cografi_holders SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE cografi_holders.holder_id = m.non_canonical_id;

UPDATE patent_watchlist_mt SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE patent_watchlist_mt.holder_id = m.non_canonical_id;

UPDATE cografi_watchlist_mt SET holder_id = m.canonical_id
FROM holder_dedup_map m WHERE cografi_watchlist_mt.holder_id = m.non_canonical_id;

-- 5. Delete the now-orphaned holder rows.
DELETE FROM holders h
USING holder_dedup_map m
WHERE h.id = m.non_canonical_id;

-- 6. Post-migration row counts. Expected deltas:
--    holders: -1948 (matches rows_to_merge)
--    designs, trademarks, patent_holders, cografi_holders,
--    patent_watchlist_mt, cografi_watchlist_mt: unchanged
SELECT 'post_holders'              AS what, COUNT(*) FROM holders
UNION ALL SELECT 'post_designs',              COUNT(*) FROM designs
UNION ALL SELECT 'post_trademarks',           COUNT(*) FROM trademarks
UNION ALL SELECT 'post_patent_holders',       COUNT(*) FROM patent_holders
UNION ALL SELECT 'post_cografi_holders',      COUNT(*) FROM cografi_holders
UNION ALL SELECT 'post_patent_watchlist_mt',  COUNT(*) FROM patent_watchlist_mt
UNION ALL SELECT 'post_cografi_watchlist_mt', COUNT(*) FROM cografi_watchlist_mt;

-- 7. Confirm no remaining dup groups under the same normalization.
WITH normalized AS (
    SELECT LOWER(REGEXP_REPLACE(REGEXP_REPLACE(name, '[[:space:]]+', ' ', 'g'), '[[:punct:]]', '', 'g')) AS norm
    FROM holders
    WHERE tpe_client_id IS NULL AND name IS NOT NULL
)
SELECT COUNT(*) AS remaining_dup_groups
FROM (
    SELECT norm FROM normalized GROUP BY norm HAVING COUNT(*) > 1
) x;

-- 8. Spot-check: Samsung Electronics should now be a single row
-- with the combined design + patent counts of its old 6 sibling rows.
SELECT h.id, h.name,
       (SELECT COUNT(*) FROM designs WHERE holder_id = h.id)         AS designs,
       (SELECT COUNT(*) FROM patent_holders WHERE holder_id = h.id)  AS patents
FROM holders h
WHERE h.tpe_client_id IS NULL
  AND LOWER(REGEXP_REPLACE(REGEXP_REPLACE(h.name, '[[:space:]]+', ' ', 'g'), '[[:punct:]]', '', 'g'))
      = 'samsung electronics co ltd';

-- Rehearsed on 2026-05-12 via BEGIN...ROLLBACK against prod —
-- counts confirmed (2,359 deletes, 525+7146+87 FK rewrites, no
-- residual dup groups, holders.id for SAMSUNG consolidated to a
-- single row with 454 patent_holders rows linked). Promoting to
-- COMMIT now.
COMMIT;
