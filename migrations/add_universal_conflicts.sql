-- ============================================
-- OPPOSITION RADAR: Universal Conflicts Table
-- ============================================
-- Stores conflicts between NEW applications (aggressors)
-- and EXISTING registrations (victims/potential clients)
-- ============================================

-- Main conflicts table
CREATE TABLE IF NOT EXISTS universal_conflicts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- The "Aggressor" (New Application from bulletin)
    new_mark_id UUID NOT NULL,
    new_mark_name VARCHAR(500),
    new_mark_app_no VARCHAR(100),
    new_mark_holder_name VARCHAR(500),
    new_mark_nice_classes INTEGER[],

    -- The "Victim" (Existing Registration - Potential Client)
    existing_mark_id UUID NOT NULL,
    existing_mark_name VARCHAR(500),
    existing_mark_app_no VARCHAR(100),
    existing_mark_holder_id UUID,
    existing_mark_holder_name VARCHAR(500),
    existing_mark_nice_classes INTEGER[],

    -- Conflict Intelligence
    similarity_score FLOAT NOT NULL,           -- 0.0 - 1.0
    text_similarity FLOAT,                     -- Text/phonetic score
    visual_similarity FLOAT,                   -- Image/CLIP score
    semantic_similarity FLOAT,                 -- Embedding score
    conflict_type VARCHAR(50) NOT NULL,        -- 'TEXT', 'VISUAL', 'SEMANTIC', 'HYBRID'
    overlapping_classes INTEGER[],             -- Nice classes in common

    -- Risk Assessment
    risk_level VARCHAR(20),                    -- 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'
    conflict_reasons TEXT[],                   -- Array of reasons

    -- Opportunity Data (The "Ticking Clock")
    bulletin_no VARCHAR(50),
    bulletin_date DATE,
    opposition_deadline DATE NOT NULL,         -- Bulletin Date + 2 months
    days_until_deadline INTEGER GENERATED ALWAYS AS (opposition_deadline - CURRENT_DATE) STORED,

    -- Lead Status
    lead_status VARCHAR(50) DEFAULT 'new',     -- 'new', 'viewed', 'contacted', 'converted', 'dismissed'
    viewed_by UUID[],                          -- Users who viewed this lead
    contacted_at TIMESTAMP,
    notes TEXT,

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Constraints
    CONSTRAINT fk_new_mark FOREIGN KEY(new_mark_id) REFERENCES trademarks(id) ON DELETE CASCADE,
    CONSTRAINT fk_existing_mark FOREIGN KEY(existing_mark_id) REFERENCES trademarks(id) ON DELETE CASCADE,
    CONSTRAINT unique_conflict UNIQUE(new_mark_id, existing_mark_id)
);

-- ============================================
-- INDEXES for Performance
-- ============================================

-- Primary query: "Show me urgent high-value leads"
CREATE INDEX IF NOT EXISTS idx_uc_deadline_score ON universal_conflicts(opposition_deadline ASC, similarity_score DESC);

-- Filter by urgency
CREATE INDEX IF NOT EXISTS idx_uc_days_until ON universal_conflicts(days_until_deadline) WHERE days_until_deadline > 0;

-- Filter by Nice class
CREATE INDEX IF NOT EXISTS idx_uc_overlapping_classes ON universal_conflicts USING GIN(overlapping_classes);

-- Filter by risk level
CREATE INDEX IF NOT EXISTS idx_uc_risk_level ON universal_conflicts(risk_level, similarity_score DESC);

-- Lead management
CREATE INDEX IF NOT EXISTS idx_uc_lead_status ON universal_conflicts(lead_status, created_at DESC);

-- Lookup by marks
CREATE INDEX IF NOT EXISTS idx_uc_new_mark ON universal_conflicts(new_mark_id);
CREATE INDEX IF NOT EXISTS idx_uc_existing_mark ON universal_conflicts(existing_mark_id);

-- Bulletin filtering
CREATE INDEX IF NOT EXISTS idx_uc_bulletin ON universal_conflicts(bulletin_no, bulletin_date);

-- ============================================
-- SCAN QUEUE TABLE (Track what needs scanning)
-- ============================================

CREATE TABLE IF NOT EXISTS universal_scan_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trademark_id UUID NOT NULL REFERENCES trademarks(id) ON DELETE CASCADE,
    bulletin_no VARCHAR(50),
    bulletin_date DATE,
    priority INTEGER DEFAULT 0,              -- Higher = process first
    status VARCHAR(20) DEFAULT 'pending',    -- 'pending', 'processing', 'completed', 'failed'
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,

    CONSTRAINT unique_queue_item UNIQUE(trademark_id)
);

CREATE INDEX IF NOT EXISTS idx_usq_status ON universal_scan_queue(status, priority DESC, created_at);

-- ============================================
-- LEAD ACCESS LOG (Track who viewed what)
-- ============================================

CREATE TABLE IF NOT EXISTS lead_access_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    organization_id UUID,
    conflict_id UUID NOT NULL REFERENCES universal_conflicts(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,             -- 'viewed', 'exported', 'contacted', 'dismissed'
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lal_user ON lead_access_log(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_lal_conflict ON lead_access_log(conflict_id);

-- ============================================
-- HELPER VIEW: Active Leads (Not Expired)
-- ============================================

CREATE OR REPLACE VIEW active_leads AS
SELECT
    uc.*,
    CASE
        WHEN uc.days_until_deadline <= 7 THEN 'critical'
        WHEN uc.days_until_deadline <= 14 THEN 'urgent'
        WHEN uc.days_until_deadline <= 30 THEN 'soon'
        ELSE 'normal'
    END as urgency_level
FROM universal_conflicts uc
WHERE uc.opposition_deadline >= CURRENT_DATE
  AND uc.lead_status NOT IN ('dismissed', 'converted')
ORDER BY uc.opposition_deadline ASC, uc.similarity_score DESC;

-- ============================================
-- HELPER VIEW: Lead Statistics
-- ============================================

CREATE OR REPLACE VIEW lead_statistics AS
SELECT
    COUNT(*) as total_leads,
    COUNT(*) FILTER (WHERE days_until_deadline <= 7) as critical_leads,
    COUNT(*) FILTER (WHERE days_until_deadline <= 14) as urgent_leads,
    COUNT(*) FILTER (WHERE days_until_deadline <= 30) as upcoming_leads,
    COUNT(*) FILTER (WHERE lead_status = 'new') as new_leads,
    COUNT(*) FILTER (WHERE lead_status = 'viewed') as viewed_leads,
    COUNT(*) FILTER (WHERE lead_status = 'contacted') as contacted_leads,
    COUNT(*) FILTER (WHERE lead_status = 'converted') as converted_leads,
    AVG(similarity_score) as avg_similarity,
    MAX(created_at) as last_scan_at
FROM universal_conflicts
WHERE opposition_deadline >= CURRENT_DATE;

-- ============================================
-- UPDATE TIMESTAMP TRIGGER
-- ============================================

CREATE OR REPLACE FUNCTION update_universal_conflicts_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_uc_updated_at ON universal_conflicts;
CREATE TRIGGER trigger_uc_updated_at
    BEFORE UPDATE ON universal_conflicts
    FOR EACH ROW
    EXECUTE FUNCTION update_universal_conflicts_timestamp();
