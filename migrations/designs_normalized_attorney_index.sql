-- ============================================================
-- BTree index backing attorney-portfolio lookups on designs.
--
-- Attorney clicks on the design card open a portfolio modal that
-- shows every design listing the same (attorney_name, attorney_firm)
-- pair under conservative normalization. Without this index the
-- lookup is a seq scan over the 732k-row designs table.
--
-- Why name+firm together: the user chose stricter matching so a
-- common attorney name ("Ali Demir") at different firms is not
-- collapsed. About 59% of designs have an attorney_firm; rows
-- with NULL firm match other NULL-firm rows for the same name via
-- COALESCE(...,'').
--
-- Reuses normalize_designer_name() from
-- migrations/designs_normalized_designer_index.sql — the function
-- is name-agnostic despite the name (it normalizes any text
-- string: LOWER + collapse whitespace + strip ASCII punctuation,
-- preserves Turkish letters).
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_des_attorney_normalized
    ON designs (
        normalize_designer_name(attorney_name),
        COALESCE(normalize_designer_name(attorney_firm), '')
    )
    WHERE attorney_name IS NOT NULL;
