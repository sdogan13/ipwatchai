-- ============================================
-- Patent / Faydalı Model Schema Migration
-- Adds:
--   * patent_record_type enum
--   * ipc_classes_lookup (empty in Stage 0; WIPO IPC scheme loaded later)
--   * patents (main table; natural unique key on publication_no)
--   * patent_holders (multi-row; FK to global holders table)
--   * patent_inventors, patent_attorneys, patent_priorities (multi-row)
--   * patent_figures (per-figure embeddings; mirrors design_views)
--   * patent_events (reserved for Stage 7; populated later)
-- Idempotent: safe to run multiple times.
-- ============================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. Record-type enum
-- ============================================
-- Mirrors pdf_extract_patent.RecordType. Values come from the
-- (11) publication-number kind code (B/T4 -> GRANTED_PATENT, etc.).
-- LEGACY is reserved for the 1996-2015 multi-month bundles whose
-- inner PDFs lack INID-coded fields (Stage 8, deferred).
-- UNKNOWN catches kind codes the classifier doesn't yet map (A3, U3,
-- T7 — see patent_kind_code_gap memory; Stage 5 ingest may filter).
DO $$ BEGIN
    CREATE TYPE patent_record_type AS ENUM (
        'GRANTED_PATENT',
        'GRANTED_UM',
        'PUBLISHED_APP',
        'PUBLISHED_UM_APP',
        'EP_FASCICLE',
        'LEGACY',
        'UNKNOWN'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================
-- 2. IPC classification reference table
-- ============================================
-- Empty in Stage 0. The WIPO IPC scheme has thousands of entries and
-- is loaded as a follow-up task (separate from this migration). The
-- column shape ships now so Stage 5 ingest's IPC normalization has
-- a target.
CREATE TABLE IF NOT EXISTS ipc_classes_lookup (
    code            VARCHAR(20) PRIMARY KEY,    -- "A61M 5/31"
    section         CHAR(1),                    -- A through H
    class_code      VARCHAR(3),                 -- "A61"
    subclass        VARCHAR(5),                 -- "A61M"
    description_tr  TEXT,
    description_en  TEXT,
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- 3. patents (main table)
-- ============================================
-- Mirrors designs in shape. Differences from designs:
--   * Natural unique key is publication_no (NOT application_no) — same
--     application can ship multiple publications in one bulletin (B
--     grant + A1 republication; verified on app 2024/000746 in 2025/8).
--     application_no is indexed but not unique; cross-publication
--     portfolio queries pivot on it.
--   * record_type enum captures the kind-code classification (designs
--     use design_status which is a lifecycle state instead).
--   * patent_holders / patent_inventors / patent_attorneys are
--     separate join tables (CD ships multiple per record); designs
--     puts a single holder_id directly on the row.
--   * Two embedding columns: title_abstract_embedding (text) +
--     primary_figure_embedding (image, pooled). Designs has only the
--     image side because they're typically non-textual.
CREATE TABLE IF NOT EXISTS patents (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Registry discriminator (matches designs.registry_type pattern;
    -- enables future cross-registry views)
    registry_type               VARCHAR(20) NOT NULL DEFAULT 'patent'
                                CHECK (registry_type IN ('trademark', 'design', 'patent')),
    -- Natural keys
    application_no              VARCHAR(50) NOT NULL,             -- "2017/15048"
    publication_no              VARCHAR(50),                       -- "TR 2017 15048 U3"
    kind_code                   VARCHAR(10),                       -- "B", "A1", "U3", "T4"
    record_type                 patent_record_type DEFAULT 'UNKNOWN',
    -- Dates
    application_date            DATE,
    publication_date            DATE,
    grant_date                  DATE,                              -- PDF-only field
    bulletin_no                 VARCHAR(20),                       -- "2025/8"
    bulletin_date               DATE,
    -- Content
    title                       TEXT,
    abstract                    TEXT,
    ipc_classes                 TEXT[] DEFAULT '{}'::TEXT[],
    patent_type                 VARCHAR(10),                       -- CD-only "1" (patent) or "2" (UM)
    -- Embeddings (populated by Stage 6)
    title_abstract_embedding    halfvec(1024),
    primary_figure_embedding    halfvec(1024),
    -- Source / provenance
    source_format               VARCHAR(10) NOT NULL DEFAULT 'CD'
                                CHECK (source_format IN ('CD','PDF','BOTH')),
    source_archive              VARCHAR(100),                      -- e.g. "2025_07_CD.rar"
    source_pdf                  VARCHAR(100),                      -- e.g. "2025_08.pdf"
    bulletin_folder             VARCHAR(100),                      -- "PT_2025_8_2025-08-21"
    page_range_start            INTEGER,                           -- PDF-only
    page_range_end              INTEGER,                           -- PDF-only
    created_at                  TIMESTAMP DEFAULT NOW(),
    updated_at                  TIMESTAMP DEFAULT NOW()
);

-- Natural unique constraint. Partial index because some HSQLDB rows
-- ship a blank publication_no (verified: 142 records in bulletin
-- 2019/11 — see patent_kind_code_gap memory). Those records still
-- need to be ingestable; just not deduped on publication_no.
CREATE UNIQUE INDEX IF NOT EXISTS uq_patents_publication_no
    ON patents (publication_no) WHERE publication_no IS NOT NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_pat_app_no            ON patents (application_no);
CREATE INDEX IF NOT EXISTS idx_pat_record_type       ON patents (record_type);
CREATE INDEX IF NOT EXISTS idx_pat_kind_code         ON patents (kind_code);
CREATE INDEX IF NOT EXISTS idx_pat_application_date  ON patents (application_date DESC);
CREATE INDEX IF NOT EXISTS idx_pat_publication_date  ON patents (publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_pat_bulletin_date     ON patents (bulletin_date DESC);
CREATE INDEX IF NOT EXISTS idx_pat_ipc_arr           ON patents USING GIN (ipc_classes);
CREATE INDEX IF NOT EXISTS idx_pat_title_trgm        ON patents USING GIST (title gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_pat_text_vec          ON patents USING hnsw (title_abstract_embedding halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE title_abstract_embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pat_fig_vec           ON patents USING hnsw (primary_figure_embedding halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE primary_figure_embedding IS NOT NULL;
