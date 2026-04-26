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
- `/api/v1/trademark`

Search scoring response note:
- search routes continue to expose the existing scoring fields such as `total`, `text_similarity`, `semantic_similarity`, `phonetic_similarity`, `visual_similarity`, `translation_similarity`, `path_a_score`, `path_b_score`, `scoring_path_source`, `dynamic_weights`, and `matched_words`
- the canonical scorer now also returns `score_version: "v2_text_visual"`, `textual_breakdown`, `visual_breakdown`, and `decision_reason`
- `translation_similarity` is the translated-name textual path score from `name_tr`; it is not an additional overall combiner signal
- textual diagnostics may include `token_role` for matched words, `descriptor_terms` for corpus-derived descriptor-like terms that cannot become anchors, compatibility alias `non_protectable_terms`, `descriptor_stats` evidence when available, `compound_expansions` for compact generic-suffix compounds, `short_anchor_guard` for blocked non-exact acronym-style phonetic/fuzzy matches, `anchor_quality_guard` for weak dominant-anchor fuzzy or phonetic matches, compatibility alias `fuzzy_anchor_guard`, `added_matter_breakdown` for dominant-core/additional-word scoring, and `translation_quality_flags` when `name_tr` is capped for dropping material from the original candidate name or for weak translated non-exact-anchor evidence
- textual diagnostics may include `calibration_breakdown` when a guarded cap is applied; these diagnostics show the evidence-weighted score under the cap ceiling so similarly capped cases do not all return the same value
- result diagnostics may include `text_visual_guard` when weak text such as generic-only, missing-anchor, dominant-anchor-missing, semantic/phonetic-only evidence, weak fuzzy/phonetic-anchor evidence, or limited one-anchor changed-matter/asymmetric-added-matter evidence prevents visual similarity from dominating the final score; `short_non_exact_anchor_visual_guard` suppresses agreement boosts for short one-token marks when OCR disagrees with a non-exact anchor match
- translated-path diagnostics may include `translation_duplicate_original` when `name_tr` normalizes to the same candidate text and is capped so translated IDF flags cannot inflate Path B over Path A
- result diagnostics may include internal retrieval context such as `retrieval_sources`, `retrieval_matched_fields`, `retrieval_matched_stages`, and `retrieval_query_variants`; these explain how the candidate entered the scoring pool and do not change scoring math
- visual scoring uses active CLIP, DINOv2, and OCR components; color similarity is accepted by compatibility callers but ignored by the V2 risk score
- `visual_breakdown` may include OCR-disagreement and weak-text visual cap diagnostics; moderate neural visual similarity cannot create high risk when wordmark OCR disagrees and textual evidence is weak
- `dynamic_weights` is a compatibility explanation of active text/visual contribution under the max-plus combiner

Portfolio and monitoring:
- `/api/v1/watchlist`
- `/api/v1/alerts`
- `/api/v1/reports`
- `/api/v1/dashboard`
- `/api/v1/education/progress`
- `/api/v1/education/progress/sync`
- `/api/v1/education/moderation` (admin and superadmin only)

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
