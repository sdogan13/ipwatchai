# Results Display Audit - Full Project Report

**Date:** 2026-02-08
**Scope:** Every view/endpoint that returns or displays trademark data

---

## 1. API Response Fields

### 1.1 Quick Search

- **Path:** `GET /api/v1/search/quick`
- **Auth:** JWT required
- **Rate Limit:** 60/min + daily plan quota
- **Params:** `query` (required), `classes` (comma-sep), `page` (default=1), `per_page` (default=20, max=100)

**SQL columns selected from `trademarks`:**
```
application_no, name, current_status, nice_class_numbers, image_path,
name_tr, name_en, name_ku, name_fa,
holder_name, holder_tpe_client_id,
(1 - (text_embedding <=> query_vec)) as score_semantic,
similarity(name, query) as score_lexical,
0.0 as score_visual,
bulletin_no,
(dmetaphone(name) = dmetaphone(query)) as phonetic_match,
(extracted_goods IS NOT NULL AND ...) as has_extracted_goods
```

**Response:**
```json
{
  "query": { "name": "NIKE", "classes": [25], "has_logo": false },
  "auto_suggested_classes": [],
  "final_risk_score": 0.92,
  "top_candidates": [
    {
      "application_no": "2023/123456",
      "name": "NIKEA",
      "status": "Registered",
      "classes": [25, 35],
      "image_path": "some_image.jpg",
      "holder_name": "ACME LTD",
      "holder_tpe_client_id": 12345,
      "bulletin_no": "BLT2023001",
      "exact_match": false,
      "has_extracted_goods": true,
      "scores": {
        "total": 0.85,
        "text_similarity": 0.78,
        "semantic_similarity": 0.82,
        "visual_similarity": 0.0,
        "translation_similarity": 0.0,
        "phonetic_match": true,
        "phonetic_similarity": 1.0,
        "exact_match": false,
        "containment": 0.8,
        "token_overlap": 0.5,
        "weighted_overlap": 0.6,
        "distinctive_match": 0.9,
        "semi_generic_match": 0.0,
        "generic_match": 0.0,
        "distinctive_weight_matched": 0.9,
        "semi_generic_weight_matched": 0.0,
        "generic_weight_matched": 0.0,
        "matched_words": [
          {
            "query_word": "NIKE",
            "target_word": "NIKEA",
            "match_type": "fuzzy",
            "idf": 8.2,
            "word_class": "distinctive",
            "weight": 1.0,
            "similarity": 0.85
          }
        ],
        "text_idf_score": 0.78,
        "dynamic_weights": { "text": 0.55, "visual": 0.0, "translation": 0.25, "phonetic": 0.20 },
        "scoring_path": "A: High distinctive match (>=80%)"
      }
    }
  ],
  "page": 1,
  "per_page": 20,
  "total": 47,
  "total_pages": 3,
  "total_candidates": 47,
  "max_score": 0.92,
  "risk_level": "CRITICAL",
  "source": "database",
  "scrape_triggered": false,
  "scraped_count": 0,
  "ingested_count": 0,
  "score_before": null,
  "score_improvement": null,
  "image_used": false,
  "elapsed_seconds": 0.789,
  "timestamp": "2026-02-08T14:30:00Z"
}
```

---

### 1.2 Intelligent Search (GET - text only)

- **Path:** `GET /api/v1/search/intelligent`
- **Auth:** JWT required, Professional+ plan
- **Rate Limit:** 10/min + monthly credit check
- **Params:** `query`, `classes`, `threshold` (default=0.75), `force_scrape` (bool), `page`, `per_page`
- **Feature flag:** `live_scraping_enabled` (503 if disabled)

**Additional behavior vs Quick Search:**
- May trigger live scraping of turkpatent.gov.tr if DB score < threshold or force_scrape=true
- If scraping: deducts 1 monthly credit, saves scraped data, generates embeddings, ingests, re-scores
- Response identical to Quick Search + these extra fields when scrape triggered:
  - `scrape_triggered: true`
  - `source: "combined"`
  - `scraped_count: 15`
  - `ingested_count: 12`
  - `score_before: 0.65`
  - `score_improvement: 0.27`
  - `credits_used: 1`
  - `credits_remaining: 8`

**HTTP Status Codes:** 200, 401, 402 (credits exhausted), 403 (plan upgrade), 429, 503 (disabled)

---

### 1.3 Intelligent Search (POST - with image)

- **Path:** `POST /api/v1/search/intelligent`
- **Content-Type:** `multipart/form-data`
- **Params:** `query`, `image` (UploadFile, optional), `classes`, `threshold`, `force_scrape`, `page`, `per_page`

**Additional behavior when image provided:**
- Generates CLIP (512-dim), DINOv2 (768-dim), color histogram, OCR embeddings from uploaded image
- `visual_similarity` now populated (was 0.0 in text-only): `0.35*clip + 0.30*dinov2 + 0.15*color + 0.20*ocr`
- Response: `"image_used": true`
- Temp file cleanup in finally block

---

### 1.4 Watchlist - List Items

- **Path:** `GET /api/v1/watchlist`
- **Auth:** JWT required
- **Params:** `page`, `page_size` (default=100, max=2000), `active_only` (default=true)

**Response per item:**
```json
{
  "id": "uuid",
  "organization_id": "uuid",
  "user_id": "uuid",
  "brand_name": "MY BRAND",
  "nice_class_numbers": [25, 35],
  "description": "Clothing brand",
  "application_no": "2023/001",
  "bulletin_no": "BLT2023",
  "registration_no": "REG001",
  "filing_date": "2023-01-15",
  "similarity_threshold": 0.7,
  "monitor_text": true,
  "monitor_visual": true,
  "monitor_phonetic": true,
  "alert_frequency": "daily",
  "alert_email": true,
  "alert_webhook": false,
  "webhook_url": null,
  "is_active": true,
  "last_scan_at": "2026-02-08T10:00:00Z",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-02-08T10:00:00Z",
  "has_logo": true,
  "logo_url": "/api/v1/watchlist/uuid/logo",
  "new_alerts_count": 3,
  "total_alerts_count": 12,
  "conflict_summary": {
    "total": 12,
    "pre_publication": 2,
    "active_critical": 1,
    "active_urgent": 3,
    "active": 4,
    "expired": 2,
    "nearest_deadline": "2026-02-15",
    "nearest_deadline_days": 7
  }
}
```

---

### 1.5 Alerts - List

- **Path:** `GET /api/v1/alerts`
- **Auth:** JWT required
- **Params:** `page`, `page_size` (default=20, max=100), `status` (array), `severity` (array), `watchlist_id`

**SQL JOINs:** `alerts_mt` LEFT JOIN `watchlist_mt` LEFT JOIN `trademarks`

**Response per alert:**
```json
{
  "id": "uuid",
  "organization_id": "uuid",
  "watchlist_id": "uuid",
  "watched_brand_name": "MY BRAND",
  "watchlist_bulletin_no": "BLT2023",
  "watchlist_application_no": "2023/001",
  "watchlist_classes": [25],
  "conflicting": {
    "id": "uuid",
    "name": "MYBRAND",
    "application_no": "2024/999",
    "status": "Published",
    "classes": [25, 35],
    "holder": "OTHER LTD",
    "image_path": "img.jpg",
    "filing_date": "2024-06-01",
    "has_extracted_goods": true
  },
  "conflict_bulletin_no": "BLT2024005",
  "overlapping_classes": [25],
  "scores": {
    "total": 0.82,
    "text_similarity": 0.75,
    "semantic_similarity": 0.80,
    "visual_similarity": 0.45,
    "translation_similarity": 0.0,
    "phonetic_match": true
  },
  "severity": "very_high",
  "status": "new",
  "source_type": "bulletin_scan",
  "source_reference": "BLT2024005",
  "source_date": "2024-07-01",
  "appeal_deadline": "2026-03-01",
  "conflict_bulletin_date": "2024-07-01",
  "deadline_status": "active_urgent",
  "deadline_days_remaining": 21,
  "deadline_label": "21 gun",
  "deadline_urgency": "urgent",
  "detected_at": "2026-02-01T00:00:00Z",
  "seen_at": null,
  "acknowledged_at": null,
  "resolved_at": null,
  "resolution_notes": null
}
```

**Post-processing:** `_format_alert()` calls `classify_deadline_status()` to compute deadline metadata from trademarks JOIN.

---

### 1.6 Alerts - Summary

- **Path:** `GET /api/v1/alerts/summary`
- **Response:**
```json
{
  "by_status": { "new": 5, "seen": 3, "acknowledged": 1 },
  "by_severity": { "critical": 2, "very_high": 1, "high": 2 },
  "total_new": 5
}
```

---

### 1.7 Opposition Radar - Lead Feed

- **Path:** `GET /api/v1/leads/feed`
- **Auth:** JWT required, Professional+ plan
- **Params:** `urgency`, `nice_class`, `min_score` (default=0.6), `risk_level`, `status` (default=new), `page`, `limit` (default=20, max=100)
- **Plan gate:** daily view limit per plan

**SQL:** `universal_conflicts` LEFT JOIN `trademarks` (twice: new_tm + exist_tm)

**Response per lead:**
```json
{
  "id": "uuid",
  "new_mark_name": "NEWBRAND",
  "new_mark_app_no": "2026/100",
  "new_mark_holder_name": "STARTUP LTD",
  "new_mark_nice_classes": [9, 42],
  "new_mark_image": "newbrand.jpg",
  "new_mark_has_extracted_goods": true,
  "existing_mark_name": "NEWBRAND TECH",
  "existing_mark_app_no": "2020/500",
  "existing_mark_holder_name": "TECH CORP",
  "existing_mark_nice_classes": [9, 35, 42],
  "existing_mark_image": "newbrandtech.jpg",
  "existing_mark_has_extracted_goods": false,
  "similarity_score": 0.88,
  "text_similarity": 0.85,
  "semantic_similarity": 0.82,
  "visual_similarity": 0.40,
  "translation_similarity": 0.0,
  "risk_level": "VERY_HIGH",
  "conflict_type": "HYBRID",
  "overlapping_classes": [9, 42],
  "conflict_reasons": ["High text similarity", "Same Nice classes"],
  "bulletin_no": "BLT2026003",
  "bulletin_date": "2026-01-15",
  "opposition_deadline": "2026-03-15",
  "days_until_deadline": 35,
  "urgency_level": "soon",
  "lead_status": "new",
  "created_at": "2026-01-20T00:00:00Z"
}
```

---

### 1.8 Opposition Radar - Stats

- **Path:** `GET /api/v1/leads/stats`
- **Response:**
```json
{
  "total_leads": 150,
  "critical_leads": 12,
  "urgent_leads": 28,
  "upcoming_leads": 45,
  "new_leads": 100,
  "viewed_leads": 30,
  "contacted_leads": 15,
  "converted_leads": 5,
  "avg_similarity": 0.72,
  "last_scan_at": "2026-02-07T23:00:00Z"
}
```

---

### 1.9 Opposition Radar - Credits

- **Path:** `GET /api/v1/leads/credits`
- **Response:**
```json
{
  "can_access": true,
  "plan": "professional",
  "daily_limit": 20,
  "used_today": 5,
  "remaining": 15
}
```

---

### 1.10 Holder Portfolio

- **Path:** `GET /api/v1/holders/{tpe_client_id}/trademarks`
- **Auth:** JWT required, Professional+ plan
- **Params:** `page`, `page_size` (default=20, max=100)

**SQL columns:** `id, application_no, name, current_status, nice_class_numbers, application_date, registration_date, image_path, bulletin_no, gazette_no, has_extracted_goods`

**Response:**
```json
{
  "holder_name": "ACME LTD",
  "holder_tpe_client_id": "12345",
  "total_count": 87,
  "page": 1,
  "page_size": 20,
  "total_pages": 5,
  "trademarks": [
    {
      "id": "uuid",
      "application_no": "2020/001",
      "name": "ACME BRAND",
      "status": "Registered",
      "classes": [9, 42],
      "application_date": "2020-03-15",
      "registration_date": "2021-01-10",
      "image_path": "acme.jpg",
      "has_extracted_goods": true
    }
  ]
}
```

---

### 1.11 Holder Search

- **Path:** `GET /api/v1/holders/search`
- **Auth:** JWT required, Professional+ plan
- **Params:** `query` (min 2 chars), `limit` (default=10, max=50)
- **Response:**
```json
{
  "query": "ACME",
  "results": [
    { "holder_name": "ACME LTD", "holder_tpe_client_id": "12345", "trademark_count": 87 }
  ]
}
```

---

### 1.12 Reports - Generate

- **Path:** `POST /api/v1/reports/generate`
- **Auth:** JWT required
- **Plan limits:** Free=1/mo, Starter=5, Pro=20, Enterprise=100
- **Request:** `{ report_type, title, file_format, date_range_start, date_range_end }`
- **Response:**
```json
{
  "id": "uuid",
  "organization_id": "uuid",
  "report_type": "watchlist_summary",
  "title": "Weekly Report",
  "status": "completed",
  "file_path": "/reports/uuid.pdf",
  "file_format": "pdf",
  "file_size_bytes": 45000,
  "generated_at": "2026-02-08T14:00:00Z",
  "created_at": "2026-02-08T14:00:00Z"
}
```

---

### 1.13 Reports - List

- **Path:** `GET /api/v1/reports`
- **Params:** `page`, `page_size` (default=20)
- **Response includes usage info:**
```json
{
  "reports": [ { "id": "...", "report_type": "...", "status": "...", ... } ],
  "total": 5,
  "page": 1,
  "page_size": 20,
  "total_pages": 1,
  "usage": { "reports_used": 3, "reports_limit": 20, "can_export": true }
}
```

---

### 1.14 Extracted Goods

- **Path:** `GET /api/v1/trademark/{application_no}/extracted-goods`
- **Response:**
```json
{
  "application_no": "2024/999",
  "name": "MYBRAND",
  "has_extracted_goods": true,
  "extracted_goods": [ ... ],
  "nice_classes": [25, 35],
  "total_items": 12
}
```

---

### 1.15 Dashboard Stats

- **Path:** `GET /api/v1/dashboard/stats`
- **Response:**
```json
{
  "watchlist_count": 15,
  "active_watchlist": 12,
  "total_alerts": 45,
  "new_alerts": 8,
  "critical_alerts": 3,
  "alerts_this_week": 5,
  "searches_this_month": 22,
  "plan_usage": {}
}
```

---

### 1.16 Usage Summary

- **Path:** `GET /api/v1/usage/summary`
- **Response:**
```json
{
  "plan": "professional",
  "display_name": "Professional",
  "usage": {
    "daily_quick_searches": { "used": 12, "limit": 100 },
    "monthly_live_searches": { "used": 3, "limit": 50 },
    "monthly_name_generations": { "used": 2, "limit": 15 },
    "logo_credits": { "remaining": 3, "limit": 3 },
    "watchlist_items": { "used": 8, "limit": 50 }
  }
}
```

---

### 1.17 Trademark Image

- **Path:** `GET /api/trademark-image/{image_path:path}`
- **Auth:** None (public)
- **Response:** Binary image file (FileResponse)
- **Lookup:** `bulletins/Marka/LOGOS/{image_path}.{ext}` (tries .jpg, .png, .gif, .webp)

---

### 1.18 Pipeline Status

- **Path:** `GET /api/v1/pipeline/status`
- **Auth:** admin/owner role
- **Response:**
```json
{
  "is_running": false,
  "current_step": null,
  "next_scheduled": "2026-02-09T03:00:00Z",
  "recent_runs": [
    {
      "id": "uuid",
      "status": "success",
      "step_download": { "status": "success", "count": 5 },
      "step_extract": { ... },
      "step_metadata": { ... },
      "step_embeddings": { ... },
      "step_ingest": { ... },
      "started_at": "...",
      "completed_at": "...",
      "duration_seconds": 3600
    }
  ]
}
```

---

## 2. Frontend Display

### 2.1 Overview Tab (`_results_panel.html`)

**Template:** `templates/partials/_results_panel.html`
**Layout:** max-w-7xl, grid lg:grid-cols-3 gap-8

#### Pipeline Status (Admin/Owner only)
- 5-step grid: Indirme / Cikarma / Metadata / Yapay Zeka / Yukleme
- Each step: number + name, bold count, status text
- Running spinner with current step name
- "Tam Calistir" and "Indirme Atla" buttons
- Last run info + next scheduled time

#### KPI Cards (4-column grid)
| Card | Icon | Label | Data Property |
|------|------|-------|---------------|
| Portfolio | building | "Portfolio Size" | `stats.total_watched` |
| Critical | warning | "Critical Risks" | `stats.high_risk_count` |
| Deadlines | clock | "Aktif Itiraz Sureleri" | `stats.pending_deadlines` |
| Activity | chart | "7-Day Activity" | `stats.recent_activity_count` |

#### Pre-Publication Banner
- `x-show="stats.pre_publication_count > 0"`
- Blue info icon + count + warning text

#### Recent Alerts List (left column, lg:col-span-2)
**Per alert (x-for):**
- **Risk score badge:** 16x16, rounded-lg, border-2, color via `getScoreColor(alert.risk_score)`
- **Brand name:** `alert.conflicting_brand` (lg, bold, gray-900) + app number badge
- **Deadline badge:** `renderDeadlineStatusBadge(alert)` - 8 status types with colors
- **Description:** "[brand] ile cakisma"
- **Score breakdown:** 4 badges (conditional, >0.3 threshold):
  - "Metin X%" (gray)
  - "Anlamsal X%" (gray)
  - "Gorsel X%" (gray)
  - "Ceviri X%" (blue - distinct color)
- **Extracted goods button:** amber badge if `alert.has_extracted_goods`
- **Pre-pub banner:** blue if `deadline_status === 'pre_publication'`
- **Date:** `alert.date` (text-xs, gray-400)
- **Action:** "Analiz ->" button (indigo-600)

#### Risk Distribution Chart
- Canvas element, Chart.js doughnut
- 5 categories: Kritik/Cok Yuksek/Yuksek/Orta/Dusuk

#### Deadlines Widget (right column)
**Per deadline:**
- Border-left-4: red if <10 days, orange otherwise
- Brand name, App No, Days Left (xl bold), Deadline Date
- "Itiraz Basvurusu" button
- **Empty state:** "Aktif itiraz suresi yok."

#### Portfolio/Watchlist Widget (right column)
**Per item (rendered by `renderPortfolioGrid()` in app.js):**
- `item.name`, `item.nice_class_numbers`, `item.has_logo`, `item.logo_url`
- `item.conflict_summary` badges: Erken Uyari / Kritik / Acil / Aktif / Suresi dolmus
- "En yakin: X gun kaldi" line

---

### 2.2 Search Results (rendered by `displayAgenticResults()` + `renderResultCard()`)

**Layout:** Vertical card list in search results container

**Banner:** Source type indicator
- "Canli Arama Sonuclari" (live) or "Veritabani Sonuclari" (database)
- Image analysis badge if `data.image_used === true`
- Sort dropdown

**Per result card (`renderResultCard()`):**
| Element | Field | Display |
|---------|-------|---------|
| Thumbnail | `r.image_path` | 48x48 rounded, SVG fallback on error |
| Name | `r.name` | font-semibold, gray-900, truncate |
| Status | `r.status` | text-sm, gray-500 |
| TURKPATENT button | `r.application_no` | Copy + external link (2 buttons) |
| Holder link | `r.holder_name` + `r.holder_tpe_client_id` | Clickable if Pro+, locked icon if Free/Starter |
| Score badges | `r.scores.*` | 4 badges (text/semantic/visual/translation), >0.3 threshold |
| Extracted goods | `r.has_extracted_goods` | Amber button "CIKARILMIS URUN: EVET" |
| AI Studio CTA | (if score >= 70%) | Name Lab button; Logo Studio if visual_similarity > 0.75 |
| Risk score | `r.scores.total * 100` | Right-aligned badge, color-coded |

**Pagination:** Previous/Next + "Sayfa X / Y" + "Toplam Z sonuc"

**Fields in API but NOT displayed:**
- `scores.phonetic_match` / `scores.phonetic_similarity`
- `scores.exact_match`
- `scores.containment`
- `scores.token_overlap` / `scores.weighted_overlap`
- `scores.distinctive_match` / `scores.semi_generic_match` / `scores.generic_match`
- `scores.distinctive_weight_matched` / `scores.semi_generic_weight_matched` / `scores.generic_weight_matched`
- `scores.matched_words[]` (full IDF word breakdown)
- `scores.text_idf_score`
- `scores.dynamic_weights`
- `scores.scoring_path`
- `r.bulletin_no`
- `r.classes` (Nice class numbers)

---

### 2.3 Opposition Radar Tab (`_leads_panel.html`)

**Layout:** max-w-7xl, vertical stack

**Header:** Title + credits badge (`Gunluk Hak: X`) + Export CSV button (Enterprise, hidden for others)

**Stats cards (4-column grid):**
| Card | Label | Element ID |
|------|-------|-----------|
| Critical | "Kritik (<=7 gun)" | stat-critical |
| Urgent | "Acil (<=14 gun)" | stat-urgent |
| Total | "Toplam Lead" | stat-total |
| Converted | "Donusturulen" | stat-converted |

**Filters:** Urgency, Risk, Nice Class (hardcoded 5 options), Status + Refresh button

**Per lead card (`renderLeadCard()`):**
| Element | Field | Display |
|---------|-------|---------|
| Urgency badge | `lead.urgency_level` | "Kritik" (red) / "Acil" (orange) / "Yakinda" (yellow) |
| Status badge | `lead.lead_status` | "Goruntulendi" (blue) / "Iletisim" (purple) |
| Score badge | `lead.similarity_score * 100` | Right-aligned, color-coded |
| Score breakdown | text/semantic/visual/translation | 4 badges, >0.3 threshold |
| New Application box | `new_mark_*` fields | Red-50 bg: thumbnail + name + app_no + holder + extracted goods |
| Existing Mark box | `existing_mark_*` fields | Green-50 bg: same layout |
| Footer | bulletin_no, conflict_type, overlapping_classes, opposition_deadline | Text-xs, gray-500 |

**Click:** Opens lead detail modal

**Empty state:** Magnifying glass icon + "Henuz lead bulunamadi."
**Upgrade prompt:** For non-Pro plans - locked view with upgrade CTA

---

### 2.4 AI Studio Tab (`_ai_studio_panel.html`)

**Two modes:** Name Lab / Logo Studio (toggle buttons)

**Name Lab inputs:** Brand Name/Concept, Nice Class, Industry, Style (Modern/Klasik/Eglenceli/Teknik)
**Name card (`renderNameCard()`):**
| Element | Field |
|---------|-------|
| Name | `name.name` |
| Safety badge | `name.is_safe` (green check / amber warning) |
| Risk level | `name.risk_level` (if not 'low') |
| Closest match | `name.closest_match` + max(text,semantic) similarity |
| Breakdown | text/semantic/phonetic/translation badges (>0.3) |
| Logo button | "Logo Olustur" (only if is_safe) |
| Score | `name.risk_score * 100` (right-aligned) |

**Logo Studio inputs:** Brand Name, Style, Visual Description, Color Preferences, Nice Class
**Logo card (`renderLogoCard()`):**
| Element | Field |
|---------|-------|
| Image | `logo.image_url` (loaded async with auth headers) |
| Safety badge | `logo.is_safe` (green/red) |
| Score | `logo.similarity_score * 100` |
| Closest match | `logo.closest_match_name` + image (if unsafe) |
| Visual breakdown | `logo.visual_breakdown` {clip, dino, ocr, color} |
| Download button | Download PNG |

---

### 2.5 Reports Tab (`_reports_panel.html`)

**Header:** Title + usage badge (`Kalan: X`) + "Rapor Olustur" button
**Report list:** Dynamically populated (id="reports-list")
**Empty state:** "Henuz rapor olusturulmadi."
**Upgrade prompt:** For plans without report access

**Generation modal:**
- Report Type: 5 options (Haftalik/Aylik/Portfolio/Tekli/Tam)
- Title (optional)
- Format: PDF / Excel
- Date Range: start + end date pickers

---

### 2.6 Modals (`_modals.html`)

| Modal | ID | Purpose | Actions |
|-------|----|---------|---------|
| Alert Detail | alert-detail-modal | Full alert view | Onayla / Cozuldu / Reddet |
| Opposition Filing | opposition-modal | Filing info | (content-only) |
| Lead Detail | lead-detail-modal | Full lead view | Iletisime Gecildi / Musteri Oldu / Reddet |
| Agentic Loading | agentic-loading-modal | Live search progress | Terminal log + progress bar + Cancel |
| Upgrade | upgrade-modal | Feature gate (403) | Belki Sonra / Yukselt |
| Credits Exhausted | credits-modal | Limit hit (402) | Sales contact / Plans / Kapat |
| Holder Portfolio | holderPortfolioModal | Holder's trademarks | Search + paginated list |
| Report Generate | report-generate-modal | Create new report | Iptal / Olustur |

**Holder Portfolio Modal details:**
- Header: gradient blue-purple with holder name
- Search bar for holder lookup
- Stats: Total / Registered / Pending counts
- Per trademark: thumbnail, name, status (Turkish), app_no, TURKPATENT button, date, classes (first 3 + ellipsis), extracted goods button

---

### 2.7 Navbar (`_navbar.html`)

- "IP Watchdog" logo
- Superadmin link (red, gear icon, conditional: `currentUserIsSuperadmin`)
- Client ID badge
- Refresh button

---

## 3. Data Comparison Matrix

Legend:
- API+FE = API returns AND frontend displays
- API = API returns but frontend does NOT display
- -- = Not available in this view
- GATED = API returns but gated by plan (locked/upgrade prompt)

| Field | DB Column | Quick Search | Intelligent | Watchlist List | Alerts | Leads | Holder Portfolio | Reports |
|---|---|---|---|---|---|---|---|---|
| Trademark name | `name` | API+FE | API+FE | API+FE (brand_name) | API+FE (conflicting.name) | API+FE (both marks) | API+FE | -- |
| Application number | `application_no` | API+FE | API+FE | API (customer_app_no) | API+FE (conflicting.app_no) | API+FE (both marks) | API+FE | -- |
| Registration number | -- | -- | -- | API (registration_no) | -- | -- | -- | -- |
| Logo/image | `image_path` | API+FE | API+FE | API+FE (has_logo) | API (conflicting.image_path) | API+FE (both marks) | API+FE | -- |
| Status | `current_status` | API+FE | API+FE | -- | API+FE (conflicting.status) | -- | API+FE | -- |
| Holder name | `holder_name` | API+FE | API+FE | -- | -- | API+FE (both marks) | API+FE (header) | -- |
| Holder TPE ID | `holder_tpe_client_id` | API+FE (plan-gated link) | API+FE (plan-gated) | -- | -- | -- | API+FE | -- |
| Nice classes | `nice_class_numbers` | API | API | API+FE | API (overlapping only) | API+FE (overlapping) | API+FE | -- |
| Application date | `application_date` | -- | -- | API (filing_date) | -- | -- | API+FE | -- |
| Registration date | `registration_date` | -- | -- | -- | -- | -- | API | -- |
| Overall score | `scores.total` | API+FE | API+FE | -- | API+FE (risk_score) | API+FE (similarity_score) | -- | -- |
| Text similarity | `scores.text_similarity` | API+FE (>0.3) | API+FE (>0.3) | -- | API+FE (>0.3) | API+FE (>0.3) | -- | -- |
| Semantic similarity | `scores.semantic_similarity` | API+FE (>0.3) | API+FE (>0.3) | -- | API+FE (>0.3) | API+FE (>0.3) | -- | -- |
| Visual similarity | `scores.visual_similarity` | API+FE (>0.3) | API+FE (>0.3) | -- | API+FE (>0.3) | API+FE (>0.3) | -- | -- |
| Translation similarity | `scores.translation_similarity` | API+FE (>0.3, blue) | API+FE (>0.3, blue) | -- | API+FE (>0.3, blue) | API+FE (>0.3) | -- | -- |
| Phonetic match | `scores.phonetic_match` | API | API | -- | API | -- | -- | -- |
| Exact match | `scores.exact_match` | API | API | -- | -- | -- | -- | -- |
| Containment | `scores.containment` | API | API | -- | -- | -- | -- | -- |
| Token overlap | `scores.token_overlap` | API | API | -- | -- | -- | -- | -- |
| Weighted overlap | `scores.weighted_overlap` | API | API | -- | -- | -- | -- | -- |
| Distinctive match | `scores.distinctive_match` | API | API | -- | -- | -- | -- | -- |
| IDF matched words | `scores.matched_words[]` | API | API | -- | -- | -- | -- | -- |
| Dynamic weights | `scores.dynamic_weights` | API | API | -- | -- | -- | -- | -- |
| Scoring path | `scores.scoring_path` | API | API | -- | -- | -- | -- | -- |
| Risk level | computed | API+FE (color) | API+FE (color) | -- | API+FE (severity) | API+FE (risk_level) | -- | -- |
| Bulletin number | `bulletin_no` | API | API | API | API+FE | API+FE | API | -- |
| Bulletin date | `bulletin_date` | -- | -- | -- | API+FE | API+FE | -- | -- |
| Appeal deadline | `appeal_deadline` | -- | -- | FE (via conflict_summary) | API+FE | API+FE (opposition_deadline) | -- | -- |
| Deadline status | computed | -- | -- | FE (conflict_summary badges) | API+FE | API+FE (urgency_level) | -- | -- |
| Days remaining | computed | -- | -- | FE (nearest_deadline_days) | API+FE | API+FE | -- | -- |
| Extracted goods | `extracted_goods` | API+FE (bool+button) | API+FE (bool+button) | -- | API+FE (bool+button) | API+FE (bool+button) | API+FE (bool+button) | -- |
| Has logo | computed | -- | -- | API+FE | -- | -- | -- | -- |
| Alert count | computed | -- | -- | API+FE (new + total) | -- | -- | -- | -- |
| Conflict summary | computed | -- | -- | API+FE | -- | -- | -- | -- |
| Last scan time | `last_scan_at` | -- | -- | API | -- | -- | -- | -- |
| Scrape triggered | response-only | API+FE (banner) | API+FE (banner) | -- | -- | -- | -- | -- |
| Credits remaining | response-only | -- | API+FE | -- | -- | -- | -- | -- |
| Image used | response-only | -- | API+FE (badge) | -- | -- | -- | -- | -- |
| Conflict type | `universal_conflicts.conflict_type` | -- | -- | -- | -- | API+FE | -- | -- |
| Conflict reasons | `universal_conflicts.conflict_reasons` | -- | -- | -- | -- | API (detail modal only) | -- | -- |
| Lead status | `universal_conflicts.lead_status` | -- | -- | -- | -- | API+FE | -- | -- |
| Translations (TR/EN/KU/FA) | `name_tr/en/ku/fa` | API (used in scoring) | API (used in scoring) | -- | -- | -- | -- | -- |
| Name (detected language) | `detected_lang` | -- | -- | -- | -- | -- | -- | -- |

---

## 4. Inconsistencies Found

### 4.1 Mixed Language Labels
- **Results panel:** Mix of English and Turkish KPI labels: "Portfolio Size", "Critical Risks" (English) alongside "Aktif Itiraz Sureleri" (Turkish)
- **Deadlines widget:** "Action Required" header is English, "Opposition" badge is English, but content is Turkish
- **Score breakdown:** "Metin", "Anlamsal", "Gorsel", "Ceviri" (Turkish) but only in alerts; lead cards have same Turkish labels

### 4.2 Score Threshold Display
- **Score breakdown badges:** Show only if > 0.3 across all views (consistent)
- **Translation badge:** Blue color only in `renderSimilarityBadges()` (score-badge.js) and alerts panel. Lead cards use same style (consistent).

### 4.3 Field Naming Across Views
| Concept | Search API | Alerts API | Leads API | Holder API |
|---------|-----------|-----------|----------|-----------|
| Total score | `scores.total` | `overall_risk_score` | `similarity_score` | -- |
| Status | `status` | `conflicting_status` → `conflicting.status` | -- | `current_status` → `status` |
| Classes | `classes` | `conflicting_classes` → `conflicting.classes` | `new_mark_nice_classes` | `nice_class_numbers` → `classes` |
| Holder | `holder_name` | `conflicting_holder_name` → `conflicting.holder` | `new_mark_holder_name` | `holder_name` |
| Image | `image_path` | `conflicting_image_path` → `conflicting.image_path` | `new_mark_image` | `image_path` |

### 4.4 Nice Classes Display
- **Search results:** Nice classes returned in API (`r.classes`) but NOT displayed in result cards
- **Lead cards:** Overlapping classes shown in footer
- **Holder portfolio:** Classes shown (first 3 + ellipsis)
- **Watchlist list:** Classes shown

### 4.5 Status Display
- **Search results:** Shows raw English status ("Registered", "Published")
- **Holder portfolio:** Translates to Turkish via `getStatusText()` ("Tescilli", "Yayinda")
- **Alerts:** Shows conflicting status (English, from API)

### 4.6 Date Formatting
- **Holder portfolio:** Turkish locale `formatHolderDate()` → "01.02.2026"
- **Alerts:** Raw `alert.date` shown as-is (text-xs)
- **Leads:** `opposition_deadline` shown in footer (raw format)
- **Deadlines widget:** `d.appeal_deadline` shown raw

---

## 5. UX Issues

### 5.1 Missing Data
1. **Nice classes not shown in search results** - API returns `r.classes` but `renderResultCard()` never displays them. Users can't see class overlap without opening holder portfolio.
2. **Phonetic match not displayed** - API returns `phonetic_match: true/false` but no badge shown. This is a significant similarity signal users would want to see.
3. **Scoring path not exposed** - The IDF scoring path (e.g., "A: High distinctive match") explains WHY a score is what it is, but is never shown.
4. **Matched words not displayed** - The `matched_words[]` array with IDF weights explains exactly which words caused the match. Not shown anywhere.
5. **Registration date not shown in holder portfolio** - API returns it but frontend doesn't display it.
6. **Translations (name_tr/en/ku/fa) not displayed** - Used internally for scoring but never shown to user, who might benefit from seeing the detected translation.
7. **Alert images not shown** - `conflicting.image_path` returned by alerts API but alert list doesn't render thumbnails.
8. **Last scan time** - Returned in watchlist API but not displayed in portfolio widget.

### 5.2 Inconsistency
1. **Mixed English/Turkish** - KPI cards, widget headers, and labels inconsistently use English vs Turkish.
2. **Status translation** - Holder portfolio translates status to Turkish; search results show English status.
3. **Score field naming** - `scores.total`, `overall_risk_score`, `similarity_score` all mean the same thing across views.
4. **Nice class display** - Shown in leads + holder + watchlist but NOT in search results.
5. **Date formatting** - No consistent date formatting across views.

### 5.3 Clutter
1. **Dynamic weights / scoring_path / text_idf_score** - Only useful for debugging, returned in every search result but never shown. Adds ~200 bytes per result.
2. **Full matched_words array** - Detailed IDF breakdown per word, useful for expert users but potentially overwhelming if displayed raw.

### 5.4 Missing Context
1. **Score percentage with no explanation** - A "78%" badge tells users there's a risk but not what kind (text? visual? phonetic?). The breakdown badges help but are small and only shown if > 0.3.
2. **Deadline without filing guidance** - Deadlines show "X days left" but no explanation of what the deadline means or how to file an opposition.

### 5.5 Broken References
- No broken references found. All fields accessed by JS exist in API responses.

### 5.6 Empty States
| View | Empty State | Consistent? |
|------|------------|-------------|
| Alerts list | No explicit empty state (just empty container) | Missing |
| Deadlines widget | "Aktif itiraz suresi yok." | OK |
| Lead feed | Icon + "Henuz lead bulunamadi." | OK |
| Reports | Icon + "Henuz rapor olusturulmadi." | OK |
| Portfolio | "Yukleniyor..." (loading only, no empty) | Missing |
| AI Studio names | "Tum olusturulan isimler cakisma iceriyor." | OK |
| Search results | No explicit empty state | Missing |

### 5.7 Loading States
| View | Loading State | Consistent? |
|------|--------------|-------------|
| Lead feed | Spinner + "Leadler yukleniyor..." | OK |
| Reports | Spinner + "Raporlar yukleniyor..." | OK |
| AI Studio names | 2x2 skeleton cards + text | OK |
| AI Studio logos | 2x2 skeleton cards + text | OK |
| Holder portfolio | Spinner + "Portfolio yukleniyor..." | OK |
| Search results | No dedicated loading state (button disabled) | Inconsistent |
| Alerts | No loading state | Missing |

### 5.8 Error States
| View | Error State | Consistent? |
|------|------------|-------------|
| AI Studio names | "Isim olusturma servisi kullanilamiyor" | OK |
| AI Studio logos | "Logo olusturma basarisiz oldu" | OK |
| Holder portfolio | Warning emoji + error message | OK |
| Search | Toast notification only | Inconsistent |
| Alerts | No error state | Missing |
| Leads | No error state | Missing |
| Reports | No error state | Missing |

### 5.9 Action Button Consistency
- **"Add to watchlist"** - NOT available from search results. Users must manually go to watchlist and add items.
- **TURKPATENT button** - Available in: search results, lead cards, holder portfolio. NOT in: alerts list (only in detail modal via "Analiz ->").
- **Extracted goods button** - Consistent across search results, alerts, leads, holder portfolio.

### 5.10 Plan Gating UX
- **Holder portfolio link** - Locked with icon for Free/Starter, clickable for Pro+. Clear indication.
- **Live search** - 403 → upgrade modal. 402 → credits modal. Clear.
- **Leads tab** - Shows upgrade prompt with feature list for non-Pro. Clear.
- **CSV export** - Hidden button on non-Enterprise plans. No indication that it exists.
- **Reports** - Upgrade prompt for non-eligible plans. Clear.

---

## 6. Trademarks Table Full Schema

```sql
CREATE TABLE trademarks (
    -- Primary Key
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Unique Identifier
    application_no       VARCHAR(255) UNIQUE NOT NULL,

    -- Basic Info
    name                 TEXT,
    current_status       tm_status DEFAULT 'Published',
    -- tm_status ENUM: 'Applied','Published','Opposed','Registered','Refused',
    --   'Withdrawn','Transferred','Renewed','Partial Refusal','Expired','Unknown'

    -- Holder Info
    holder_name          VARCHAR(500),
    holder_tpe_client_id VARCHAR(50),

    -- Classification
    nice_class_numbers   INTEGER[],
    extracted_goods      JSONB,

    -- Dates
    application_date     DATE,
    registration_date    DATE,
    last_event_date      DATE,
    bulletin_date        DATE,
    gazette_date         DATE,
    appeal_deadline      DATE,
    expiry_date          DATE,

    -- References
    bulletin_no          VARCHAR(255),
    gazette_no           VARCHAR(255),
    image_path           TEXT,

    -- AI Embeddings
    image_embedding      halfvec(512),    -- CLIP ViT-B-32
    dinov2_embedding     halfvec(768),    -- DINOv2 ViT-B/14
    text_embedding       halfvec(384),    -- MiniLM-L12-v2
    color_histogram      halfvec(32),     -- RGB histogram
    logo_ocr_text        TEXT,            -- EasyOCR extracted text

    -- Translations (NLLB-200-distilled-600M)
    name_tr              VARCHAR(500),
    name_en              VARCHAR(500),
    name_ku              VARCHAR(500),
    name_fa              VARCHAR(500),
    detected_lang        VARCHAR(10),

    -- Source Authority (APP=3 highest, GZ=2, BLT=1 lowest)
    status_source        VARCHAR(10),     -- 'APP', 'GZ', 'BLT'
    availability_status  VARCHAR(50),

    -- Timestamps
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX idx_tm_name_trgm      ON trademarks USING GIST (name gist_trgm_ops);
CREATE INDEX idx_tm_holder_tpe_id  ON trademarks(holder_tpe_client_id);
CREATE INDEX idx_tm_holder_name    ON trademarks(holder_name);
-- Plus HNSW indexes on image_embedding, text_embedding, dinov2_embedding
```

**Total columns: 28** (excluding indexes/constraints)

---

*End of audit. No code changes made.*
