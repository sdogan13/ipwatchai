-- Creative Suite: Credit tracking + generation logs
-- Migration for Name Generator & Logo Studio features
-- Usage: python migrations/run_creative_suite_migration.py

-- ============================================================
-- 1. Add credit columns to organizations table
-- ============================================================
ALTER TABLE organizations
ADD COLUMN IF NOT EXISTS logo_credits_monthly INTEGER DEFAULT 1,
ADD COLUMN IF NOT EXISTS logo_credits_purchased INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS name_credits_purchased INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS logo_credits_reset_at TIMESTAMP DEFAULT NOW();

-- ============================================================
-- 2. Add Creative Suite limits to subscription_plans
-- ============================================================
ALTER TABLE subscription_plans
ADD COLUMN IF NOT EXISTS name_suggestions_per_session INTEGER DEFAULT 5,
ADD COLUMN IF NOT EXISTS logo_runs_per_month INTEGER DEFAULT 1;

-- Update plan rows with Creative Suite limits
UPDATE subscription_plans SET name_suggestions_per_session = 5,  logo_runs_per_month = 1  WHERE name = 'free';
UPDATE subscription_plans SET name_suggestions_per_session = 15, logo_runs_per_month = 3  WHERE name = 'starter';
UPDATE subscription_plans SET name_suggestions_per_session = 50, logo_runs_per_month = 15 WHERE name = 'professional';
UPDATE subscription_plans SET name_suggestions_per_session = -1, logo_runs_per_month = 50 WHERE name = 'enterprise';

-- ============================================================
-- 3. Generation history log
-- ============================================================
CREATE TABLE IF NOT EXISTS generation_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES organizations(id),
    user_id UUID REFERENCES users(id),
    feature_type VARCHAR(50) NOT NULL,      -- 'NAME' or 'LOGO'
    input_prompt TEXT,
    input_params JSONB,                     -- search query, nice classes, style prefs, etc.
    output_data JSONB,                      -- generated names/image URLs
    credits_used INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gen_logs_org ON generation_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_gen_logs_user ON generation_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_gen_logs_type ON generation_logs(feature_type);
CREATE INDEX IF NOT EXISTS idx_gen_logs_created ON generation_logs(created_at DESC);

-- ============================================================
-- 4. Generated images storage
-- ============================================================
CREATE TABLE IF NOT EXISTS generated_images (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    generation_log_id UUID REFERENCES generation_logs(id) ON DELETE CASCADE,
    org_id UUID REFERENCES organizations(id),
    image_path TEXT NOT NULL,               -- relative path in uploads/generated/
    clip_embedding halfvec(512),            -- for visual similarity audit
    similarity_score FLOAT,                -- max similarity vs existing trademarks
    is_safe BOOLEAN DEFAULT TRUE,           -- similarity < 65% threshold
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gen_images_log ON generated_images(generation_log_id);
CREATE INDEX IF NOT EXISTS idx_gen_images_org ON generated_images(org_id);
