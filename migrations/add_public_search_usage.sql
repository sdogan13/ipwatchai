-- Track anonymous landing-page search usage by stable browser client id.
CREATE TABLE IF NOT EXISTS public_search_usage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id VARCHAR(255) NOT NULL,
    usage_date DATE NOT NULL,
    searches INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, usage_date)
);

CREATE INDEX IF NOT EXISTS idx_public_search_usage_client_date
    ON public_search_usage(client_id, usage_date);
