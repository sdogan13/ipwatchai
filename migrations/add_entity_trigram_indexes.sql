-- Add GIN trigram indexes for fuzzy search on attorney and holder names
-- These support ILIKE and similarity() queries for autocomplete
-- Run with: psql -h 127.0.0.1 -p 5433 -U turk_patent -d trademark_db -f migrations/add_entity_trigram_indexes.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_attorney_name_trgm
    ON trademarks USING gin(attorney_name gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_holder_name_trgm
    ON trademarks USING gin(holder_name gin_trgm_ops);
