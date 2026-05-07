# IP Watch AI API Reference

Last updated: 2026-04-21
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
- `GET /api/v1/portfolio/public`
- `GET /api/v1/portfolio/public/csv`

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
- `/api/v1/trademark`

Search scoring response note:
- `POST /api/v1/search/risk-report` is authenticated, consumes 1 monthly risk report allowance from the existing `monthly_reports` package quota, accepts up to 20 visible search results, sends factual candidate fields to the Qwen-first text provider chain with DeepSeek/Gemini fallbacks or the Qwen-VL-first multimodal chain with Gemini fallback, excludes deterministic score context, prompts the selected provider to write the advisory report in the submitted app language (`tr`, `en`, or `ar`), returns report candidates sorted by provider risk score descending with `image_url` values for thumbnails, writes a PDF copy into the `reports` table for the dashboard Reports tab, and does not change canonical search scores
- `POST /api/v1/search/risk-report/public` accepts the same JSON or multipart request without authentication for the landing page, generates a short-lived pending PDF, returns `is_pending: true` plus `claim_token`, and does not consume package quota until the user logs in and claims it
- `POST /api/v1/search/risk-report/claim` is authenticated and accepts `{ "claim_token": "..." }`; it validates the pending report, applies the user's monthly risk-report quota, moves the PDF into the normal dashboard Reports list, and returns the saved `report_id`/download URL
- search routes continue to expose the existing scoring fields such as `total`, `text_similarity`, `text_idf_score`, `semantic_similarity`, `phonetic_similarity`, `visual_similarity`, `translation_similarity`, `path_a_score`, `path_b_score`, `scoring_path_source`, `dynamic_weights`, and `matched_words`
- `text_idf_score` is the selected V2 textual path used by the overall combiner; score-card display should use `path_a_score` for original-name text and `translation_similarity` for translated-name text
- the canonical scorer now also returns `score_version: "v2_text_visual"`, `textual_breakdown`, `visual_breakdown`, and `decision_reason`
- `translation_similarity` is the translated-name textual path score from `name_tr`; it is not an additional overall combiner signal
- `/api/v1/alerts` score payloads keep the existing scalar fields and may also include `text_idf_score`, `path_a_score`, `path_b_score`, `scoring_path_source`, `decision_reason`, `textual_breakdown`, and `visual_breakdown` from `alerts_mt.score_details` for new watchlist similarity alerts; legacy alerts fall back to the old scalar columns until rescanned
- `/api/v1/alerts` and watchlist conflict summaries expose active similarity conflicts only when the conflicting trademark is still published and its opposition deadline has not passed; historical registered, renewed, refused, or withdrawn marks may remain stored as old alert rows but are not returned as active conflicts
- textual diagnostics may include `token_role` for matched words, `descriptor_terms` for corpus-derived descriptor-like terms that cannot become anchors, compatibility alias `non_protectable_terms`, `descriptor_stats` evidence when available, `low_protectability_terms` and `low_protectability_stats` for corpus-distinctive weak modifier-like anchors, `weak_shared_anchor_guard` when shared weak-anchor-only evidence is capped, `short_acronym_subset_guard` when an exact short acronym is copied but material matter is missing on either side, `compound_expansions` for compact generic-suffix compounds, `short_anchor_guard` for blocked non-exact acronym-style phonetic/fuzzy matches, `anchor_quality_guard` for weak dominant-anchor fuzzy or phonetic matches, compatibility alias `fuzzy_anchor_guard`, `added_matter_breakdown` for dominant-core/additional-word scoring, and `translation_quality_flags` when `name_tr` is capped for dropping material from the original candidate name, short collapsed translated tokens, or weak translated non-exact-anchor evidence
- textual diagnostics may include `calibration_breakdown` when a guarded cap is applied; these diagnostics show the evidence-weighted score under the cap ceiling so similarly capped cases do not all return the same value
- result diagnostics may include `text_visual_guard` when weak text such as generic-only, missing-anchor, dominant-anchor-missing, semantic/phonetic-only evidence, weak fuzzy/phonetic-anchor evidence, weak shared low-protectability-anchor evidence, short-acronym subset evidence, limited one-anchor changed-matter/asymmetric-added-matter evidence, or plain-text-wordmark visual evidence prevents visual similarity from dominating the final score; OCR disagreement is diagnostic only and no longer suppresses agreement boosts
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
- `/api/v1/reports`
- `/api/v1/dashboard`
- `/api/v1/education/progress`
- `/api/v1/education/progress/sync`
- `/api/v1/education/moderation` (admin and superadmin only)

Watchlist similarity alerts exclude same-holder conflicts when the watched mark's `customer_application_no` can be resolved to a trademark holder identifier and the candidate trademark has the same `holder_tpe_client_id` or `holder_id`. Event alerts for the watched trademark remain visible.

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

Admin, tooling, and pipeline:
- `/api/v1/admin`
- `/api/v1/tools`
- `/api/v1/pipeline`

AI Studio lives under `/api/v1/tools`:
- `GET /api/v1/tools/status` is public and reports per-tool availability, disabled reason, AI credit cost, Logo Studio audit readiness, and non-breaking Logo Studio provider diagnostics for OpenAI/Gemini
- `POST /api/v1/tools/suggest-names` is authenticated, costs 1 unified AI credit only when usable safe names are returned, and validates Nice classes as `1..45`
- `POST /api/v1/tools/generate-logo` is authenticated, costs 5 unified AI credits, generates four logo candidates with `audit_status="pending"`, creates or appends to a Logo Studio project thread, queues visual trademark audits in the request background task, and uses OpenAI GPT Image 2 before falling back to Gemini Nano Banana Pro
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

- public search is rate-limited separately from authenticated search
- public landing-page search also enforces the free-tier daily quota and returns structured `429` detail when that quota is exhausted
- authenticated quick search reads the plan daily cap from runtime settings, and startup now realigns the known legacy quick-search overrides to the current product defaults
- some legacy routes remain for compatibility while newer flows live under `/api/v1`
- browser and live E2E suites in `tests/` are often the best source for real end-to-end request/response behavior
- Education moderation notes:
  - `PUT /api/v1/education/moderation` stores tester-only overrides for flashcards and quiz questions
  - supported item types are `flashcard` and `quiz_question`
  - flashcard overrides support category reassignment and soft-delete hiding
  - quiz-question overrides support category reassignment, explanation text edits, summary text edits, and soft-delete hiding through `education/moderation_overrides.json`
