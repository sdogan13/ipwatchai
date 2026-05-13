-- ============================================================
-- Functional GIN index backing designer-portfolio lookups.
--
-- A dashboard click on a designer name in a Tasarım result card
-- needs to find every design whose ``designers`` array contains a
-- matching entry. We want the same conservative name
-- normalization the holder consolidation work uses (LOWER + strip
-- ASCII punctuation + collapse whitespace), preserving Turkish
-- diacritics so different individuals don't get false-merged.
--
-- ``designs.designers`` is a ``VARCHAR[]`` with a plain GIN index
-- (``idx_des_designers_arr``) — that index can answer
-- ``designers @> ARRAY[$1]`` quickly but only for the literal
-- name. To match the normalized form fast, we need a *functional*
-- GIN index on a normalized-array expression. That requires an
-- IMMUTABLE function to wrap the per-element transformation.
--
-- The two functions are SQL-language + IMMUTABLE + PARALLEL SAFE
-- so the planner can inline them into index/query expressions.
-- ============================================================

CREATE OR REPLACE FUNCTION normalize_designer_name(name varchar)
RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT LOWER(
        REGEXP_REPLACE(
            REGEXP_REPLACE(name, '[[:space:]]+', ' ', 'g'),
            '[[:punct:]]', '', 'g'
        )
    )
$$;

CREATE OR REPLACE FUNCTION normalize_designer_name_array(arr varchar[])
RETURNS text[]
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$
    SELECT array_agg(normalize_designer_name(n))
    FROM unnest(arr) AS n
    WHERE n IS NOT NULL
$$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_des_designers_normalized_gin
    ON designs
    USING gin (normalize_designer_name_array(designers))
    WHERE designers IS NOT NULL;
