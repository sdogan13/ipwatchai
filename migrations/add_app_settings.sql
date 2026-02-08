-- Migration: Create app_settings table for runtime-configurable settings
-- Date: 2026-02-08
-- Description: Key-value store with JSONB values, categories, and audit trail.
--              Used by SettingsManager with in-memory caching.

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
