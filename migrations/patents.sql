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
