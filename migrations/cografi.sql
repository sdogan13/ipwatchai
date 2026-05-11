-- ============================================
-- Coğrafi İşaret ve Geleneksel Ürün Adı Schema Migration
-- Adds:
--   * cografi_section_key + cografi_record_type enums
--   * cografi_records (main table; natural unique key on
--     (bulletin_no, section_key, COALESCE(application_no, registration_no::text)))
--   * cografi_holders (multi-row; FK to global holders table for
--     applicants / registrants / agents)
--   * cografi_change_requests (one row per change tuple in an
--     Article 42 change-request or finalized record)
--   * cografi_figures (per-figure embeddings; mirrors design_views +
--     patent_figures)
-- Idempotent: safe to run multiple times.
-- ============================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. Section-key enum
-- ============================================
-- Mirrors pdf_extract_cografi.SECTION_KEY_* constants. Both modern
-- (SMK 6769) and legacy (KHK 555) bulletins classify into the same
-- 8 semantic keys; the legal regime difference is captured by the
-- record's bulletin_date rather than a separate dimension.
DO $$ BEGIN
    CREATE TYPE cografi_section_key AS ENUM (
        'examined',
        'registered',
        'article_40_modified',
        'article_42_change_requests',
        'article_42_finalized',
        'article_43_modified',
        'corrections',
        'gazette_only_announcements'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================
-- 2. Record-type enum
-- ============================================
-- "GI" = Coğrafi İşaret (geographical indication)
-- "TPN" = Geleneksel Ürün Adı (traditional product name)
-- "UNKNOWN" reserved for stubs whose record_type couldn't be
-- determined from the Section 2 sub-index header.
DO $$ BEGIN
    CREATE TYPE cografi_record_type AS ENUM ('GI', 'TPN', 'UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================
-- 3. Holder-role enum
-- ============================================
-- Cografi records have one applicant or registrant + an optional
-- agent (Vekil); the role enum lets the join table model all three
-- with a single row each.
DO $$ BEGIN
    CREATE TYPE cografi_holder_role AS ENUM ('APPLICANT', 'REGISTRANT', 'AGENT');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================
-- 4. cografi_records (main table)
-- ============================================
-- One row per record-publication. A given GI passes through multiple
-- lifecycle stages (examined -> registered -> art42 modifications) and
-- each stage is captured as its own row in this table. Cross-stage
-- portfolio queries pivot on application_no or registration_no.
--
-- Natural unique key: (bulletin_no, section_key, COALESCE(application_no,
-- registration_no::text, name)) — examined/art40 records have an
-- application_no; registered/art42 records have a registration_no;
-- a small number of stub records have only the name (legacy art42
-- whose registration_no fell outside the parser's preamble regex).
CREATE TABLE IF NOT EXISTS cografi_records (
    id                                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Registry discriminator (matches patent + design pattern).
    registry_type                       VARCHAR(20) NOT NULL DEFAULT 'cografi'
                                        CHECK (registry_type IN ('trademark', 'design', 'patent', 'cografi')),
    -- Natural keys
    bulletin_no                         INTEGER NOT NULL,
    bulletin_date                       DATE,
    section_key                         cografi_section_key NOT NULL,
    record_type                         cografi_record_type NOT NULL DEFAULT 'GI',
    application_no                      VARCHAR(50),                -- e.g. "C2022/000469"
    registration_no                     INTEGER,                    -- e.g. 1838
    -- Names + dates
    name                                TEXT NOT NULL,
    application_date                    DATE,
    registration_date                   DATE,
    -- Header content
    product_group                       TEXT,                       -- "Halı / Halılar ve kilimler" or legacy "Peynir"
    gi_type                             VARCHAR(100),               -- "Mahreç işareti" / "Menşe adı" / "Geleneksel ürün adı"
    geographical_boundary               TEXT,                       -- "Konya ili Karapınar ilçesi"
    usage_description                   TEXT,                       -- Kullanım Biçimi free text
    agent                               TEXT,                       -- Vekil
    -- Body free-text subsections (B2). JSONB so we can grow new
    -- subsection types without a migration.
    body_sections                       JSONB DEFAULT '{}'::jsonb,
    -- Stub fallback for art42 / correction records the parser couldn't
    -- structure (legacy era). Keeps the row searchable until a future
    -- parser iteration extracts proper fields.
    raw_text                            TEXT,
    -- Article 42 specifics: which existing registration is being
    -- modified. Denormalised onto the record for query convenience;
    -- the per-change diff lives in cografi_change_requests.
    existing_registration_no            INTEGER,
    -- Corrections (Düzeltmeler) section specifics.
    correction_referenced_bulletin_no   INTEGER,
    correction_referenced_bulletin_date DATE,
    correction_referenced_record_id     VARCHAR(50),
    correction_old_text                 TEXT,
    correction_new_text                 TEXT,
    -- Embeddings (C1)
    text_embedding                      halfvec(1024),
    primary_figure_embedding            halfvec(1024),
    -- Source / provenance
    bulletin_folder                     VARCHAR(100),               -- "CI_220_2026-05-04"
    start_page                          INTEGER,
    extractor_version                   INTEGER,
    extracted_at                        TIMESTAMP,
    embeddings_at                       TIMESTAMP,
    created_at                          TIMESTAMP DEFAULT NOW(),
    updated_at                          TIMESTAMP DEFAULT NOW()
);

-- Natural unique constraint. Functional index because the
-- discriminating ID is application_no for some sections and
-- registration_no::text for others; the COALESCE picks whichever
-- the record has, falling back to name for the rare stub case.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cografi_record
    ON cografi_records (
        bulletin_no,
        section_key,
        (COALESCE(application_no, registration_no::text, name))
    );

-- Indexes
CREATE INDEX IF NOT EXISTS idx_cog_app_no
    ON cografi_records (application_no) WHERE application_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_reg_no
    ON cografi_records (registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_existing_reg_no
    ON cografi_records (existing_registration_no) WHERE existing_registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_section_key       ON cografi_records (section_key);
CREATE INDEX IF NOT EXISTS idx_cog_record_type       ON cografi_records (record_type);
CREATE INDEX IF NOT EXISTS idx_cog_bulletin_no       ON cografi_records (bulletin_no DESC);
CREATE INDEX IF NOT EXISTS idx_cog_bulletin_date     ON cografi_records (bulletin_date DESC);
CREATE INDEX IF NOT EXISTS idx_cog_application_date  ON cografi_records (application_date DESC) WHERE application_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_registration_date ON cografi_records (registration_date DESC) WHERE registration_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_gi_type           ON cografi_records (gi_type);
CREATE INDEX IF NOT EXISTS idx_cog_name_trgm
    ON cografi_records USING GIST (name gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_cog_geo_trgm
    ON cografi_records USING GIST (geographical_boundary gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_cog_text_vec
    ON cografi_records USING hnsw (text_embedding halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE text_embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_fig_vec
    ON cografi_records USING hnsw (primary_figure_embedding halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE primary_figure_embedding IS NOT NULL;

-- ============================================
-- 5. cografi_holders (applicants / registrants / agents)
-- ============================================
-- Single join table for all three applicant-side roles. The role enum
-- discriminates: APPLICANT for examined-section records,
-- REGISTRANT for registered records, AGENT for the Vekil field.
-- holder_id FKs to the existing global holders table (TPECLIENT IDs
-- are shared across all four registries — locked decision).
-- holder_id is nullable: many cografi applicants are public bodies
-- (Karapınar Ticaret ve Sanayi Odası, ilçe Tarım Müdürlükleri) which
-- may not have a TPECLIENT ID.
CREATE TABLE IF NOT EXISTS cografi_holders (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id     UUID NOT NULL REFERENCES cografi_records(id) ON DELETE CASCADE,
    holder_id     UUID REFERENCES holders(id) ON DELETE SET NULL,
    role          cografi_holder_role NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 1,
    -- Denormalised name + address (kept verbatim from the bulletin
    -- in case the holders.id link is absent or stale).
    name          TEXT NOT NULL,
    address       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cografi_holder
    ON cografi_holders (record_id, role, seq);
CREATE INDEX IF NOT EXISTS idx_cog_holder_record
    ON cografi_holders (record_id);
CREATE INDEX IF NOT EXISTS idx_cog_holder_holder
    ON cografi_holders (holder_id) WHERE holder_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_holder_name_trgm
    ON cografi_holders USING GIST (name gist_trgm_ops);

-- ============================================
-- 6. cografi_change_requests (Article 42 children)
-- ============================================
-- One row per change tuple in an Article 42 change-request or
-- finalized record. The parent record's existing_registration_no
-- gives the registration being modified; this table holds the
-- per-field diff (e.g. Denetleme: "<old>" -> "<new>").
CREATE TABLE IF NOT EXISTS cografi_change_requests (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id     UUID NOT NULL REFERENCES cografi_records(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    field         TEXT NOT NULL,
    old_text      TEXT,
    new_text      TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cografi_change_request
    ON cografi_change_requests (record_id, seq);
CREATE INDEX IF NOT EXISTS idx_cog_chreq_record
    ON cografi_change_requests (record_id);
CREATE INDEX IF NOT EXISTS idx_cog_chreq_field
    ON cografi_change_requests (field);

-- ============================================
-- 7. cografi_figures (per-figure embeddings)
-- ============================================
-- Mirrors patent_figures + design_views. image_path is relative to
-- the bulletin folder's figures/ subdir (e.g. "C2022_000469/1.jpeg")
-- — combine with bulletin_folder on cografi_records to resolve.
CREATE TABLE IF NOT EXISTS cografi_figures (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id     UUID NOT NULL REFERENCES cografi_records(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    image_path    TEXT NOT NULL,
    page          INTEGER,
    bbox          NUMERIC[],
    width         INTEGER,
    height        INTEGER,
    -- Embeddings (C1)
    dinov2_vitl14 halfvec(1024),
    clip_vitb32   halfvec(512),
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cografi_figure
    ON cografi_figures (record_id, seq);
CREATE INDEX IF NOT EXISTS idx_cog_fig_record
    ON cografi_figures (record_id);
CREATE INDEX IF NOT EXISTS idx_cog_fig_dinov2_vec
    ON cografi_figures USING hnsw (dinov2_vitl14 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE dinov2_vitl14 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cog_fig_clip_vec
    ON cografi_figures USING hnsw (clip_vitb32 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE clip_vitb32 IS NOT NULL;
