# TIER 2: Wire 13 Critical Missing Fields — Results

## Summary

All 13 missing fields have been wired in the frontend. No backend changes were needed — the API already returns these fields; the frontend now reads and displays them.

## Fix Details

| Fix # | Field | File:Line | What was added | Status |
|-------|-------|-----------|---------------|--------|
| 1 | `severity` | app.js:486 (helper), app.js:135 (data map), _results_panel.html:232 (list), app.js:297 (detail) | Severity badge (Critical/High/Medium/Low) in alert list + detail modal | Done |
| 2 | `conflicting.status` | app.js:310 | Status badge (Published/Registered/etc.) next to conflicting brand name in alert detail | Done |
| 3 | `conflicting.classes` | app.js:316 | Full Nice class badges of conflicting mark in alert detail | Done |
| 4 | `scores.phonetic_match` | app.js:300 | Phonetic Match badge below similarity scores in alert detail | Done |
| 5 | `watchlist_classes` | app.js:305 | Watched brand Nice class badges in alert detail | Done |
| 6 | `by_status` | app.js:480+ (renderChart), _results_panel.html:206 (container) | Status breakdown (New/Acknowledged/Resolved/Dismissed) below risk chart | Done |
| 7 | `upcoming_leads` | api.js:155, _leads_panel.html:79+ | 5th stat card with clock icon in leads panel | Done |
| 8 | `avg_similarity` | api.js:160+ , _leads_panel.html:79 | "Avg. Similarity: XX%" indicator above urgency bar | Done |
| 9 | `similarity_threshold` | app.js:2308 (renderPortfolioGrid) | "Threshold: XX%" label on watchlist cards | Done |
| 10 | `total_alerts_count` | app.js:2278 (renderPortfolioGrid) | "N total alerts" text alongside new alerts badge on watchlist cards | Done |
| 11 | `registration_date` | app.js:1254 (renderEntityTrademarks) | "Registration: YYYY-MM-DD" below application date in portfolio modal | Done |
| 12 | `source_reference`, `conflict_bulletin_no` | app.js:329-330 | Bulletin/Source references in alert detail metadata line | Done |
| 13 | `acknowledged_at`, `resolved_at`, `resolution_notes` | app.js:332-348 | Resolution timeline with colored dots and timestamps at bottom of alert detail | Done |

## i18n Keys Added (21 total)

All keys added to `en.json`, `tr.json`, and `ar.json`:

| Key | EN | TR | AR |
|-----|----|----|-----|
| `alerts.severity_critical` | Critical | Kritik | حرج |
| `alerts.severity_high` | High | Yuksek | عالي |
| `alerts.severity_medium` | Medium | Orta | متوسط |
| `alerts.severity_low` | Low | Dusuk | منخفض |
| `alerts.conflict_classes` | All Classes | Tum Siniflar | جميع الفئات |
| `alerts.phonetic_match` | Phonetic Match | Fonetik Eslesme | تطابق صوتي |
| `alerts.watched_classes` | Watched Classes | Izlenen Siniflar | الفئات المراقبة |
| `alerts.status_new` | New | Yeni | جديد |
| `alerts.status_acknowledged` | Acknowledged | Onaylandi | تم الاطلاع |
| `alerts.status_resolved` | Resolved | Cozuldu | تم الحل |
| `alerts.status_dismissed` | Dismissed | Reddedildi | مرفوض |
| `alerts.bulletin` | Bulletin | Bulten | النشرة |
| `alerts.source_ref` | Source | Kaynak | المصدر |
| `alerts.resolution_timeline` | Resolution History | Cozum Gecmisi | سجل الحل |
| `alerts.acknowledged_at` | Acknowledged | Onaylandi | تم الاطلاع |
| `alerts.resolved_at` | Resolved | Cozuldu | تم الحل |
| `leads.stat_upcoming` | Upcoming | Yaklasan | قادم |
| `leads.avg_similarity` | Avg. Similarity | Ort. Benzerlik | متوسط التشابه |
| `watchlist.threshold` | Threshold | Esik | الحد الأدنى |
| `watchlist.total_alerts` | total alerts | toplam uyari | إجمالي التنبيهات |
| `holder.registration_date` | Registration | Tescil | التسجيل |

## Files Modified

| File | Changes |
|------|---------|
| `static/js/app.js` | +renderSeverityBadge helper, severity in data maps, all alert detail fields, watchlist card fields, portfolio registration_date, by_status chart breakdown |
| `static/js/api.js` | +upcoming_leads stat wiring, +avg_similarity indicator |
| `templates/partials/_results_panel.html` | +severity badge in alert list, +alert-status-breakdown container |
| `templates/partials/_leads_panel.html` | +upcoming stat card (5th), +avg-similarity indicator, grid changed to 5-col |
| `static/locales/en.json` | +21 i18n keys |
| `static/locales/tr.json` | +21 i18n keys |
| `static/locales/ar.json` | +21 i18n keys |

## Validation Results

- **JS Syntax**: `node --check` passed for both `app.js` and `api.js`
- **JSON Validation**: All 3 locale files are valid JSON (32 top-level keys each)
- **i18n Coverage**: 21/21 keys present in all 3 locales (en, tr, ar)
- **Deployment**: All 7 files copied to `ipwatch_backend` container via `docker cp`
- **Container Verification**: 23 field references confirmed in deployed app.js
