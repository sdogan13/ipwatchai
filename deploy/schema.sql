-- ============================================
-- IP Watch AI - Consolidated Database Schema
-- All 30+ tables in dependency order
-- Idempotent: safe to run multiple times
-- ============================================

-- ==========================================
-- 0. EXTENSIONS (created by init-db.sh, but safe to repeat)
-- ==========================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "fuzzystrmatch";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ==========================================
-- 1. ENUM TYPES
-- ==========================================
DO $$ BEGIN
    CREATE TYPE tm_status AS ENUM (
        'Başvuruldu', 'Yayında', 'İtiraz Edildi', 'Tescil Edildi',
        'Reddedildi', 'Geri Çekildi', 'Devredildi', 'Yenilendi',
        'Kısmi Red', 'Süresi Doldu', 'Bilinmiyor'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- ==========================================
-- 2. SYSTEM / REFERENCE TABLES (no FK deps)
-- ==========================================

-- Processed files tracking
CREATE TABLE IF NOT EXISTS processed_files (
    filename VARCHAR(512) PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) CHECK (status IN ('success', 'failed', 'processing')),
    record_count INT DEFAULT 0,
    error_log TEXT
);

-- Nice classes lookup
CREATE TABLE IF NOT EXISTS nice_classes_lookup (
    class_number INTEGER PRIMARY KEY,
    name_tr VARCHAR(500),
    name_en VARCHAR(500),
    description TEXT,
    description_tr TEXT,
    description_en TEXT,
    description_embedding halfvec(384),
    keywords TEXT[],
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Seed nice classes if empty
INSERT INTO nice_classes_lookup (class_number, description)
SELECT v.class_number, v.description FROM (VALUES
    (1, 'Chemicals used in industry, science...'),
    (2, 'Paints, varnishes, lacquers...'),
    (3, 'Bleaching preparations...'),
    (4, 'Industrial oils and greases...'),
    (5, 'Pharmaceuticals...'),
    (6, 'Common metals...'),
    (7, 'Machines...'),
    (8, 'Hand tools...'),
    (9, 'Scientific, research...'),
    (10, 'Surgical, medical...'),
    (11, 'Apparatus for lighting...'),
    (12, 'Vehicles...'),
    (13, 'Firearms...'),
    (14, 'Precious metals...'),
    (15, 'Musical instruments...'),
    (16, 'Paper and cardboard...'),
    (17, 'Unprocessed rubber...'),
    (18, 'Leather...'),
    (19, 'Building materials...'),
    (20, 'Furniture...'),
    (21, 'Household utensils...'),
    (22, 'Ropes and string...'),
    (23, 'Yarns and threads...'),
    (24, 'Textiles...'),
    (25, 'Clothing, footwear, headwear...'),
    (26, 'Lace, braid and embroidery...'),
    (27, 'Carpets, rugs...'),
    (28, 'Games, toys...'),
    (29, 'Meat, fish, poultry...'),
    (30, 'Coffee, tea, cocoa...'),
    (31, 'Raw agricultural products...'),
    (32, 'Beers; non-alcoholic beverages...'),
    (33, 'Alcoholic beverages (except beers)...'),
    (34, 'Tobacco...'),
    (35, 'Advertising; business management...'),
    (36, 'Financial; real estate...'),
    (37, 'Construction; repair...'),
    (38, 'Telecommunications...'),
    (39, 'Transport; storage...'),
    (40, 'Treatment of materials...'),
    (41, 'Education; entertainment...'),
    (42, 'Scientific and technological...'),
    (43, 'Services for providing food and drink...'),
    (44, 'Medical services...'),
    (45, 'Legal services...')
) AS v(class_number, description)
WHERE NOT EXISTS (SELECT 1 FROM nice_classes_lookup WHERE nice_classes_lookup.class_number = v.class_number);

-- Class 99 (Global Brand)
INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description)
SELECT 99, 'Global Marka (Tum Siniflar)', 'Global Brand (All Classes)',
       'Special class covering all 45 Nice classes'
WHERE NOT EXISTS (SELECT 1 FROM nice_classes_lookup WHERE class_number = 99);

-- Holders
CREATE TABLE IF NOT EXISTS holders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tpe_client_id VARCHAR(255) UNIQUE,
    name TEXT NOT NULL,
    address TEXT,
    city VARCHAR(255),
    country VARCHAR(255),
    postal_code VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_holders_name_trgm ON holders USING GIST (name gist_trgm_ops);

-- Word IDF table (computed from trademarks, populated by compute_idf.py)
CREATE TABLE IF NOT EXISTS word_idf (
    word VARCHAR(255) PRIMARY KEY,
    idf_score FLOAT DEFAULT 0,
    doc_frequency INTEGER DEFAULT 0,
    word_class VARCHAR(50) DEFAULT 'common',
    is_generic BOOLEAN DEFAULT FALSE,
    document_frequency INTEGER DEFAULT 0,
    total_documents INTEGER DEFAULT 0,
    weight_multiplier FLOAT DEFAULT 1.0,
    updated_at TIMESTAMP,
    descriptor_like BOOLEAN DEFAULT FALSE,
    descriptor_score DOUBLE PRECISION DEFAULT 0,
    descriptor_stats JSONB DEFAULT '{}'::jsonb
);

-- Word IDF table for TRANSLATED names (computed from trademarks.name_tr, populated by compute_idf.py)
-- Used by dual-path scoring: Path B scores query against translated name with its own IDF weights
CREATE TABLE IF NOT EXISTS word_idf_tr (
    word VARCHAR(255) PRIMARY KEY,
    idf_score FLOAT DEFAULT 0,
    doc_frequency INTEGER DEFAULT 0,
    word_class VARCHAR(50) DEFAULT 'common',
    is_generic BOOLEAN DEFAULT FALSE,
    document_frequency INTEGER DEFAULT 0,
    total_documents INTEGER DEFAULT 0,
    weight_multiplier FLOAT DEFAULT 1.0,
    updated_at TIMESTAMP,
    descriptor_like BOOLEAN DEFAULT FALSE,
    descriptor_score DOUBLE PRECISION DEFAULT 0,
    descriptor_stats JSONB DEFAULT '{}'::jsonb
);

-- ==========================================
-- 3. TRADEMARKS (main data table)
-- ==========================================
CREATE TABLE IF NOT EXISTS trademarks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    registry_type VARCHAR(20) NOT NULL DEFAULT 'trademark'
        CHECK (registry_type IN ('trademark', 'design')),
    application_no VARCHAR(255) UNIQUE NOT NULL,
    registration_no VARCHAR(255),
    wipo_no VARCHAR(255),
    current_status tm_status DEFAULT 'Yayında',
    holder_id UUID REFERENCES holders(id),
    name TEXT,
    nice_class_numbers INTEGER[],
    vienna_class_numbers INTEGER[],
    extracted_goods JSONB,
    image_path TEXT,
    bulletin_no VARCHAR(255),
    bulletin_date DATE,
    gazette_no VARCHAR(255),
    gazette_date DATE,
    -- AI Vectors
    image_embedding halfvec(512),
    dinov2_embedding halfvec(768),
    color_histogram halfvec(512),
    -- Translation fields
    name_tr VARCHAR(500),
    name_en VARCHAR(500),
    name_ku VARCHAR(500),
    name_fa VARCHAR(500),
    detected_lang VARCHAR(10),
    name_tr_backend VARCHAR(32),
    name_tr_model VARCHAR(255),
    name_tr_updated_at TIMESTAMP,
    -- Dates
    application_date DATE,
    registration_date DATE,
    expiry_date DATE,
    appeal_deadline DATE,
    last_event_date DATE,
    availability_status VARCHAR(100),
    -- Event-derived columns (computed by ingest_events.py)
    effective_status tm_status,
    active_restriction_count INTEGER DEFAULT 0,
    current_holder_name TEXT,
    holder_changed_at DATE,
    renewal_expiry DATE,
    last_event_type VARCHAR(50),
    has_restrictions BOOLEAN DEFAULT FALSE,
    event_flags JSONB DEFAULT '{}',
    total_event_count INTEGER DEFAULT 0,
    -- Unified final status (reconciled from current_status + effective_status)
    final_status tm_status,
    final_status_at DATE,
    final_status_source VARCHAR(10),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Trademark indexes
CREATE INDEX IF NOT EXISTS idx_tm_app_no ON trademarks(application_no);
CREATE INDEX IF NOT EXISTS idx_tm_application_date_id_desc
    ON trademarks(application_date DESC, id DESC)
    WHERE name IS NOT NULL AND name != '';
CREATE INDEX IF NOT EXISTS idx_tm_status ON trademarks(current_status);
CREATE INDEX IF NOT EXISTS idx_tm_name_trgm ON trademarks USING GIST (name gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tm_phonetic ON trademarks (dmetaphone(name));
CREATE INDEX IF NOT EXISTS idx_tm_nice_classes_arr ON trademarks USING GIN (nice_class_numbers);
CREATE INDEX IF NOT EXISTS idx_tm_extracted_goods ON trademarks USING GIN (extracted_goods);
CREATE INDEX IF NOT EXISTS idx_tm_image_vec ON trademarks USING hnsw (image_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200) WHERE image_embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_dinov2_vec ON trademarks USING hnsw (dinov2_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200) WHERE dinov2_embedding IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_color_vec ON trademarks USING hnsw (color_histogram halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200) WHERE color_histogram IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_ocr_trgm ON trademarks USING gin (logo_ocr_text gin_trgm_ops);
-- Translation indexes
CREATE INDEX IF NOT EXISTS idx_trademarks_name_tr ON trademarks(name_tr);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_en ON trademarks(name_en);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_ku ON trademarks(name_ku);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_fa ON trademarks(name_fa);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_tr_trgm ON trademarks USING gin(name_tr gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_en_trgm ON trademarks USING gin(name_en gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_ku_trgm ON trademarks USING gin(name_ku gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_trademarks_name_fa_trgm ON trademarks USING gin(name_fa gin_trgm_ops);
-- Event-derived indexes
CREATE INDEX IF NOT EXISTS idx_tm_effective_status ON trademarks(effective_status)
    WHERE effective_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_restrictions ON trademarks(active_restriction_count)
    WHERE active_restriction_count > 0;
CREATE INDEX IF NOT EXISTS idx_tm_holder_changed ON trademarks(holder_changed_at)
    WHERE holder_changed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tm_event_flags ON trademarks USING GIN (event_flags)
    WHERE event_flags != '{}';
CREATE INDEX IF NOT EXISTS idx_tm_final_status ON trademarks(final_status);

-- Trademark history (partitioned)
CREATE TABLE IF NOT EXISTS trademark_history (
    id UUID DEFAULT uuid_generate_v4(),
    trademark_id UUID NOT NULL,
    event_date DATE NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    source_file VARCHAR(512),
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, event_date)
) PARTITION BY RANGE (event_date);

-- Create partitions (ignore if already exist)
DO $$ BEGIN
    CREATE TABLE trademark_history_legacy PARTITION OF trademark_history FOR VALUES FROM (MINVALUE) TO ('2023-01-01');
EXCEPTION WHEN duplicate_table THEN null; END $$;
DO $$ BEGIN
    CREATE TABLE trademark_history_2024 PARTITION OF trademark_history FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
EXCEPTION WHEN duplicate_table THEN null; END $$;
DO $$ BEGIN
    CREATE TABLE trademark_history_2025 PARTITION OF trademark_history FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
EXCEPTION WHEN duplicate_table THEN null; END $$;
DO $$ BEGIN
    CREATE TABLE trademark_history_2026 PARTITION OF trademark_history FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
EXCEPTION WHEN duplicate_table THEN null; END $$;

-- ==========================================
-- 4. AUTH & MULTI-TENANT TABLES
-- ==========================================

-- Subscription plans
CREATE TABLE IF NOT EXISTS subscription_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    max_watchlist_items INTEGER NOT NULL,
    max_alerts_per_month INTEGER NOT NULL,
    max_reports_per_month INTEGER NOT NULL,
    max_api_calls_per_day INTEGER NOT NULL,
    can_use_live_search BOOLEAN DEFAULT FALSE,
    can_export_reports BOOLEAN DEFAULT FALSE,
    can_use_visual_search BOOLEAN DEFAULT FALSE,
    price_monthly DECIMAL(10,2) DEFAULT 0,
    price_yearly DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    -- Lead limits
    daily_lead_limit INTEGER DEFAULT 0,
    can_access_leads BOOLEAN DEFAULT FALSE,
    -- Creative Suite limits
    name_suggestions_per_session INTEGER DEFAULT 5,
    logo_runs_per_month INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed plans
INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'free', 'Free Trial', 5, 10, 1, 50, FALSE, FALSE, FALSE, 0, 0, FALSE, 5, 1
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'free');

INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'starter', 'Starter', 15, 100, 10, 500, TRUE, TRUE, FALSE, 499, 0, FALSE, 10, 6
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'starter');

INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'professional', 'Professional', 50, 200, 20, 1000, TRUE, TRUE, TRUE, 799, 5, TRUE, 20, 20
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'professional');

INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'business', 'Business', 1000, 500, 30, 5000, TRUE, TRUE, TRUE, 1299, 10, TRUE, 30, 60
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'business');

INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'enterprise', 'Enterprise', 999999, 999999, 999999, 999999, TRUE, TRUE, TRUE, 2999, 999999, TRUE, 999999, 999999
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'enterprise');

-- Organizations
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE,
    tax_id VARCHAR(50),
    industry VARCHAR(100),
    address TEXT,
    city VARCHAR(100),
    country VARCHAR(100) DEFAULT 'Turkiye',
    phone VARCHAR(50),
    website VARCHAR(255),
    email_notifications BOOLEAN DEFAULT TRUE,
    weekly_report BOOLEAN DEFAULT TRUE,
    subscription_plan_id UUID REFERENCES subscription_plans(id),
    subscription_start_date DATE,
    subscription_end_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    -- Creative Suite credits (legacy)
    logo_credits_monthly INTEGER DEFAULT 1,
    logo_credits_purchased INTEGER DEFAULT 0,
    name_credits_purchased INTEGER DEFAULT 0,
    logo_credits_reset_at TIMESTAMP DEFAULT NOW(),
    -- Unified AI credits
    ai_credits_monthly INTEGER DEFAULT 0,
    ai_credits_purchased INTEGER DEFAULT 0,
    ai_credits_reset_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    -- Authentication
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    -- Profile
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    phone VARCHAR(50),
    avatar_url TEXT,
    preferred_language VARCHAR(10) DEFAULT 'tr',
    timezone VARCHAR(50) DEFAULT 'Europe/Istanbul',
    title VARCHAR(100),
    department VARCHAR(100),
    linkedin VARCHAR(200),
    -- Role & Permissions
    role VARCHAR(50) DEFAULT 'user',
    is_organization_admin BOOLEAN DEFAULT FALSE,
    is_superadmin BOOLEAN NOT NULL DEFAULT FALSE,
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    is_email_verified BOOLEAN DEFAULT FALSE,
    email_verified_at TIMESTAMP,
    -- Individual plan
    individual_plan_id UUID REFERENCES subscription_plans(id),
    -- Security
    last_login_at TIMESTAMP,
    last_login_ip VARCHAR(45),
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    password_changed_at TIMESTAMP,
    must_change_password BOOLEAN DEFAULT FALSE,
    -- Notification Preferences
    notify_email BOOLEAN DEFAULT TRUE,
    notify_sms BOOLEAN DEFAULT FALSE,
    notify_webhook BOOLEAN DEFAULT FALSE,
    webhook_url TEXT,
    alert_threshold FLOAT DEFAULT 0.5,
    digest_frequency VARCHAR(20) DEFAULT 'daily',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id);
CREATE INDEX IF NOT EXISTS idx_users_is_superadmin ON users (is_superadmin) WHERE is_superadmin = TRUE;
CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at);

-- User sessions
CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    device_info TEXT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token_hash);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Email verification tokens
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(255) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 5. MULTI-TENANT WATCHLIST & ALERTS
-- ==========================================

-- Watchlist (multi-tenant)
CREATE TABLE IF NOT EXISTS watchlist_mt (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    brand_name TEXT NOT NULL,
    brand_name_normalized VARCHAR(500),
    nice_class_numbers INTEGER[] NOT NULL,
    description TEXT,
    customer_application_no VARCHAR(100),
    customer_registration_no VARCHAR(100),
    customer_registration_date DATE,
    customer_bulletin_no VARCHAR(100),
    logo_path TEXT,
    logo_embedding halfvec(512),
    logo_dinov2_embedding halfvec(768),
    logo_color_histogram halfvec(512),
    logo_ocr_text TEXT,
    text_embedding halfvec(384),
    alert_threshold FLOAT DEFAULT 0.5,
    monitor_new_applications BOOLEAN DEFAULT TRUE,
    monitor_registrations BOOLEAN DEFAULT TRUE,
    monitor_similar_names BOOLEAN DEFAULT TRUE,
    monitor_similar_logos BOOLEAN DEFAULT TRUE,
    is_active BOOLEAN DEFAULT TRUE,
    priority VARCHAR(20) DEFAULT 'normal',
    total_alerts_generated INTEGER DEFAULT 0,
    last_scan_at TIMESTAMP,
    last_alert_at TIMESTAMP,
    notes TEXT,
    tags VARCHAR(100)[],
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_watchlist_mt_user ON watchlist_mt(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_mt_org ON watchlist_mt(organization_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_mt_active ON watchlist_mt(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_watchlist_mt_name_trgm ON watchlist_mt USING GIST (brand_name gist_trgm_ops);

-- Alerts (multi-tenant)
CREATE TABLE IF NOT EXISTS alerts_mt (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    watchlist_item_id UUID NOT NULL REFERENCES watchlist_mt(id) ON DELETE CASCADE,
    conflicting_trademark_id UUID REFERENCES trademarks(id) ON DELETE SET NULL,
    conflicting_application_no VARCHAR(255),
    conflicting_name TEXT,
    conflicting_classes INTEGER[],
    conflicting_status VARCHAR(50),
    conflicting_holder_name TEXT,
    conflicting_image_path TEXT,
    overall_risk_score FLOAT NOT NULL,
    text_similarity_score FLOAT,
    semantic_similarity_score FLOAT,
    visual_similarity_score FLOAT,
    translation_similarity_score REAL DEFAULT 0,
    phonetic_match BOOLEAN DEFAULT FALSE,
    exact_match BOOLEAN DEFAULT FALSE,
    alert_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) DEFAULT 'medium',
    source_bulletin VARCHAR(100),
    source_type VARCHAR(50),
    status VARCHAR(30) DEFAULT 'new',
    acknowledged_at TIMESTAMP,
    acknowledged_by UUID REFERENCES users(id),
    resolved_at TIMESTAMP,
    resolved_by UUID REFERENCES users(id),
    resolution_notes TEXT,
    email_sent BOOLEAN DEFAULT FALSE,
    email_sent_at TIMESTAMP,
    sms_sent BOOLEAN DEFAULT FALSE,
    sms_sent_at TIMESTAMP,
    webhook_sent BOOLEAN DEFAULT FALSE,
    webhook_sent_at TIMESTAMP,
    included_in_digest BOOLEAN DEFAULT FALSE,
    digest_sent_at TIMESTAMP,
    opposition_deadline DATE,
    days_until_deadline INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_user ON alerts_mt(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_org ON alerts_mt(organization_id);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_watchlist ON alerts_mt(watchlist_item_id);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_status ON alerts_mt(status);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_severity ON alerts_mt(severity);
CREATE INDEX IF NOT EXISTS idx_alerts_mt_created ON alerts_mt(created_at DESC);

-- ==========================================
-- 6. SCANNING & MONITORING
-- ==========================================

CREATE TABLE IF NOT EXISTS scan_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_type VARCHAR(50) NOT NULL,
    source_folder VARCHAR(255),
    source_file VARCHAR(255),
    total_trademarks_scanned INTEGER DEFAULT 0,
    total_watchlist_items_checked INTEGER DEFAULT 0,
    total_alerts_generated INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scan_jobs_status ON scan_jobs(status);
CREATE INDEX IF NOT EXISTS idx_scan_jobs_source ON scan_jobs(source_folder);

CREATE TABLE IF NOT EXISTS scan_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scan_job_id UUID NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    watchlist_item_id UUID NOT NULL REFERENCES watchlist_mt(id) ON DELETE CASCADE,
    matches_found INTEGER DEFAULT 0,
    highest_similarity FLOAT,
    alerts_created INTEGER DEFAULT 0,
    scan_duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 7. REPORTS
-- ==========================================
CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    report_type VARCHAR(50) NOT NULL,
    report_name VARCHAR(255),
    description TEXT,
    watchlist_item_id UUID,
    date_range_start DATE,
    date_range_end DATE,
    file_path TEXT,
    file_format VARCHAR(20) DEFAULT 'pdf',
    file_size_bytes INTEGER,
    status VARCHAR(30) DEFAULT 'pending',
    generated_at TIMESTAMP,
    expires_at TIMESTAMP,
    download_count INTEGER DEFAULT 0,
    last_downloaded_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_org ON reports(organization_id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);

CREATE TABLE IF NOT EXISTS pending_risk_reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    claim_token_hash VARCHAR(128) UNIQUE NOT NULL,
    query_text VARCHAR(300),
    selected_classes JSONB DEFAULT '[]'::jsonb,
    language VARCHAR(5) DEFAULT 'tr',
    image_used BOOLEAN DEFAULT FALSE,
    summary TEXT,
    overall_risk_score NUMERIC,
    highest_risk_application_no VARCHAR(80),
    results_json JSONB NOT NULL,
    request_json JSONB NOT NULL,
    response_json JSONB NOT NULL,
    model VARCHAR(160),
    report_name VARCHAR(255),
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER,
    expires_at TIMESTAMPTZ NOT NULL,
    claimed_at TIMESTAMPTZ,
    claimed_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    claimed_by_organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pending_risk_reports_token ON pending_risk_reports(claim_token_hash);
CREATE INDEX IF NOT EXISTS idx_pending_risk_reports_expires ON pending_risk_reports(expires_at);

-- ==========================================
-- 8. AUDIT LOG
-- ==========================================
CREATE TABLE IF NOT EXISTS audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id UUID,
    ip_address VARCHAR(45),
    user_agent TEXT,
    request_method VARCHAR(10),
    request_path TEXT,
    old_values JSONB,
    new_values JSONB,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(organization_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

-- ==========================================
-- 9. API USAGE
-- ==========================================
CREATE TABLE IF NOT EXISTS api_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    api_calls INTEGER DEFAULT 0,
    watchlist_scans INTEGER DEFAULT 0,
    alerts_generated INTEGER DEFAULT 0,
    reports_generated INTEGER DEFAULT 0,
    live_searches INTEGER DEFAULT 0,
    leads_viewed INTEGER DEFAULT 0,
    name_generations INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, usage_date)
);
CREATE INDEX IF NOT EXISTS idx_api_usage_user_date ON api_usage(user_id, usage_date);

CREATE TABLE IF NOT EXISTS public_search_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id VARCHAR(255) NOT NULL,
    usage_date DATE NOT NULL,
    searches INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, usage_date)
);
CREATE INDEX IF NOT EXISTS idx_public_search_usage_client_date ON public_search_usage(client_id, usage_date);

CREATE TABLE IF NOT EXISTS education_progress (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_type VARCHAR(20) NOT NULL CHECK (item_type IN ('pdf', 'flashcard', 'quiz')),
    item_key VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'completed')),
    percent_complete INTEGER NOT NULL DEFAULT 0 CHECK (percent_complete >= 0 AND percent_complete <= 100),
    progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    completed_at TIMESTAMP,
    last_interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, item_type, item_key)
);
CREATE INDEX IF NOT EXISTS idx_education_progress_user ON education_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_education_progress_user_status ON education_progress(user_id, status);
CREATE INDEX IF NOT EXISTS idx_education_progress_updated ON education_progress(updated_at DESC);

-- ==========================================
-- 10. NOTIFICATION QUEUE
-- ==========================================
CREATE TABLE IF NOT EXISTS notification_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    alert_id UUID REFERENCES alerts_mt(id) ON DELETE CASCADE,
    notification_type VARCHAR(30) NOT NULL,
    priority INTEGER DEFAULT 5,
    subject VARCHAR(500),
    body TEXT,
    template_name VARCHAR(100),
    template_data JSONB,
    recipient VARCHAR(255),
    status VARCHAR(30) DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    last_attempt_at TIMESTAMP,
    sent_at TIMESTAMP,
    error_message TEXT,
    scheduled_for TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notification_queue_status ON notification_queue(status) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_notification_queue_scheduled ON notification_queue(scheduled_for) WHERE status = 'pending';

-- ==========================================
-- 11. APP SETTINGS (runtime configuration)
-- ==========================================
CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(200) PRIMARY KEY,
    value JSONB NOT NULL,
    category VARCHAR(50) NOT NULL,
    description TEXT,
    value_type VARCHAR(20) NOT NULL DEFAULT 'string',
    updated_at TIMESTAMP DEFAULT NOW(),
    updated_by UUID REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_app_settings_category ON app_settings(category);

-- ==========================================
-- 12. DISCOUNT CODES
-- ==========================================
CREATE TABLE IF NOT EXISTS discount_codes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT,
    discount_type VARCHAR(20) NOT NULL DEFAULT 'percentage',
    discount_value DECIMAL(10,2) NOT NULL,
    applies_to_plan VARCHAR(50),
    max_uses INTEGER,
    current_uses INTEGER DEFAULT 0,
    valid_from TIMESTAMP DEFAULT NOW(),
    valid_until TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_discount_codes_code ON discount_codes(code);
CREATE INDEX IF NOT EXISTS idx_discount_codes_active ON discount_codes(is_active);

CREATE TABLE IF NOT EXISTS discount_code_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    discount_code_id UUID REFERENCES discount_codes(id),
    organization_id UUID REFERENCES organizations(id),
    applied_at TIMESTAMP DEFAULT NOW(),
    discount_amount DECIMAL(10,2),
    plan_name VARCHAR(50)
);
CREATE INDEX IF NOT EXISTS idx_discount_usage_code ON discount_code_usage(discount_code_id);
CREATE INDEX IF NOT EXISTS idx_discount_usage_org ON discount_code_usage(organization_id);

-- ==========================================
-- 13. RADAR (universal conflicts)
-- ==========================================
CREATE TABLE IF NOT EXISTS universal_conflicts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    new_mark_id UUID NOT NULL,
    new_mark_name VARCHAR(500),
    new_mark_app_no VARCHAR(100),
    new_mark_holder_name VARCHAR(500),
    new_mark_nice_classes INTEGER[],
    existing_mark_id UUID NOT NULL,
    existing_mark_name VARCHAR(500),
    existing_mark_app_no VARCHAR(100),
    existing_mark_holder_id UUID,
    existing_mark_holder_name VARCHAR(500),
    existing_mark_nice_classes INTEGER[],
    similarity_score FLOAT NOT NULL,
    text_similarity FLOAT,
    visual_similarity FLOAT,
    semantic_similarity FLOAT,
    translation_similarity REAL DEFAULT 0,
    conflict_type VARCHAR(50) NOT NULL,
    overlapping_classes INTEGER[],
    risk_level VARCHAR(20),
    conflict_reasons TEXT[],
    bulletin_no VARCHAR(50),
    bulletin_date DATE,
    opposition_deadline DATE NOT NULL,
    -- days_until_deadline: computed dynamically as (opposition_deadline - CURRENT_DATE) in queries
    lead_status VARCHAR(50) DEFAULT 'new',
    viewed_by UUID[],
    contacted_at TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT fk_new_mark FOREIGN KEY(new_mark_id) REFERENCES trademarks(id) ON DELETE CASCADE,
    CONSTRAINT fk_existing_mark FOREIGN KEY(existing_mark_id) REFERENCES trademarks(id) ON DELETE CASCADE,
    CONSTRAINT unique_conflict UNIQUE(new_mark_id, existing_mark_id)
);
CREATE INDEX IF NOT EXISTS idx_uc_deadline_score ON universal_conflicts(opposition_deadline ASC, similarity_score DESC);
CREATE INDEX IF NOT EXISTS idx_uc_opposition_deadline ON universal_conflicts(opposition_deadline);
CREATE INDEX IF NOT EXISTS idx_uc_overlapping_classes ON universal_conflicts USING GIN(overlapping_classes);
CREATE INDEX IF NOT EXISTS idx_uc_risk_level ON universal_conflicts(risk_level, similarity_score DESC);
CREATE INDEX IF NOT EXISTS idx_uc_lead_status ON universal_conflicts(lead_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uc_new_mark ON universal_conflicts(new_mark_id);
CREATE INDEX IF NOT EXISTS idx_uc_existing_mark ON universal_conflicts(existing_mark_id);
CREATE INDEX IF NOT EXISTS idx_uc_bulletin ON universal_conflicts(bulletin_no, bulletin_date);

CREATE TABLE IF NOT EXISTS universal_scan_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trademark_id UUID NOT NULL REFERENCES trademarks(id) ON DELETE CASCADE,
    bulletin_no VARCHAR(50),
    bulletin_date DATE,
    priority INTEGER DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    CONSTRAINT unique_queue_item UNIQUE(trademark_id)
);
CREATE INDEX IF NOT EXISTS idx_usq_status ON universal_scan_queue(status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS lead_access_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    organization_id UUID,
    conflict_id UUID NOT NULL REFERENCES universal_conflicts(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lal_user ON lead_access_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lal_conflict ON lead_access_log(conflict_id);

-- ==========================================
-- 14. CREATIVE SUITE
-- ==========================================
CREATE TABLE IF NOT EXISTS generation_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES organizations(id),
    user_id UUID REFERENCES users(id),
    feature_type VARCHAR(50) NOT NULL,
    input_prompt TEXT,
    input_params JSONB,
    output_data JSONB,
    credits_used INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gen_logs_org ON generation_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_gen_logs_user ON generation_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_gen_logs_type ON generation_logs(feature_type);
CREATE INDEX IF NOT EXISTS idx_gen_logs_created ON generation_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS generated_images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    generation_log_id UUID REFERENCES generation_logs(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id),
    image_path TEXT NOT NULL,
    clip_embedding halfvec(512),
    dino_embedding halfvec(768),
    ocr_text TEXT,
    visual_breakdown JSONB,
    similarity_score FLOAT,
    is_safe BOOLEAN DEFAULT TRUE,
    style VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gen_images_log ON generated_images(generation_log_id);
CREATE INDEX IF NOT EXISTS idx_gen_images_org ON generated_images(org_id);

-- ==========================================
-- 15. PIPELINE RUNS
-- ==========================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    triggered_by VARCHAR(50) DEFAULT 'schedule',
    skip_download BOOLEAN DEFAULT FALSE,
    step_download JSONB,
    step_extract JSONB,
    step_metadata JSONB,
    step_embeddings JSONB,
    step_ingest JSONB,
    step_event_ingest JSONB,
    step_final_status_repair JSONB,
    total_downloaded INTEGER DEFAULT 0,
    total_extracted INTEGER DEFAULT 0,
    total_parsed INTEGER DEFAULT 0,
    total_embedded INTEGER DEFAULT 0,
    total_ingested INTEGER DEFAULT 0,
    total_event_scopes_ingested INTEGER DEFAULT 0,
    total_final_status_repaired INTEGER DEFAULT 0,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    heartbeat_at TIMESTAMP DEFAULT NOW(),
    current_step VARCHAR(50),
    duration_seconds FLOAT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);

-- ==========================================
-- 16. LEGACY TABLES (kept for backward compat)
-- ==========================================
-- Legacy watchlist (original non-MT version)
CREATE TABLE IF NOT EXISTS watchlist (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    brand_name TEXT NOT NULL,
    nice_class_numbers INTEGER[] NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Legacy alerts
CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100),
    watched_trademark_id UUID REFERENCES watchlist(id),
    conflicting_trademark_id UUID,
    risk_score FLOAT,
    status VARCHAR(20) DEFAULT 'Pending',
    immediate_sent BOOLEAN DEFAULT FALSE,
    reminder_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ==========================================
-- 17. FUNCTIONS & TRIGGERS
-- ==========================================

-- update_updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_organizations_updated_at ON organizations;
CREATE TRIGGER update_organizations_updated_at BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_watchlist_mt_updated_at ON watchlist_mt;
CREATE TRIGGER update_watchlist_mt_updated_at BEFORE UPDATE ON watchlist_mt
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_alerts_mt_updated_at ON alerts_mt;
CREATE TRIGGER update_alerts_mt_updated_at BEFORE UPDATE ON alerts_mt
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_education_progress_updated_at ON education_progress;
CREATE TRIGGER update_education_progress_updated_at BEFORE UPDATE ON education_progress
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Normalize brand name
CREATE OR REPLACE FUNCTION normalize_brand_name()
RETURNS TRIGGER AS $$
BEGIN
    NEW.brand_name_normalized = LOWER(TRIM(NEW.brand_name));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS normalize_watchlist_mt_brand_name ON watchlist_mt;
CREATE TRIGGER normalize_watchlist_mt_brand_name BEFORE INSERT OR UPDATE ON watchlist_mt
    FOR EACH ROW EXECUTE FUNCTION normalize_brand_name();

-- Opposition deadline calculator
CREATE OR REPLACE FUNCTION calculate_opposition_deadline(bulletin_date DATE)
RETURNS DATE AS $$
BEGIN
    RETURN bulletin_date + INTERVAL '2 months';
END;
$$ LANGUAGE plpgsql;

-- Alert counter increment
CREATE OR REPLACE FUNCTION increment_watchlist_mt_alert_counter()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE watchlist_mt
    SET total_alerts_generated = total_alerts_generated + 1,
        last_alert_at = CURRENT_TIMESTAMP
    WHERE id = NEW.watchlist_item_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS increment_alert_mt_counter ON alerts_mt;
CREATE TRIGGER increment_alert_mt_counter AFTER INSERT ON alerts_mt
    FOR EACH ROW EXECUTE FUNCTION increment_watchlist_mt_alert_counter();

-- Universal conflicts timestamp
CREATE OR REPLACE FUNCTION update_universal_conflicts_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_uc_updated_at ON universal_conflicts;
CREATE TRIGGER trigger_uc_updated_at BEFORE UPDATE ON universal_conflicts
    FOR EACH ROW EXECUTE FUNCTION update_universal_conflicts_timestamp();

-- ==========================================
-- 18. VIEWS
-- ==========================================

DROP VIEW IF EXISTS user_dashboard_stats;
CREATE VIEW user_dashboard_stats AS
SELECT
    u.id AS user_id,
    u.organization_id,
    COUNT(DISTINCT w.id) AS total_watchlist_items,
    COUNT(DISTINCT w.id) FILTER (WHERE w.is_active) AS active_watchlist_items,
    COUNT(DISTINCT a.id) AS total_alerts,
    COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'new') AS new_alerts,
    COUNT(DISTINCT a.id) FILTER (WHERE a.severity = 'critical') AS critical_alerts,
    COUNT(DISTINCT a.id) FILTER (WHERE a.status IN ('new', 'seen')) AS pending_alerts,
    MAX(a.created_at) AS latest_alert_at,
    MAX(w.last_scan_at) AS last_scan_at
FROM users u
LEFT JOIN watchlist_mt w ON w.user_id = u.id
LEFT JOIN alerts_mt a ON a.user_id = u.id
GROUP BY u.id, u.organization_id;

DROP VIEW IF EXISTS organization_dashboard_stats;
CREATE VIEW organization_dashboard_stats AS
SELECT
    o.id AS organization_id,
    o.name AS organization_name,
    COUNT(DISTINCT u.id) AS total_users,
    COUNT(DISTINCT w.id) AS total_watchlist_items,
    COUNT(DISTINCT a.id) AS total_alerts,
    COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'new') AS new_alerts,
    COUNT(DISTINCT a.id) FILTER (WHERE a.severity = 'critical') AS critical_alerts
FROM organizations o
LEFT JOIN users u ON u.organization_id = o.id
LEFT JOIN watchlist_mt w ON w.organization_id = o.id
LEFT JOIN alerts_mt a ON a.organization_id = o.id
GROUP BY o.id, o.name;

DROP VIEW IF EXISTS alert_summary_view;
CREATE VIEW alert_summary_view AS
SELECT
    a.id AS alert_id,
    a.user_id,
    a.organization_id,
    w.brand_name AS watched_brand,
    w.nice_class_numbers AS watched_classes,
    a.conflicting_name,
    a.conflicting_application_no,
    a.conflicting_classes,
    a.conflicting_status,
    a.conflicting_holder_name,
    a.overall_risk_score,
    a.severity,
    a.status,
    a.alert_type,
    a.source_type,
    a.opposition_deadline,
    a.days_until_deadline,
    a.created_at
FROM alerts_mt a
JOIN watchlist_mt w ON w.id = a.watchlist_item_id;

DROP VIEW IF EXISTS trademark_dashboard_view;
CREATE VIEW trademark_dashboard_view AS
SELECT
    t.id AS trademark_id,
    t.application_no,
    t.name AS trademark_name,
    t.current_status,
    t.image_path,
    h.name AS holder_name,
    h.city AS holder_city,
    t.nice_class_numbers,
    t.application_date,
    t.registration_date,
    t.expiry_date,
    t.availability_status
FROM trademarks t
LEFT JOIN holders h ON t.holder_id = h.id;

-- Active leads view
CREATE OR REPLACE VIEW active_leads AS
SELECT
    uc.*,
    (uc.opposition_deadline - CURRENT_DATE) as days_until_deadline,
    CASE
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 7 THEN 'critical'
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 14 THEN 'urgent'
        WHEN (uc.opposition_deadline - CURRENT_DATE) <= 30 THEN 'soon'
        ELSE 'normal'
    END as urgency_level
FROM universal_conflicts uc
WHERE uc.opposition_deadline >= CURRENT_DATE
  AND uc.lead_status NOT IN ('dismissed', 'converted')
ORDER BY uc.opposition_deadline ASC, uc.similarity_score DESC;

-- Lead statistics view
CREATE OR REPLACE VIEW lead_statistics AS
SELECT
    COUNT(*) as total_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 7) as critical_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 14) as urgent_leads,
    COUNT(*) FILTER (WHERE (opposition_deadline - CURRENT_DATE) <= 30) as upcoming_leads,
    COUNT(*) FILTER (WHERE lead_status = 'new') as new_leads,
    COUNT(*) FILTER (WHERE lead_status = 'viewed') as viewed_leads,
    COUNT(*) FILTER (WHERE lead_status = 'contacted') as contacted_leads,
    COUNT(*) FILTER (WHERE lead_status = 'converted') as converted_leads,
    AVG(similarity_score) as avg_similarity,
    MAX(created_at) as last_scan_at
FROM universal_conflicts
WHERE opposition_deadline >= CURRENT_DATE;

-- ==========================================
-- 19. SEED DATA (test organization + admin)
-- ==========================================
INSERT INTO organizations (id, name, slug, subscription_plan_id)
SELECT
    'a0000000-0000-0000-0000-000000000001'::uuid,
    'Test Sirketi A.S.',
    'test-sirketi',
    (SELECT id FROM subscription_plans WHERE name = 'professional')
WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE slug = 'test-sirketi');

INSERT INTO users (id, organization_id, email, password_hash, first_name, last_name, role, is_organization_admin, is_email_verified)
SELECT
    'b0000000-0000-0000-0000-000000000001'::uuid,
    'a0000000-0000-0000-0000-000000000001'::uuid,
    'admin@test.com',
    crypt('test123', gen_salt('bf')),
    'Test',
    'Admin',
    'admin',
    TRUE,
    TRUE
WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = 'admin@test.com');

-- ==========================================
-- TASARIM (INDUSTRIAL DESIGN) TABLES
-- Mirrors the trademark/holders pattern but adapted for designs:
--   * multi-design per application
--   * multi-view per design (per-view embeddings + design-level mean-pool)
--   * Locarno classification (no Vienna)
--   * Hague-route entries (registration_no, no application_no)
-- See migrations/designs.sql + migrations/run_designs_migration.py for the
-- canonical migration; this block keeps deploy/schema.sql in sync as the
-- bootstrap source of truth.
-- ==========================================
DO $$ BEGIN
    CREATE TYPE design_status AS ENUM (
        'Yayında', 'Tescil Edildi', 'Hükümsüz', 'Yenilendi',
        'Süresi Doldu', 'Devredildi', 'İptal Edildi',
        'Yayım Ertelendi', 'Bilinmiyor'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS locarno_classes_lookup (
    class_number VARCHAR(2) PRIMARY KEY,
    name_tr      VARCHAR(500),
    name_en      VARCHAR(500),
    description  TEXT,
    updated_at   TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS designs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    registry_type        VARCHAR(20) NOT NULL DEFAULT 'design'
                         CHECK (registry_type IN ('trademark', 'design')),
    application_no       VARCHAR(50),
    design_index         INTEGER NOT NULL DEFAULT 1,
    registration_no      VARCHAR(50),
    section              VARCHAR(20) NOT NULL,
    current_status       design_status DEFAULT 'Yayında',
    effective_status     design_status,
    final_status         design_status,
    final_status_at      DATE,
    final_status_source  VARCHAR(10),
    application_date     DATE,
    filing_date          DATE,
    registration_date    DATE,
    bulletin_no          VARCHAR(10),
    bulletin_date        DATE,
    opposition_end       DATE,
    product_name_tr      VARCHAR(500),
    product_name_en      VARCHAR(500),
    locarno_classes      TEXT[],
    design_count         INTEGER DEFAULT 1,
    holder_id            UUID REFERENCES holders(id) ON DELETE SET NULL,
    designers            TEXT[],
    attorney_name        TEXT,
    attorney_firm        TEXT,
    priorities           JSONB DEFAULT '[]'::jsonb,
    hague_reference      JSONB,
    deferred_publication JSONB,
    dinov2_vitl14_mean   halfvec(1024),
    clip_vitb32_mean     halfvec(512),
    source_issue_folder  VARCHAR(255),
    page_range_start     INTEGER,
    page_range_end       INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_designs_tr_natural
    ON designs (application_no, design_index, section)
    WHERE application_no IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_designs_hague_natural
    ON designs (registration_no, section)
    WHERE application_no IS NULL AND registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_des_app_no ON designs(application_no);
CREATE INDEX IF NOT EXISTS idx_des_reg_no ON designs(registration_no);
CREATE INDEX IF NOT EXISTS idx_des_status ON designs(current_status);
CREATE INDEX IF NOT EXISTS idx_des_section ON designs(section);
CREATE INDEX IF NOT EXISTS idx_des_holder ON designs(holder_id);
CREATE INDEX IF NOT EXISTS idx_des_locarno_arr ON designs USING GIN (locarno_classes);
CREATE INDEX IF NOT EXISTS idx_des_designers_arr ON designs USING GIN (designers);
CREATE INDEX IF NOT EXISTS idx_des_application_date ON designs(application_date DESC);
CREATE INDEX IF NOT EXISTS idx_des_bulletin_date ON designs(bulletin_date DESC);
CREATE INDEX IF NOT EXISTS idx_des_product_trgm ON designs USING GIST (product_name_tr gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_des_dinov2_vec ON designs USING hnsw (dinov2_vitl14_mean halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE dinov2_vitl14_mean IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_des_clip_vec ON designs USING hnsw (clip_vitb32_mean halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE clip_vitb32_mean IS NOT NULL;

CREATE TABLE IF NOT EXISTS design_views (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    design_id UUID NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
    view_index INTEGER NOT NULL,
    page INTEGER,
    image_xref INTEGER,
    bbox NUMERIC[],
    image_path TEXT,
    dinov2_vitl14 halfvec(1024),
    clip_vitb32   halfvec(512),
    color_hsv     halfvec(512),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_design_view ON design_views (design_id, view_index);
CREATE INDEX IF NOT EXISTS idx_dv_design ON design_views(design_id);
CREATE INDEX IF NOT EXISTS idx_dv_dinov2_vec ON design_views USING hnsw (dinov2_vitl14 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE dinov2_vitl14 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dv_clip_vec ON design_views USING hnsw (clip_vitb32 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE clip_vitb32 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dv_color_vec ON design_views USING hnsw (color_hsv halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE color_hsv IS NOT NULL;

CREATE TABLE IF NOT EXISTS design_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    design_id UUID REFERENCES designs(id) ON DELETE SET NULL,
    application_no  VARCHAR(50),
    registration_no VARCHAR(50),
    event_type      VARCHAR(50) NOT NULL,
    event_date      DATE,
    bulletin_no     VARCHAR(10),
    bulletin_date   DATE,
    page            INTEGER,
    details         JSONB DEFAULT '{}'::jsonb,
    free_text       TEXT,
    event_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_design_event ON design_events(event_fingerprint);
CREATE INDEX IF NOT EXISTS idx_de_app_no ON design_events(application_no);
CREATE INDEX IF NOT EXISTS idx_de_reg_no ON design_events(registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_de_type ON design_events(event_type);
CREATE INDEX IF NOT EXISTS idx_de_design ON design_events(design_id) WHERE design_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_de_bulletin_date ON design_events(bulletin_date);

-- ==========================================
-- SCHEMA COMPLETE
-- ==========================================
