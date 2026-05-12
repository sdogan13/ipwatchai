-- ============================================================
-- Functional btree indexes backing patent inventor + attorney
-- portfolio lookups.
--
-- The dashboard patent result card (Phase 2 of the actor click-
-- through work) lets users click an inventor name or attorney
-- pair to see every other patent listing the same person/firm.
-- Inventors live in `patent_inventors` (m2m); attorneys live in
-- `patent_attorneys` (m2m). Both currently have trigram indexes
-- on name for fuzzy autocomplete but no exact-normalized index
-- for the portfolio lookup — that's what these add.
--
-- Reuses normalize_designer_name(varchar) from
-- migrations/designs_normalized_designer_index.sql. The function
-- is name-agnostic (just normalizes any text string), so it
-- works equally well on patent inventor names.
--
-- The attorney index is composite (name + COALESCE(firm, '')) so
-- the same "name+firm pair under conservative normalization"
-- matching rule the design attorney click uses applies here too.
-- ============================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pinv_normalized_name
    ON patent_inventors (normalize_designer_name(name));

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_patt_normalized_pair
    ON patent_attorneys (
        normalize_designer_name(name),
        COALESCE(normalize_designer_name(firm), '')
    );
