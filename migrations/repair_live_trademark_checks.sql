CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS repair_live_trademark_checks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trademark_id UUID REFERENCES trademarks(id) ON DELETE CASCADE,
    application_no VARCHAR(255) NOT NULL,
    check_kind VARCHAR(20) NOT NULL,
    query_text TEXT,
    result_code VARCHAR(50) NOT NULL,
    live_status_text TEXT,
    resolved_status TEXT,
    live_nice_classes INTEGER[],
    artifact_dir TEXT,
    error TEXT,
    checked_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (application_no, check_kind)
);

CREATE INDEX IF NOT EXISTS idx_repair_live_checks_kind_result
    ON repair_live_trademark_checks(check_kind, result_code);

CREATE INDEX IF NOT EXISTS idx_repair_live_checks_checked_at
    ON repair_live_trademark_checks(checked_at DESC);
