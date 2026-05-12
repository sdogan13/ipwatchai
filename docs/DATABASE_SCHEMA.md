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
- `migrations/credit_packs.sql` — extends `payments` with `kind` (`subscription` | `credit_pack`), `pack_id`, `credits_amount`, `discount_code`; relaxes `plan_name`/`billing_period` to NULLable so credit-pack rows can store their own metadata
- `migrations/regional_payment_providers.sql` — extends `payments` with provider/region metadata and Stripe lookup fields (`provider`, `region`, `billing_country`, `stripe_checkout_session_id`, `stripe_customer_id`, `stripe_subscription_id`, `stripe_payment_intent_id`, `stripe_raw_response`); existing rows default to `provider='iyzico'`
- `migrations/trademark_applications.sql`
- `migrations/trademark_events.sql`
- `migrations/add_universal_conflicts.sql`
- `migrations/creative_suite.sql`
- `migrations/logo_studio_projects.sql`
- `migrations/pipeline_runs.sql`
- `migrations/descriptor_idf_stats.sql`
- `migrations/designs.sql` plus `migrations/run_designs_migration.py` (industrial-design tables)

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
- trademark text-semantic embeddings are no longer part of the trademark retrieval/scoring source of truth; `migrations/remove_trademark_text_embedding.sql` drops the stale `trademarks.text_embedding` column and vector indexes while name retrieval stays on normalized lexical, fuzzy, phonetic, and translation-aware paths
- V2 scores are similarity-risk diagnostics only and intentionally exclude legal factors such as status, Nice-class relatedness, seniority, and enforceability
- historical MADLAD refresh runs now consume `trademarks` newest-first by `application_date DESC NULLS LAST, id DESC`, backed by an `application_date/id` btree index so campaign reruns can skip already-watermarked rows efficiently
- `trademark_history` is partitioned by date range in the bootstrap schema
- vector similarity support depends on `pgvector`
- ingest runtime prerequisites for `processed_files`, `nice_classes_lookup`, `tm_status`, and ingest-owned `trademarks` columns now come from `migrations/ingest_runtime.sql` plus `migrations/run_ingest_runtime_migration.py`, not opportunistic schema mutation during `pipeline/ingest.py` runs
- ingest owns `trademarks.current_status` and `status_source`; APP source updates preserve existing BLT/GZ status unless APP supplies explicit recognized strong status text or blank status with a valid registration number, and APP Nice-class updates cannot shrink or replace existing classes with the live grid's suspicious six-class list
- the post-ingest `repair` step can fix known DB data pollution, including APP-applied status downgrades, `sekil`/`şekil` shape descriptors in `trademarks.name` and `trademarks.name_tr`, six-class scraper truncation in `trademarks.nice_class_numbers` when BLT/GZ metadata proves more classes, and batched live TURKPATENT status/class checks tracked in `repair_live_trademark_checks`
- live status repair may temporarily mark unchecked `Yayında` rows older than 1 year as `Reddedildi` with `status_source='LIVE_PROV'`; these rows stay eligible for the live checker, and original status fields are audited in `repair_live_provisional_status_marks`

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
- `payments` — unified store for plan subscriptions and one-shot AI credit-pack purchases. `provider` distinguishes `iyzico` and `stripe`; `region` stores `UK`, `EU`, or `TR`; `billing_country` records the resolved organization/request country when available. The `kind` column (`subscription` | `credit_pack`) drives fulfillment. Credit-pack rows carry `pack_id`, `credits_amount`, and the optional `discount_code`. Stripe rows additionally store checkout session, customer, subscription, payment-intent, and raw webhook response IDs for idempotent lookup. Successful credit-pack payments increment `organizations.ai_credits_purchased` (never-expiring pool).

### Monitoring And Alerts

Primary monitoring tables:
- `watchlist_mt`
- `alerts_mt`
- `design_watchlist_mt`
- `design_alerts_mt`
- `scan_jobs`
- `scan_results`
- `reports`
- `pending_risk_reports`
- `notification_queue`
- `api_usage`
- `public_search_usage`
- `education_progress`
- `audit_log`

Notes:
- `_mt` tables are the current multi-tenant watchlist/alert surface
- `alerts_mt.score_details` stores the full V2 `score_pair()` diagnostic payload for new similarity alerts; legacy scalar score columns remain the compatibility and filtering surface
- `design_watchlist_mt` mirrors `watchlist_mt` but with design-specific columns: `product_name`, `locarno_classes TEXT[]`, DINOv2 ViT-L/14 (1024-d) + CLIP ViT-B/32 (512-d) + HSV (512-d) embeddings, `reference_design_id` FK to `designs` for clone-from-existing
- `design_alerts_mt` mirrors `alerts_mt` but stores design conflict refs (`conflicting_design_id` FK, `conflicting_locarno_classes`) and design-only similarity scores (`dino_/clip_/color_/text_similarity_score`); no phonetic / translation columns
- design watchlist quota: rows count against the existing `subscription_plans.max_watchlist_items` budget alongside `watchlist_mt` rows (combined limit, not per-registry)
- migration: `migrations/design_watchlist.sql` plus `migrations/run_design_watchlist_migration.py`
- free/paid risk-report limits depend on organization-linked data; authenticated search risk reports consume `api_usage.reports_generated` and save a downloadable PDF row in `reports`, while other downloadable report types are not monthly-limited
- `pending_risk_reports` stores short-lived anonymous landing-page risk-report PDFs keyed by a hashed claim token; the row is attached to a user's organization and copied into `reports` only after login and quota validation
- report deletion is organization-scoped; deleting a report removes the `reports` row and cleans the stored file only when that file resolves under the configured `REPORT_DIR`
- `public_search_usage` tracks anonymous landing-page free-search quota consumption by stable browser client id
- `education_progress` stores per-user landing-page study progress for PDFs, flashcards, and quiz sections

### Search, Lead, And Creative Workflows

Additional product tables:
- `universal_conflicts`
- `universal_scan_queue`
- `lead_access_log`
- `generation_logs`
- `generated_images`
- `logo_projects`
- `pipeline_runs`
- `trademark_applications_mt`
- `trademark_events`

Notes:
- `logo_projects` groups Logo Studio initial candidates and revision runs into an organization-scoped project thread. It stores the original brief, Nice classes, color preference, selected safe candidate, and updated timestamp.
- `generated_images` now stores Logo Studio project metadata (`project_id`, `parent_image_id`, `variant_index`, `generation_kind`, `revision_prompt`) plus asynchronous audit state (`audit_status`, `audit_error`, `audited_at`). New candidates start as `pending`; background visual audit updates similarity, safety, visual breakdown, and completion state.
- `pipeline_runs` stores pipeline step results plus run liveness fields so the superadmin dashboard can show the active step and recover interrupted runs that were left in `running` state
- `pipeline_runs` now tracks automatic event-ingest execution via `step_event_ingest` plus `total_event_scopes_ingested`, alongside the existing core step JSON and aggregate counters
- `pipeline_runs` also records manual maintenance runs such as `final_status_repair`
- `trademark_events` stores reconciled per-bulletin event timelines; exact duplicate rows are prevented by a full-payload `event_fingerprint` unique key
- event-derived materialized fields such as effective status, current holder, renewal expiry, and event counts live on `trademarks` and are recomputed from `trademark_events`
- `final_status`, `final_status_source`, and `final_status_at` on `trademarks` are reconciler-owned derived fields computed from ingest-owned `current_status`/source dates and event-owned `effective_status`/`last_event_date`

### Registry Discriminator

Both `trademarks` and `designs` carry a `registry_type VARCHAR(20) NOT NULL` column constrained by `CHECK (registry_type IN ('trademark', 'design'))`. The trademark side defaults to `'trademark'`, the design side to `'design'`. Stable internal identifier — UI labels live in i18n locale files, not in the column.

Joint queries can branch on this column without table-name inspection:

```sql
SELECT registry_type, application_no, name AS title FROM trademarks
UNION ALL
SELECT registry_type, application_no, product_name_tr AS title FROM designs;
```

Migration: `migrations/registry_type.sql` (trademarks side) + the column shipped with `migrations/designs.sql` (designs side).

### Tasarım (Industrial Design) Tables

Mirror of the trademark/holders pattern, adapted for designs:
- `locarno_classes_lookup`: 32 top-level Locarno classes seeded with Turkish + English names; subclasses (~241) deferred to a follow-up migration if needed for UI display
- `designs`: main table; one row per (application, design_index) for TR records, one row per (registration_no) for Hague-route entries; reuses existing `holders` via `holder_id` FK because TPECLIENT IDs are shared between the trademark and design registries
- `design_views`: per-view embeddings (DINOv2 ViT-L/14 1024-dim, CLIP ViT-B/32 512-dim, HSV histogram 512-dim) with HNSW indexes on each vector column
- `design_events`: events on existing designs (transfer, seizure, renewal, cancellation in 4 sub-flavors, …); `event_fingerprint` UNIQUE for idempotent ingest

Notes:
- `designs.section` enum-by-string: `tr_native | deferred | deferred_lifted | republished | hague`
- `designs.dinov2_vitl14_mean` and `designs.clip_vitb32_mean` are mean-pool aggregates across the design's views; per-view vectors stay on `design_views` for refined "any-view-matches" queries
- `designs.locarno_classes` is `TEXT[]` (e.g. `['06-01','06-02']`); a multi-design application is limited to a single Locarno class but the bulletin lists all subclasses present, so the array preserves them
- `designs.holder_id` is nullable to accommodate Hague entries where TPECLIENT lookup may fail (foreign holders)
- `design_events.details` JSONB packs per-event-type fields (transfer: previous_holder/new_holder; seizure: court+case_no; partial_*: design_indices; YİDK board: decision_date/decision_no/referenced_bulletin_*)

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
