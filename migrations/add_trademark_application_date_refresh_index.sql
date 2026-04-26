CREATE INDEX IF NOT EXISTS idx_tm_application_date_id_desc
    ON trademarks(application_date DESC, id DESC)
    WHERE name IS NOT NULL AND name != '';
