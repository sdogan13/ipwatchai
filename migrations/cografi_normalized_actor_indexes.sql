-- ============================================================
-- Functional btree index for cografi agent portfolio lookup.
--
-- Agents for Geographical Indications live as a sparse text
-- column on `cografi_records.agent` (no separate m2m table —
-- agent is roughly 11% populated, ~397 of ~3.5k records).
-- Applicant lookup goes through the shared `cografi_holders`
-- m2m (filtered on role='APPLICANT') and is already covered
-- by idx_cog_holder_holder + the holder resolver.
--
-- The functional btree below mirrors the pattern used by the
-- design + patent inventor/attorney indexes — exact match on
-- normalize_designer_name(agent), which preserves Turkish
-- letters and ignores whitespace/punctuation variance.
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cog_agent_normalized
    ON cografi_records (normalize_designer_name(agent))
    WHERE agent IS NOT NULL;
