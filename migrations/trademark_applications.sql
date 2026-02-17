-- ============================================
-- Trademark Applications Table
-- Stores user-submitted trademark registration applications
-- that specialists process and file with TURKPATENT
-- ============================================

-- Application status enum
DO $$ BEGIN
    CREATE TYPE application_status AS ENUM (
        'draft', 'submitted', 'under_review', 'approved', 'rejected', 'completed'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Mark type enum
DO $$ BEGIN
    CREATE TYPE mark_type AS ENUM (
        'word', 'figurative', 'combined'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Application type enum
DO $$ BEGIN
    CREATE TYPE application_type AS ENUM (
        'registration', 'appeal', 'renewal'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Main table
CREATE TABLE IF NOT EXISTS trademark_applications_mt (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Status
    status application_status NOT NULL DEFAULT 'draft',

    -- Application type
    application_type application_type NOT NULL DEFAULT 'registration',

    -- Applicant info
    applicant_full_name VARCHAR(255),
    applicant_id_no VARCHAR(50),
    applicant_id_type VARCHAR(20) DEFAULT 'tc_kimlik',  -- tc_kimlik or vergi_no
    applicant_address TEXT,
    applicant_phone VARCHAR(30),
    applicant_email VARCHAR(255),

    -- Trademark info
    brand_name VARCHAR(500) NOT NULL,
    mark_type mark_type NOT NULL DEFAULT 'word',
    nice_class_numbers INTEGER[] NOT NULL DEFAULT '{}',
    goods_services_description TEXT,
    logo_path VARCHAR(500),

    -- Notes
    notes TEXT,
    specialist_notes TEXT,
    rejection_reason TEXT,

    -- Admin / processing
    assigned_specialist_id UUID REFERENCES users(id),
    turkpatent_application_no VARCHAR(50),
    turkpatent_filing_date DATE,

    -- Context from search
    source_search_query VARCHAR(500),
    source_risk_score REAL,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at TIMESTAMPTZ,
    reviewed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tma_org_id ON trademark_applications_mt(organization_id);
CREATE INDEX IF NOT EXISTS idx_tma_user_id ON trademark_applications_mt(user_id);
CREATE INDEX IF NOT EXISTS idx_tma_status ON trademark_applications_mt(status);
CREATE INDEX IF NOT EXISTS idx_tma_created_at ON trademark_applications_mt(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tma_org_status ON trademark_applications_mt(organization_id, status);
