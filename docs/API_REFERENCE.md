# IP Watch AI API Reference

Last updated: 2026-05-12
Status: Current high-level map

## Purpose

This file is a high-level map of the current API surface.

It is not a generated OpenAPI dump.
- use `/docs` when debug mode is enabled
- use `tests/test_api_endpoints.py` for the broadest contract coverage in this repo
- use the route modules in `api/` and `app_*.py` for implementation detail

## Base URLs

Local app:

```text
http://127.0.0.1:8000
```

Primary API prefix:

```text
/api/v1
```

Docs UI:
- `/docs` only when debug mode is enabled

## Authentication

Protected routes use JWT bearer auth:

```text
Authorization: Bearer <access_token>
```

Current auth flow lives under:
- `/api/v1/auth/register`
- `/api/v1/auth/login`
- `/api/v1/auth/refresh`
- `/api/v1/auth/change-password`
- `/api/v1/auth/forgot-password`
- `/api/v1/auth/reset-password`
- `/api/v1/auth/verify-email`
- `/api/v1/auth/resend-verification`
- `/api/v1/auth/me`

`/api/v1/auth/login` accepts either:
- JSON body with `email` and `password`
- form body with `username` or `email`, plus `password`

## Public And System Endpoints

System:
- `GET /health`
- `GET /api/info`
- `GET /api/v1/status`
- `GET /api/v1/config`

Public search and portfolio:
- `GET /api/v1/search/public`
- `POST /api/v1/search/public`
- `GET /api/v1/patent-search/public`
- `POST /api/v1/patent-search/public`
- `GET /api/v1/design-search/public`
- `POST /api/v1/design-search/public`
- `GET /api/v1/cografi-search/public`
- `POST /api/v1/cografi-search/public`
- `GET /api/v1/portfolio/public`
- `GET /api/v1/portfolio/public/csv`
- `GET /api/v1/portfolio/public/designs`
- `GET /api/v1/portfolio/public/designs/csv`
- `GET /api/v1/portfolio/public/designers`
- `GET /api/v1/portfolio/public/designers/csv`
- `GET /api/v1/portfolio/public/attorneys`
- `GET /api/v1/portfolio/public/attorneys/csv`
- `GET /api/v1/portfolio/public/patents`
- `GET /api/v1/portfolio/public/patents/csv`
- `GET /api/v1/portfolio/public/patent-inventors`
- `GET /api/v1/portfolio/public/patent-inventors/csv`
- `GET /api/v1/portfolio/public/patent-attorneys`
- `GET /api/v1/portfolio/public/patent-attorneys/csv`
- `GET /api/v1/portfolio/public/cografi-applicants`
- `GET /api/v1/portfolio/public/cografi-applicants/csv`
- `GET /api/v1/portfolio/public/cografi-agents`
- `GET /api/v1/portfolio/public/cografi-agents/csv`

All four public search endpoints share a single anonymous daily quota — 5 searches/day per visitor, tracked by the long-lived `public_search_client_id` cookie. Switching registries does not reset the count; one bucket covers trademark + patent + design + cografi. Quota enforcement lives in `app_public_search_quota.py` (`enforce_public_search_quota` / `record_public_search_usage`), wired in front of each public route. Failed searches are not counted — the usage increment runs only after a successful retrieval.

Public education content:
- `GET /api/v1/education/catalog`
- `GET /api/v1/education/flashcards/{deck_id}`
- `GET /api/v1/education/quizzes/{section_id}`
- `GET /api/v1/education/assets/{file_name}`

Education catalog note:
- `GET /api/v1/education/catalog` now returns category summaries that pair the categorized flashcard deck and quiz section for each vekillik study category on the landing page

Nice class helpers:
- `GET /api/nice-classes`
- `POST /api/validate-classes`
- `POST /api/suggest-classes`

Legacy compatibility search utilities:
- `POST /api/search`
- `POST /api/search-by-image`
- `GET /api/search/simple` (deprecated)
- `POST /api/search/unified` (deprecated)

## Authenticated Route Groups

The current authenticated API is split by feature area.

Core account and org:
- `/api/v1/users`
- `/api/v1/user`
- `/api/v1/organization`
- `/api/v1/usage`

Search and trademark:
- `/api/v1/search/quick`
- `/api/v1/search/intelligent`
- `/api/v1/search/risk-report`
- `/api/v1/search/risk-report/public`
- `/api/v1/search/risk-report/claim`
- `/api/v1/search/intelligent-risk-report`
- `/api/v1/search/intelligent-risk-report/public`
- `/api/v1/trademark`

Search scoring response note:
- `POST /api/v1/search/risk-report` is authenticated, consumes 1 monthly risk report allowance from the existing `monthly_reports` package quota, accepts up to 20 visible search results, sends factual candidate fields to the Qwen-first text provider chain with DeepSeek/Gemini fallbacks or the Qwen-VL-first multimodal chain with Gemini fallback, excludes deterministic score context, prompts the selected provider to write the advisory report in the submitted app language (`tr`, `en`, or `ar`), returns report candidates sorted by provider risk score descending with `image_url` values for thumbnails, writes a PDF copy into the `reports` table for the dashboard Reports tab, and does not change canonical search scores
- `POST /api/v1/search/risk-report/public` accepts the same JSON or multipart request without authentication for the landing page, generates a short-lived pending PDF, returns `is_pending: true` plus `claim_token`, and does not consume package quota until the user logs in and claims it
- `POST /api/v1/search/risk-report/claim` is authenticated and accepts `{ "claim_token": "..." }`; it validates the pending report, applies the user's monthly risk-report quota, moves the PDF into the normal dashboard Reports list, and returns the saved `report_id`/download URL
- `POST /api/v1/search/intelligent-risk-report` is authenticated, runs an agentic TurkPatent search and an LLM risk report in one call, accepts `multipart/form-data` with `query`, optional `image`, `classes`, `attorney_no`, `language`; charges only the `monthly_reports` quota (the bundled agentic search does not consume a live-search credit); returns the standard `SearchRiskReportResponse` shape with an extra `search` field carrying the agentic search response so the dashboard can render the result list; falls back to a DB-only assessment if the scrape fails or returns no records; the bundled search emits the existing Redis-backed progress events under the user_id, and the existing `/api/v1/search/cancel` endpoint cancels the in-flight agentic search (cancellation returns `{cancelled: true, search: ...}` with no quota consumed)
- `POST /api/v1/search/intelligent-risk-report/public` is the unauthenticated landing-page variant of the above, rate-limited tighter (default `1/minute` per IP via the `rate_limit.public_intelligent_risk_report` setting); returns a pending report with a `claim_token` (no quota consumed until claimed via `/risk-report/claim`); does not emit Redis progress events
- search routes continue to expose the existing scoring fields such as `total`, `text_similarity`, `text_idf_score`, `semantic_similarity`, `phonetic_similarity`, `visual_similarity`, `translation_similarity`, `path_a_score`, `path_b_score`, `scoring_path_source`, `dynamic_weights`, and `matched_words`; `semantic_similarity` remains a compatibility field for trademark responses and is not backed by trademark text embeddings or used for ranking/scoring
- `text_idf_score` is the selected V2 textual path used by the overall combiner; score-card display should use `path_a_score` for original-name text and `translation_similarity` for translated-name text
- the canonical scorer now also returns `score_version: "v2_text_visual"`, `textual_breakdown`, `visual_breakdown`, and `decision_reason`
- `translation_similarity` is the translated-name textual path score from `name_tr`; it is not an additional overall combiner signal
- `/api/v1/alerts` score payloads keep the existing scalar fields and may also include `text_idf_score`, `path_a_score`, `path_b_score`, `scoring_path_source`, `decision_reason`, `textual_breakdown`, and `visual_breakdown` from `alerts_mt.score_details` for new watchlist similarity alerts; legacy alerts fall back to the old scalar columns until rescanned
- `/api/v1/alerts` and watchlist conflict summaries expose active similarity conflicts only when the conflicting trademark is still published and its opposition deadline has not passed; historical registered, renewed, refused, or withdrawn marks may remain stored as old alert rows but are not returned as active conflicts
- textual diagnostics may include `token_role` for matched words, `descriptor_terms` for corpus-derived descriptor-like terms that cannot become anchors, compatibility alias `non_protectable_terms`, `descriptor_stats` evidence when available, `low_protectability_terms` and `low_protectability_stats` for corpus-distinctive weak modifier-like anchors, `weak_shared_anchor_guard` when shared weak-anchor-only evidence is capped, `short_acronym_subset_guard` when an exact short acronym is copied but material matter is missing on either side, `compound_expansions` for compact generic-suffix compounds, `short_anchor_guard` for blocked non-exact acronym-style phonetic/fuzzy matches, `anchor_quality_guard` for weak dominant-anchor fuzzy or phonetic matches, compatibility alias `fuzzy_anchor_guard`, `added_matter_breakdown` for dominant-core/additional-word scoring, and `translation_quality_flags` when `name_tr` is capped for dropping material from the original candidate name, short collapsed translated tokens, or weak translated non-exact-anchor evidence
- textual diagnostics may include `calibration_breakdown` when a guarded cap is applied; these diagnostics show the evidence-weighted score under the cap ceiling so similarly capped cases do not all return the same value
- result diagnostics may include `text_visual_guard` when weak text such as generic-only, missing-anchor, dominant-anchor-missing, phonetic-only evidence, weak fuzzy/phonetic-anchor evidence, weak shared low-protectability-anchor evidence, short-acronym subset evidence, limited one-anchor changed-matter/asymmetric-added-matter evidence, or plain-text-wordmark visual evidence prevents visual similarity from dominating the final score; OCR disagreement is diagnostic only and no longer suppresses agreement boosts
- translated-path diagnostics may include `translation_duplicate_original` when `name_tr` normalizes to the same candidate text and `short_collapsed_candidate_translation` when a one-token short `name_tr` collapses from a longer original candidate; both cap translated Path B so translated IDF flags cannot inflate risk
- result diagnostics may include internal retrieval context such as `retrieval_sources`, `retrieval_matched_fields`, `retrieval_matched_stages`, and `retrieval_query_variants`; these explain how the candidate entered the scoring pool and do not change scoring math
- retrieval uses exact token-boundary matching for short anchor tokens across `name` and `name_tr`, while broad substring token retrieval is limited to longer anchors; this keeps short-query recall consistent with watchlist scoring without admitting unrelated fragments as anchor-token matches
- visual scoring uses active CLIP, DINOv2, and OCR components; OCR is compared only against candidate logo OCR with conservative exact/character evidence, can drive plain text wordmark visual matches when both images are text-on-blank, and cannot cap or drag down CLIP/DINO evidence; color similarity is accepted by compatibility callers but ignored by the V2 risk score
- image-only search responses may include `query_text_source` and `query_ocr_text_used`; image-only OCR remains low-weight visual evidence and is not promoted into the trademark-name text query; graphic/mixed layout variants may bypass the moderate image-only cap only when CLIP and DINOv2 both provide corroborating visual evidence, either through strict high-component evidence or balanced close-component evidence
- `visual_breakdown` may include OCR-disagreement, `logo_profile`, `plain_text_wordmark_visual_guard`, `image_only_visual_quality_guard`, and weak-text visual cap diagnostics; OCR disagreement is no longer a cap reason
- `dynamic_weights` is a compatibility explanation of active text/visual contribution under the max-plus combiner
- when unified scoring is enabled, `POST /api/search` returns the same response shape but is backed by canonical `RiskEngine.assess_brand_risk()` retrieval and scoring instead of the legacy SQL prefilter

Portfolio and monitoring:
- `/api/v1/watchlist`
- `/api/v1/alerts`
- `/api/v1/design-watchlist`
- `/api/v1/design-alerts`
- `/api/v1/reports`
- `/api/v1/dashboard`
- `/api/v1/education/progress`
- `/api/v1/education/progress/sync`
- `/api/v1/education/moderation` (admin and superadmin only)

Watchlist similarity alerts exclude same-holder conflicts when the watched mark's `customer_application_no` can be resolved to a trademark holder identifier and the candidate trademark has the same `holder_tpe_client_id` or `holder_id`. Event alerts for the watched trademark remain visible.

Patent search:
- `POST /api/v1/patent-search/quick` is authenticated and runs a text-first hybrid retrieval (trigram on `patents.title` plus cosine on `title_abstract_embedding` produced by `intfloat/multilingual-e5-large`); accepts `query`, `ipc` (comma-separated IPC class filter), `holder` (free-text trigram on `patent_holders.name`), `date_from`/`date_to` (filing-date window), `kind_code` (e.g. `B`, `A1`, `U3`, `T4`), and `limit` (default 20, max 100); shares the daily `max_daily_quick_searches` quota with trademark and design quick searches; returns 429 with the upgrade-hint payload over quota
- queries that look like an application/publication number (`2017/15048`, `TR 2017 15048 U3`, etc.) short-circuit to a direct row lookup and skip embedding/trigram retrieval
- `GET /api/v1/patent-search/public` and `POST /api/v1/patent-search/public` are anonymous variants capped at 10 results, rate-limited at 10/min per IP and gated by the shared landing-page free-tier quota. The POST surface accepts the same inputs as `/quick` — `query`, optional `image` upload, `ipc`, `holder`, `date_from`, `date_to`, `kind_code` — and runs the same hybrid retrieval (e5 text embedding + DINOv2 figure embedding when an image is supplied). The GET variant accepts `query` plus the same filter set as query params for link-share use cases
- `GET /api/v1/patent-search/ipc-autocomplete?q=` returns IPC classes that actually appear in the corpus (`patents.ipc_classes`) prefix-matching the query, joined to `ipc_classes_lookup` for descriptions when available; rate-limited at 60/min
- `GET /api/v1/patent-image/{path:path}` serves figure thumbnails for search result cards; resolves under `bulletins/Patent__Faydali_Model/` with a directory-traversal guard, direct-serves PNG/JPEG figures, and converts CD-era `.tif` figures to JPEG on the fly so browsers can render them; cached for 24h. Search results include an `image_url` field built from `patent_figures.image_path` and `patents.bulletin_folder` (the first non-null figure by `seq` is selected)

Design search:
- `POST /api/v1/design-search/quick` is authenticated and runs visual-dominant retrieval against `designs` (DINOv2 ViT-L/14 ≈55% + CLIP ViT-B/32 ≈30% + HSV color ≈10% + trigram text ≈5%); accepts `query`, optional `image`, `locarno` (comma-separated Locarno classes), and `limit` (default 20); shares the daily `max_daily_quick_searches` quota with trademark/patent/cografi quick searches
- `GET /api/v1/design-search/public` and `POST /api/v1/design-search/public` are anonymous variants capped at 10 results, rate-limited at 10/min per IP and gated by the shared landing-page free-tier quota. The POST surface accepts `query`, optional `image`, and `locarno` — the same input shape as `/quick`. Image-based retrieval runs the same visual model stack on the public path
- `GET /api/v1/locarno-classes` is public and lists the 32 top-level Locarno classes with localized names for the design search filter UI; rate-limited at 60/min
- `POST /api/v1/tools/suggest-locarno-classes` is authenticated and returns AI-suggested Locarno classes for a free-text product description; rate-limited at 20/min
- `GET /api/v1/design-image/{path:path}` serves design view JPEGs from `bulletins/Tasarim/` with a directory-traversal guard; resolves both pre-CD-refactor paths (no `cd_images/`/`images/` prefix) and post-refactor paths, cached for 24h
- `GET /api/v1/portfolio/public/designs?holder_id=X` is anonymous, returns up to 10 designs for a holder (joined to `holders` via `designs.holder_id`) with the same response shape as the trademark `/portfolio/public` so the dashboard portfolio modal can render either registry; rate-limited at 5/min
- `GET /api/v1/portfolio/public/designs/csv?holder_id=X` is authenticated, returns a CSV export of every design for the holder, plan-gated by `can_download_portfolio` (paid plans only); rate-limited at 3/min
- `POST /api/v1/design-watchlist/bulk-from-portfolio` is authenticated, accepts `{holder_id}` and bulk-adds every design in the holder's portfolio to the user's design watchlist (mirrors `/api/v1/watchlist/bulk-from-portfolio` for trademark). Reuses `create_design_watchlist_item` so each insert clones DINOv2/CLIP/color embeddings from the source design via `reference_design_id`, dedups against existing rows, and respects the combined `max_watchlist_items` plan quota; gated by `can_view_holder_portfolio`; rate-limited at 5/min; returns `{added, skipped, errors, total, limit_reached, scan_item_ids, queued_scans}` and queues a background scan for each newly created row
- `GET /api/v1/portfolio/public/patents?holder_id=X` is anonymous, returns up to 10 patents for a holder (joined to `holders` via `patent_holders.holder_id`); accepts either a TPE client id or the internal `holders.id` UUID (resolved by `_resolve_holder_row`); same response shape as the design/trademark portfolios; rate-limited at 5/min
- `GET /api/v1/portfolio/public/patents/csv?holder_id=X` is authenticated, returns a CSV export of every patent for the holder, plan-gated by `can_download_portfolio`; rate-limited at 3/min
- `GET /api/v1/portfolio/public/patent-inventors?name=X` is anonymous, returns up to 10 patents whose `patent_inventors.name` matches under conservative normalization (`normalize_designer_name`), backed by `idx_pinv_normalized_name`; rate-limited at 5/min
- `GET /api/v1/portfolio/public/patent-inventors/csv?name=X` is authenticated, plan-gated CSV variant of the inventor lookup; rate-limited at 3/min
- `GET /api/v1/portfolio/public/patent-attorneys?name=X&firm=Y` is anonymous, matches the `(name, firm)` pair under conservative normalization (firm optional — empty matches NULL-firm rows via `COALESCE(...,'')`), backed by `idx_patt_normalized_pair`; rate-limited at 5/min
- `GET /api/v1/portfolio/public/patent-attorneys/csv?name=X&firm=Y` is authenticated, plan-gated CSV variant; rate-limited at 3/min
- `GET /api/v1/portfolio/public/cografi-applicants?holder_id=X` is anonymous, returns up to 10 cografi records joined to `cografi_holders` filtered by `role='APPLICANT'`; accepts TPE client id or internal `holders.id` UUID; rate-limited at 5/min
- `GET /api/v1/portfolio/public/cografi-applicants/csv?holder_id=X` is authenticated, plan-gated CSV variant; rate-limited at 3/min
- `GET /api/v1/portfolio/public/cografi-agents?name=X` is anonymous, matches the sparse `cografi_records.agent` text column under conservative normalization (`normalize_designer_name`), backed by `idx_cog_agent_normalized`; rate-limited at 5/min
- `GET /api/v1/portfolio/public/cografi-agents/csv?name=X` is authenticated, plan-gated CSV variant; rate-limited at 3/min

Cografi search (Geographical Indications + Traditional Specialties):
- `POST /api/v1/cografi-search/quick` is authenticated and runs a hybrid retrieval against `cografi_records` (trigram on name plus cosine on `text_embedding` from e5-large, fused with DINOv2 figure embedding when an image is supplied); accepts `query`, optional `image`, `section_keys` (comma-separated), `record_types` (comma-separated), `gi_type` (`mensei` / `mahreç` / `geleneksel`), `region` (free-text trigram on `geographical_boundary`), `date_from`/`date_to`, `application_no`, `registration_no`, `include_admin`, and `limit` (default 20); shares the daily `max_daily_quick_searches` quota
- queries that look like an application/registration number short-circuit to a direct row lookup and skip embedding/trigram retrieval
- `GET /api/v1/cografi-search/public` and `POST /api/v1/cografi-search/public` are anonymous variants capped at 10 results, rate-limited at 10/min per IP and gated by the shared landing-page free-tier quota. The POST surface accepts the same input as `/quick` (less `include_admin`, which stays admin-side). The public path runs the same e5 text + DINOv2 figure embeddings
- `GET /api/v1/cografi-search/autocomplete?q=` returns name + region typeahead from the cografi corpus; rate-limited at 60/min
- `GET /api/v1/cografi/{record_id}` returns the full cografi detail record
- `GET /api/v1/cografi-image/{path:path}` serves figure thumbnails from `bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/` with a directory-traversal guard; auto-inserts the `figures/` segment when the caller uses the service's compact URL form; cached for 24h

Design watchlist + alerts:
- `POST /api/v1/design-watchlist` creates a tracked design (text + Locarno classes, optional `customer_application_no`, optional `reference_design_id` to clone embeddings from an existing design row); subject to the combined trademark+design watchlist quota (`subscription_plans.max_watchlist_items`)
- `POST /api/v1/design-watchlist/{id}/image` uploads a reference image (max 10 MB; jpeg/png/webp) and embeds it inline with DINOv2 ViT-L/14, CLIP ViT-B/32, and HSV histogram; the watchlist row's per-signal embedding columns are updated and the next scan uses image+text combiner weights
- `POST /api/v1/design-watchlist/{id}/scan` queues a full-corpus scan against the active design corpus for that single watchlist item
- `GET /api/v1/design-alerts` lists design alerts with status / severity / `min_score` filters; severity buckets follow the trademark conventions (low / medium / high / critical) computed from the overall similarity score
- design alert lifecycle endpoints (`/acknowledge`, `/resolve`, `/dismiss`) accept an optional `notes` body and stamp `acknowledged_by` / `resolved_by` from the current user
- design alerts are generated automatically by the post-ingest hook in `pipeline/ingest_designs.py`; the conflict storage floor is `0.50` overall similarity, capped at 10 alerts per watchlist item per scan

Reports:
- `GET /api/v1/reports` lists organization reports
- `POST /api/v1/reports/generate` creates downloadable reports
- `GET /api/v1/reports/{report_id}` fetches one report record
- `GET /api/v1/reports/{report_id}/download` downloads a completed organization-owned report file; report downloads are not plan-gated
- `DELETE /api/v1/reports/{report_id}` deletes one organization report and cleans its stored file when the file is under the configured reports directory
- `DELETE /api/v1/reports` deletes all reports for the current user's organization and cleans eligible stored files

Commercial and workflow:
- `/api/v1/leads`
- `/api/v1/holders`
- `/api/v1/attorneys`
- `/api/v1/applications`
- `/api/v1/billing`
- `/api/v1/payments`

### Billing — regional catalog, subscriptions, and AI credit packs

Regional billing is resolved before provider selection. `UK` and `EU` use Stripe
Checkout (`GBP`/`EUR`); `TR` uses iyzico Checkout Form (`TRY`). Unknown regions
fall back to `UK`. Catalog prices and Stripe Price IDs come from
`BILLING_REGION_CATALOG_JSON`; plan entitlements still come from
`utils.subscription.PLAN_FEATURES`.

- `GET /api/v1/billing/catalog?region=UK|EU|TR` — public catalog endpoint.
  Returns `{region, provider, currency, region_options, plans, credit_packs}`.
  `plans` include entitlement data; `credit_packs` include `{id, label_key,
  credits, price}` in the selected currency.
- `POST /api/v1/payments/initialize` — initialize a plan checkout. Body:
  `{"plan":"starter"|"professional"|"enterprise","billing":"monthly"|"annual","region":"UK"|"EU"|"TR"}`.
  Stripe regions return `{provider:"stripe", checkout_url, session_id,
  payment_id}`. Turkey returns `{provider:"iyzico", checkout_form_content,
  token, conversation_id, payment_id}`.
- `GET /api/v1/billing/credit-packs` — legacy authenticated pack list. Without
  `region`, returns the legacy TRY shape. With `region`, returns regional
  `{region, provider, currency, packs}`.
- `POST /api/v1/billing/purchase-credits` — initialize a one-shot AI credit
  pack. Body: `{"pack_id":"small"|"medium"|"large","region":"UK"|"EU"|"TR",
  "discount_code":"..."}`. Stripe regions return `checkout_url`; Turkey returns
  iyzico form HTML. Purchased credits never expire and are spent only after the
  monthly plan allowance is exhausted.
- `POST /api/v1/payments/stripe/webhook` — Stripe signed webhook endpoint.
  Handles checkout completion/success, invoice paid, checkout expiry, and
  payment failure. The webhook is the authoritative Stripe fulfillment path.
- iyzico success/failure callbacks continue to use `/api/v1/payments/callback`
  and `/api/v1/payments/webhook`. Completion branches on `payments.kind`:
  `subscription` runs `activate_subscription`; `credit_pack` runs
  `apply_credit_pack_purchase`.

### Lead feed sub-routes (Radar modes)

All lead-feed routes are plan-gated via `daily_lead_views`; CSV exports
additionally require `can_export_csv_leads`. They share `LEADS_PER_PAGE`
defaulting to 20, `page` ≥ 1, and `limit` 1–100.

Existing modes:
- `GET /api/v1/leads/feed` — opposition leads (similarity-driven, from
  `universal_conflicts`).
- `GET /api/v1/leads/stats`, `GET /api/v1/leads/credits`,
  `GET /api/v1/leads/export/csv`.
- `GET /api/v1/leads/renewals/feed`, `/renewals/stats`,
  `/renewals/export/csv` — renewal leads driven by `final_status` +
  `expiry_date`.

Event-driven modes (Phase 2 — see `phase1_2_events_status` memory):
- `GET /api/v1/leads/cancellations/feed` — recently-cancelled marks
  (`event_type='cancellation'`, last 12 months). Filters: `nice_class`,
  `search` (name + holder ILIKE).
- `GET /api/v1/leads/cancellations/export/csv`.
- `GET /api/v1/leads/transfers/feed` — M&A signal (event_type IN
  `transfer`, `merger`, `partial_transfer`, last 12 months). Filters:
  `event_type` (single sub-type or omitted for all), `nice_class`,
  `search` (name + holder + previous_holder + new_holder ILIKE).
  Each row exposes `transfer_event_type`, `previous_holder_name`, and
  `new_holder_name` from `te.old_value` / `te.new_value`.
- `GET /api/v1/leads/transfers/export/csv`.
- `GET /api/v1/leads/bankruptcies/feed` — bankrupt holders
  (`event_type='bankruptcy'`, last 24 months — longer window because
  bankruptcy effects play out over time). Filters: `nice_class`,
  `search`. Each row exposes `bankruptcy_details` from `te.new_value`.
- `GET /api/v1/leads/bankruptcies/export/csv`.

Caveat: event-driven exports skip the `_log_lead_access` audit insert
because its `lead_access_log_conflict_id_fkey` constraint can't be
satisfied without a real `universal_conflicts.id`. The renewal CSV
export has the same latent FK bug. Tracked as open follow-up.

### Holder/attorney portfolio response shape (Phase 1)

`GET /api/v1/holders/{tpe_client_id}/trademarks` and
`GET /api/v1/attorneys/{attorney_no}/trademarks` (and their
`/trademarks/csv` siblings) include event-derived fields populated by
`ingest_events.materialize_all`:

- `holder_changed_at` (ISO date or null)
- `last_event_type` (e.g. `transfer`, `cancellation`, `renewal`)
- `last_event_date` (ISO date)
- `last_event_severity` (`critical | high | medium | low | null`,
  classified by `utils.event_severity.classify_event_severity`)
- `has_restrictions` (bool)
- `active_restriction_count` (int)

The CSV exports add 4 trailing columns: `Sahip Degisim Tarihi`,
`Son Olay`, `Son Olay Tarihi`, `Aktif Kisitlama`.

`GET /api/v1/alerts/aggregate` items now include `alert_type`
(`event` | `similarity`) and `source_type` (event_type for event
alerts; legacy bucket for similarity alerts). Severity-rank tiebreaker
in the SQL `ORDER BY` keeps similarity-alert ordering unchanged but
sorts event alerts critical → low within their bucket.

Admin, tooling, and pipeline:
- `/api/v1/admin`
- `/api/v1/tools`
- `/api/v1/pipeline`

AI Studio lives under `/api/v1/tools`:
- `GET /api/v1/tools/status` is public and reports per-tool availability, disabled reason, AI credit cost, Logo Studio audit readiness, and non-breaking Logo Studio provider diagnostics for OpenAI/Gemini
- `POST /api/v1/tools/suggest-names` is authenticated, costs 2 unified AI credits only when usable safe names are returned, requires a non-empty concept, at least one Nice class, a non-empty sector/description, style, and language, validates Nice classes as `1..45`, retrieves the top 10 shared `RiskEngine` name candidates for the submitted concept as forbidden pre-generation context including `name_tr`, generates 10 names, retrieves 10 factual trademark candidates per generated name through the same shared name path, and sends those candidates to the internal score-only risk-report LLM flow without deterministic scores or monthly report quota usage
- `POST /api/v1/tools/generate-logo` is authenticated, costs 5 unified AI credits, generates four logo candidates with `audit_status="pending"`, creates or appends to a Logo Studio project thread, queues visual trademark audits in the request background task, and uses OpenAI GPT Image 2 before falling back to Gemini Nano Banana Pro; Logo Studio retrieval uses the shared `RiskEngine` image/OCR path, but OCR and trademark facts stay retrieval-only and the multimodal LLM receives only generated/candidate images for visual risk scoring
- `GET /api/v1/tools/logo-projects/{project_id}` is authenticated and returns an organization-scoped Logo Studio project with all initial and revision candidates
- `POST /api/v1/tools/logo-projects/{project_id}/select` is authenticated and selects only candidates whose audit is completed and safe
- `POST /api/v1/tools/generated-image/{image_id}/audit-retry` is authenticated and queues another audit for a failed completed image
- `GET /api/v1/tools/generated-image/{image_id}` is authenticated, organization-scoped, and serves only generated images stored under the configured AI Studio logo output directory; UI download remains blocked until the audit is completed and safe
- `GET /api/v1/tools/generation-history` is authenticated and returns organization-scoped Name Lab and Logo Studio generation logs with logo project/audit metadata

Pipeline note:
- pipeline trigger endpoints launch `workers.pipeline_worker` as a detached child process after persisting the `pipeline_runs` record, so the run is no longer tied to the request lifecycle of the FastAPI worker that accepted the trigger

## Common Usage Patterns

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

Public search:

```powershell
curl "http://127.0.0.1:8000/api/v1/search/public?query=wosen"
```

Login with JSON:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d "{\"email\":\"mobiletest@test.com\",\"password\":\"Test1234!\"}"
```

Authenticated quick search:

```powershell
curl "http://127.0.0.1:8000/api/v1/search/quick?query=wosen&classes=9,35" `
  -H "Authorization: Bearer <access_token>"
```

Report generation:

```powershell
curl -X POST http://127.0.0.1:8000/api/v1/reports/generate `
  -H "Authorization: Bearer <access_token>" `
  -H "Content-Type: application/json" `
  -d "{\"report_type\":\"watchlist_summary\",\"file_format\":\"pdf\"}"
```

Delete a report:

```powershell
curl -X DELETE http://127.0.0.1:8000/api/v1/reports/<report_id> `
  -H "Authorization: Bearer <access_token>"
```

## Notes

- public search is rate-limited separately from authenticated search; each public route has its own slowapi limit (10/min per IP) but all four registries share a single 5/day per-visitor counter via the `public_search_client_id` cookie. Over-quota responses return structured `429` detail with the upgrade-hint payload so the landing page can render the upgrade modal
- the shared anonymous counter lets a visitor mix and match across trademark / patent / design / cografi within their free 5/day allowance; it is implemented in `services/search_service.py` (primitives) and wrapped by `app_public_search_quota.py` (route-level helpers)
- all four `/public` POST endpoints accept multipart with an optional `image` field plus the full filter surface of their `/quick` siblings; the dashboard and landing page hit the same service-layer search functions, so retrieval quality on the public path matches the authenticated path (only the result cap and quota differ)
- authenticated quick search reads the plan daily cap from runtime settings, and startup now realigns the known legacy quick-search overrides to the current product defaults
- some legacy routes remain for compatibility while newer flows live under `/api/v1`
- browser and live E2E suites in `tests/` are often the best source for real end-to-end request/response behavior
- Education moderation notes:
  - `PUT /api/v1/education/moderation` stores tester-only overrides for flashcards and quiz questions
  - supported item types are `flashcard` and `quiz_question`
  - flashcard overrides support category reassignment and soft-delete hiding
  - quiz-question overrides support category reassignment, explanation text edits, summary text edits, and soft-delete hiding through `education/moderation_overrides.json`
