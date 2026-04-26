-- Pipeline Runs Tracking Table
-- Tracks execution history of the core pipeline steps:
--   data_collection → zip → metadata → ai.py (embeddings) → ingest → event_ingest

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    status VARCHAR(20) NOT NULL DEFAULT 'running',  -- running, success, partial, failed
    triggered_by VARCHAR(50) DEFAULT 'schedule',     -- schedule, manual, api
    skip_download BOOLEAN DEFAULT FALSE,

    -- Per-step results (JSONB for flexibility)
    step_download JSONB,     -- Step 1: data_collection.py
    step_extract JSONB,      -- Step 2: zip.py
    step_metadata JSONB,     -- Step 3: metadata.py
    step_embeddings JSONB,   -- Step 4: ai.py
    step_ingest JSONB,       -- Step 5: ingest.py
    step_event_ingest JSONB, -- Step 6: ingest_events.py
    step_final_status_repair JSONB, -- Manual maintenance: final_status repair

    -- Aggregate counts
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

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP;

ALTER TABLE pipeline_runs
    ALTER COLUMN heartbeat_at SET DEFAULT NOW();

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS current_step VARCHAR(50);

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS step_event_ingest JSONB;

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS total_event_scopes_ingested INTEGER DEFAULT 0;

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS step_final_status_repair JSONB;

ALTER TABLE pipeline_runs
    ADD COLUMN IF NOT EXISTS total_final_status_repaired INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs(started_at DESC);
