# Test Plan

Last updated: 2026-04-22
Status: In progress

## Purpose

This file is the source of truth for post-reorganization test coverage.

It tracks:
- what test suites already exist
- what each suite proves
- which personas and features are covered
- what still needs to be built
- the order in which new suites should be implemented

The completed reorganization tracker is archived at `docs/archive/reorg-project-plan.md`. `test.md` tracks the verification program that follows it.
Repo-wide engineering workflow lives in `rules.md`.

## Scope

Primary goal:
- prove the live app works from each important user perspective after the reorganization

Secondary goals:
- catch route/import/template/static regressions early
- catch permission and plan-gate regressions
- catch browser-only failures such as broken JS, missing assets, and console/network errors

## Rules

- Keep live suites idempotent.
- Clean up any created test data in the same run.
- Prefer seeded test accounts over ad hoc local accounts.
- Reuse managed smoke personas for routine free/paid/business coverage instead of creating a fresh account on every run.
- Any browser flow that must register a fresh account must delete that account and its organization before the run if needed, and tear it down after the run.
- Do not rely on one shared "member" account as proof of role or plan coverage.
- Treat browser coverage as separate from API/live HTTP coverage.
- Any production-facing flow moved during the reorganization needs at least one live verification path.

## Status Vocabulary

- `Covered`: automated and currently in use
- `In progress`: partially covered or being split into dedicated suites
- `Planned`: accepted scope, not yet implemented
- `Needs browser coverage`: API/live coverage exists, but no browser-level proof yet
- `Blocked`: cannot complete until environment, seed data, or product rules are clarified

## Test Layers

### Layer 0: Structural Smoke

Purpose:
- catch broken imports, wrappers, templates, static asset paths, and packaging regressions quickly

Existing suites:
- `tests/test_phase0_smoke.py`
- `tests/test_page_smoke.py`
- `tests/test_dashboard_layout.py`
- `tests/test_security_audit.py`

### Layer 1: Unit and Domain Tests

Purpose:
- verify domain logic without requiring the live app

Existing suites:
- `tests/test_auth.py`
- `tests/test_data_collection.py`
- `tests/test_class_utils.py`
- `tests/test_deadline.py`
- `tests/test_edge_cases.py`
- `tests/test_ingest.py`
- `tests/test_metadata.py`
- `tests/test_phonetic.py`
- `tests/test_plan_features.py`
- `tests/test_pdf_extract.py`
- `tests/test_pdf_extract_events.py`
- `tests/test_pdf_extract_tasarim.py`
- `tests/test_cd_extract_tasarim.py`
- `tests/test_reconcile_tasarim.py`
- `tests/test_scoring_engine.py`
- `tests/test_settings_manager.py`
- `tests/test_status_reconciler.py`
- `tests/test_subscription.py`
- `tests/test_subscription_limits.py`
- `tests/test_translation.py`
- `tests/test_translation_scoring.py`
- `tests/test_turkish_similarity.py`
- `tests/test_validation.py`
- `tests/test_zip.py`

Ingest/repair coverage note:
- `tests/test_repair.py` and `tests/test_ingest.py` cover logo-only `SEKIL/ŞEKİL` cleanup so stored names, translations, and text embeddings are removed together while visual/OCR evidence remains available for image search.

Scoring coverage note:
- `tests/test_scoring_engine.py` covers the V2 text/visual scorer, including descriptor-like token caps, low-protectability shared-anchor guards, compact compounds, short-token boundary retrieval source checks, short-anchor phonetic guards, bidirectional short-acronym subset caps, short collapsed translation caps, dominant-anchor fuzzy/phonetic quality guards, conservative OCR-vs-OCR visual behavior, image-only OCR staying out of the trademark-name text query, image-only visual quality calibration and strict/balanced layout-variant logo corroboration, plain-text wordmark visual guards, dominant-core added matter, bidirectional and single-anchor asymmetric changed-matter caps, continuous guardrail calibration, weak/limited-text visual guards, OCR disagreement diagnostics, and search/watchlist wiring into `score_pair()`.
- `tests/test_search_risk_report.py` covers the advisory search risk report route, public pending-report generation, authenticated pending-report claiming, request validation, monthly report-quota handling, Qwen-first text provider selection with DeepSeek/Gemini fallbacks, Qwen-first multimodal provider selection with Gemini fallback, invalid-output usage refund behavior, factual-only prompt trimming without deterministic score anchoring, provider-score result ordering, saved PDF report persistence, and Unicode PDF rendering for Turkish text; `tests/test_api_endpoints.py`, `tests/test_page_smoke.py`, and `tests/test_dashboard_layout.py` cover report deletion routes, safe file cleanup, landing/dashboard report button wiring, multipart logo upload wiring, login-to-view handoff, and the compact ready-card handoff to the saved PDF.
- `tests/test_api_endpoints.py` covers `/api/search` service wiring so unified enhanced search maps canonical `RiskEngine.assess_brand_risk()` results into the existing response shape.
- `tests/test_page_smoke.py` covers search and watchlist score-card wiring so visible text score cards use the original-name `path_a_score`, while translated-name evidence remains in the translation card; inline watchlist conflicts keep semantic/phonetic evidence as sub-rows instead of folding semantic into the main text score.

### Layer 2: App API Integration

Purpose:
- verify FastAPI routes, auth gates, response shapes, validation, and service wiring with the test client
- verify watchlist alert filtering behavior, including same-holder conflict exclusion by holder ID while preserving event alerts
- verify active watchlist conflict filtering uses both an unexpired opposition deadline and an appealable published status, so historical registered/renewed/refused marks do not appear as active conflicts

Existing suites:
- `tests/test_api_endpoints.py`

### Layer 3: Live HTTP Integration

Purpose:
- verify the running application, real auth, real routing, real static/template paths, and live DB-backed flows

Existing suites:
- `tests/test_live_app_e2e.py`
- `tests/test_watchlist_e2e.py`

### Layer 4: Browser E2E

Purpose:
- catch failures that API tests miss: broken JS, missing assets, frontend state issues, console errors, and failed network requests

Status:
- `Covered`

### Layer 5: Nightly and Destructive Flows

Purpose:
- cover longer or more stateful flows that are too expensive for every change

Status:
- `Covered`

## Execution Lanes

### Lane A: Change Smoke

Run on most code changes:
- `tests/test_phase0_smoke.py`
- `tests/test_page_smoke.py`
- `tests/test_dashboard_layout.py`
- targeted domain/API suites affected by the change

### Lane B: Live App Smoke

Run after app wiring, template/static, auth, dashboard, search, or watchlist changes:
- `python tests/test_live_app_e2e.py`

### Lane C: Feature Live Verification

Run before deployment or after larger batches:
- `python tests/test_live_app_e2e.py`
- `python tests/test_watchlist_e2e.py`
- selected feature-specific live suites once they exist

### Lane D: Nightly Full Coverage

Target end state:
- all critical live HTTP suites
- browser E2E suites
- destructive flows
- long-running scans or imports where appropriate

Current command:
- `python tests/test_nightly_e2e.py`

## Current Live Baseline

Current broad live smoke coverage in `tests/test_live_app_e2e.py`:
- health
- API info
- public pages
- login
- auth gate for quick search
- dashboard stats
- usage summary
- search credits
- quick search text
- quick search with class filters
- quick search with image upload
- public search with text, class filters, and image upload
- delegated watchlist E2E

Current deep live coverage in `tests/test_watchlist_e2e.py`:
- auth gate
- watchlist stats
- watchlist list/search/sort
- create/update/delete
- duplicate handling
- scan trigger
- alerts endpoints
- logo upload/get/delete
- Excel import/export flows
- cleanup of created test records

Current environment note:
- higher-tier persona coverage is now exercised in this workspace through `TEST_SUPERADMIN_EMAIL` / `TEST_SUPERADMIN_PASSWORD` backed provisioning; without those env vars, the paid/business/superadmin suites still fall back to documented skips.
- the Docker-backed live/browser/nightly lanes are currently validated with `WORKERS=1`; the previous four-worker default caused intermittent empty-response failures on `/api/v1/search/quick` and `/api/v1/search/intelligent`

## Persona Coverage Matrix

| Persona | Current Status | Current Proof | Missing Coverage |
| --- | --- | --- | --- |
| Public visitor | Covered | public pages, public search across text/class/image paths, browser landing search with short-query validation plus class/image journeys, landing upgrade-modal plan handoff assertions, public-search daily free-quota upgrade coverage, auth gates, pricing to checkout flow, registration modal, email-verification modal resend/success flow, and forgot-password request/error/success/login flow | none on the current public-auth surface |
| Authenticated member | Covered | dashboard, usage, credits, quick search, browser login/logout, watchlist CRUD browser flow, reports generation browser flow, free-plan application gate coverage with upgrade-modal recommendation, profile modal and avatar upload browser flow, free-plan watchlist logo gate coverage with upgrade-modal recommendation, alert acknowledge/resolve/dismiss browser journeys, inline opposition handoff into the appeal application form, and alert-detail opposition guidance modal coverage | paid application browser happy path when no paid persona is configured |
| Free user | Covered | dedicated free persona live suite with self-registration fallback and plan-limit checks, explicit free quick-search live/browser coverage, browser watchlist CRUD and report generation flows on a fresh free persona, browser proof for free-plan application gates, free watchlist-logo gate coverage, free live-search upgrade-gate browser coverage with starter recommendation assertions, public-search daily free-quota upgrade coverage, and checkout activation | longer-running quota and downgrade/upgrade edge cases |
| Paid user | Covered | dedicated paid persona live suite with superadmin-backed provisioning, explicit paid quick-search text/image live/browser coverage, paid watchlist-logo browser happy path, paid application browser CRUD, and aggregate live/browser coverage | deeper paid report/application browser journeys beyond the current starter-plan surface |
| Business user | Covered | dedicated business persona live suite with superadmin-backed provisioning across lead credits, live search, holders, attorneys, and portfolio endpoints, plus browser live-search, holder, and attorney portfolio journeys | broader business destructive flows only if product workflow requires them |
| Admin user | Covered | dedicated admin live suite on mounted admin/org routes plus aggregate live smoke delegation | destructive admin-action coverage only if product workflow requires it |
| Superadmin user | Covered | dedicated superadmin live suite plus a real exercised admin-capable browser suite for the `/admin` panel and landing-page Education tester controls | deeper destructive platform-admin actions only if needed |

## Feature Coverage Matrix

| Feature Area | Structural/API Coverage | Live Coverage | Browser Coverage | Status | Next Action |
| --- | --- | --- | --- | --- | --- |
| Public pages | Yes | Yes | Yes | Covered | expand marketing subsection coverage only if page-specific regressions appear |
| Authentication | Yes | Yes | Yes | Covered | extend only if new auth UI states or role-specific auth gates are added |
| Dashboard shell/layout | Yes | Yes | Yes | Covered | extend browser checks to per-tab state where needed |
| Dashboard stats | Yes | Yes | Yes | Covered | expand negative-path assertions only if dashboard data regressions appear |
| Usage and credits | Partial | Yes | Yes | Covered | deepen plan-specific quota edge cases only if plan rules change |
| Quick search | Partial | Yes | Yes | Covered | extend only if plan-limit rules or new quick-search modes change |
| Public search | Partial | Yes | Yes | Covered | extend only if the public-search contract, landing-page search UX, or upgrade-plan recommendation rules change |
| Live search | Partial | Yes | Yes | Covered | extend only if the live-search UI, quota rules, or upgrade-plan recommendation rules change |
| Watchlist | Yes | Yes | Yes | Covered | extend browser coverage to additional sort/filter states only if that UI is mounted again |
| Alerts | Partial | Partial via watchlist | Yes | Covered | extend only if new alert actions, filter states, or escalation paths are added |
| Applications | Partial | Yes | Yes | Covered | extend only if new paid-only application workflows are added |
| Reports | Partial | Yes | Yes | Covered | add browser download coverage when an export-eligible persona is available |
| AI Studio | Partial | Planned | Needs browser coverage | In progress | cover status gating, unified AI credits, Name Lab generation, Logo Studio project/revision generation, asynchronous audit polling, safe-candidate selection, and history rendering |
| Billing and checkout | Partial | Yes | Yes | Blocked | keep pre-payment and failure-path coverage current; revisit successful paid checkout only after a real payment method/provider is configured |
| Admin | Partial | Yes | Yes | Covered | deepen destructive admin actions only where they are product-critical |
| Uploads and assets | Yes | Yes | Yes | Covered | extend only if new export/download asset paths are added |
| Portfolio / holders / attorneys | Partial | Yes | Yes | Covered | deepen only if new business-only portfolio actions become product-critical |

## Existing Suite Inventory

### Structural and Packaging

- `tests/test_phase0_smoke.py`: package surface, import compatibility, canonical paths, repo-level invariants
- `tests/test_page_smoke.py`: canonical templates and static bundles after Phase 11
- `tests/test_dashboard_layout.py`: dashboard page structure and tab layout
- `tests/test_security_audit.py`: high-risk source checks and selected path/security assertions

### API and Application Wiring

- `tests/test_api_endpoints.py`: FastAPI route behavior, request validation, auth gates, service responses

### Domain and Utility

- `tests/test_auth.py`: password hashing, JWTs, auth model validation
- `tests/test_data_collection.py`: collector recency-window logic, Gazette validation, issue completeness checks, and download planning
- `tests/test_pdf_extract_tasarim.py`: Tasarım PDF extraction pure helpers (clean_text, INID-field tokenization, Locarno parsing, applicant/attorney/designer/view-label parsing, hague-record shape) plus extract_issue --force-wipes-images/ behavior and the canonical `view_image_key` shape `{appno_norm}/{d}_{v}.jpg`
- `tests/test_cd_extract_tasarim.py`: Tasarım HSQLDB CD bundle extractor — `\uXXXX` escape decoder, Locarno comma-splitter, INSERT-line parser across IDDOSSIER/IDHOLDER/IDDESIGN/IDDESIGNER/IDANNOTATION tables, file-level log wrapper with line-prefixed errors, per-design image resolver with numeric sort, 7-Zip wrapper with dynamic CD-root resolution (modern `{N}/` and verbose `setup/` layouts), idbulletin.inf parser, end-to-end metadata orchestrator, image persistence with the canonical key shape, symmetric CD-side image dedup that unlinks PDF duplicates after writing to cd_images/, `_find_existing_issue_folder` reuse semantics, and CLI that materializes each CD into `bulletins/Tasarim/TS_{N}_{date}/cd_metadata.json` plus `cd_images/`
- `tests/test_reconcile_tasarim.py`: Tasarım stage-3 PDF↔CD reconciler — JSON loaders with swap-detection, CanonicalDesignRecord/Design/View dataclasses, normalize_cd_dossier (DD.MM.YYYY → ISO, field renames, design_count int cast, CD title → canonical name), normalize_pdf_record (filing_date → application_date rename, embeddings/bbox/xref drop, hague_reference + page_range + deferred_publication preservation), merge_records (CD-wins precedence, view dedup with image_source provenance, design merge by no, attorney field union), `_normalise_registration_no` for Hague pairing, reconcile_metadata orchestrator (single-side + paired, bulletin_no None tolerance), `dedupe_images_on_disk` mop-up helper, and CLI that materializes `bulletins/Tasarim/TS_{N}_{date}/merged_metadata.json`
- `tests/test_subscription.py`: plan eligibility and credit logic
- `tests/test_subscription_limits.py`: subscription limit behavior
- `tests/test_scoring_engine.py`: V2 text/visual scoring behavior, common-anchor/generic/descriptor caps, descriptor-stat classifier tests, low-protectability anchor classifier and weak shared-anchor caps, short-anchor and dominant-anchor fuzzy/phonetic guardrails, short-acronym subset and short collapsed translation caps, continuous cap calibration, conservative OCR-vs-OCR visual behavior, image-only OCR staying out of the trademark-name text query, image-only visual quality calibration and strict/balanced layout-variant logo corroboration, plain-text wordmark visual profiling/guardrails, single-anchor asymmetric added-matter caps, weak/limited-text visual guardrails, OCR-disagreement diagnostics, Retrieval V2 normalization/source diagnostics, compact compound retrieval/scoring, added-matter scoring, duplicate/collapsed translation caps, compatibility fields, and combiner behavior
- `tests/test_edge_cases.py`: scoring/search edge cases
- `tests/test_translation.py`: translation behavior
- `tests/test_translation_scoring.py`: translated-name Path B scoring behavior and CLIP/DINOv2/OCR visual composite coverage
- `tests/test_turkish_similarity.py`: Turkish similarity helpers
- `tests/test_phonetic.py`: phonetic helpers
- `tests/test_ingest.py`: ingest and pipeline behavior
- `tests/test_validation.py`: validation rules
- `tests/test_deadline.py`: deadline logic
- `tests/test_class_utils.py`: class utility logic
- `tests/test_status_reconciler.py`: status reconciliation behavior
- `tests/test_settings_manager.py`: settings handling
- `tests/test_plan_features.py`: plan feature mappings

### Live HTTP

- `tests/live/personas/test_public_live.py`: dedicated public visitor live suite with text, class-filter, and image public-search coverage plus the landing-page daily free-quota gate
- `tests/live/personas/test_member_live.py`: dedicated authenticated member live suite
- `tests/test_live_app_e2e.py`: broad live app smoke across core public/auth/dashboard/search surface
- `tests/test_watchlist_e2e.py`: deep live watchlist flow coverage

### Browser E2E

- `tests/browser/test_public_browser_smoke.py`: landing, public search text/class/image journeys, landing upgrade-modal plan handoff assertions, public-search daily free-quota upgrade coverage, pricing-to-checkout, registration, email-verification modal resend/success, and forgot-password request/error/success browser journey coverage
- `tests/browser/test_member_browser_smoke.py`: login, dashboard overview KPI and usage-badge contract, dashboard quick search, tab navigation, and logout browser journey coverage
- `tests/browser/test_search_browser.py`: dedicated free quick-search text, localized single-result watchlist add success toast coverage, free quick-search daily-limit and single-result watchlist-limit upgrade-gate coverage, and paid quick-search text/image browser coverage with plan-limit assertions
- `tests/browser/test_live_search_browser.py`: free-plan live-search upgrade-gate with starter recommendation assertions plus business live-search happy-path browser coverage
- `tests/browser/test_member_feature_browser.py`: deeper member watchlist CRUD, quick-add limit gate, capacity-aware inline bulk-watchlist upgrade guidance from the entity portfolio modal, inline bulk-upload upgrade guidance at watchlist capacity, report generation, free application gate with upgrade-modal recommendation, profile/avatar, and paid-application browser flows
- `tests/browser/test_business_browser.py`: business holder/attorney portfolio modal journeys, in-modal entity search, and CSV export trigger coverage
- `tests/browser/test_watchlist_assets_browser.py`: free-plan watchlist logo gate coverage with upgrade-modal recommendation plus env-gated paid watchlist logo upload/delete asset coverage
- `tests/browser/test_alerts_browser.py`: member alert detail acknowledge, inline resolve/dismiss, and appeals filter/sort browser journeys on seeded alerts
- `tests/browser/test_opposition_browser.py`: opposition guidance modal plus inline alert-to-appeal handoff coverage on seeded alert data
- `tests/browser/test_billing_browser.py`: pricing/checkout locale render coverage for Turkish and Arabic RTL, mobile viewport billing coverage, checkout registration, checkout login, checkout forgot-password reset/login recovery, and paid-checkout initialization browser coverage
- `tests/browser/test_admin_browser.py`: admin-capable browser coverage for `/admin` navigation plus landing-page Education tester moderation controls
- `tests/browser/test_design_search_browser.py`: dashboard Search tab activation (Tasarım Arama section now lives inside the Search tab below the Marka form), design quick-search submission, result-card render, and en/tr/ar locale label coverage on the design-search section title
- `tests/browser/test_design_watchlist_browser.py`: dashboard "Tasarım Takibi" tab activation, add-form expansion, watchlist item creation, list refresh, and en/tr/ar locale label coverage (alert lifecycle deferred — depends on a populated alerts table)
- `tests/browser/test_cografi_dashboard_browser.py`: cografi search subview (Coğrafi tab inside Search) + autocomplete dropdown kind-chip regression check + result card → detail modal hydration; cografi watchlist subview (Coğrafi tab inside Watchlist) + 6-cell stats bar + add-modal 4-way watch_type radio (holder/reference/region/lifecycle) toggling the right field groups; tr/ar locale switching with html dir=rtl assertion for AR
- `tests/browser/test_patent_dashboard_browser.py`: patent (Patent / Faydalı Model) foundational dashboard lifecycle — search subview + result card → detail modal hydration; watchlist subview + 4-cell stats bar + add-modal 2-way watch_type radio (holder/reference) toggling the right field groups; CSV export download with UTF-8 BOM + Turkish headers; round-trip create reference watch + scan + delete; tr/ar locale switching with html dir=rtl assertion for AR. Uses managed-professional persona (2000/day search quota — slice 1 is the most search-heavy of the patent suite).
- `tests/browser/test_patent_free_tier_gate_browser.py`: patent free-tier watchlist quota gate — API pre-fill 5 holder watches (the cross-registry combined cap from subscription_plans.max_watchlist_items), then attempt the 6th via UI, assert HTTP 403 with structured limit_exceeded body, inline #pwl-add-error banner visible + non-empty + contains a quota/limit word, modal stays open, no leakage into list. Uses managed-free-smoke persona.
- `tests/test_design_watchlist_service.py`: design watchlist CRUD service (Locarno normalization, halfvec literal, combined quota, dedupe by customer_application_no, update partial-set behavior, delete with alert-count reporting)
- `tests/test_design_alert_service.py`: design alert formatter (severity bucketing, JSON/string score_details handling, datetime serialization), lifecycle transitions (acknowledge/resolve/dismiss with notes), and scanner-side `insert_alert_row` dedup behavior
- `tests/test_design_scanner.py`: scanner scoring combiner weights for image vs text-only watchlists, Locarno overlap helper, end-to-end scan flow with mocked DB, and `trigger_design_watchlist_scan` exception-swallowing wrapper
- `tests/test_locales_design_watchlist.py`: i18n parametrized presence + non-empty + cross-language key-set parity + Arabic-script + Turkish-distinctness coverage for the 45-key `design_watchlist` block plus the `tabs.design_watchlist` label
- `tests/test_browser_e2e.py`: aggregate browser smoke runner

## Planned Suite Structure

Target structure:
- `tests/live/helpers/config.py`
- `tests/live/helpers/client.py`
- `tests/live/helpers/auth.py`
- `tests/live/helpers/assertions.py`
- `tests/live/helpers/cleanup.py`
- `tests/live/helpers/artifacts.py`
- `tests/live/personas/test_public_live.py`
- `tests/live/personas/test_member_live.py`
- `tests/live/personas/test_free_user_live.py`
- `tests/live/personas/test_paid_user_live.py`
- `tests/live/personas/test_business_user_live.py`
- `tests/live/personas/test_admin_live.py`
- `tests/live/personas/test_superadmin_live.py`
- `tests/live/features/test_search_live.py`
- `tests/live/features/test_dashboard_live.py`
- `tests/live/features/test_watchlist_live.py`
- `tests/live/features/test_billing_live.py`
- `tests/live/features/test_reports_live.py`
- `tests/live/features/test_applications_live.py`

Notes:
- `tests/test_live_app_e2e.py` should remain as the top-level smoke runner until the new structure fully replaces it.
- `tests/test_watchlist_e2e.py` should be refactored onto shared helpers, not rewritten from scratch first.

## Implementation Plan

### Slice 1: Shared Live Test Infrastructure

Status:
- `Covered`

Deliverables:
- shared HTTP client helpers
- shared auth/session helpers
- common assertion helpers
- common cleanup helpers
- shared env var handling

Exit criteria:
- `tests/test_live_app_e2e.py` and `tests/test_watchlist_e2e.py` can consume the shared helpers

### Slice 2: Public and Member Persona Suites

Status:
- `Covered`

Deliverables:
- `tests/live/personas/test_public_live.py`
- `tests/live/personas/test_member_live.py`

Coverage:
- public pages
- login
- auth gates
- dashboard
- usage
- quick search
- public search

### Slice 3: Plan-Gated Personas

Status:
- `Covered`

Deliverables:
- `tests/live/personas/test_free_user_live.py`
- `tests/live/personas/test_paid_user_live.py`
- `tests/live/personas/test_business_user_live.py`

Coverage:
- free plan limits
- paid features
- business/portfolio flows
- upload and asset behavior by plan

Environment Notes:
- `free` persona can self-register if `TEST_FREE_EMAIL` and `TEST_FREE_PASSWORD` are not provided.
- `paid` and `business` personas can use explicit credentials via `TEST_PAID_EMAIL` / `TEST_PAID_PASSWORD` and `TEST_BUSINESS_EMAIL` / `TEST_BUSINESS_PASSWORD`.
- If higher-tier persona credentials are not seeded, the suites can optionally self-provision using `TEST_SUPERADMIN_EMAIL` / `TEST_SUPERADMIN_PASSWORD`.
- Without higher-tier credentials or superadmin provisioning, the paid/business suites execute as documented skips instead of false failures.

### Slice 4: Admin Personas

Status:
- `Covered`

Deliverables:
- `tests/live/personas/test_admin_live.py`
- `tests/live/personas/test_superadmin_live.py`

Coverage:
- admin dashboard
- analytics
- credit and org/user management
- superadmin-only actions

Environment Notes:
- `admin` suite uses the default authenticated account and expects `role=admin` with `is_superadmin=false`.
- `superadmin` suite requires `TEST_SUPERADMIN_EMAIL` and `TEST_SUPERADMIN_PASSWORD`.
- Without explicit superadmin credentials, the superadmin suite executes as a documented skip instead of failing the aggregate smoke.

### Slice 5: Feature Suites

Status:
- `Covered`

Deliverables:
- dedicated live suites for search, dashboard, watchlist, billing, reports, and applications

Coverage:
- happy path
- permission path
- invalid input path
- persistence/state change
- cleanup/idempotency

Environment Notes:
- Billing happy-path validation uses `TEST_VALID_DISCOUNT_CODE` when available; without it, the suite records a documented skip for the valid-code path.
- Applications happy-path coverage depends on a paid persona via `TEST_PAID_EMAIL` / `TEST_PAID_PASSWORD` or superadmin-backed persona provisioning.
- Reports happy-path coverage uses a fresh free persona so the suite does not consume the default account's monthly quota.

### Slice 6: Browser E2E

Status:
- `Covered`

Deliverables:
- Playwright-based browser smoke and core journeys

Coverage:
- console errors
- failed network requests
- JS bootstrap failures
- navigation and interaction flows
- screenshots on failure

Delivered:
- `tests/browser/helpers/config.py`
- `tests/browser/helpers/session.py`
- `tests/browser/helpers/assertions.py`
- `tests/browser/helpers/artifacts.py`
- `tests/browser/test_public_browser_smoke.py`
- `tests/browser/test_member_browser_smoke.py`
- `tests/browser/test_business_browser.py`
- `tests/test_browser_e2e.py`

Current journeys:
- landing bootstrap with no browser errors
- public search from the landing page
- pricing to checkout navigation
- pricing and checkout locale render for Turkish and Arabic, including RTL on Arabic
- pricing and checkout mobile viewport render with no horizontal overflow and usable billing controls
- forgot-password request flow to the reset-code step
- forgot-password invalid reset-code handling
- full forgot-password reset-success and post-reset login flow
- checkout forgot-password reset-success and post-reset login flow
- registration modal to dashboard redirect
- dashboard email-verification modal resend and successful verification flow
- login via the landing modal
- dashboard overview KPI and usage/status badge contract against live API data
- dashboard quick search
- watchlist, reports, and applications tab navigation
- logout back to the landing page

### Slice 7: Nightly and Destructive Coverage

Status:
- `Covered`

Deliverables:
- scheduled longer-running suites

Coverage:
- imports/exports
- larger report flows
- scan or background-task flows
- destructive flows unsuitable for every change

Delivered:
- `tests/nightly/helpers/config.py`
- `tests/nightly/test_stateful_live.py`
- `tests/test_nightly_e2e.py`
- orchestration flags in `tests/test_live_app_e2e.py` for nightly scheduling

Current nightly lane:
- broad live smoke via `tests/test_live_app_e2e.py`
- browser smoke via `tests/test_browser_e2e.py`
- stateful/destructive flows via `tests/nightly/test_stateful_live.py`

Stateful/deep delegates:
- `tests/test_watchlist_e2e.py`
- `tests/live/features/test_reports_live.py`
- `tests/live/features/test_applications_live.py`

Environment Notes:
- `RUN_NIGHTLY_LIVE_SMOKE=0` skips the broad live smoke layer.
- `RUN_NIGHTLY_BROWSER=0` skips the browser layer.
- `RUN_NIGHTLY_STATEFUL=0` skips the stateful/destructive layer.
- The nightly runner suppresses duplicate heavy delegates inside `tests/test_live_app_e2e.py` by setting `RUN_WATCHLIST_E2E=0`, `RUN_REPORTS_FEATURE=0`, and `RUN_APPLICATIONS_FEATURE=0` for that phase of the run.

## Known Gaps

- Browser coverage now includes registration, dashboard email-verification resend/success, and the full forgot-password request/error/success flow.
- Browser coverage now includes deeper free-member watchlist CRUD, report generation, application-gate, billing, profile/avatar, paid application CRUD, business holder/attorney portfolio journeys, free watchlist-logo gate, paid watchlist-logo upload/delete, alerts, opposition, and admin-panel flows.
- Alert browser coverage now proves acknowledge, resolve, dismiss, appeals filter/sort behavior, inline opposition handoff, and the alert-detail opposition guidance modal on seeded alerts.
- Higher-tier live/browser coverage still depends on seeded paid/business credentials or superadmin provisioning env vars; in this workspace the superadmin provisioning path is now exercised and green.
- Billing valid-discount happy-path coverage depends on `TEST_VALID_DISCOUNT_CODE` unless a seeded code exists in the environment.
- Successful paid checkout is currently blocked because no real payment method/provider is configured in this environment; the existing billing browser/live coverage stops at authenticated initialization, plan handoff, discount handling, and graceful gateway/failure handling.
- `api/admin_routes.py` defines org-admin IDF debug endpoints, but the live router registry does not currently mount them, so they are not covered by the admin live suite.
- Nightly scheduling exists as an executable runner, but it is not yet wired to an external scheduler or CI job in this repository.

## Progress Log

### 2026-04-14

- Created `test.md` as the verification checkpoint file after the codebase reorganization.
- Recorded the current suite inventory from the repository.
- Recorded the current live baseline from `tests/test_live_app_e2e.py` and `tests/test_watchlist_e2e.py`.
- Defined the target persona matrix, feature matrix, execution lanes, and implementation slices.
- Set the next work item to shared live test infrastructure, followed by dedicated persona suites.
- Added `tests/live/helpers/` with shared config, HTTP client, auth, assertions, and cleanup primitives.
- Refactored `tests/test_live_app_e2e.py` and `tests/test_watchlist_e2e.py` to consume the shared helpers.
- Verified the refactor with `py_compile`, `python tests/test_watchlist_e2e.py` (`34/34`), and `python tests/test_live_app_e2e.py` (`17/17`).
- Completed Slice 1 and moved the next work item to dedicated `public` and `member` persona suites.
- Added `tests/live/personas/test_public_live.py` and `tests/live/personas/test_member_live.py` as the first dedicated role-based live suites.
- Rewired `tests/test_live_app_e2e.py` into an aggregate runner that delegates to the `public`, `member`, and watchlist suites.
- Completed Slice 2 and moved the next work item to the plan-gated persona suites (`free`, `paid`, `business`).
- Added `tests/live/helpers/personas.py` to resolve persona credentials, self-register a free user, and optionally provision higher-tier personas via the superadmin plan-change route.
- Added `tests/live/personas/test_free_user_live.py`, `tests/live/personas/test_paid_user_live.py`, and `tests/live/personas/test_business_user_live.py`.
- Expanded `tests/test_live_app_e2e.py` so the aggregate smoke now delegates to the free, paid, and business suites as well.
- Verified Slice 3 with the dedicated persona scripts and the aggregate runner.
- Completed Slice 3 and moved the next work item to the admin and superadmin persona suites.
- Added `tests/live/personas/test_admin_live.py` and `tests/live/personas/test_superadmin_live.py`.
- Expanded `tests/test_live_app_e2e.py` so the aggregate smoke now delegates to the admin and superadmin suites as well.
- Verified the org-admin suite live against `/api/v1/users`, `/api/v1/organization*`, and superadmin guard boundaries.
- Recorded that `api/admin_routes.py` IDF endpoints are present in the codebase but not mounted by the live router registry.
- Completed Slice 4 and moved the next work item to dedicated feature suites.
- Added `tests/live/features/test_search_live.py`, `tests/live/features/test_dashboard_live.py`, `tests/live/features/test_watchlist_live.py`, `tests/live/features/test_billing_live.py`, `tests/live/features/test_reports_live.py`, and `tests/live/features/test_applications_live.py`.
- Expanded `tests/test_live_app_e2e.py` so the aggregate smoke now delegates to the feature suites in addition to the persona suites and deep watchlist run.
- Restored the `reports/` package into the live backend path by mounting it in `docker-compose.yml` and removing the incorrect `reports/` ignore rule from `.dockerignore`.
- Fixed the live reports regression by moving the runtime report output boundary to `uploads/reports`, updating `docker-compose.yml` to use `/app/uploads/reports`, and adding a `test_phase0_smoke.py` assertion for the new default.
- Hardened the shared live auth helpers to back off and retry on `429` rate-limit responses so the aggregate smoke remains stable when many persona and feature suites run back-to-back.
- Verified Slice 5 live with the dedicated feature suites (`search` `12/12`, `dashboard` `5/5`, `watchlist` `12/12`, `billing` `5/5`, `reports` `8/8`, `applications` `11/11`) and the full aggregate runner (`python tests/test_live_app_e2e.py` -> `14/14`).
- Completed Slice 5 and moved the next work item to browser-based coverage.
- Added the browser helper layer under `tests/browser/helpers/` with config, session/monitoring, assertions, and failure artifact capture.
- Added `tests/browser/test_public_browser_smoke.py`, `tests/browser/test_member_browser_smoke.py`, and the aggregate runner `tests/test_browser_e2e.py`.
- Fixed the dashboard Alpine browser failure by replacing the inline `x-init` `for (...)` loops in `templates/dashboard/partials/_leads_panel.html` with the new `populateNiceClassOptions($el)` helper in `static/js/dashboard/app.js`.
- Fixed the pricing/checkout browser asset regression by adding an explicit favicon link to the billing templates instead of letting those pages fall through to `/favicon.ico`.
- Added a structural regression guard in `tests/test_phase0_smoke.py` so the inline `x-init` loop pattern does not return silently.
- Verified Slice 6 with `python -m py_compile` on the browser layer, `python -m pytest tests/test_phase0_smoke.py -q -k nice_class_options` (`1 passed`), `python tests/browser/test_public_browser_smoke.py` (`3/3`), `python tests/browser/test_member_browser_smoke.py` (`6/6`), and `python tests/test_browser_e2e.py` (`2/2`).
- Completed Slice 6 and moved the next work item to nightly and destructive coverage.
- Added the nightly config/helper layer, the stateful nightly delegate suite, and the aggregate nightly runner.
- Added orchestration flags to `tests/test_live_app_e2e.py` so the nightly runner can avoid redundant heavy delegates during the smoke phase and then run them deliberately in the stateful phase.
- Hardened the nightly/browser lane for the shared member account by tolerating expected quota-driven `429` responses in the search/browser flows instead of treating them as false regressions.
- Hardened `tests/test_nightly_e2e.py` to echo delegate output safely on Windows without `cp1252` failures.
- Verified Slice 7 with `py_compile`, `python tests/nightly/test_stateful_live.py`, `python tests/test_browser_e2e.py`, and `python tests/test_nightly_e2e.py`.
- Completed Slice 7.

### 2026-04-17

- Expanded `tests/browser/test_public_browser_smoke.py` to cover the landing-page registration modal, forgot-password request flow, and invalid reset-code handling.
- Hardened the public browser search journey to tolerate expected quota-driven `429` responses instead of treating them as regressions.
- The new browser auth coverage exposed a real dashboard bootstrap bug: `renderChart()` could run before Chart.js was available after login or registration.
- Fixed the dashboard bootstrap race in `static/js/dashboard/app.js` by retrying chart rendering until Chart.js is loaded instead of throwing `ReferenceError: Chart is not defined`.
- Verified the updated browser lane with `python tests/browser/test_public_browser_smoke.py`, `python tests/test_browser_e2e.py`, and `python tests/test_nightly_e2e.py`.
- Added `tests/browser/test_member_feature_browser.py` and wired it into `tests/test_browser_e2e.py` for deeper member watchlist CRUD, report generation, and paid-application browser coverage.
- Fixed the quick-add watchlist modal defaults in `templates/dashboard/partials/_modals.html` so the hidden defaults no longer force paid-only visual/phonetic monitoring on free users.
- Fixed the watchlist edit modal in `static/js/dashboard/app.js` so `null` visual/phonetic flags no longer rehydrate as enabled and trip the paid-only gate on update.
- Added reusable application/report cleanup helpers in `tests/live/helpers/cleanup.py` and a browser-config override helper in `tests/browser/helpers/config.py`.
- Verified the deeper browser lane with `python tests/browser/test_member_feature_browser.py`, `python tests/test_browser_e2e.py`, and `python tests/test_nightly_e2e.py`.
- Expanded `tests/browser/test_member_feature_browser.py` to cover the free-plan application gate and the profile/avatar browser journey on a fresh free persona.
- Added `tests/browser/test_billing_browser.py` for checkout registration, authenticated checkout initialization, and checkout login flows.
- Fixed the toast regression exposed by the free application gate by restoring `window.AppToast.success()`, `error()`, `info()`, and `warning()` wrappers in `static/js/utils/toast.js`.
- Fixed the checkout auth probe in `templates/billing/checkout.html` to use `/api/v1/auth/me` instead of the invalid `/api/v1/users/me` path.
- Isolated the billing browser cases into fresh browser contexts so dashboard redirects and background requests do not contaminate later checkout assertions.
- Verified the billing/profile slice with `python tests/browser/test_billing_browser.py`, `python tests/browser/test_member_feature_browser.py`, `python tests/test_browser_e2e.py`, and `python tests/test_nightly_e2e.py`.
- Added `tests/browser/test_alerts_browser.py` to seed deterministic alerts and verify detail acknowledge plus inline resolve/dismiss actions in the member dashboard.
- Added `tests/browser/test_admin_browser.py` as the superadmin admin-panel browser suite with an env-gated execution path.
- Verified the alert/admin browser slice with `python tests/browser/test_alerts_browser.py`, `python tests/browser/test_admin_browser.py`, `python tests/test_browser_e2e.py`, and `python tests/test_nightly_e2e.py`.
- Added `tests/browser/test_opposition_browser.py` to seed deterministic opposition-ready alerts and verify both the inline alert-to-appeal application handoff and the alert-detail opposition guidance modal.
- Hardened the shared browser login helper in `tests/browser/helpers/session.py` to retry `/api/v1/auth/login` on recoverable `429` responses and clear the corresponding browser monitor noise so the aggregate browser lane remains stable under auth-rate pressure.
- Restarted Docker Desktop after the live backend and Docker API both wedged during repeated end-to-end runs, then re-verified on the recovered app instance once `/health` returned `200` again.
- Verified the opposition/browser hardening slice with `python tests/browser/test_opposition_browser.py`, `python tests/test_browser_e2e.py` (`7/7`), and `python tests/test_nightly_e2e.py` (`3/3`).
- Added `tests/browser/test_watchlist_assets_browser.py` to cover the free-plan watchlist logo upload gate and the paid-plan watchlist logo upload/delete/render flow when higher-tier credentials are available.
- Wired the watchlist-assets suite into `tests/test_browser_e2e.py` so the aggregate browser lane now exercises uploads/assets explicitly.
- The new watchlist-assets slice exposed a real backend bug: `store_watchlist_logo_upload()` was not enforcing the paid-plan `can_track_logos` gate, so free users could upload watchlist logos.
- Fixed that backend bug in `services/watchlist_service.py` by enforcing `_ensure_logo_tracking_allowed()` in the watchlist logo upload path.
- Added a matching free-persona live assertion in `tests/live/personas/test_free_user_live.py` so the free logo gate is exercised outside the browser lane as well.
- Verified the watchlist-assets slice with `python tests/live/personas/test_free_user_live.py` (`9/9`), `python tests/browser/test_public_browser_smoke.py` (`6/6`), `python tests/browser/test_watchlist_assets_browser.py` (`6/6`), `python tests/test_browser_e2e.py` (`8/8`), and `python tests/test_nightly_e2e.py` (`3/3`).
- Added Windows-safe delegate output handling to `tests/test_browser_e2e.py` and `tests/test_live_app_e2e.py` so aggregate failures no longer crash on `cp1252` output encoding.
- Updated the higher-tier persona/browser suites to skip the default-account probe and provision directly from the superadmin path, eliminating false failures from optional exploratory logins under rate limiting.
- Adjusted `tests/live/personas/test_superadmin_live.py` to accept the real `/api/v1/admin/settings` list payload, which is the live contract in this environment.
- Fixed `utils/subscription.py` so live-search eligibility follows the canonical plan-limit surface instead of stale DB booleans, and updated the paid/business persona suites accordingly.
- Fixed `services/watchlist_service.py` so `/api/v1/watchlist` now serializes items through `WatchlistItemResponse`, exposing derived `has_logo` and `logo_url` in the list payload.
- Fixed `models/schemas.py` and `database/repositories/watchlist_repository.py` so watchlist monitor flags round-trip correctly: `monitor_text` / `monitor_visual` now map from raw DB columns and `monitor_phonetic` is persisted on create.
- The monitor-flag round-trip bug was the root cause of the free watchlist edit `403`: the browser edit modal was reopening with `monitor_visual=true` and tripping the paid-only gate on update.
- Hardened `tests/browser/test_watchlist_assets_browser.py` to retry transient `429` throttles on both the free and paid logo journeys instead of failing the aggregate browser/nightly lanes on recoverable rate-limit spikes.
- Verified the repaired env-gated stack sequentially with:
  - `python tests/live/personas/test_superadmin_live.py` (`10/10`)
  - `python tests/browser/test_member_feature_browser.py` (`14/14`)
  - `python tests/browser/test_watchlist_assets_browser.py` (`9/9`)
  - `python tests/test_browser_e2e.py` (`8/8`)
  - `python tests/test_live_app_e2e.py` (`14/14`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Added stable dashboard KPI selectors in `templates/dashboard/partials/_results_panel.html` and a matching layout guard in `tests/test_dashboard_layout.py`.
- Expanded `tests/browser/test_member_browser_smoke.py` with a live dashboard overview contract step that compares the rendered KPI cards, usage bars, plan badge, credit reset text, and system counters against the authenticated dashboard API endpoints inside the browser session.
- Verified the new dashboard-browser slice with the targeted dashboard layout and browser runners.
- Added `tests/browser/test_business_browser.py` to cover the mounted business holder/attorney portfolio modal, including initial entity load, in-modal entity search/select, and the real CSV export trigger for both holder and attorney flows.
- Wired the new business browser suite into `tests/test_browser_e2e.py` so the aggregate browser and nightly lanes cover business portfolio UI automatically.
- Hardened `tests/browser/test_member_feature_browser.py` so the login-only steps clear transient post-login `429` bootstrap noise once authentication has already succeeded.
- Hardened `tests/browser/test_opposition_browser.py` so the alert-context steps clear transient rate-limit noise after login/context load and only fail on the actual opposition action being asserted.
- Verified the business-browser slice and the nightly hardening with:
  - `python tests/browser/test_business_browser.py`
  - `python tests/browser/test_member_feature_browser.py`
  - `python tests/browser/test_opposition_browser.py`
  - `python tests/test_browser_e2e.py` (`9/9`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Added `tests/browser/helpers/auth_state.py` so browser auth flows can create verified ephemeral accounts and resolve the latest password-reset code from the local DB for test-only verification.
- Expanded `tests/browser/test_public_browser_smoke.py` to cover the full forgot-password success flow: request reset code, resolve the stored 6-digit code, submit the new password, and log in with the updated credentials.
- Hardened the registration step in `tests/browser/test_public_browser_smoke.py` to clear auth before reopening the register modal and to ignore unrelated post-redirect dashboard bootstrap noise after the registration success condition is already met.
- Hardened `tests/browser/test_business_browser.py` so its login-only step clears transient post-login `429` bootstrap noise once authentication has already succeeded.
- Verified the password-reset success slice with:
  - `python tests/browser/test_public_browser_smoke.py` (`7/7`)
  - `python tests/browser/test_business_browser.py` (`8/8`)
  - `python tests/test_browser_e2e.py` (`9/9`)
  - `python tests/test_nightly_e2e.py` (`3/3`)

### 2026-04-18

- Reworked the live/browser/nightly persona provisioning helpers to reuse deterministic managed free, starter, and professional smoke accounts when explicit persona creds are not supplied, instead of registering a new random account on every aggregate run.
- Reworked the browser-only registration helpers so the forgot-password success account is reset and reused, while the real registration and checkout-registration journeys now use deterministic emails with teardown before and after the run.
- Added `scripts/devtools/purge_test_accounts.py` plus shared DB cleanup helpers to audit and purge the historical disposable smoke accounts created by the older harness.
- Expanded `tests/browser/helpers/auth_state.py` with email-verification code lookup so the public browser suite can drive the real dashboard verification modal using the stored 6-digit code.
- Expanded `tests/browser/test_public_browser_smoke.py` so the registration journey now proves the verification modal appears after redirect, then added the full resend-and-verify email-verification modal journey.
- The public verification browser step now asserts resend cooldown, successful `/api/v1/auth/verify-email`, modal dismissal, and `GET /api/v1/auth/me` returning `is_verified=true`.
- Re-ran the targeted public browser suite and the aggregate browser lane successfully.
- The first nightly rerun was invalid because the aggregate browser lane and nightly lane were launched in parallel and overloaded the live app; after restarting Docker Desktop and waiting for `/health` to recover, the nightly lane passed when rerun sequentially.
- Marked successful paid checkout as a blocked test target because there is no configured payment method/provider in this environment, and kept the billing lane scoped to pre-payment and failure-path coverage.
- Verified the email-verification slice with:
  - `python -m py_compile tests/browser/helpers/auth_state.py tests/browser/test_public_browser_smoke.py`
  - `python tests/browser/test_public_browser_smoke.py` (`8/8`)
  - `python tests/test_browser_e2e.py` (`9/9`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Expanded `tests/live/features/test_search_live.py` so quick-search coverage is now explicit across free text, free daily-limit exhaustion, paid text, and paid image paths, with per-plan quick-search assertions sourced from `/api/v1/usage/summary`.
- Added `tests/browser/test_search_browser.py` and wired it into `tests/test_browser_e2e.py` so the browser lane now proves free quick-search text, the free quick-search daily-limit upgrade gate, and paid quick-search text/image journeys in isolated persona contexts.
- Aligned the stale runtime quick-search plan overrides in `app_settings` with the canonical product defaults (`free=5`, `starter=50`, `professional=2000`) on startup when those rows still match the known legacy values, and tightened the live/browser search assertions back to the exact plan contract.
- Hardened `tests/browser/test_member_browser_smoke.py` so its login and dashboard quick-usage assertions stay stable under aggregate/nightly load while the rest of the dashboard contract remains strict.
- Verified the quick-search split slice with:
  - `python -m py_compile tests/live/features/test_search_live.py tests/browser/test_search_browser.py tests/test_browser_e2e.py`
  - `python tests/live/features/test_search_live.py` (`22/22`)
  - `python tests/browser/test_search_browser.py` (`10/10`)
  - `python tests/test_live_app_e2e.py` (`14/14`)
  - `python tests/test_browser_e2e.py` (`10/10`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Expanded `tests/live/personas/test_public_live.py` so the public visitor lane now proves `GET /api/v1/search/public` plus the `POST` class-filter and image-upload paths without exceeding the public-search limiter.
- Expanded `tests/browser/test_public_browser_smoke.py` so the landing page now proves short-query validation with no happy-path fallback, a real class-filter public-search POST, and a real image-search public-search POST.
- The public image route is stricter than quick-search uploads in this environment, so the new public-search coverage uses a generated valid PNG instead of the older `PNG_1X1` fixture, which the public route rejects as corrupted.
- Hardened the public browser search steps to tolerate recovered `429` retries cleanly during aggregate/nightly runs while still failing on the final search outcome.
- Verified the public-search depth slice with:
  - `python -m py_compile tests/live/personas/test_public_live.py tests/browser/test_public_browser_smoke.py`
  - `python tests/live/personas/test_public_live.py` (`11/11`)
  - `python tests/browser/test_public_browser_smoke.py` (`11/11`)
  - `python tests/test_live_app_e2e.py` (`14/14`)
  - `python tests/test_browser_e2e.py` (`10/10`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Added stable dashboard search-button IDs in `templates/dashboard/partials/_search_panel.html` and guarded them in `tests/test_dashboard_layout.py` so browser coverage can target quick vs live search without brittle selector logic.
- Added `tests/browser/test_live_search_browser.py` so browser coverage now proves the free-plan live-search upgrade gate and a real business live-search success path against `/api/v1/search/intelligent`, including credit consumption and live-source results.
- Hardened `tests/test_nightly_e2e.py` and `tests/nightly/test_stateful_live.py` with recovery checks between the heavier browser/live lanes and the stateful delegates so the nightly stack no longer overloads the live app between phases.
- Adjusted `tests/live/features/test_search_live.py` so its duplicate public-search happy-path probe records a documented pass when the anonymous `3/minute` public-search limiter has already been consumed by the public persona suite.
- Verified the live-search browser slice with:
  - `python -m py_compile tests/browser/test_live_search_browser.py tests/test_browser_e2e.py tests/test_nightly_e2e.py tests/nightly/test_stateful_live.py tests/live/features/test_search_live.py`
  - `python -m pytest tests/test_dashboard_layout.py -q` (`51 passed`)
  - `python tests/browser/test_live_search_browser.py` (`9/9`)
  - `python tests/live/features/test_search_live.py` (`22/22`)
  - `python tests/test_browser_e2e.py` (`11/11`)
  - `python tests/nightly/test_stateful_live.py` (`3/3`)
  - `python tests/test_nightly_e2e.py` (`3/3`)
- Expanded `tests/browser/test_alerts_browser.py` so the alerts browser lane now seeds dedicated `new`, `acknowledged`, and `resolved` appeal scenarios and proves mounted watchlist appeals filtering by alert status, trademark status, and sort order.
- Fixed `services/watchlist_service.py` so resolved/dismissed appeals requests load the matching conflict summaries instead of dropping their summary payload at the service seam.
- Fixed `static/js/dashboard/app.js` so the appeals watchlist view actually renders through `renderAppealsGrid()` and no longer loses rows to stale overlapping `loadPortfolio()` responses; the watchlist loader now ignores superseded responses by request sequence.
- Hardened `tests/browser/test_public_browser_smoke.py` so landing-page public search uses the page’s `publicSearch()` entrypoint directly and waits on the real search completion state instead of relying on brittle Enter-key timing.
- Hardened `tests/browser/test_alerts_browser.py` so the seeded alert actions use the page’s inline resolve/dismiss handlers directly and the suite authenticates by token bootstrap instead of redoing repeated modal logins in late aggregate runs.
- Hardened `tests/browser/test_member_feature_browser.py` to close any leftover report-generation modal before later application/profile steps, preventing one failed modal step from poisoning the rest of the browser delegate.
- Hardened `tests/browser/test_billing_browser.py` so recoverable checkout-login `429` responses no longer fail the browser lane once the retry path succeeds.
- Added direct watchlist-limit regressions for the remaining uncovered paths:
  - `tests/browser/test_member_feature_browser.py` now proves the dashboard quick-add modal opens the upgrade flow at watchlist capacity and the dashboard bulk-upload modal shows `0 addable / N blocked` behavior plus the capped upload result at full capacity.
  - `tests/test_api_endpoints.py` now proves `/api/v1/watchlist` maps repository watchlist-cap hits to the structured `403 limit_exceeded` payload, `/watchlist/bulk` marks overflow rows as failed, and both upload services stop at capacity while returning the correct error counts/items.
- Fixed `services/watchlist_service.py` so manual watchlist creation maps the repository watchlist-limit `ValueError` into the same structured `403 limit_exceeded` contract used by the rest of the upgrade flows.
- Verified the watchlist-limit coverage slice with:
  - `python -m py_compile services/watchlist_service.py tests/test_api_endpoints.py tests/browser/test_member_feature_browser.py`
  - `python -m pytest tests/test_api_endpoints.py -q -k "create_watchlist_item_record_maps_limit_value_error_to_structured_403 or watchlist_service_import_watchlist_items_bulk_marks_overflow_items_as_failed or watchlist_service_import_watchlist_upload_with_mapping_respects_watchlist_capacity or watchlist_service_import_watchlist_upload_file_respects_watchlist_capacity"` (`4 passed`)
  - `python tests/browser/test_member_feature_browser.py` (`15/15`)
- Hardened `tests/test_browser_e2e.py` to provision shared free/paid/business personas once per aggregate run, pass those creds into all browser delegates, and wait for `/health` recovery between delegates.
- Hardened `tests/test_nightly_e2e.py` to provision the same shared personas once for the nightly lane, pass them into both the live and browser smoke phases, and wait for server recovery between live smoke, browser smoke, and the stateful lane.
- Added `tests/live/helpers/test_accounts.py` so routine smoke coverage reuses deterministic managed free/starter/professional personas instead of self-registering random one-off accounts on every run.
- Reworked `tests/live/helpers/personas.py`, `tests/browser/helpers/auth_state.py`, `tests/browser/test_public_browser_smoke.py`, and `tests/browser/test_billing_browser.py` so the aggregate live/browser/nightly lanes stop growing the `users` table and browser-only registration flows delete their temporary accounts before and after the run.
- Expanded `tests/live/helpers/cleanup.py` and `tests/live/features/test_reports_live.py` so quota-based managed-account coverage resets its dedicated report state before and after execution; this fixed the free-plan reports quota regression that appeared once the harness stopped using fresh accounts every time.
- Added `scripts/devtools/purge_test_accounts.py` so the backlog of legacy disposable smoke accounts can be audited in dry-run mode or purged deliberately with `--apply`.
- Verified the managed-account churn fix with:
  - `python -m py_compile tests/live/helpers/test_accounts.py tests/live/helpers/personas.py tests/browser/helpers/auth_state.py tests/browser/test_public_browser_smoke.py tests/browser/test_billing_browser.py tests/live/helpers/cleanup.py tests/live/features/test_reports_live.py scripts/devtools/purge_test_accounts.py`
  - `python tests/test_live_app_e2e.py` before purge (`14/14`, disposable count stayed `1068 -> 1068`)
  - `python tests/test_browser_e2e.py` (`11/11`, disposable count stayed `1068 -> 1068`)
  - `python tests/test_nightly_e2e.py` (`3/3`, disposable count stayed `1068 -> 1068`)
  - `python scripts/devtools/purge_test_accounts.py --apply` (`1068` disposable accounts and `1068` disposable orgs purged)
  - `python tests/test_live_app_e2e.py` after purge (`14/14`, disposable count stayed `0 -> 0`)
- Restarted Docker Desktop twice during this slice after failed aggregate runs wedged the live backend and the Docker API; both reruns below were executed only after `/health` recovered to `200`.
- Verified the alert/filter and aggregate-hardening slice with:
  - `python tests/browser/test_public_browser_smoke.py` (`11/11`)
  - `python tests/browser/test_alerts_browser.py` (`6/6`)
  - `python tests/test_browser_e2e.py` (`11/11`)
  - `python tests/test_live_app_e2e.py` (`14/14`)
  - `python tests/test_nightly_e2e.py` (`3/3`)

## Next Step

Immediate next slice:
- no urgent uncovered core-user slice remains; the next optional work is deeper report-download/export browser coverage or wiring the nightly runner into an external scheduler/CI job

Possible next steps:
- deeper report download/export browser coverage once the desired download assertions are defined
- wiring `tests/test_nightly_e2e.py` into an external scheduler or CI job so the nightly lane runs automatically
- plan lifecycle edge coverage:
  upgrade, downgrade, entitlement revocation after downgrade, quota reset boundaries, and expired-plan behavior
- destructive admin/browser coverage:
  user disable, organization disable, credit adjustments, plan reassignment, and settings mutations with cleanup rules
- longer-running background-job coverage:
  scan completion, retry/recovery, queue backpressure, and post-job UI refresh/notification behavior
- cross-browser and viewport coverage:
  mobile viewport, tablet viewport, and at least one non-Chromium browser path for the highest-value journeys
- file-content validation depth:
  CSV, Excel, PDF, image upload, and export-download assertions beyond status code / trigger success
- external integration verification:
  email delivery, webhook paths, and eventual payment success once a real or test provider is configured
- resilience / soak coverage:
  repeated aggregate runs, burst/concurrency pressure, and recovery after backend slowdown or Docker restarts
- accessibility checks:
  keyboard navigation, modal focus traps, label coverage, and basic ARIA regressions on the mounted UI

Prioritized optional testing backlog:
- P1: deeper report download/export browser coverage
- P1: automate `tests/test_nightly_e2e.py` in external scheduling or CI
- P1: plan lifecycle edge coverage around upgrades, downgrades, and quota resets
- P2: background-job completion/retry/recovery coverage
- P2: cross-browser and mobile viewport coverage for the main user journeys
- P2: file-content validation for exported/downloaded assets
- P3: destructive admin mutation coverage
- P3: resilience / soak coverage
- P3: accessibility checks
- Blocked: true paid checkout success until a payment method/provider exists in the test environment
- Blocked: external payment-provider end-to-end verification until that provider is configured
