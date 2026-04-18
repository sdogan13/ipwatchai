# Trademark Events System — Implementation Plan

## Goal
Extract ALL supplementary sections from Turkish Patent bulletin PDFs (both **GZ** gazette and **BLT** bulletin) and store them as structured events in the database. This enables tracking the full lifecycle of a trademark (transfers, seizures, licenses, court decisions, corrections, etc.) and triggering watchlist alerts on relevant changes.

---

## PDF Format Audit Results (2026-03-27)

### Two bulletin types with different roles

| Property | BLT (Bülten) | GZ (Gazete) |
|----------|-------------|-------------|
| **Purpose** | Application publications (pre-registration) | Registered trademarks + post-registration events |
| **Title** | "Resmi Marka Bülteni" | "Marka Gazetesi Bülteni" |
| **Size** | 3,000–8,000 pages | 10,000–15,000+ pages |
| **Events section** | "Marka Bülteni Şerhleri" | "Tescilli Markalar Üzerindeki İşlemlere İlişkin İlanlar" |
| **Has (111) reg no** | No | Yes |
| **Has transfers** | No | Yes (DEVİR, BİRLEŞME, KISMİ DEVİR) |
| **Has licenses** | No | Yes (LİSANS KAYDI) |
| **Has cancellations** | No | Yes (İPTAL EDİLENLER) |
| **Has Madrid** | Yes (English WIPO format) | No |
| **Renewals** | No | Yes (section 5) |
| **Available range** | BLT_119–488 (~2005–2026) | GZ_421–499 (~2017–2026) |

### Three PDF format eras

| Era | Bulletin range | TOC? | PDF naming | Parseable? |
|-----|---------------|------|------------|------------|
| **Era 1**: Pre-2012 | BLT_119–200 | No | Multi-PDF split by page range (`128_1-1306.pdf`) | Hard — defer |
| **Era 2**: 2012–2016 | BLT_200–288 | No | Single PDF, custom naming (`ulusal.pdf`) | Medium — scan for headers |
| **Era 3**: 2017–2026 | BLT_289–488, GZ_421–499 | Yes | Single `bulletin.pdf` | Easy — start here |

### Event sub-sections found in each bulletin type

**GZ (Gazette) — Section 3: "Tescilli Markalar Üzerindeki İşlemler"**

| Sub-section | Event Type | Format |
|-------------|-----------|--------|
| BİRLEŞME (Merger) | `merger` | `(210)...(566) name` + `Devreden:` / `Devralan(lar):` + address |
| DEVİR (Transfer) | `transfer` | Same as merger format |
| KISMİ DEVİR (Partial Transfer) | `partial_transfer` | Same + goods detail text |
| EŞYA SINIRLANDIRMA (MAHKEME KARARI İLE) | `goods_limitation_court` | `(210)...(566) name` |
| HACİZ KONULANLAR (Seizures Imposed) | `seizure` | `(210)...(566) name` + `Esas No:` court + date |
| İHTİYATİ HACİZ KONULANLAR (Precautionary Seizure) | `precautionary_seizure` | Same as seizure |
| TEDBİR KONULANLAR (Injunctions) | `injunction` | Same as seizure |
| İHTİYATİ TEDBİR KONULANLAR (Precautionary Injunction) | `precautionary_injunction` | Same as seizure |
| İFLAS İLANI (Bankruptcy) | `bankruptcy` | `(210)...(566) name` |
| LİSANS KAYDI (License Registration) | `license` | `(210)...(566) name` + `Esas No:` court |
| MAL HİZMET SINIRLANDIRMA (Goods Limitation) | `goods_limitation` | `(210)...(566) name` |
| İPTAL EDİLENLER (MAHKEME KARARI İLE) (Cancellations) | `cancellation` | `(210)...(566) name` + `Esas No:` court |
| İŞLEMDEN ÇEKİLEN BAŞVURULAR (Withdrawals) | `withdrawal` | `(210)...(566) name` |

**GZ — Section 4: Düzeltmeler** → `correction` (free-text prose)
**GZ — Section 5: Yenilenen Markalar** → `renewal` (flat list: app_no, date, name)

**BLT (Bulletin) — "Marka Bülteni Şerhleri"**

| Sub-section | Event Type | Also in GZ? |
|-------------|-----------|-------------|
| BOLUNMELER (Splits) | `split` | No |
| HACİZ KALDIRMA (Seizure Lifted) | `seizure_lift` | No |
| HACİZ KONULANLAR (Seizures Imposed) | `seizure` | Yes |
| KISITLAMA KALDIRMA (Restriction Lifted) | `restriction_lift` | No |
| TEDBİR KALDIRMA (Injunction Lifted) | `injunction_lift` | No |
| TEDBİR KONULANLAR (Injunctions) | `injunction` | Yes |
| İFLAS İLANI (Bankruptcy) | `bankruptcy` | Yes |
| MAL HİZMET SINIRLANDIRMA (Goods Limitation) | `goods_limitation` | Yes |
| İŞLEMDEN ÇEKİLEN BAŞVURULAR (Withdrawals) | `withdrawal` | Yes |
| MARKA ÖRNEĞİ DÜZELTİLDİ (Logo Corrected) | `logo_correction` | No |
| SAHİP DÜZELTİLDİ (Holder Corrected) | `holder_correction` | No |
| EŞYA SINIRLANDIRMA (MAHKEME KARARI İLE) | `goods_limitation_court` | Yes |

**BLT — "Düzeltmeler"** → `correction` (free-text prose)
**BLT — "Madrid Bölümü Şerhleri"** → `madrid_*` (English WIPO notification format)

### Observed record formats

**Format A — Transfer/Merger (GZ only)**
```
(210) 2024/170165 başvuru numaralı, (220) 17/12/2024 (111)2024 170165 sayılı,
(566) tadelle çikolata  bitter
Devreden : SAGRA GRUP GIDA ÜRETİM VE TİCARET ANONİM ŞİRKETİ
Devralan(lar) : TAMEK SAGRA GRUP GIDA ÜRETİM ANONİM ŞİRKETİ(ADDRESS)
```

**Format B — Seizure/Injunction with court info (both GZ and BLT)**
```
(210) 2016/08701 (220) 02/02/2016 Esas No: 2022/7358 (KONYA 4.
İCRA DAİRESİ MÜDÜRLÜĞÜ) Esas Tarihi : 19/02/2026
(566) çini ali 1922'den günümüze
```

**Format C — Simple record (both GZ and BLT)**
```
(210) 2025/049814 (220) 18.04.2025
(566) sani+
```

**Format D — Correction (free text, both)**
```
12.02.2020 tarih ve 342 sayılı Resmi Marka Bülteninde ilan edilen
2019/106292 numaralı "gazpromneft" ibareli başvurunun mal/hizmet
listesinde yer alan ... sehven reddedilmiş olduğu tespit edildiğinden ...
Şerh ve ilan olunur.
```

**Format E — Madrid (BLT only, English)**
```
Limitations
Designated Contracting Party: Türkiye
NOTIFICATION LIN/2025/45
Registration number 789 986 (LIPOMED)
Name and address of holder ...
```

**Format F — Renewal (GZ only, flat list)**
```
2005 42226
30/09/2015
serpil yıldırım şb 1969 şehzade
```

### Data quality issues observed
- Template placeholders: `$COURTNO$`, `$COURT$`, `$COURTDATE$` (BLT_289)
- Garbled UTF-8 on some section title pages (GZ_499 page 14821)
- Mixed sections: MAL HİZMET SINIRLANDIRMA page has Madrid conversions mixed in (GZ_499)
- Some `(111)` registration numbers missing in GZ records (older applications)

---

## Step 1: Database Schema — `trademark_events` Table

**File**: `migrations/003_trademark_events.sql`

```sql
CREATE TABLE IF NOT EXISTS trademark_events (
    id UUID DEFAULT uuid_generate_v4() PRIMARY KEY,

    -- Link to trademark (nullable — event may reference unknown app_no)
    trademark_id UUID REFERENCES trademarks(id) ON DELETE SET NULL,
    application_no VARCHAR(20) NOT NULL,       -- (210) always present
    registration_no VARCHAR(20),               -- (111) from GZ records

    -- Event classification
    event_type VARCHAR(50) NOT NULL,           -- transfer, seizure, cancellation, etc.
    event_subtype VARCHAR(50),                 -- e.g. "partial" for partial_transfer, "court" for court-ordered

    -- Source
    source_type VARCHAR(3) NOT NULL,           -- 'GZ' or 'BLT'
    bulletin_no VARCHAR(10) NOT NULL,          -- gazette/bulletin number
    bulletin_date DATE,                        -- publication date
    page_number INTEGER,                       -- page in PDF where found

    -- Event details (structured)
    old_value TEXT,                             -- previous state (holder name, etc.)
    new_value TEXT,                             -- new state (new holder + address for transfers)
    details JSONB DEFAULT '{}',                -- flexible: court_name, case_no, case_date, goods text, etc.
    raw_text TEXT,                              -- original extracted text block

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Dedup: same event shouldn't be inserted twice
    CONSTRAINT uq_event UNIQUE (application_no, event_type, source_type, bulletin_no,
                                COALESCE(old_value, ''), COALESCE(new_value, ''))
);

-- Indexes
CREATE INDEX idx_events_app_no ON trademark_events(application_no);
CREATE INDEX idx_events_reg_no ON trademark_events(registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX idx_events_type ON trademark_events(event_type);
CREATE INDEX idx_events_source ON trademark_events(source_type, bulletin_no);
CREATE INDEX idx_events_trademark_id ON trademark_events(trademark_id) WHERE trademark_id IS NOT NULL;
CREATE INDEX idx_events_date ON trademark_events(bulletin_date);
```

**Why separate from `trademark_history`?**
- `trademark_history` is partitioned by date and tracks insert/update lifecycle
- `trademark_events` stores rich structured data from PDF sections with dedup
- They serve different purposes: history = internal audit trail, events = external bulletin data

### Also add to `trademarks` table:
```sql
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS last_event_type VARCHAR(50);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS last_event_date DATE;
```

---

## Step 2: PDF Event Extraction Module

**File**: `pdf_extract_events.py` (NEW)

### Architecture

```
extract_events_from_pdf(pdf_path, source_type, bulletin_no, bulletin_date)
  ├── detect_format_era(doc) → era1 | era2 | era3
  ├── find_events_section(doc, source_type)
  │   ├── GZ: TOC → "Tescilli Markalar Üzerindeki İşlemlere İlişkin İlanlar" page
  │   └── BLT: TOC → "Marka Bülteni Şerhleri" page
  ├── split_into_subsections(text, page_range)
  │   └── Scan for known uppercase headers: BİRLEŞME, DEVİR, HACİZ KONULANLAR, etc.
  └── For each sub-section → dispatch to parser:
      ├── parse_transfer_records()    — Format A (Devreden/Devralan)
      ├── parse_court_records()       — Format B (Esas No / court)
      ├── parse_simple_records()      — Format C ((210)+(566) only)
      ├── parse_correction_prose()    — Format D (free text)
      ├── parse_madrid_records()      — Format E (English WIPO)
      └── parse_renewal_list()        — Format F (flat list)
```

### Sub-section header detection

Events sections are NOT individually listed in the TOC. They are sub-sections within a single
TOC entry, separated by uppercase Turkish headers. Detection approach:

```python
# Known section headers (order matters for boundary detection)
GZ_SECTION_HEADERS = [
    ("BİRLEŞME", "merger"),
    ("KISMİ DEVİR", "partial_transfer"),    # must come before DEVİR
    ("DEVİR", "transfer"),
    ("EŞYA SINIRLANDIRMA", "goods_limitation_court"),
    ("HACİZ KONULANLAR", "seizure"),
    ("İHTİYATİ HACİZ KONULANLAR", "precautionary_seizure"),
    ("TEDBİR KONULANLAR", "injunction"),
    ("İHTİYATİ TEDBİR KONULANLAR", "precautionary_injunction"),
    ("İFLAS İLANI", "bankruptcy"),
    ("LİSANS KAYDI", "license"),
    ("MAL HİZMET SINIRLANDIRMA", "goods_limitation"),
    ("MADRİD DÖNÜŞTÜRME", "madrid_conversion"),
    ("MADRİD YERDEĞİŞTİRME", "madrid_replacement"),
    ("İPTAL EDİLENLER", "cancellation"),
    ("İŞLEMDEN ÇEKİLEN BAŞVURULAR", "withdrawal"),
]

BLT_SECTION_HEADERS = [
    ("BOLUNMELER", "split"),
    ("BÖLÜNMELER", "split"),
    ("HACİZ KALDIRMA", "seizure_lift"),
    ("HACİZ KONULANLAR", "seizure"),
    ("KISITLAMA KALDIRMA", "restriction_lift"),
    ("TEDBİR KALDIRMA", "injunction_lift"),
    ("TEDBİR KONULANLAR", "injunction"),
    ("İFLAS İLANI", "bankruptcy"),
    ("MAL HİZMET SINIRLANDIRMA", "goods_limitation"),
    ("EŞYA SINIRLANDIRMA", "goods_limitation_court"),
    ("MARKA ÖRNEĞİ DÜZELTİLDİ", "logo_correction"),
    ("SAHİP DÜZELTİLDİ", "holder_correction"),
    ("İŞLEMDEN ÇEKİLEN BAŞVURULAR", "withdrawal"),
]
```

### Parser functions (6 formats covering all sub-sections)

**1. `parse_transfer_records(text, event_type)`** — Format A
- Used for: BİRLEŞME, DEVİR, KISMİ DEVİR
- Split on `(210)` markers
- Extract: app_no, filing_date `(220)`, reg_no `(111)`, trademark_name `(566)`
- Extract: `Devreden :` (old holder), `Devralan(lar) :` (new holder + address in parens)
- For KISMİ DEVİR: also capture goods/class text after the Devralan block

**2. `parse_court_records(text, event_type)`** — Format B
- Used for: HACİZ, İHTİYATİ HACİZ, TEDBİR, İHTİYATİ TEDBİR, KISITLAMA, LİSANS, İPTAL
- Split on `(210)` markers
- Extract: app_no, filing_date `(220)`, reg_no `(111)`, trademark_name `(566)`
- Extract: `Esas No:` (case number), court name in parens, `Esas Tarihi :` (case date)

**3. `parse_simple_records(text, event_type)`** — Format C
- Used for: BOLUNMELER, MAL HİZMET SINIRLANDIRMA, İŞLEMDEN ÇEKİLEN, İFLAS
- Split on `(210)` markers
- Extract: app_no, filing_date `(220)`, trademark_name `(566)`
- Minimal parsing — capture remaining text as raw_text

**4. `parse_correction_prose(text)`** — Format D
- Used for: DÜZELTMELER, MARKA ÖRNEĞİ DÜZELTİLDİ, SAHİP DÜZELTİLDİ
- Split on `Şerh ve ilan olunur.` paragraph boundaries
- Extract app_no via regex `\d{4}/\d{3,6}` from each paragraph
- Store full paragraph as raw_text

**5. `parse_madrid_records(text)`** — Format E
- Used for: Madrid Bölümü Şerhleri (BLT only)
- Split on `NOTIFICATION` markers
- Extract: registration number, holder, designated party, limitation/correction text
- Sub-types: Limitations (LIN), Corrections (RIN), Holder's Right (HRN), etc.

**6. `parse_renewal_list(text)`** — Format F
- Used for: Yenilenen Markalar (GZ section 5)
- Three-line groups: `reg_no`, `date`, `name`
- Simple line-based parsing

### Format era handling

| Era | Strategy |
|-----|----------|
| **Era 3** (2017+) | Use TOC to find events section page range, then scan for sub-section headers |
| **Era 2** (2012–2016) | No TOC — scan all pages for sub-section headers directly. Wider search range. |
| **Era 1** (pre-2012) | Multi-PDF — must iterate all PDFs in folder. No TOC. Headers may be inline with records. Best-effort extraction. |

### Entry point:
```python
def extract_events_from_pdf(
    pdf_path: Path,
    source_type: str,        # "GZ" or "BLT"
    bulletin_no: str,
    bulletin_date: str,
) -> List[dict]:
    """Extract all events from a bulletin PDF.

    Returns list of event dicts:
    {
        "application_no": "2019/12345",
        "registration_no": "2019 12345",       # GZ only, nullable
        "event_type": "transfer",
        "event_subtype": null,
        "source_type": "GZ",
        "bulletin_no": "499",
        "bulletin_date": "2026-01-30",
        "page_number": 14823,
        "old_value": "SAGRA GRUP GIDA ...",
        "new_value": "TAMEK SAGRA GRUP GIDA ...(ADDRESS)",
        "details": {"case_no": null, "court": null},
        "raw_text": "original block text"
    }
    """
```

---

## Step 3: Event Ingestion

**File**: `ingest_events.py` (NEW)

### Flow:
1. For each GZ/BLT folder with a `bulletin.pdf`:
   - Detect source_type from folder prefix (GZ_ or BLT_)
   - Call `extract_events_from_pdf()`
   - Save events to `events.json` in the folder (cache)
   - Upsert into `trademark_events` table (ON CONFLICT DO NOTHING)
   - Link `trademark_id` where `application_no` matches existing trademarks
   - Update `trademarks.last_event_type` and `last_event_date`
   - Apply side effects on `trademarks` table (see below)

### Side effects on `trademarks` table:
| Event Type | Trademarks Update |
|---|---|
| `transfer`, `merger` | `holder_name` = new_value |
| `partial_transfer` | Log only (complex — partial class reassignment) |
| `cancellation` | `current_status` = 'İptal Edildi' |
| `seizure`, `precautionary_seizure` | `details` JSONB flag `"seized": true` |
| `seizure_lift` | `details` JSONB flag `"seized": false` |
| `goods_limitation` | Log only (class text changes too complex to auto-apply) |
| `withdrawal` | `current_status` = 'Çekildi'` |
| `renewal` | `registration_date` update if applicable |

### CLI:
```bash
python ingest_events.py                           # all GZ/BLT folders with bulletin.pdf
python ingest_events.py --folder GZ_499_*         # specific folder
python ingest_events.py --folder "BLT_48*"        # glob pattern
python ingest_events.py --reparse                 # re-extract from PDF (ignore events.json cache)
python ingest_events.py --source-type GZ          # only gazette bulletins
python ingest_events.py --source-type BLT         # only bulletin PDFs
python ingest_events.py --dry-run                 # extract + print stats, don't write to DB
```

---

## Step 4: Pipeline Integration

**File**: `workers/pipeline_worker.py` (MODIFY)

Run event extraction + ingestion as a **post-ingest step** (events reference existing trademarks):
```
download → extract (ZIP+PDF) → metadata → embeddings → ingest → **extract_events → ingest_events**
```

---

## Step 5: Watchlist Event Alerts

**File**: `watchlist/scanner.py` (MODIFY)

### New alert types:
- `event_transfer` — trademark in watchlist was transferred to new owner
- `event_seizure` — trademark in watchlist was seized
- `event_cancellation` — trademark in watchlist was cancelled
- `event_license` — trademark in watchlist was licensed
- `event_injunction` — trademark in watchlist has court injunction

### Scanner addition:
After similarity scanning, also check `trademark_events` for new events on watched trademarks:
```python
async def _check_events_for_watchlist(self, watchlist_items, since_date):
    """Find new events for trademarks that match watchlist items."""
    # Query trademark_events where application_no matches any watched trademark
    # and bulletin_date >= since_date (last scan)
```

---

## Step 6: API Endpoints

**File**: `api/routes.py` (MODIFY)

```
GET /api/v1/trademarks/{id}/events       — timeline of all events for a trademark
GET /api/v1/events?bulletin_no=499       — all events from a specific bulletin
GET /api/v1/events?source_type=GZ        — filter by source
GET /api/v1/events/stats                  — event counts by type, by source, by bulletin
```

---

## Step 7: UI — Trademark Timeline

**File**: `templates/partials/_results_panel.html` (MODIFY)

Add an "Events" or "History" tab to trademark detail view showing:
- Timeline of all events (transfers, seizures, etc.) in chronological order
- Each event shows: date, type icon/badge, description, old→new values
- Source badge: GZ or BLT
- Link to source bulletin PDF page

---

## Step 8: Newer-to-Older Backfill Strategy

Build the parser incrementally, starting with the newest and most structured PDFs, working
backwards to handle progressively messier formats. Each wave validates and extends the parser.

### Wave 1 — Latest GZ gazettes (GZ_495–499, 2025–2026)
- **Goal**: Build and validate all 6 parser functions against clean, consistent data
- These have full TOC, consistent sub-section headers, `(111)` registration numbers
- Contains the richest event types: transfers, mergers, licenses, cancellations
- **Expected yield**: ~5 gazettes × ~400 pages of events each = ~2,000+ pages → thousands of events
- **Validation**: Cross-check transfer records against known holder changes in DB

### Wave 2 — Latest BLT bulletins (BLT_480–488, 2025–2026)
- **Goal**: Add BLT-specific parsers (splits, seizure lifts, Madrid, injunction lifts)
- Same era as Wave 1 GZ — consistent TOC, same `(210)+(220)+(566)` format
- Madrid section parser (English WIPO notifications) is BLT-only
- **Validation**: Seizure records should match between GZ and BLT for same period

### Wave 3 — Expand GZ backwards (GZ_470–494, 2021–2025)
- **Goal**: Catch format variations as we go further back
- Watch for: different sub-section header spellings, different court info formatting
- ~25 gazettes → tens of thousands of events
- **Checkpoint**: Review extraction stats (events/page ratio, parse failure %)

### Wave 4 — Expand BLT backwards (BLT_400–479, 2022–2025)
- **Goal**: Cover modern BLT era
- Same format as Wave 2, just more data
- ~80 bulletins, consistent `bulletin.pdf` naming

### Wave 5 — Early GZ (GZ_421–469, 2017–2021)
- **Goal**: Push GZ coverage to its full available range
- May encounter older header variants, different page formatting

### Wave 6 — BLT Era 3 remainder (BLT_289–399, 2017–2022)
- **Goal**: Complete the TOC-era BLT coverage
- ~110 bulletins, all with TOC

### Wave 7 — BLT Era 2 (BLT_200–288, 2012–2016) — OPTIONAL
- **Goal**: Extend to pre-TOC bulletins
- Strategy: scan all pages for sub-section headers (no TOC to guide us)
- Need broader page scanning, potentially slower
- Risk: may find unexpected formats, lower success rate

### Wave 8 — BLT Era 1 (BLT_119–199, 2005–2012) — OPTIONAL
- **Goal**: Best-effort extraction from oldest bulletins
- Multi-PDF per bulletin, no TOC, possible different section structure entirely
- Approach: attempt header scanning, accept partial results
- Historical data — lower priority unless needed for specific research

### Extraction command pattern:
```bash
# Wave 1
python ingest_events.py --folder "GZ_49*" --dry-run    # audit first
python ingest_events.py --folder "GZ_49*"               # then ingest

# Wave 2
python ingest_events.py --folder "BLT_48*" --dry-run
python ingest_events.py --folder "BLT_48*"

# Wave 3+: expand ranges progressively
python ingest_events.py --folder "GZ_47*"
python ingest_events.py --folder "GZ_46*"
# ...
```

---

## Implementation Order

1. **Schema migration** — `trademark_events` table + trademarks columns
2. **`pdf_extract_events.py`** — Build parser module with the 6 format parsers:
   - Start with GZ transfers (Format A) — richest, most valuable
   - Add court records (Format B) — seizures, injunctions, cancellations
   - Add simple records (Format C) — splits, withdrawals, limitations
   - Add corrections (Format D) — free-text prose
   - Add Madrid (Format E) — English WIPO (BLT only)
   - Add renewals (Format F) — flat list (GZ only)
3. **Validate against Wave 1** — Parse GZ_499, manually verify a sample of events
4. **`ingest_events.py`** — Event ingestion + trademark side effects
5. **Wave 2 validation** — Parse BLT_488, verify BLT-specific sections
6. **Pipeline wiring** — Add to `pipeline_worker.py`
7. **Backfill Waves 3–6** — Progressively expand coverage
8. **Watchlist alerts** — Event-based alerts
9. **API + UI** — Endpoints and timeline view
10. **Waves 7–8** — Older bulletins (optional, based on need)

---

## Key Design Decisions

- **Separate `trademark_events` table** (not reuse `trademark_history`): events have rich structured data (old/new values, details JSON, page numbers) that don't fit the simple history schema
- **`source_type` column** (GZ/BLT): same event types appear in both bulletin types with different data richness; need to distinguish source
- **`application_no` as primary link** (not `trademark_id`): events reference trademarks that may not yet exist in our DB
- **`registration_no` column**: GZ records include (111) which BLT records don't — enables matching to registered trademarks
- **`events.json` cache** in each folder: avoids re-parsing 500MB PDFs on every ingest run
- **Dedup constraint** includes `source_type`: same event may appear in both GZ and BLT — store both
- **JSONB `details` field**: flexible enough for court info, goods text, Madrid notification codes without schema changes per event type
- **Newer-to-older approach**: builds parser confidence on clean data first, catches format variations incrementally, delivers value early with the most recent/relevant events
- **COALESCE in dedup constraint**: handles NULL old_value/new_value (simple records have no old/new)
