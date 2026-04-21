# IP Watch AI Database Schema

Last updated: 2026-04-19
Status: Current high-level map

## Purpose

This file describes the current schema at a system level.

It is not a complete DDL dump.
- bootstrap schema lives in `deploy/schema.sql`
- follow-on changes live in `migrations/`
- the running database may include both bootstrap and migration-backed tables

## Core Schema Sources

Primary bootstrap:
- `deploy/schema.sql`

Important migration add-ons:
- `migrations/payments.sql`
- `migrations/trademark_applications.sql`
- `migrations/trademark_events.sql`
- `migrations/add_universal_conflicts.sql`
- `migrations/creative_suite.sql`
- `migrations/pipeline_runs.sql`

## Main Table Groups

### Reference And Search Data

Core search/reference tables:
- `processed_files`
- `nice_classes_lookup`
- `holders`
- `word_idf`
- `word_idf_tr`
- `trademarks`
- `trademark_history`

Notes:
- `trademarks` is the main search corpus
- `trademark_history` is partitioned by date range in the bootstrap schema
- vector similarity support depends on `pgvector`

### Multi-Tenant And Auth

Tenant and identity tables:
- `subscription_plans`
- `organizations`
- `users`
- `user_sessions`
- `password_reset_tokens`
- `email_verification_tokens`

Settings and commercial support:
- `app_settings`
- `discount_codes`
- `discount_code_usage`
- `payments`

### Monitoring And Alerts

Primary monitoring tables:
- `watchlist_mt`
- `alerts_mt`
- `scan_jobs`
- `scan_results`
- `reports`
- `notification_queue`
- `api_usage`
- `public_search_usage`
- `audit_log`

Notes:
- `_mt` tables are the current multi-tenant watchlist/alert surface
- free/paid limits and report usage depend on organization-linked data
- `public_search_usage` tracks anonymous landing-page free-search quota consumption by stable browser client id

### Search, Lead, And Creative Workflows

Additional product tables:
- `universal_conflicts`
- `universal_scan_queue`
- `lead_access_log`
- `generation_logs`
- `generated_images`
- `pipeline_runs`
- `trademark_applications_mt`
- `trademark_events`

### Legacy Compatibility Tables

Older compatibility tables still present in bootstrap schema:
- `watchlist`
- `alerts`

These should be treated as legacy compatibility surface, not the primary product model.

## Extensions

The schema expects:
- `uuid-ossp`
- `pg_trgm`
- `vector`
- `fuzzystrmatch`

## Indexing Strategy

The schema uses a mix of:
- btree indexes for exact lookups
- trigram indexes for name similarity
- GIN indexes for arrays and JSONB
- HNSW vector indexes for embedding search

## Practical Rules

- Treat `deploy/schema.sql` plus `migrations/` as the schema source of truth
- Do not document one table in isolation without checking related migrations
- For destructive data work, audit foreign keys and cleanup behavior first
- If you change schema behavior, update this file only at the structural level and keep exact DDL in schema or migration files
