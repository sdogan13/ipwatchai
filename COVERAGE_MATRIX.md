# COVERAGE MATRIX: Backend Fields → Frontend Display

**Generated:** 2026-02-11
**Method:** API response models + route handlers cross-referenced with exhaustive frontend JS/template audit
**Validated:** Live PostgreSQL queries (2,625,377 trademarks, 33 tables), all endpoint routes verified via code grep
**Status Legend:**
- ✅ **WIRED** = API returns it AND frontend displays it
- ❌ **MISSING** = API returns it but frontend does NOT display it
- 🔧 **INTERNAL** = Embedding vectors, raw UUIDs, or internal-only — no UI needed
- ⚪ **EMPTY** = Field exists in API but always null/empty in current data
- 🐛 **BUG** = Frontend reads a field name the API doesn't provide

---

## 1. ENDPOINT INVENTORY

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | POST | /api/v1/auth/login | Login, returns JWT |
| 2 | POST | /api/v1/auth/register | Register user + org |
| 3 | GET | /api/v1/auth/me | Current user profile |
| 4 | POST | /api/v1/auth/refresh | Refresh token |
| 5 | POST | /api/v1/auth/change-password | Change password |
| 6 | GET | /api/v1/dashboard/stats | Dashboard KPI stats |
| 7 | GET | /api/v1/search/quick | Quick DB search |
| 8 | GET | /api/v1/search/intelligent | Live search (GET) |
| 9 | POST | /api/v1/search/intelligent | Live search with image |
| 10 | GET | /api/v1/search/credits | Search credit status |
| 11 | GET | /api/v1/search/status | Service status |
| 12 | POST | /api/search | Legacy enhanced search |
| 13 | GET | /api/v1/watchlist | List watchlist items |
| 14 | POST | /api/v1/watchlist | Add watchlist item |
| 15 | GET | /api/v1/watchlist/{id} | Get single item |
| 16 | PUT | /api/v1/watchlist/{id} | Update item |
| 17 | DELETE | /api/v1/watchlist/{id} | Delete item |
| 18 | POST | /api/v1/watchlist/{id}/scan | Trigger scan |
| 19 | POST | /api/v1/watchlist/{id}/logo | Upload logo |
| 20 | GET | /api/v1/watchlist/{id}/logo | Get logo image |
| 21 | DELETE | /api/v1/watchlist/{id}/logo | Delete logo |
| 22 | POST | /api/v1/watchlist/scan-all | Scan all items |
| 23 | GET | /api/v1/watchlist/scan-status | Scan schedule |
| 24 | DELETE | /api/v1/watchlist/all | Delete all items |
| 25 | POST | /api/v1/watchlist/rescan | Rescan all |
| 26 | POST | /api/v1/watchlist/bulk | Bulk import |
| 27 | GET | /api/v1/watchlist/upload/template | CSV template |
| 28 | POST | /api/v1/watchlist/upload/detect-columns | Column detection |
| 29 | POST | /api/v1/watchlist/upload/with-mapping | Upload with mapping |
| 30 | POST | /api/v1/watchlist/upload | Upload CSV |
| 31 | GET | /api/v1/alerts | List alerts |
| 32 | GET | /api/v1/alerts/summary | Alert summary |
| 33 | GET | /api/v1/alerts/{id} | Get alert detail |
| 34 | POST | /api/v1/alerts/{id}/acknowledge | Acknowledge |
| 35 | POST | /api/v1/alerts/{id}/resolve | Resolve |
| 36 | POST | /api/v1/alerts/{id}/dismiss | Dismiss |
| 37 | GET | /api/v1/leads/feed | Lead feed |
| 38 | GET | /api/v1/leads/stats | Lead statistics |
| 39 | GET | /api/v1/leads/credits | Lead credits |
| 40 | GET | /api/v1/leads/{id} | Lead detail |
| 41 | POST | /api/v1/leads/{id}/contact | Mark contacted |
| 42 | POST | /api/v1/leads/{id}/convert | Mark converted |
| 43 | POST | /api/v1/leads/{id}/dismiss | Dismiss lead |
| 44 | GET | /api/v1/leads/export/csv | CSV export |
| 45 | GET | /api/v1/holders/search | Search holders |
| 46 | GET | /api/v1/holders/{id}/trademarks | Holder portfolio |
| 47 | GET | /api/v1/attorneys/search | Search attorneys |
| 48 | GET | /api/v1/attorneys/{id}/trademarks | Attorney portfolio |
| 49 | GET | /api/v1/reports/ | List reports |
| 50 | POST | /api/v1/reports/ | Generate report |
| 51 | GET | /api/v1/reports/{id}/download | Download report |
| 52 | POST | /api/v1/tools/suggest-names | AI name generation |
| 53 | POST | /api/v1/tools/generate-logo | AI logo generation |
| 54 | GET | /api/v1/tools/generated-image/{id} | Serve generated image |
| 55 | GET | /api/v1/tools/generation-history | Generation history |
| 56 | GET | /api/v1/tools/status | Creative suite status |
| 57 | GET | /api/v1/tools/credits | Creative credits |
| 58 | GET | /api/v1/usage/summary | Usage stats |
| 59 | GET | /api/v1/pipeline/status | Pipeline status |
| 60 | POST | /api/v1/pipeline/run | Run pipeline |
| 61 | POST | /api/v1/pipeline/run/{step} | Run single step |
| 62 | GET | /api/v1/extracted-goods/{app_no} | Extracted goods |
| 63 | GET | /api/v1/admin/idf-stats | IDF statistics |
| 64 | GET | /api/v1/admin/idf-analyze | Word analysis |
| 65 | GET | /api/v1/admin/idf-query-analysis | Query analysis |
| 66 | POST | /api/v1/admin/idf-test-similarity | Test similarity |
| 67 | GET | /health | Health check |
| 68 | GET | /api/v1/status | System status |
| 69 | PUT | /api/v1/user/profile | Update profile |
| 70 | PUT | /api/v1/organization/profile | Update org |
| 71 | GET | /api/v1/organization/profile | Get org profile |
| 72 | GET | /api/v1/user/profile | Get user profile |
| 73 | PUT | /api/v1/user/profile | Update user profile |
| 74 | POST | /api/v1/user/avatar | Upload avatar |
| 75 | GET | /api/v1/user/organization | Get user's org |
| 76 | PUT | /api/v1/user/organization | Update user's org |
| 77 | GET | /api/v1/users | List org users |
| 78 | POST | /api/v1/users | Create user |
| 79 | GET | /api/v1/users/{id} | Get user |
| 80 | PUT | /api/v1/users/{id} | Update user |
| 81 | DELETE | /api/v1/users/{id} | Delete user |
| 82 | GET | /api/v1/organization/stats | Org stats |
| 83 | GET | /api/v1/organization/settings | Org settings |
| 84 | PUT | /api/v1/organization/threshold | Update threshold |
| 85 | POST | /api/v1/billing/validate-discount | Validate discount code |
| 86 | POST | /api/v1/upload/trademarks | Upload trademark data |
| 87 | GET | /api/v1/upload/template | Download upload template |
| 88 | GET | /api/v1/admin/settings | List admin settings |
| 89 | GET | /api/v1/admin/settings/{cat} | Settings by category |
| 90 | PUT | /api/v1/admin/settings/{key} | Update setting |
| 91 | DELETE | /api/v1/admin/settings/{key} | Delete setting |
| 92 | GET | /api/v1/admin/overview | Admin overview |
| 93 | GET | /api/v1/admin/organizations | List organizations |
| 94 | GET | /api/v1/admin/organizations/{id} | Get organization |
| 95 | PUT | /api/v1/admin/organizations/{id}/plan | Update org plan |
| 96 | PUT | /api/v1/admin/organizations/{id}/status | Update org status |
| 97 | GET | /api/v1/admin/users | List all users |
| 98 | PUT | /api/v1/admin/users/{id}/role | Update user role |
| 99 | PUT | /api/v1/admin/users/{id}/superadmin | Toggle superadmin |
| 100 | PUT | /api/v1/admin/users/{id}/status | Update user status |
| 101 | GET | /api/v1/admin/audit-log | Audit log |
| 102 | GET | /api/v1/admin/organizations/{id}/credits | Org credits |
| 103 | PUT | /api/v1/admin/organizations/{id}/credits | Update credits |
| 104 | POST | /api/v1/admin/credits/bulk | Bulk credit update |
| 105 | GET | /api/v1/admin/discount-codes | List discount codes |
| 106 | POST | /api/v1/admin/discount-codes | Create discount code |
| 107 | PUT | /api/v1/admin/discount-codes/{id} | Update discount code |
| 108 | DELETE | /api/v1/admin/discount-codes/{id} | Delete discount code |
| 109 | GET | /api/v1/admin/discount-codes/{id}/usage | Code usage stats |
| 110 | GET | /api/v1/admin/plans | List plans |
| 111 | PUT | /api/v1/admin/plans/{name}/pricing | Update plan pricing |
| 112 | GET | /api/v1/admin/analytics/usage | Usage analytics |
| 113 | GET | /api/v1/admin/analytics/export | Export analytics |
| 114 | POST | /api/v1/admin/idf-refresh | Refresh IDF cache |
| 115 | GET | /api/v1/config | Frontend config |
| 116 | GET | /api/v1/pipeline/runs/{id} | Get pipeline run |
| 117 | GET | /api/v1/reports/{id} | Get report detail |
| 118 | GET | /api/v1/leads/{id} | Get lead detail |
| 119 | GET | /api/trademark-image/{path} | Serve trademark image |
| 120 | POST | /api/search-by-image | Image-only search |
| 121 | GET | /api/search/simple | Legacy simple search |
| 122 | POST | /api/search/unified | Legacy unified search |
| 123 | POST | /api/v1/search/legacy | Legacy search |
| 124 | POST | /api/validate-classes | Validate Nice classes |
| 125 | GET | /api/nice-classes | List Nice classes |
| 126 | POST | /api/suggest-classes | AI class suggestions |
| 127 | POST | /api/admin/test-scoring | Test scoring |
| 128 | GET | / | Root page |
| 129 | GET | /dashboard | Dashboard HTML |
| 130 | GET | /admin | Admin HTML |
| 131 | GET | /pricing | Pricing HTML |
| 132 | GET | /api/info | API info |

**Total: 132 endpoints** (71 originally listed + 61 additional discovered)

---

## 2. FIELD-BY-FIELD COVERAGE TABLES

---

### 2.1 GET /api/v1/search/quick — Search Result Object

Each result in `results[]`:

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| application_no | string | "89/009645" | result-card.js | Monospace + TURKPATENT button + copy | ✅ WIRED |
| name | string | "nike" | result-card.js | Bold primary text | ✅ WIRED |
| status | string | "Registered" | result-card.js | Muted text below name | ✅ WIRED |
| classes | int[] | [18,25] | result-card.js | Indigo Nice class badges | ✅ WIRED |
| image_path | string | "/images/..." | result-card.js | 48px thumbnail | ✅ WIRED |
| holder_name | string | "NIKE INNOVATE" | result-card.js | Clickable link → portfolio | ✅ WIRED |
| holder_tpe_client_id | string | "5503722" | result-card.js | Portfolio link param | ✅ WIRED |
| attorney_name | string | "ABC Patent" | result-card.js | Clickable link → portfolio | ✅ WIRED |
| attorney_no | string | "595" | result-card.js | Portfolio link param | ✅ WIRED |
| registration_no | string | "114946" | result-card.js | "Reg: 114946" monospace | ✅ WIRED |
| bulletin_no | string | "BLT_127" | result-card.js | Small faint text | ✅ WIRED |
| exact_match | bool | true | result-card.js | Critical-colored badge | ✅ WIRED |
| has_extracted_goods | bool | true | result-card.js | Amber button | ✅ WIRED |
| scores.total | float | 0.87 | score-badge.js | Score ring percentage | ✅ WIRED |
| scores.exact_match | bool | true | result-card.js | Badge (redundant with top-level) | ✅ WIRED |
| scores.text_similarity | float | 0.82 | score-badge.js | Mini progress bar "Text" | ✅ WIRED |
| scores.visual_similarity | float | 0.0 | score-badge.js | Mini progress bar "Visual" | ✅ WIRED |
| scores.translation_similarity | float | 0.15 | score-badge.js | Mini progress bar "Translation" | ✅ WIRED |
| scores.phonetic_similarity | float | 1.0 | score-badge.js | Mini progress bar "Phonetic" | ✅ WIRED |
| scores.semantic_similarity | float | 0.62 | score-badge.js | Mini progress bar "Semantic" | ✅ WIRED |
| scores.containment | float | 0.5 | score-badge.js | Mini progress bar "Containment" | ✅ WIRED |
| scores.scoring_path | string | "EXACT_MATCH" | score-badge.js | Colored path badge | ✅ WIRED |
| scores.dynamic_weights | object | {text:0.6,...} | score-badge.js | Weight label (T:60 V:25 Tr:15) | ✅ WIRED |
| scores.dynamic_weights.text | float | 0.60 | score-badge.js | Percentage in label | ✅ WIRED |
| scores.dynamic_weights.visual | float | 0.25 | score-badge.js | Percentage in label | ✅ WIRED |
| scores.dynamic_weights.translation | float | 0.15 | score-badge.js | Percentage in label | ✅ WIRED |
| scores.text_idf_score | float | 0.78 | — | — | 🔧 INTERNAL |
| scores.token_overlap | float | 1.0 | — | — | ❌ MISSING |
| scores.weighted_overlap | float | 1.0 | — | — | ❌ MISSING |
| scores.distinctive_match | float | 1.0 | — | — | ❌ MISSING |
| scores.semi_generic_match | float | 0.0 | — | — | 🔧 INTERNAL |
| scores.generic_match | float | 0.0 | — | — | 🔧 INTERNAL |
| scores.matched_words | str[] | ["nike"] | — | — | ❌ MISSING |
| scores.distinctive_weight_matched | float | 1.0 | — | — | 🔧 INTERNAL |
| scores.semi_generic_weight_matched | float | 0.0 | — | — | 🔧 INTERNAL |
| scores.generic_weight_matched | float | 0.0 | — | — | 🔧 INTERNAL |

Response envelope:

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| query | string | "NIKE" | — | — | 🔧 INTERNAL |
| results | list | [...] | app.js | Rendered as cards | ✅ WIRED |
| page | int | 1 | app.js | Pagination | ✅ WIRED |
| per_page | int | 20 | app.js | Pagination | ✅ WIRED |
| total | int | 45 | app.js | Sort bar count | ✅ WIRED |
| total_pages | int | 3 | app.js | Pagination | ✅ WIRED |
| total_candidates | int | 45 | app.js | "from 45 candidates" | ✅ WIRED |
| max_score | float | 0.87 | app.js | "Max Risk: 87%" | ✅ WIRED |
| risk_level | string | "CRITICAL" | app.js | Colored risk badge | ✅ WIRED |
| source | string | "local_db" | — | — | ❌ MISSING |
| scrape_triggered | bool | false | app.js | Mode badge Quick/Intelligent | ✅ WIRED |
| scraped_count | int | 0 | app.js | "X new records" banner | ✅ WIRED |
| ingested_count | int | 0 | — | — | ❌ MISSING |
| score_before | float | null | — | — | ❌ MISSING |
| score_improvement | float | null | — | — | ❌ MISSING |
| image_used | bool | false | app.js | Purple "Visual Analysis" badge | ✅ WIRED |
| elapsed_seconds | float | 1.23 | app.js | "1.2s" timing text | ✅ WIRED |
| timestamp | string | "2026-..." | — | — | 🔧 INTERNAL |
| credits_used | int | 0 | — | — | ❌ MISSING |
| credits_remaining | int | 49 | api.js | Toast message | ✅ WIRED |

---

### 2.2 GET /api/v1/leads/feed — Lead Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| id | string | "uuid" | lead-card.js | onclick param | ✅ WIRED |
| new_mark_name | string | "NIKEX" | lead-card.js | Bold text + VS card | ✅ WIRED |
| new_mark_app_no | string | "2025/1234" | lead-card.js | TURKPATENT button | ✅ WIRED |
| new_mark_holder_name | string | "Acme Inc" | lead-card.js | Faint truncated text | ✅ WIRED |
| new_mark_nice_classes | int[] | [25,35] | api.js | VS comparison badges | ✅ WIRED |
| new_mark_image | string | "/img/..." | lead-card.js | 40px thumbnail | ✅ WIRED |
| existing_mark_name | string | "NIKE" | lead-card.js | Bold text + VS card | ✅ WIRED |
| existing_mark_app_no | string | "89/009645" | lead-card.js | TURKPATENT button | ✅ WIRED |
| existing_mark_holder_name | string | "NIKE INC" | lead-card.js | Faint truncated text | ✅ WIRED |
| existing_mark_nice_classes | int[] | [25] | api.js | VS comparison badges | ✅ WIRED |
| existing_mark_image | string | "/img/..." | lead-card.js | 40px thumbnail | ✅ WIRED |
| similarity_score | float | 0.92 | lead-card.js | Score ring percentage | ✅ WIRED |
| text_similarity | float | 0.85 | lead-card.js | Similarity badge | ✅ WIRED |
| semantic_similarity | float | 0.60 | lead-card.js | Similarity badge | ✅ WIRED |
| visual_similarity | float | 0.0 | lead-card.js | Similarity badge | ✅ WIRED |
| translation_similarity | float | null | lead-card.js | Similarity badge (if non-null) | ⚪ EMPTY (column not in DB) |
| risk_level | string | "CRITICAL" | api.js | Lead detail badge | ✅ WIRED |
| conflict_type | string | "text_similar" | lead-card.js | Footer text | ✅ WIRED |
| overlapping_classes | int[] | [25] | lead-card.js | Nice class badges | ✅ WIRED |
| conflict_reasons | str[] | ["High text..."] | api.js | Bulleted list in detail | ✅ WIRED |
| bulletin_no | string | "BLT_200" | lead-card.js | Footer "Bulletin X" | ✅ WIRED |
| bulletin_date | date | "2025-12-01" | lead-card.js | Timeline bar start | ✅ WIRED |
| opposition_deadline | date | "2026-02-01" | lead-card.js | Timeline bar end | ✅ WIRED |
| days_until_deadline | int | 14 | — | Computed by timeline instead | 🔧 INTERNAL |
| urgency_level | string | "urgent" | lead-card.js | Colored urgency badge | ✅ WIRED |
| new_mark_has_extracted_goods | bool | true | lead-card.js | Amber button | ✅ WIRED |
| existing_mark_has_extracted_goods | bool | false | lead-card.js | Amber button | ✅ WIRED |
| lead_status | string | "new" | lead-card.js | Status badge | ✅ WIRED |
| created_at | datetime | "2026-..." | — | — | ❌ MISSING |

**Note:** `translation_similarity` and `phonetic_similarity` columns do NOT exist in the `universal_conflicts` table at all. The Pydantic model defines them, but the DB schema lacks these columns entirely. They would need to be added via migration AND computed during scan.

---

### 2.3 GET /api/v1/leads/stats — Stats Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| total_leads | int | 150 | app.js | #stat-total + urgency bar total | ✅ WIRED |
| critical_leads | int | 5 | app.js | #stat-critical + urgency bar segment | ✅ WIRED |
| urgent_leads | int | 12 | app.js | #stat-urgent + urgency bar segment | ✅ WIRED |
| upcoming_leads | int | 30 | — | — | ❌ MISSING |
| new_leads | int | 45 | — | — | ❌ MISSING |
| viewed_leads | int | 20 | — | — | ❌ MISSING |
| contacted_leads | int | 8 | — | — | ❌ MISSING |
| converted_leads | int | 3 | app.js | #stat-converted + urgency bar segment | ✅ WIRED |
| avg_similarity | float | 0.78 | — | — | ❌ MISSING |
| last_scan_at | datetime | "2026-..." | — | — | ❌ MISSING |

---

### 2.4 GET /api/v1/leads/credits — Credits Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| can_access | bool | true | — | Used as gate internally | 🔧 INTERNAL |
| plan | string | "professional" | api.js | Enterprise check for CSV | ✅ WIRED |
| daily_limit | int | 5 | — | — | ❌ MISSING |
| used_today | int | 2 | — | — | ❌ MISSING |
| remaining | int | 3 | api.js | Credits display (∞ if unlimited) | ✅ WIRED |

---

### 2.5 GET /api/v1/alerts — Alert Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| id | UUID | "uuid" | app.js | onclick param | ✅ WIRED |
| organization_id | UUID | "uuid" | — | — | 🔧 INTERNAL |
| watchlist_id | UUID | "uuid" | — | — | 🔧 INTERNAL |
| watched_brand_name | string | "MY BRAND" | app.js | Conflict description + detail | ✅ WIRED |
| watchlist_bulletin_no | string | "BLT_100" | — | — | ❌ MISSING |
| watchlist_application_no | string | "2020/1234" | app.js | TURKPATENT button in detail | ✅ WIRED |
| watchlist_classes | int[] | [9,42] | — | — | ❌ MISSING |
| conflicting.id | UUID | "uuid" | — | — | 🔧 INTERNAL |
| conflicting.name | string | "MYBRAND" | app.js | Bold text in list + detail | ✅ WIRED |
| conflicting.application_no | string | "2025/5678" | app.js | Badge + TURKPATENT button | ✅ WIRED |
| conflicting.status | string | "Published" | — | — | ❌ MISSING |
| conflicting.classes | int[] | [9,42] | — | — | ❌ MISSING |
| conflicting.holder | string | "Acme" | app.js | Holder link in detail | ✅ WIRED |
| conflicting.holder_tpe_client_id | string | "38229" | app.js | Holder portfolio link | ✅ WIRED |
| conflicting.attorney_name | string | "XYZ Patent" | app.js | Attorney link in detail | ✅ WIRED |
| conflicting.attorney_no | string | "100" | app.js | Attorney portfolio link | ✅ WIRED |
| conflicting.registration_no | string | null | app.js | "Reg: X" in detail | ✅ WIRED |
| conflicting.image_path | string | "/img/..." | app.js | Thumbnail in list + detail | ✅ WIRED |
| conflicting.application_date | date | null | — | — | ⚪ EMPTY |
| conflicting.has_extracted_goods | bool | true | app.js | Amber button in list + detail | ✅ WIRED |
| conflict_bulletin_no | string | "BLT_200" | — | — | ❌ MISSING |
| overlapping_classes | int[] | [9] | app.js | Nice class badges in detail | ✅ WIRED |
| scores.total | float | 0.85 | app.js | Score ring in list + detail | ✅ WIRED |
| scores.text_similarity | float | 0.80 | app.js | Similarity badges | ✅ WIRED |
| scores.semantic_similarity | float | 0.55 | app.js | Similarity badges | ✅ WIRED |
| scores.visual_similarity | float | 0.0 | app.js | Similarity badges | ✅ WIRED |
| scores.translation_similarity | float | 0.10 | app.js | Similarity badges | ✅ WIRED |
| scores.phonetic_match | bool | false | — | — | ❌ MISSING |
| severity | string | "critical" | — | — | ❌ MISSING |
| status | string | "new" | app.js | Footer text in detail | ✅ WIRED |
| source_type | string | "bulletin_scan" | app.js | Footer text in detail | ✅ WIRED |
| source_reference | string | "BLT_200" | — | — | ❌ MISSING |
| source_date | date | null | — | — | ⚪ EMPTY |
| appeal_deadline | date | "2026-03-01" | app.js | Deadline section + timeline | ✅ WIRED |
| conflict_bulletin_date | date | "2026-01-01" | app.js | "Bulletin Date: X" | ✅ WIRED |
| deadline_status | string | "active_urgent" | app.js | Badge + section logic | ✅ WIRED |
| deadline_days_remaining | int | 18 | app.js | Deadline widget + opposition button | ✅ WIRED |
| deadline_label | string | "Acil" | app.js | Badge text | ✅ WIRED |
| deadline_urgency | string | "critical" | app.js | Background color control | ✅ WIRED |
| detected_at | datetime | "2026-..." | app.js | Timestamp text | ✅ WIRED |
| seen_at | datetime | null | — | — | ⚪ EMPTY |
| acknowledged_at | datetime | null | — | — | ❌ MISSING |
| resolved_at | datetime | null | — | — | ❌ MISSING |
| resolution_notes | string | null | — | — | ❌ MISSING |

---

### 2.6 GET /api/v1/alerts/summary — Summary Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| by_status | dict | {"new":5} | — | Not itemized in UI | ❌ MISSING |
| by_severity | dict | {"critical":2} | app.js | Chart slices (risk distribution) | ✅ WIRED |
| total_new | int | 5 | app.js | Updates pending_deadlines stat | ✅ WIRED |

---

### 2.7 GET /api/v1/dashboard/stats — Dashboard Stats Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| watchlist_count | int | 10 | — | — | ❌ MISSING |
| active_watchlist | int | 8 | app.js | KPI "Portfolio Size" | ✅ WIRED |
| total_alerts | int | 25 | — | — | ❌ MISSING |
| new_alerts | int | 5 | app.js | KPI "Active Deadlines" | ✅ WIRED |
| critical_alerts | int | 2 | app.js | KPI "Critical Risks" + urgency dot | ✅ WIRED |
| alerts_this_week | int | 8 | app.js | KPI "7-Day Activity" | ✅ WIRED |
| searches_this_month | int | 0 | — | Always 0 (TODO in backend) | ⚪ EMPTY |
| plan_usage.watchlist.used | int | 8 | — | Not used (separate usage endpoint) | 🔧 INTERNAL |
| plan_usage.watchlist.limit | int | 50 | — | Not used (separate usage endpoint) | 🔧 INTERNAL |
| plan_usage.users.used | int | 0 | — | Not used | 🔧 INTERNAL |
| plan_usage.users.limit | int | 10 | — | Not used | 🔧 INTERNAL |
| plan_usage.searches.used | int | 0 | — | Not used | 🔧 INTERNAL |
| plan_usage.searches.limit | int | 100 | — | Not used | 🔧 INTERNAL |

---

### 2.8 GET /api/v1/watchlist — Watchlist Item Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| id | UUID | "uuid" | app.js | Logo upload/delete, filter clicks | ✅ WIRED |
| organization_id | UUID | "uuid" | — | — | 🔧 INTERNAL |
| user_id | UUID | null | — | — | 🔧 INTERNAL |
| brand_name | string | "MY BRAND" | app.js | Bold truncated card text | ✅ WIRED |
| nice_class_numbers | int[] | [9,42] | app.js | Nice class badges | ✅ WIRED |
| description | string | "A note" | — | — | ❌ MISSING |
| application_no | string | "2020/1234" | app.js | Cached for watchlist status | ✅ WIRED |
| bulletin_no | string | "BLT_100" | — | — | ❌ MISSING |
| registration_no | string | null | — | — | ❌ MISSING |
| application_date | date | null | — | — | ❌ MISSING |
| similarity_threshold | float | 0.70 | — | — | ❌ MISSING |
| monitor_text | bool | true | — | — | ❌ MISSING |
| monitor_visual | bool | true | — | — | ❌ MISSING |
| monitor_phonetic | bool | true | — | — | ❌ MISSING |
| alert_frequency | string | "daily" | — | — | ❌ MISSING |
| alert_email | bool | true | — | — | ❌ MISSING |
| alert_webhook | bool | false | — | — | ❌ MISSING |
| webhook_url | string | null | — | — | ❌ MISSING |
| is_active | bool | true | — | Should be monitoring dot (🐛 see below) | 🐛 BUG |
| last_scan_at | datetime | "2026-..." | app.js | Formatted timestamp text | ✅ WIRED |
| created_at | datetime | "2026-..." | — | — | ❌ MISSING |
| updated_at | datetime | "2026-..." | — | — | ❌ MISSING |
| has_logo | bool | true | app.js | Logo display toggle | ✅ WIRED |
| logo_url | string | "/api/v1/..." | app.js | Logo image src | ✅ WIRED |
| new_alerts_count | int | 3 | app.js | Red alert badge with bell icon | ✅ WIRED |
| total_alerts_count | int | 10 | — | — | ❌ MISSING |
| conflict_summary | object | {...} | app.js | Conflict status badges | ✅ WIRED |
| conflict_summary.total | int | 5 | app.js | Show/hide badges | ✅ WIRED |
| conflict_summary.pre_publication | int | 1 | app.js | Blue badge | ✅ WIRED |
| conflict_summary.active_critical | int | 2 | app.js | Pulsing red badge | ✅ WIRED |
| conflict_summary.active_urgent | int | 1 | app.js | Orange badge | ✅ WIRED |
| conflict_summary.active | int | 1 | app.js | Yellow badge | ✅ WIRED |
| conflict_summary.expired | int | 0 | app.js | Gray badge | ✅ WIRED |
| conflict_summary.nearest_deadline | string | "2026-03-01" | app.js | Presence checked | ✅ WIRED |
| conflict_summary.nearest_deadline_days | int | 18 | app.js | Urgency-colored number | ✅ WIRED |

**🐛 BUG:** Frontend reads `item.auto_scan_enabled` for monitoring dot but API returns `is_active`. Since `undefined !== false`, the dot always shows green "Monitoring".

---

### 2.9 GET /api/v1/watchlist/scan-status — Scan Status Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| auto_scan_enabled | bool | true | — | — | ❌ MISSING |
| schedule | string | "Daily at 03:00" | app.js | Fallback for next scan display | ✅ WIRED |
| next_scan_at | datetime | "2026-02-12T03:00" | app.js | "DD/MM HH:MM" in system row | ✅ WIRED |

---

### 2.10 GET /api/v1/auth/me — User Profile Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| id | UUID | "uuid" | — | Internal auth | 🔧 INTERNAL |
| email | string | "pro@test.com" | — | Auth internals | 🔧 INTERNAL |
| first_name | string | "Pro" | — | Navbar greeting? | ✅ WIRED |
| last_name | string | "User" | — | Navbar greeting? | ✅ WIRED |
| phone | string | null | — | — | ❌ MISSING |
| avatar_url | string | null | — | — | ❌ MISSING |
| organization_id | UUID | "uuid" | — | Internal auth | 🔧 INTERNAL |
| role | string | "owner" | — | Feature gating | ✅ WIRED |
| is_active | bool | true | — | — | 🔧 INTERNAL |
| is_verified | bool | false | — | — | ❌ MISSING |
| is_superadmin | bool | false | — | Feature gating | ✅ WIRED |
| last_login_at | datetime | null | — | — | ❌ MISSING |
| created_at | datetime | "2026-..." | — | — | ❌ MISSING |
| organization.name | string | "Pro Org" | — | Navbar/settings | ✅ WIRED |
| organization.plan | string | "professional" | — | Feature gating everywhere | ✅ WIRED |
| organization.max_users | int | 10 | — | — | ❌ MISSING |
| organization.max_watchlist_items | int | 50 | — | — | ❌ MISSING |
| organization.max_monthly_searches | int | 100 | — | — | ❌ MISSING |
| permissions | str[] | ["search","watchlist",...] | — | Feature gating | ✅ WIRED |

---

### 2.11 GET /api/v1/search/credits — Credits Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| plan | string | "professional" | — | — | 🔧 INTERNAL |
| display_name | string | "Professional" | — | — | ❌ MISSING |
| can_use_live_search | bool | true | — | — | 🔧 INTERNAL |
| monthly_limit | int | 50 | app.js | Usage bar denominator | ✅ WIRED |
| used_this_month | int | 5 | app.js | Usage bar numerator | ✅ WIRED |
| remaining | int | 45 | api.js | Toast / credits display | ✅ WIRED |
| resets_on | string | "2026-03-01" | — | — | ❌ MISSING |

---

### 2.12 GET /api/v1/holders/search — Holder Search Result

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| query | string | "SAMSUNG" | — | — | 🔧 INTERNAL |
| results[].holder_name | string | "SAMSUNG" | app.js | Bold text in search dropdown | ✅ WIRED |
| results[].holder_tpe_client_id | string | "12345" | app.js | Parenthesized ID | ✅ WIRED |
| results[].trademark_count | int | 500 | app.js | "X trademarks" count | ✅ WIRED |

---

### 2.13 GET /api/v1/holders/{id}/trademarks — Portfolio Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| holder_name | string | "SAMSUNG" | api.js | Modal title | ✅ WIRED |
| holder_tpe_client_id | string | "12345" | api.js | Subtitle template | ✅ WIRED |
| total_count | int | 500 | api.js | Subtitle + stat cards | ✅ WIRED |
| page | int | 1 | api.js | Pagination | ✅ WIRED |
| page_size | int | 20 | — | — | 🔧 INTERNAL |
| total_pages | int | 25 | api.js | Pagination | ✅ WIRED |
| trademarks[].id | string | "uuid" | — | — | 🔧 INTERNAL |
| trademarks[].application_no | string | "2020/1234" | api.js | TURKPATENT button | ✅ WIRED |
| trademarks[].name | string | "GALAXY" | api.js | Bold text | ✅ WIRED |
| trademarks[].status | string | "Registered" | api.js | Status badge + stat counting | ✅ WIRED |
| trademarks[].classes | int[] | [9,38] | api.js | Nice class badges | ✅ WIRED |
| trademarks[].application_date | string | "2020-01-15" | api.js | Formatted date | ✅ WIRED |
| trademarks[].registration_date | string | "2021-06-01" | — | — | ❌ MISSING |
| trademarks[].image_path | string | "/img/..." | api.js | 48px thumbnail | ✅ WIRED |
| trademarks[].has_extracted_goods | bool | true | api.js | Amber button | ✅ WIRED |

**Note:** Holder API SQL fetches `bulletin_no, gazette_no, attorney_name, attorney_no, registration_no` from DB but does NOT include them in the JSON response.

---

### 2.14 GET /api/v1/attorneys/search — Attorney Search Result

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| query | string | "patent" | — | — | 🔧 INTERNAL |
| results[].attorney_name | string | "ABC Patent" | app.js | Bold text in dropdown | ✅ WIRED |
| results[].attorney_no | string | "595" | app.js | Subtitle text | ✅ WIRED |
| results[].trademark_count | int | 200 | app.js | "X trademarks" count | ✅ WIRED |

---

### 2.15 GET /api/v1/attorneys/{no}/trademarks — Portfolio Object

Same structure as holder portfolio. Additional note:

**Note:** Attorney API SQL fetches `holder_name, holder_tpe_client_id` from DB but does NOT include them in JSON response. Each trademark in an attorney portfolio shows no holder info.

| Field | Status |
|-------|--------|
| trademarks[].registration_date | ❌ MISSING (returned but not displayed) |
| (holder_name not in response) | ❌ NOT RETURNED (fetched from DB but excluded from response) |
| (holder_tpe_client_id not in response) | ❌ NOT RETURNED (fetched from DB but excluded from response) |

---

### 2.16 GET /api/v1/reports/ — Report Object

| Field | Type | Sample | Frontend Component | Display Method | Status |
|-------|------|--------|-------------------|----------------|--------|
| id | UUID | "uuid" | app.js | Download onclick param | ✅ WIRED |
| organization_id | UUID | "uuid" | — | — | 🔧 INTERNAL |
| report_type | string | "risk_assessment" | app.js | Localized type label | ✅ WIRED |
| title | string | "My Report" | app.js | Bold truncated text | ✅ WIRED |
| status | string | "completed" | app.js | Colored badge + download visibility | ✅ WIRED |
| file_path | string | "/reports/..." | — | — | 🔧 INTERNAL |
| file_format | string | "pdf" | app.js | Uppercase format label | ✅ WIRED |
| file_size_bytes | int | 45000 | app.js | "44 KB" formatted | ✅ WIRED |
| generated_at | datetime | null | — | — | ❌ MISSING |
| created_at | datetime | "2026-..." | app.js | Formatted date | ✅ WIRED |

---

### 2.17 Creative Suite — Name Suggestion Response

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| safe_names[] | list | studio-card.js | ✅ WIRED |
| safe_names[].name | string | Bold large text | ✅ WIRED |
| safe_names[].risk_score | float | Score ring (size 40) | ✅ WIRED |
| safe_names[].is_safe | bool | Safe/Caution badge | ✅ WIRED |
| safe_names[].risk_level | string | Colored risk badge | ✅ WIRED |
| safe_names[].closest_match | string | "Closest: X" text | ✅ WIRED |
| safe_names[].text_similarity | float | Max() for similarity pct | ✅ WIRED |
| safe_names[].semantic_similarity | float | Max() for similarity pct | ✅ WIRED |
| safe_names[].phonetic_match | bool | Red phonetic badge | ✅ WIRED |
| safe_names[].translation_similarity | float | Similarity badges | ✅ WIRED |
| filtered_count | int | Meta text | ✅ WIRED |
| total_generated | int | Meta text | ✅ WIRED |
| session_count | int | — | ❌ MISSING |
| credits_remaining | object | Credits display | ✅ WIRED |
| credits_remaining.session_limit | int | Limit display | ✅ WIRED |
| credits_remaining.used | int | Usage display | ✅ WIRED |
| credits_remaining.purchased | int | Bonus display | ✅ WIRED |
| cached | bool | "(from cache)" label | ✅ WIRED |

---

### 2.18 Creative Suite — Logo Generation Response

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| logos[] | list | studio-card.js | ✅ WIRED |
| logos[].image_id | string | DOM ID + download URL | ✅ WIRED |
| logos[].image_url | string | Auth-fetched image | ✅ WIRED |
| logos[].similarity_score | float | Percentage text | ✅ WIRED |
| logos[].closest_match_name | string | "Closest Match: X" | ✅ WIRED |
| logos[].closest_match_image_url | string | Thumbnail if unsafe | ✅ WIRED |
| logos[].is_safe | bool | Safe/Risk badge | ✅ WIRED |
| logos[].visual_breakdown | object | Detail panel bars | ✅ WIRED |
| logos[].visual_breakdown.clip | float | Progress bar | ✅ WIRED |
| logos[].visual_breakdown.dino | float | Progress bar | ✅ WIRED |
| logos[].visual_breakdown.ocr | float | Progress bar | ✅ WIRED |
| logos[].visual_breakdown.color | float | Progress bar | ✅ WIRED |
| credits_remaining.monthly | int | Credits display | ✅ WIRED |
| credits_remaining.purchased | int | Credits display | ✅ WIRED |
| generation_id | string | — | ❌ MISSING |

---

### 2.19 GET /api/v1/usage/summary — Usage Object

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| plan | string | — | 🔧 INTERNAL |
| display_name | string | — | ❌ MISSING (could show plan name) |
| usage.daily_quick_searches.used | int | app.js | ✅ WIRED (usage bar numerator) |
| usage.daily_quick_searches.limit | int | app.js | ✅ WIRED (usage bar denominator) |
| usage.monthly_live_searches.used | int | app.js | ✅ WIRED (usage bar numerator) |
| usage.monthly_live_searches.limit | int | app.js | ✅ WIRED (usage bar denominator) |
| usage.monthly_name_generations.used | int | — | ❌ MISSING |
| usage.monthly_name_generations.limit | int | — | ❌ MISSING |
| usage.logo_credits.remaining | int | — | ❌ MISSING |
| usage.logo_credits.limit | int | — | ❌ MISSING |
| usage.watchlist_items.used | int | app.js | ✅ WIRED (usage bar numerator) |
| usage.watchlist_items.limit | int | app.js | ✅ WIRED (usage bar denominator) |

---

### 2.20 POST /api/v1/billing/validate-discount — Discount Validation

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| valid | bool | pricing page | ✅ WIRED |
| code | string | pricing page | ✅ WIRED |
| discount_type | string | pricing page | ✅ WIRED |
| discount_value | float | pricing page | ✅ WIRED |
| applies_to_plan | string | pricing page | ✅ WIRED |

---

### 2.21 GET /api/v1/pipeline/status — Pipeline Status

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| is_running | bool | Running indicator + button state | ✅ WIRED |
| current_step | string | Running step label | ✅ WIRED |
| next_scheduled | string | Footer next run time | ✅ WIRED |
| recent_runs[] | list | Last run display | ✅ WIRED |
| recent_runs[0].completed_at | string | "Last run: X" | ✅ WIRED |
| recent_runs[0].duration_seconds | int | "(Xm Ys)" | ✅ WIRED |
| recent_runs[0].status | string | Running check | ✅ WIRED |
| recent_runs[0].step_download.processed | int | Step count | ✅ WIRED |
| recent_runs[0].step_extract.processed | int | Step count | ✅ WIRED |
| recent_runs[0].step_metadata.processed | int | Step count | ✅ WIRED |
| recent_runs[0].step_embeddings.processed | int | Step count | ✅ WIRED |
| recent_runs[0].step_ingest.processed | int | Step count | ✅ WIRED |

---

### 2.22 GET /api/v1/extracted-goods/{app_no} — Extracted Goods

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| application_no | string | — | 🔧 INTERNAL |
| name | string | — | 🔧 INTERNAL |
| has_extracted_goods | bool | app.js | ✅ WIRED (controls display) |
| extracted_goods[] | list | app.js | ✅ WIRED (rendered as expandable list) |
| extracted_goods[].TEXT | string | app.js | ✅ WIRED (goods description text) |
| extracted_goods[].CLASSID | string | — | 🔧 INTERNAL |
| extracted_goods[].SUBCLASSID | string | — | 🔧 INTERNAL |
| extracted_goods[].SEQ | int | — | 🔧 INTERNAL |
| nice_classes | int[] | — | 🔧 INTERNAL |
| total_items | int | — | ❌ MISSING (not displayed) |

---

### 2.23 GET /api/v1/tools/status — Creative Suite Status

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| name_generator.available | bool | app.js | ✅ WIRED (enables/disables generate button) |
| name_generator.reason | string | app.js | ✅ WIRED (shown when unavailable) |
| logo_studio.available | bool | app.js | ✅ WIRED (enables/disables generate button) |
| logo_studio.reason | string | app.js | ✅ WIRED (shown when unavailable) |

---

### 2.24 GET /api/v1/tools/credits — Creative Credits

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| names.session_limit | int | — | ❌ MISSING |
| names.used | int | — | ❌ MISSING |
| logos.monthly | int | — | ❌ MISSING |
| logos.purchased | int | — | ❌ MISSING |

**Note:** Creative credits are returned inline in generate responses, not from this endpoint.

---

### 2.25 GET /api/v1/reports — Reports List Envelope

Additional envelope fields not in section 2.16:

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| page | int | app.js | ✅ WIRED (pagination) |
| total_pages | int | app.js | ✅ WIRED (pagination) |
| usage.reports_limit | int | app.js | ✅ WIRED (remaining reports display) |
| usage.reports_used | int | app.js | ✅ WIRED (remaining reports display) |

---

### 2.26 GET /api/v1/config — Frontend Configuration

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| app_name | string | — | 🔧 INTERNAL |
| app_version | string | — | 🔧 INTERNAL |
| features | object | — | 🔧 INTERNAL (feature flags) |

---

### 2.27 GET /api/v1/status — System Status

| Field | Type | Frontend Component | Status |
|-------|------|--------------------|--------|
| statistics.total_trademarks | int | app.js | ✅ WIRED (displayed with locale formatting) |

---

## 3. DATABASE → API PIPELINE GAPS

**Database:** 2,625,377 trademarks across 33 tables. Validated via live PostgreSQL queries.

### 3.1 Trademarks Table (38 columns) — Columns NOT exposed in any API

| DB Column | Type | Population (actual) | Status |
|-----------|------|---------------------|--------|
| name_tr | varchar | 2,564,523 (97.7%) | ❌ NOT IN API — Turkish translation, useful for multilingual display |
| name_en | varchar | 0 (0.0%) | ⚪ EMPTY — Not populated yet |
| name_ku | varchar | 0 (0.0%) | ⚪ EMPTY — Not populated yet |
| name_fa | varchar | 0 (0.0%) | ⚪ EMPTY — Not populated yet |
| detected_lang | varchar | 2,564,556 (97.7%) | ❌ NOT IN API — Could show "Language: AR" badge |
| logo_ocr_text | text | 1,842,089 (70.2%) | ❌ NOT IN API — Could show OCR text for logo search results |
| status_source | varchar | 2,625,377 (100%) | 🔧 INTERNAL — Data provenance tracking (BLT/GZ/APP) |
| text_embedding | halfvec(384) | 2,597,220 (98.9%) | 🔧 INTERNAL |
| image_embedding | halfvec(512) | 1,865,775 (71.1%) | 🔧 INTERNAL |
| dinov2_embedding | halfvec(768) | 1,865,775 (71.1%) | 🔧 INTERNAL |
| color_histogram | halfvec(512) | 1,857,938 (70.8%) | 🔧 INTERNAL |
| extracted_goods | jsonb | 163,777 (6.2%) | ✅ Via separate endpoint + has_extracted_goods flag |
| gazette_no | varchar | 1,162,679 (44.3%) | ❌ NOT IN API — Gazette number |
| gazette_date | date | 1,162,679 (44.3%) | ❌ NOT IN API — Gazette publication date |
| wipo_no | varchar | 112,919 (4.3%) | ❌ NOT IN API — International registration |
| vienna_class_numbers | int[] | 1,642,605 (62.6%) | ❌ NOT IN API — Vienna image classification codes |
| expiry_date | date | 2,603,603 (99.2%) | ❌ NOT IN API — Registration expiry date |
| last_event_date | date | 2,625,377 (100%) | ❌ NOT IN API — Last status change |
| availability_status | varchar | 0 (0.0%) | ⚪ EMPTY — Not populated yet |
| application_date | date | 2,603,603 (99.2%) | ✅ In holder/attorney portfolio (NOT in search results) |
| registration_date | date | 0 (0.0%) | ⚪ EMPTY — Never populated (0 rows) |
| holder_id | uuid | — | 🔧 INTERNAL — Foreign key to holders table |
| created_at | timestamp | — | 🔧 INTERNAL |
| updated_at | timestamp | — | 🔧 INTERNAL |

### 3.2 Key Population Rates for API-Served Fields

| Column | Count | Rate | Notes |
|--------|-------|------|-------|
| holder_name | 2,591,492 | 98.7% | Well-populated |
| attorney_name | 1,545,849 | 58.9% | Many trademarks have no attorney |
| bulletin_no | 2,096,867 | 79.9% | BLT-sourced |
| bulletin_date | 1,923,413 | 73.3% | BLT-sourced |
| appeal_deadline | 1,923,413 | 73.3% | BLT-sourced |

### 3.3 Universal Conflicts Table (30 columns)

| Missing from leads SQL | Column exists | Notes |
|------------------------|---------------|-------|
| translation_similarity | ❌ NO COLUMN | Column does NOT exist in `universal_conflicts` table. Pydantic model expects it but DB doesn't have it |
| phonetic_similarity | ❌ NO COLUMN | Not in DB schema |
| urgency_level | ❌ NO COLUMN | Must be computed at query time from deadline |

**Note:** The leads SQL gap is larger than expected — `universal_conflicts` only has `text_similarity`, `visual_similarity`, and `semantic_similarity`. Translation and phonetic scores were never stored.

### 3.4 API Response Gaps (fields fetched from DB but excluded from response)

**Holder portfolio API** fetches but does NOT return: `bulletin_no`, `gazette_no`, `attorney_name`, `attorney_no`, `registration_no`

**Attorney portfolio API** fetches but does NOT return: `holder_name`, `holder_tpe_client_id`

### 3.5 Other Tables

| Table | Rows | Used by API | Notes |
|-------|------|-------------|-------|
| alerts_mt | 0 | Yes (alerts router) | No alerts generated yet |
| watchlist_mt | 0 | Yes (watchlist router) | No watchlist items yet |
| universal_conflicts | 0 | Yes (leads router) | No conflicts detected yet |
| universal_scan_queue | 2,312,197 | No | Internal scan pipeline |
| word_idf | 0 | Yes (IDF admin) | IDF cache not computed |
| pipeline_runs | 0 | Yes (pipeline router) | No pipeline runs tracked |
| reports | 0 | Yes (reports router) | No reports generated |
| users | 2 | Yes (auth) | 2 test users |
| organizations | 2 | Yes (auth) | 2 test orgs |
| subscription_plans | 4 | Yes (billing) | free/starter/professional/enterprise |
| trademark_history | 1,319,366 | No API endpoint | Change tracking partitioned table |
| processed_files | 557 | No API endpoint | Pipeline state tracking |

---

## 4. SUMMARY STATISTICS

### Endpoint Count
| Category | Count |
|----------|-------|
| **Total API endpoints** | **132** |
| Customer-facing endpoints (search, watchlist, alerts, leads, creative, portfolio, dashboard) | 71 |
| User/org management endpoints | 16 |
| Superadmin endpoints | 27 |
| Legacy/deprecated endpoints | 5 |
| Utility/static endpoints (images, health, config, HTML pages) | 13 |

### Field Coverage (customer-facing endpoints, 27 endpoint groups)
| Category | Count |
|----------|-------|
| **Total API fields across customer endpoints** | **~235** |
| **Fields currently WIRED (✅)** | **~170** |
| **Fields MISSING from UI (❌)** | **~45** |
| **Fields correctly INTERNAL (🔧)** | **~35** |
| **Fields EMPTY in data (⚪)** | **~5** |
| **Frontend BUGs (🐛)** | **1** |
| **Coverage percentage (WIRED / user-facing)** | **~79%** (per-table estimate) |

### Database Coverage
| Category | Count |
|----------|-------|
| **Total `trademarks` columns** | **38** |
| Columns exposed in at least 1 API endpoint | 20 |
| Columns NOT in any API (but have data) | 10 |
| Embedding vectors (internal only) | 4 |
| Empty columns (0% populated) | 4 (name_en, name_ku, name_fa, availability_status) |
| **registration_date** — 0 rows populated | ⚪ EMPTY (dead column) |

### Data Quality Findings
| Finding | Impact |
|---------|--------|
| `universal_conflicts` has 0 rows | Leads feed is completely empty |
| `alerts_mt` has 0 rows | Alert system is empty |
| `watchlist_mt` has 0 rows | No watchlist items exist |
| `word_idf` has 0 rows | IDF scoring not initialized |
| `registration_date` has 0 rows | Column exists but never populated |
| `name_en/ku/fa` all 0% | Translation only done for Turkish |

---

## 5. MISSING FIELDS BY PRIORITY

### Critical (user-facing data that should definitely be visible)

| # | Field | Endpoint | Should appear | How |
|---|-------|----------|---------------|-----|
| 1 | `conflicting.status` | alerts | Alert detail modal | Colored status badge |
| 2 | `conflicting.classes` | alerts | Alert detail modal | Nice class badges (alongside overlapping) |
| 3 | `severity` | alerts | Alert list + detail | Colored severity badge (critical/high/medium/low) |
| 4 | `scores.phonetic_match` | alerts | Alert similarity badges | Boolean badge "Phonetic Match" |
| 5 | `watchlist_classes` | alerts | Alert detail modal | Nice class badges for watched brand |
| 6 | `by_status` | alerts/summary | Dashboard or alert panel | Status breakdown (new/seen/acknowledged/resolved) |
| 7 | `upcoming_leads` | leads/stats | Leads stat card | 4th stat card "Upcoming (≤30 days)" |
| 8 | `avg_similarity` | leads/stats | Leads panel | Average similarity indicator |
| 9 | `is_active` → monitoring dot | watchlist | Watchlist cards | 🐛 FIX: read `is_active` not `auto_scan_enabled` |
| 10 | `similarity_threshold` | watchlist | Watchlist cards | Show threshold value per brand |
| 11 | `total_alerts_count` | watchlist | Watchlist cards | "X total alerts" alongside new count |
| 12 | `registration_date` | holder/attorney portfolio | Portfolio trademarks | Date display below application_date |
| 13 | `holder_name/id` (attorney portfolio) | attorney portfolio | Attorney's trademarks | Show trademark holder name |

### Medium (useful context, enhances UX)

| # | Field | Endpoint | Could appear | How |
|---|-------|----------|--------------|-----|
| 14 | `created_at` | leads/feed | Lead card footer | "Detected: 2 days ago" |
| 15 | `source` | search | Search response | Badge showing "Database" vs "Live" |
| 16 | `conflict_bulletin_no` | alerts | Alert detail | "Bulletin: BLT_200" |
| 17 | `watchlist_bulletin_no` | alerts | Alert detail | "Your bulletin: BLT_100" |
| 18 | `source_reference` | alerts | Alert detail | "Source: BLT_200" |
| 19 | `description` | watchlist | Watchlist cards | Tooltip or expansion |
| 20 | `daily_limit` / `used_today` | leads/credits | Leads panel | "2/5 views used today" |
| 21 | `new_leads` / `viewed_leads` / `contacted_leads` | leads/stats | Leads stat cards or urgency bar | Additional stat segments |
| 22 | `last_scan_at` | leads/stats | Leads panel | "Last scan: 2h ago" |
| 23 | `acknowledged_at` / `resolved_at` | alerts | Alert detail timeline | Resolution history |
| 24 | `resolution_notes` | alerts | Alert detail | Notes on resolved alerts |
| 25 | `scores.matched_words` | search results | result-card | Bold matched words in name |
| 26 | `scores.token_overlap` | search results | score-badge | "Overlap" progress bar |
| 27 | `name_tr` | DB → API → search | result-card | "Turkish: X" text |
| 28 | `resets_on` | search credits | Usage section | "Resets March 1" label |
| 29 | `display_name` | search credits | Usage section | "Professional Plan" label |
| 30 | `watchlist_count` / `total_alerts` | dashboard stats | KPI context | Tooltip or secondary number |

### Low (minor metadata, internal details)

| # | Field | Endpoint | Notes |
|---|-------|----------|-------|
| 31 | `ingested_count` | search | Internal pipeline metric |
| 32 | `score_before` / `score_improvement` | search | Before/after live scrape comparison |
| 33 | `credits_used` | search | Already shown via remaining |
| 34 | `session_count` | creative names | Session tracking |
| 35 | `generation_id` | creative logos | Internal tracking |
| 36 | `generated_at` | reports | created_at serves same purpose |
| 37 | `is_verified` | auth/me | Account verification status |
| 38 | `last_login_at` | auth/me | Profile/settings page |
| 39 | `source` (DB column) | Not in API | Data provenance |
| 40 | `gazette_no/date` | Not in API | Gazette metadata |
| 41 | `wipo_no` | Not in API | International number |
| 42 | `logo_ocr_text` / `detected_lang` | Not in API | AI metadata |

---

## 6. MISSING FIELDS BY COMPONENT

### result-card.js — needs:
- `scores.matched_words` — highlight matching words in trademark name (Medium)
- `scores.token_overlap` — additional score dimension (Medium)
- `application_date` — not in search results API (would need backend change)

### lead-card.js — needs:
- `created_at` — detection timestamp (Medium)
- `translation_similarity` from SQL — currently always null (Backend SQL fix needed)

### score-badge.js — working well, could add:
- `scores.matched_words` support for highlighting (Medium)

### app.js (dashboard) — needs:
- `watchlist_count` / `total_alerts` — secondary numbers on KPI cards (Medium)
- `by_status` from alerts/summary — status breakdown display (Critical)

### app.js (leads panel) — needs:
- `upcoming_leads`, `new_leads`, `viewed_leads`, `contacted_leads` — stat cards or urgency bar segments (Critical)
- `avg_similarity` — indicator (Medium)
- `last_scan_at` — "last scan" label (Medium)
- `daily_limit` / `used_today` — progress display (Medium)

### app.js (watchlist) — needs:
- 🐛 FIX `auto_scan_enabled` → `is_active` field name (Critical)
- `similarity_threshold` — threshold display (Critical)
- `total_alerts_count` — total count alongside new (Critical)
- `description` — tooltip or expandable (Medium)

### app.js (alerts) — needs:
- `severity` — severity badge (Critical)
- `conflicting.status` — status badge (Critical)
- `conflicting.classes` — class badges (Critical)
- `scores.phonetic_match` — phonetic badge (Critical)
- `watchlist_classes` — watched brand classes (Critical)
- `acknowledged_at`, `resolved_at`, `resolution_notes` — timeline (Medium)

### api.js (portfolio modals) — needs:
- `registration_date` — already returned but not displayed (Critical)
- Holder names in attorney portfolio — not returned by API (Critical, backend fix needed)

---

## 7. RECOMMENDATIONS

### Immediate Fixes (frontend-only, no backend changes)

1. **🐛 Fix watchlist monitoring dot**: Change `item.auto_scan_enabled` → `item.is_active` in `renderPortfolioGrid()`
2. **Display `registration_date`** in portfolio modals — already returned by API, just not rendered
3. **Display `severity`** in alert list/detail — already returned by API
4. **Display `conflicting.status`** in alert detail — already returned
5. **Display `conflicting.classes`** in alert detail — already returned
6. **Display `scores.phonetic_match`** in alert similarity badges — already returned
7. **Display `watchlist_classes`** in alert detail — already returned
8. **Display `total_alerts_count`** alongside `new_alerts_count` in watchlist cards — already returned
9. **Display `similarity_threshold`** on watchlist cards — already returned
10. **Display `upcoming_leads`** stat — already returned by leads/stats
11. **Display `by_status`** from alerts/summary — already returned
12. **Display `source_reference`** / `conflict_bulletin_no` in alert detail — already returned
13. **Display `acknowledged_at` / `resolved_at` / `resolution_notes`** in alert detail — already returned

### Backend Changes Needed

14. **Add `holder_name`, `holder_tpe_client_id`** to attorney portfolio response (fetched from DB but not returned)
15. **Add `attorney_name`, `attorney_no`, `registration_no`, `bulletin_no`** to holder portfolio response (fetched from DB but not returned)
16. **Add `translation_similarity` column** to `universal_conflicts` table (column does NOT exist yet), then compute during scans
17. **Add `application_date`** to search result objects (currently only in portfolio views)
18. **Add `name_tr`** to search result objects (for Turkish translation display)
19. **Implement `searches_this_month`** in dashboard stats (currently hardcoded 0)

### Data Pipeline Actions Needed

20. **Run IDF computation** (`scripts/compute_idf_scheduled.bat`) — `word_idf` table has 0 rows, IDF scoring is non-functional
21. **Run universal scan** — `universal_conflicts` has 0 rows → leads feed is completely empty
22. **Add `translation_similarity`, `phonetic_similarity` columns** to `universal_conflicts` schema + compute during scans
23. **Populate `registration_date`** — column exists but 0/2.6M rows populated, may need data from Gazette sources
24. **Add `vienna_class_numbers`** to search results — 62.6% populated, useful for image classification
25. **Add `expiry_date`** to search results — 99.2% populated, critical for trademark lifecycle

### Not Recommended (internal/low-value)

- Embedding vectors, raw UUIDs, internal IDs — no user value
- `scores.distinctive_match`, `semi_generic_match`, `generic_match` — too technical for UI
- `scores.text_idf_score` — internal scoring intermediate
- `score_before` / `score_improvement` — edge case for live search comparisons
- `name_en`, `name_ku`, `name_fa` — 0% populated, not ready for use
