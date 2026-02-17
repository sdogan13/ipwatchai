-- Reports table migration
-- Stores generated report metadata and file paths

CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,

    -- Report Details
    report_type VARCHAR(50) NOT NULL,
    report_name VARCHAR(255),
    description TEXT,

    -- Scope
    watchlist_item_id UUID,
    date_range_start DATE,
    date_range_end DATE,

    -- File
    file_path TEXT,
    file_format VARCHAR(20) DEFAULT 'pdf',
    file_size_bytes INTEGER,

    -- Status
    status VARCHAR(30) DEFAULT 'pending',
    generated_at TIMESTAMP,
    expires_at TIMESTAMP,
    download_count INTEGER DEFAULT 0,
    last_downloaded_at TIMESTAMP,

    -- Error tracking
    error_message TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id);
CREATE INDEX IF NOT EXISTS idx_reports_org ON reports(organization_id);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
