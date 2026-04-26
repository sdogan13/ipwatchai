# IP Watch AI Database Schema

Last updated: 2026-04-25
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
- `migrations/descriptor_idf_stats.sql`

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
- translation-side fields on `trademarks` now include `name_tr`, `detected_lang`, and provenance fields `name_tr_backend`, `name_tr_model`, and `name_tr_updated_at`
- scoring engine V2 uses `word_idf`, `word_idf_tr`, and `trademarks.name_tr`; the IDF tables include corpus-derived descriptor columns `descriptor_like`, `descriptor_score`, and `descriptor_stats` populated by `compute_idf.py --source both`
- V2 scores are similarity-risk diagnostics only and intentionally exclude legal factors such as status, Nice-class relatedness, seniority, and enforceability
- historical MADLAD refresh runs now consume `trademarks` newest-first by `application_date DESC NULLS LAST, id DESC`, backed by an `application_date/id` btree index so campaign reruns can skip already-watermarked rows efficiently
- `trademark_history` is partitioned by date range in the bootstrap schema
- vector similarity support depends on `pgvector`
- ingest runtime prerequisites for `processed_files`, `nice_classes_lookup`, `tm_status`, and ingest-owned `trademarks` columns now come from `migrations/ingest_runtime.sql` plus `migrations/run_ingest_runtime_migration.py`, not opportunistic schema mutation during `pipeline/ingest.py` runs

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
- `education_progress`
- `audit_log`

Notes:
- `_mt` tables are the current multi-tenant watchlist/alert surface
- free/paid limits and report usage depend on organization-linked data
- `public_search_usage` tracks anonymous landing-page free-search quota consumption by stable browser client id
- `education_progress` stores per-user landing-page study progress for PDFs, flashcards, and quiz sections

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

Notes:
- `pipeline_runs` stores pipeline step results plus run liveness fields so the superadmin dashboard can show the active step and recover interrupted runs that were left in `running` state
- `pipeline_runs` now tracks automatic event-ingest execution via `step_event_ingest` plus `total_event_scopes_ingested`, alongside the existing core step JSON and aggregate counters
- `pipeline_runs` also records manual maintenance runs such as `final_status_repair`
- `trademark_events` stores reconciled per-bulletin event timelines; exact duplicate rows are prevented by a full-payload `event_fingerprint` unique key
- event-derived materialized fields such as effective status, current holder, renewal expiry, and event counts live on `trademarks` and are recomputed from `trademark_events`
- `final_status`, `final_status_source`, and `final_status_at` on `trademarks` are reconciler-owned derived fields computed from ingest-owned `current_status`/source dates and event-owned `effective_status`/`last_event_date`

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
