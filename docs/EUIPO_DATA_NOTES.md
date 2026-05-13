# EUIPO Trademark Search API — Data Notes

Source: OpenAPI 3.0 spec, version 1.1.0 (`x-api-id: 7f7ceff3-627d-4c9d-8333-069dbd112bb0`) + empirical spike against the sandbox on 2026-05-13.

Status: Stage B (planning). No production code depends on this yet.

## 1. Auth

OAuth 2.0 Client Credentials grant (anonymous identity — sufficient for public search/dissemination data).

| Field | Value |
|---|---|
| Token endpoint (sandbox) | `https://auth-sandbox.euipo.europa.eu/oidc/accessToken` |
| Grant type | `client_credentials` |
| **Required scope** | `uid` |
| Authentication | HTTP Basic with `EUIPO_API_KEY:EUIPO_API_SECRET` |
| Token lifetime | **7200 seconds (2h)** — cache and refresh proactively |
| Token type | Bearer (JWT, issuer `auth-sandbox.euipo.europa.eu/t/euipo.europa.eu/oauth2/token`) |

### Required headers on every API call
- `Authorization: Bearer <access_token>`
- `X-IBM-Client-Id: <EUIPO_API_KEY>` (gateway-level credential, in addition to the Bearer token)
- `Accept: application/json`

### Alternative flow (not used in v1)
The spec also exposes an `authorizationCode` flow (scope `trademark-search.trademarks.read`). That returns *full* trademark data including applicant info during the pre-publication phase. For our bulk-harvest case (public published records only), `client_credentials` is the correct choice and applicant info is still returned for the post-publication records that make up >99% of the corpus.

## 2. Endpoints

Base URL (sandbox): `https://api-sandbox.euipo.europa.eu/trademark-search`

| Verb | Path | Purpose |
|---|---|---|
| GET | `/trademarks` | Search (paginated list) |
| GET | `/trademarks/{applicationNumber}` | Full record detail |
| GET | `/trademarks/{applicationNumber}/image` | Trademark image (binary, JPG/TIFF) |
| GET | `/trademarks/{applicationNumber}/image/thumbnail` | Image thumbnail |
| GET | `/trademarks/{applicationNumber}/sound` | Sound mark (MP3) |
| GET | `/trademarks/{applicationNumber}/video` | Video mark (MP4) |
| GET | `/trademarks/{applicationNumber}/model` | 3D model mark (OBJ/X3D/STL) |

Production base URL (unconfirmed, by parallel naming): `https://api.euipo.europa.eu/trademark-search`. Sandbox is sufficient for development; flip via env var when ready for prod.

## 3. Pagination

| Param | Rule |
|---|---|
| `size` | **min 10**, max 100, default 10 |
| `page` | zero-indexed, default 0 |

Response top-level fields: `trademarks[]`, `size`, `page`, `totalElements`, `totalPages`.

**Important**: `size=1` is rejected — minimum is 10. Use `size=100` for harvest.

## 4. Query syntax (RSQL)

`query` parameter accepts URL-friendly RSQL expressions. Max length 5000 chars.

Operators: `==`, `!=`, `<`, `<=`, `>`, `>=`, `=in=`, `=out=`, `=all=`. Combine with `and` / `or`.

**Date format**: `yyyy-MM-dd` (e.g. `2023-05-04`).

### Filterable fields most relevant to us
| Field | Operators | Use |
|---|---|---|
| `applicationNumber` | `==`, `!=` | Lookup by ID |
| `applicationDate` | range | **Backfill window walking** |
| `registrationDate` | range | — |
| `expiryDate` | range | Expiry-driven alerts |
| `updateDate` | `<`, `<=`, `>`, `>=` | **Delta harvesting** |
| `status` | `==`, `=in=` | Filter by lifecycle stage |
| `niceClasses` | `=in=`, `=out=`, `=all=` | Class-scoped queries |
| `markFeature` | `==`, `=in=` | WORD / FIGURATIVE / etc. |
| `applicants.identifier` | `==`, `=in=` | Holder portfolio |
| `applicants.name` | `==` (with `*` wildcard) | Holder name search |
| `wordMarkSpecification.verbalElement` | `==` (with `*`) | Mark-name search |

### Sortable fields
`applicationNumber`, `applicationDate`, `applicationReference`, `designationDate`, `registrationDate`, `markFeature`, `wordMarkSpecification.verbalElement`, `updateDate`, `sectionA1PublicationDate`. Syntax `field:asc` or `field:desc`.

### Example queries
- `updateDate>=2026-05-10` — delta since Sunday
- `applicationDate>=1996-01-01 and applicationDate<1996-04-01` — Q1 1996 backfill slice
- `status==REGISTERED and niceClasses=in=(25,28)` — registered marks in classes 25 or 28
- `applicants.name==*Coca-Cola*` — wildcard owner match

## 5. Rate limits

Empirically observed headers on every response:
- `X-RateLimit-Limit: name=default,25000;` — 25,000 calls per (period TBD, likely 24h based on plan naming)
- `X-RateLimit-Remaining: name=default,N;` — decrements per request
- `X-RateLimit-Reset: <seconds>` — present near reset
- `Retry-After: <seconds>` — on 429

**Plan**: "Default Plan" (free tier). Open question: is the 25k limit hourly, daily, or per-month? Will confirm during Stage C by observing reset behaviour, or by consulting the EUIPO subscription dashboard.

## 6. Corpus size (sandbox, 2026-05-13)

| Metric | Value |
|---|---|
| Total trademarks | **2,354,583** |
| Updated in last 3 days | **1,090** |
| Avg updates/day | **~360** |
| Pages to harvest at size=100 | **~23,546** |

## 7. Response shapes

### `/trademarks` (search) — `TrademarkSearchResultItem`
Lean summary, suitable for indexing:
- `applicationNumber` (string, 9 digits or `W########[A-Z]?`)
- `markFeature` enum (WORD, FIGURATIVE, SHAPE_3D, COLOUR, SOUND, HOLOGRAM, POSITION, PATTERN, MOTION, MULTIMEDIA, OTHER)
- `markKind` (INDIVIDUAL, EU_COLLECTIVE, EU_CERTIFICATION)
- `markBasis` (EU_TRADEMARK, INTERNATIONAL_TRADEMARK)
- `wordMarkSpecification.verbalElement` (when applicable)
- `applicants[]` — `{office, identifier, name}` (office EM=EUIPO, WO=WIPO)
- `representatives[]` — same shape
- `applicationDate`, `registrationDate`, `designationDate`, `expiryDate`
- `niceClasses[]` (int 1–45)
- `publications[]` — `{bulletinNumber, publicationDate, publicationSection}`
- `status` (see §8)

### `/trademarks/{applicationNumber}` (detail) — `Trademark`
Adds (relative to search): `applicationLanguage`, `secondLanguage`, `goodsAndServices[]` (multilingual descriptions per Nice class), `description`, `disclaimer`, `markImage`, `markSound`, `markVideo`, `markModel`, `priorities[]`, `seniorities[]`, `exhibitionPriorities[]`, `irTransformations[]`, `oppositions[]`, `cancellations[]`, `records[]`, `appeals[]`, `internationalApplications[]`, `inspectionRequests[]`, `decisions[]`, `statusDate`, `renewalStatus`, `fastTrackIndicator`, `oppositionPeriodStartDate`, `oppositionPeriodEndDate`, `tradeDistinctivenessIndicator`.

## 8. Status vocabulary (EUTM)
`RECEIVED`, `UNDER_EXAMINATION`, `APPLICATION_PUBLISHED`, `REGISTRATION_PENDING`, `REGISTERED`, `WITHDRAWN`, `REFUSED`, `OPPOSITION_PENDING`, `APPEALED`, `CANCELLATION_PENDING`, `CANCELLED`, `SURRENDERED`, `EXPIRED`, `APPEALABLE`, `START_OF_OPPOSITION_PERIOD`, `ACCEPTANCE_PENDING`, `ACCEPTED`, `REMOVED_FROM_REGISTER` (last 4 mainly for IR/international apps).

**Mapping to TR `tm_status` enum** (preliminary — finalize in ingest stage):
- `REGISTERED` → `Tescil Edildi`
- `APPLICATION_PUBLISHED` → `Yayında`
- `OPPOSITION_PENDING` → `İtiraz Edildi`
- `REFUSED` → `Reddedildi`
- `WITHDRAWN`, `SURRENDERED` → `Geri Çekildi`
- `EXPIRED`, `REMOVED_FROM_REGISTER` → `Süresi Doldu`
- `CANCELLED` → `İptal Edildi`
- `RECEIVED`, `UNDER_EXAMINATION`, `REGISTRATION_PENDING`, `ACCEPTANCE_PENDING`, `ACCEPTED` → `Başvuruldu`
- `APPEALED`, `APPEALABLE`, `CANCELLATION_PENDING`, `START_OF_OPPOSITION_PERIOD` → likely need 1–2 new TR enum values, or map to closest existing.

## 9. Harvest strategy implications

### Backfill (one-time, all historical EUTMs)
- Walk by `applicationDate` window. Year-month slices are convenient (`applicationDate>=1996-01-01 and applicationDate<1996-02-01`).
- Sort by `applicationNumber:asc` for stable pagination within each window.
- At `size=100` and ~360k records/year (estimated from total/30 years), each year ≈ 3,600 pages = 1 page per ~100 records. Full backfill ≈ 23,546 pages.
- Within a 25,000/day budget (if daily), full backfill in ~1 day. If hourly, still under a week.

### Delta (daily)
- `updateDate>=<yesterday>` sorted by `applicationNumber:asc`.
- Observed ~360 updates/day = 4 pages at `size=100`. Cost negligible.
- Run daily at low-traffic hour.

### Disk layout (mirrors `data_collection_patent.py` pattern)
```
bulletins/Marka_EU/
├── BACKFILL_1996-01/
│   ├── page_0001.json   (raw API response, includes 100 records + pagination metadata)
│   ├── page_0002.json
│   └── manifest.json    {window, page_count, total_records, completed_at, request_params}
├── BACKFILL_1996-02/
│   └── ...
└── DELTA_2026-05-12/
    ├── page_0001.json
    └── manifest.json
```

### Idempotency
- A window is "complete" iff `manifest.json` exists AND on-disk `page_*.json` count == `manifest.page_count`.
- Re-runs skip complete windows. Partial windows resume from next missing page.

## 10. Field mapping to existing `trademarks` table
*Preliminary; finalize during the ingest stage, not part of Stage C.*

| EUIPO API field | TR `trademarks` column | Notes |
|---|---|---|
| `applicationNumber` | `application_no` | + new `jurisdiction='EU'` column |
| `wordMarkSpecification.verbalElement` | `name` (and `name_en`) | EU records are multilingual |
| `markFeature` | new column or extend existing |
| `markKind` | new column |
| `applicants[].name` | `holder_name` / `holders.name` | Dedup against existing TR holders by `(name, country)` |
| `applicants[].identifier` | new `holder_euipo_id` column | Distinct namespace from `tpe_client_id` |
| `representatives[].name` | `attorney_name` |
| `applicationDate` | `application_date` |
| `registrationDate` | `registration_date` |
| `expiryDate` | `expiry_date` |
| `niceClasses[]` | `nice_class_numbers` (int[]) | Direct |
| `status` | `current_status` | Via mapping in §8 |
| `publications[]` (first A.1) | `bulletin_no` + `bulletin_date` | A.1 = application publication |
| `publications[]` (B.1 or B.2) | (use for `registration` event) |
| `oppositions[]`, `cancellations[]`, `appeals[]` | → `trademark_events` rows | With `source_type='EUIPO'`, `jurisdiction='EU'` |
| `markImage` (binary) | download to `images/` | Same image_path pattern as TR |

## 11. Open questions to confirm in Stage C
1. **Rate limit period** — 25k per hour/day/month? Will observe `X-RateLimit-Reset` during real run.
2. **Production base URL** — sandbox token endpoint accepted production-looking credentials, but our subscription portal is likely production. Need to verify `auth.euipo.europa.eu/oidc/accessToken` works.
3. **Sandbox vs production data parity** — sandbox returned real EUTMs (John Player & Sons etc.), suggesting it's a snapshot of production. Confirm corpus equivalence or whether prod has a fresher delta tail.
4. **Image binary handling** — image endpoint streams binary. Decide v1 strategy: skip images during backfill, download lazily on user request (DesignView-style), or batch-download per window. The OpenAPI spec sets no rate-limit class for images — check whether they count against the main 25k bucket.
5. **`applicants` populated in client-credentials flow?** — Spec says "applicant info hidden until basic fee paid" under anonymous flow. Spike showed `applicants[0].name = "John Player & Sons Limited"` — populated for published records. Confirm whether application-stage records hide applicant.

## 12. Spike artifacts to clean up before Stage C
- Delete `scripts/euipo_spike_output/` (contains JWT tokens, ~2h lifetime but still secrets).
- Delete `scripts/euipo_api_spike.py` once `data_collection_eutm.py` lands.
- Add `scripts/euipo_spike_output/` to `.gitignore` belt-and-braces (it's under `scripts/` so not auto-ignored).

## 13. References
- OpenAPI spec: `Trademark search 1.1.0` (from API portal, copied locally during Stage B — not committed)
- Product page: https://dev.euipo.europa.eu/product/trademark-search_110
- Nice Classification: https://euipo.europa.eu/ohimportal/en/nice-classification
- Vienna Classification: https://euipo.europa.eu/ohimportal/en/vienna-classification
- EUTM Bulletin Vademecum: https://euipo.europa.eu/pdf/mark/vademecum-ctm-en.pdf
