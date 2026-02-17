# SEARCH CAPABILITIES — DEEP INVESTIGATION

**Date:** 2026-02-10
**Investigator:** Claude Code (read-only analysis)

## Files Read

### Core Search/Scoring
- `risk_engine.py` (923 lines) — Risk scoring engine, model loading, hybrid search
- `agentic_search.py` (1033 lines) — Orchestrated 5-step search pipeline + FastAPI router
- `idf_scoring.py` (418 lines) — 3-tier IDF token scoring (Cases A-F)
- `utils/idf_scoring.py` (1777 lines) — Centralized multi-factor scoring, comprehensive score
- `utils/scoring.py` (220 lines) — Deprecated proxy to utils/idf_scoring
- `utils/translation.py` — NLLB-200 cross-language similarity
- `utils/class_utils.py` — Nice class overlap + Class 99 global brands

### AI / Embeddings
- `ai.py` (860 lines) — CLIP, DINOv2, MiniLM model loading; embedding generation; OCR
- `config/settings.py` (315 lines) — All model names, batch sizes, thresholds

### API Layer
- `main.py` — FastAPI app, search endpoints `/api/search`, `/api/search-by-image`, `/api/search/unified`, `/api/search/simple`
- `api/routes.py` (2339 lines) — Auth, watchlist, alerts, admin IDF tools
- `api/creative.py` — Name validation + visual similarity for Logo Studio

### Database
- `database/crud.py` — CRUD operations, embedding storage (no vector search here)
- `db/pool.py` (452 lines) — ThreadedConnectionPool singleton
- `deploy/schema.sql` (1036 lines) — Full DB schema with indexes
- `migrations/enhance_logo_visual_features.sql` — Visual feature migration

### Workers
- `watchlist/scanner.py` — Watchlist conflict scanning
- `workers/universal_scanner.py` — Universal conflict detection (Opposition Radar)

### Frontend
- `static/js/api.js` — AppAPI search methods
- `static/js/app.js` — Search flow, result display, pagination
- `static/js/components/result-card.js` — Result card rendering
- `static/js/components/score-badge.js` — Score/risk badge rendering
- `templates/partials/_search_panel.html` — Search input UI
- `templates/partials/_results_panel.html` — Dashboard overview tab

### Models/Schemas
- `models/schemas.py` (756 lines) — Pydantic request/response models

---

## Architecture Overview

```
                                    USER
                                     |
                         +-----------+-----------+
                         |                       |
                   [Quick Search]          [Intelligent Search]
                   (DB-only)               (DB + Live Scrape)
                         |                       |
                         v                       v
              GET /api/v1/search/quick    GET|POST /api/v1/search/intelligent
                         |                       |
                         +-----------+-----------+
                                     |
                                     v
                        AgenticTrademarkSearch.search()
                                     |
                    +----------------+----------------+
                    |                                 |
              STEP 1: DB Search              STEP 2: Live Scrape?
              assess_brand_risk()            (if max_score < 0.75)
                    |                                 |
         +----------+----------+              scrapper.search_and_ingest()
         |          |          |                      |
    pre_screen   get_query   suggest_          STEP 3: AI Embeddings
    _candidates  _vectors    classes           ai.process_folder()
    (trigram)    (encode)    (pgvector)                |
         |          |                          STEP 4: Ingest to DB
         v          v                          ingest.process_file_batch()
    calculate_hybrid_risk()                           |
         |                                     STEP 5: Re-score
    +----+----+----+----+                      assess_brand_risk() [again]
    |    |    |    |    |
   SQL: 6 signals per candidate
    |    |    |    |    |
    v    v    v    v    v
  text  CLIP DINOv2 color OCR  phonetic
  sim   sim   sim   sim  sim   match
    |    |    |    |    |       |
    +----+----+    +----+      |
         |              |      |
   score_pair()   visual_sim   |
         |         (composite) |
    +----+----+         |      |
    |         |         |      |
  IDF      translation |      |
  waterfall  sim       |      |
  (A-F)    (NLLB)     |      |
    |         |        |      |
    v         v        v      v
  _dynamic_combine(text=0.60, visual=0.25, translation=0.15)
                    |
              FINAL SCORE → RISK LEVEL
```

### Parallel Search Paths (Non-Agentic)

In addition to the agentic pipeline, `main.py` defines 4 standalone search endpoints that bypass `AgenticTrademarkSearch`:

| Endpoint | Calls | Scoring |
|----------|-------|---------|
| `POST /api/search` | Direct SQL (trigram) | `utils.idf_scoring.calculate_comprehensive_score()` |
| `POST /api/search-by-image` | Direct SQL (CLIP cosine) | `risk_engine.calculate_visual_similarity()` |
| `POST /api/search/unified` | Direct SQL (trigram + optional CLIP) | Both text + image scoring |
| `GET /api/search/simple` | Direct SQL (trigram) | `utils.idf_scoring.calculate_comprehensive_score()` |

These do NOT use `RiskEngine` class methods. They run simpler SQL queries and use the `utils/idf_scoring.py` scoring module.

---

## 1. Text Search (Trigram + IDF)

### How it works

Two-layer system:

**Layer 1 — PostgreSQL `pg_trgm` candidate retrieval:** All text search endpoints query the `trademarks` table using the `similarity()` function from the `pg_trgm` extension. Candidates with `similarity > 0.2` are returned, up to 100 rows. Turkish character normalization is applied server-side via 12 nested `REPLACE()` calls.

**Layer 2 — Python-side IDF-weighted re-scoring:** The 100 SQL candidates are re-scored in Python using one of two scoring engines:
- **Agentic path** (`risk_engine.score_pair()` → `idf_scoring.compute_idf_weighted_score()`): 3-tier IDF waterfall with Cases A-F
- **Direct path** (`utils.idf_scoring.calculate_comprehensive_score()`): 4-factor weighted scoring (word_match 0.35 + coverage 0.30 + idf 0.20 + length 0.15)

### Key code paths

**Trigram query** (used by `/api/search`, `/api/search/unified`, `/api/search/simple`):
`main.py` lines 1785-1839 — fetches 100 candidates ordered by `GREATEST(similarity(...), similarity(<turkish_normalized>), CASE LIKE ...)`.

**IDF waterfall** (used by agentic search path):
`idf_scoring.py:50-348` (`compute_idf_weighted_score()`) — 3-tier word classification:

| Tier | IDF Threshold | Weight |
|------|--------------|--------|
| GENERIC | IDF < 5.3 | 0.1 |
| SEMI_GENERIC | 5.3 ≤ IDF < 6.9 | 0.5 |
| DISTINCTIVE | IDF ≥ 6.9 | 1.0 |

Scoring Cases (evaluated in priority order):

| Case | Condition | Score Range |
|------|-----------|-------------|
| EXACT_MATCH | `q_norm == t_norm` | 1.0 |
| CONTAINMENT (distinctive) | query substring of target + has distinctive words | ≥ 0.95 |
| CONTAINMENT (generic only) | query substring, no distinctive words | 0.15 |
| A: High distinctive (≥80%) | `distinctive_pct ≥ 0.80` | ≥ 0.92 |
| B: Good distinctive (≥50%) | `distinctive_pct ≥ 0.50` | 0.75–0.85 |
| C: Some distinctive (>0) | `distinctive_match > 0` | 0.50–0.65 |
| D: Semi-generic only | `semi_generic_match > 0` | 0.20–0.35 (capped) |
| E: Generic only | `generic_match > 0` | 0.05–0.20 (capped) |
| F: No token match | fallback | `base * 0.7` |

**Turkish normalization** (`idf_scoring.py:26-40`):
```python
def normalize_turkish(text: str) -> str:
    # Maps: ğ→g, Ğ→g, ı→i, İ→i, ö→o, Ö→o, ü→u, Ü→u, ş→s, Ş→s, ç→c, Ç→c
    # Then .lower().strip()
```

### SQL queries

**Candidate retrieval** (all text endpoints share this pattern):
```sql
SELECT t.id, t.application_no, t.name, t.current_status, t.nice_class_numbers,
       GREATEST(
           similarity(LOWER(t.name), LOWER($query)),
           similarity(<turkish_normalized_name>, LOWER($query_normalized)),
           CASE WHEN LOWER(t.name) LIKE LOWER($pattern) THEN 0.9 ELSE 0 END,
           CASE WHEN <turkish_normalized_name> LIKE LOWER($pattern) THEN 0.9 ELSE 0 END
       ) as score
FROM trademarks t
WHERE LOWER(t.name) LIKE $pattern
   OR <turkish_normalized_name> LIKE $pattern
   OR similarity(LOWER(t.name), LOWER($query)) > 0.2
   OR similarity(<turkish_normalized_name>, LOWER($query)) > 0.2
   [AND (t.nice_class_numbers && $classes::int[] OR 99 = ANY(t.nice_class_numbers))]
ORDER BY score DESC
LIMIT 100
```

**Agentic pre-screening** (`risk_engine.py:563-676`) — 3-stage funnel:
- Stage 1: Exact match on `LOWER(name)` or `LOWER(name_tr)` (limit 10)
- Stage 2: Turkish-normalized exact match via 12 REPLACE chains
- Stage 3: Fuzzy trigram `similarity()` with cross-language `GREATEST()` (limit 30)

### API endpoints & response shape

**`POST /api/search`** (`main.py:1680`) — Public, no auth required
Request: `{ name, classes?, goods_description?, auto_suggest_classes?, limit? }`
Response:
```json
{
  "results": [{ "id", "name", "application_no", "status", "nice_classes",
                "similarity" (0-100), "name_similarity" (0-100), "risk_level",
                "image_url", "owner", "bulletin_no" }],
  "search_context": { "searched_name", "searched_classes", "total_results", "search_time_ms" },
  "classes_were_auto_suggested": bool,
  "auto_suggested_classes": [{ "class_number", "description", "similarity" }]
}
```

**`GET /api/v1/search/quick`** (`agentic_search.py:636`) — Auth required, daily cap
Request: `?query=...&classes=9,35&page=1&per_page=20`
Response: Agentic response with pagination (`page`, `total_pages`, `total`, `results[]`)

**`GET /api/v1/search/intelligent`** (`agentic_search.py:690`) — Auth required, Pro/Enterprise
Same params + `threshold`, `force_scrape`. May trigger live scraping.

### Frontend integration

- Quick Search: `AppAPI.handleQuickSearch(page)` → `GET /api/v1/search/quick` (`api.js:9-41`)
- Intelligent Search: `AppAPI.handleAgenticSearch(page)` → `GET|POST /api/v1/search/intelligent` (`api.js:46-118`)
- Enter key on search input triggers Quick Search (`_search_panel.html:10`)
- Results displayed via `displayAgenticResults(data)` (`app.js:966-1027`)
- Text similarity shown as "Metin XX%" badge in result cards (`score-badge.js:68-96`)

---

## 2. Visual Search — CLIP

### How it works

CLIP (Contrastive Language–Image Pre-training) embeds both images and text into a shared 512-dimensional space. The system encodes a query image with the same CLIP model used during ingestion, then finds nearest neighbors in PostgreSQL using pgvector cosine distance.

### Key code paths

**Model loading** (`ai.py:103-112`):
```python
clip_model, _, clip_preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32", pretrained="laion2b_s34b_b79k", device=device
)
clip_model.eval().half()  # FP16 on GPU
```
- Model: **OpenCLIP ViT-B-32** (laion2b_s34b_b79k weights)
- Output dimension: **512**
- Library: `open_clip`

**Query-time encoding** (`main.py:583-601`, `get_image_embedding_for_search()`):
- Opens image with PIL, converts to RGB
- Applies `clip_preprocess`, converts to FP16 on CUDA
- `clip_model.encode_image(tensor)` → L2-normalized → 512-dim float list

**Ingestion-time encoding** (`ai.py:319-355`, `get_clip_embedding_cached()`):
- Same model + preprocessing
- Redis-cached with key `clip_emb:{MD5 of image bytes}`, TTL 24h

### SQL queries

**Image search** (`main.py:734-768`):
```sql
SELECT t.id, t.name, t.application_no, t.current_status, t.nice_class_numbers,
       t.bulletin_no, t.image_path, t.logo_ocr_text,
       1 - (t.image_embedding <=> %s::vector) AS image_similarity
FROM trademarks t
WHERE t.image_embedding IS NOT NULL
  [AND (t.nice_class_numbers && %s::int[] OR 99 = ANY(t.nice_class_numbers))]
ORDER BY image_similarity DESC
LIMIT %s
```

**Combined search** (`main.py:1206-1229`):
```sql
SELECT ...,
       1 - (t.image_embedding <=> %s::vector) AS image_sim,
       GREATEST(similarity(...), similarity(...)) AS text_sim,
       (0.4 * (1 - (t.image_embedding <=> %s::vector)) + 0.6 * GREATEST(...)) AS combined_score
FROM trademarks t
WHERE t.image_embedding IS NOT NULL [AND class_filter]
ORDER BY combined_score DESC
LIMIT 100
```

**Operator**: `<=>` (pgvector cosine distance)
**Index**: HNSW — `idx_tm_image_vec` with `m=16, ef_construction=200`, partial (`WHERE image_embedding IS NOT NULL`)
**Column**: `trademarks.image_embedding` — `halfvec(512)`

### API endpoints & response shape

**`POST /api/search-by-image`** (`main.py:606`) — Public
Request: multipart form with `image` (required), `classes` (optional CSV), `limit`
Response:
```json
{
  "success": true,
  "search_type": "image",
  "ocr_enabled": true,
  "query_ocr_text": "ACME CORP",
  "results": [{
    "id", "name", "application_no", "status", "nice_classes",
    "image_url",
    "similarity" (0-100, final score),
    "image_similarity" (0-100, raw CLIP),
    "ocr_boost" (0-100),
    "ocr_similarity" (0-100),
    "risk_level"
  }]
}
```

**`POST /api/search/unified`** (`main.py:1011`) — Public
Accepts optional image + optional name. When image is provided:
- Image-only: uses CLIP cosine search
- Combined: `0.4 * image_sim + 0.6 * text_sim` in SQL

### Frontend integration

- Image upload area in `_search_panel.html:58-78` — accepts PNG/JPEG/WEBP
- Only sent with Intelligent Search (not Quick Search)
- `AppAPI.handleAgenticSearch()` detects `#search-image` file and uses FormData POST
- Visual similarity shown as "Gorsel XX%" badge in result cards

---

## 3. Visual Search — DINOv2

### How it works

DINOv2 provides self-supervised visual features that capture structural/compositional similarity (complementary to CLIP's semantic similarity). Used only as a sub-signal within risk scoring, never as a standalone search.

### Key code paths

**Model loading** (`ai.py:114-138`):
```python
dinov2_model = torch.hub.load('facebookresearch/dinov2', 'dinov2_vitb14')
dinov2_model.to(device).half().eval()
```
- Model: **DINOv2 ViT-B/14** (Facebook Research)
- Output dimension: **768**
- Custom preprocessing: SquarePad (white fill) → Resize(224) → Normalize(ImageNet stats)
- NOT L2-normalized (unlike CLIP)

**Query-time encoding** (`risk_engine.py:506-527`, `_encode_single_image()`):
```python
tensor = self.dino_preprocess(pil_img).unsqueeze(0).to(device).half()
dino_vec = self.dino_model(tensor).flatten().cpu().float().tolist()
```

**Ingestion-time encoding** (`ai.py:421-456`, `get_dino_embedding_cached()`):
- Redis-cached with key `dino_emb:{MD5 of image bytes}`, TTL 24h

### SQL queries

Only queried inside `calculate_hybrid_risk()` (`risk_engine.py:689-707`):
```sql
(1 - (t.dinov2_embedding <=> %s::halfvec)) as score_dinov2
```
This is part of a multi-column SELECT — not a standalone ORDER BY. DINOv2 is never used to filter or rank candidates at the SQL level.

**Also computed in Python** by `universal_scanner.py` and `watchlist/scanner.py` using `_cosine_sim()` on pre-fetched embeddings from the SQL result set.

### Weight in risk scoring

Inside `calculate_visual_similarity()` (`risk_engine.py:88-117`):

| Signal | Weight |
|--------|--------|
| CLIP | 0.35 |
| **DINOv2** | **0.30** |
| Color histogram | 0.15 |
| OCR text | 0.20 |

The composite visual score then enters `_dynamic_combine()` with base weight **0.25** (text=0.60, visual=0.25, translation=0.15).

### Gap analysis: what's needed for a standalone endpoint

**What exists:**
- Model loaded and ready (`risk_engine.py:434-435`)
- Encoding function exists (`_encode_single_image()` returns dino_vec)
- DB column `dinov2_embedding halfvec(768)` is populated for most trademarks with images
- pgvector cosine distance works on the column

**What's missing:**
- **No HNSW index** on `dinov2_embedding` — would do brute-force sequential scan (HIGH impact)
- No standalone SQL query that orders by DINOv2 similarity
- No API endpoint
- No frontend UI

**Effort estimate:** LOW — the function and data exist. Need:
1. Create HNSW index (~5 min DDL, but hours to build on large table)
2. Add a `/api/search-by-dino` endpoint (~30 lines, similar to search-by-image)
3. Wire up frontend tab/toggle (~20 lines)

**However:** Exposing DINOv2 as a separate endpoint may confuse users. A better UX would be a "Visual Search" toggle that uses a composite of CLIP+DINOv2 behind the scenes.

---

## 4. Color Histogram

### How it works

Computes an HSV color histogram from the trademark logo image, flattened into a 512-dimensional vector. Cosine similarity between histograms measures color distribution similarity.

### Key code paths

**Computation** (`ai.py:530-536`, `extract_color_histogram()`):
```python
hsv_img = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2HSV)
hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
cv2.normalize(hist, hist)
return hist.flatten().tolist()  # 512-dim
```
- Color space: **HSV**
- Bins: **8 × 8 × 8 = 512** (H, S, V)
- Normalized via `cv2.normalize()`
- **Not cached in Redis** (computed fresh each time)

**KNOWN BUG in risk_engine.py:** The `_encode_single_image()` function (`risk_engine.py:510-513`) generates a **32-dim** histogram (bins [8, 2, 2]) for query-time encoding, while `ai.py` generates **512-dim** (bins [8, 8, 8]) for ingestion. This dimension mismatch means color similarity scores from `calculate_hybrid_risk()` may be incorrect or error silently.

### SQL queries

Only queried as part of `calculate_hybrid_risk()` (`risk_engine.py:687`):
```sql
(1 - (t.color_histogram <=> %s::halfvec)) as score_color
```
Never used standalone for filtering or ranking.

### Weight in risk scoring

- Weight within visual composite: **0.15** (CLIP 0.35, DINOv2 0.30, **color 0.15**, OCR 0.20)
- Visual composite weight in final score: **0.25**
- Effective contribution to final score: **0.15 × 0.25 = 3.75%**

### Gap analysis: what's needed for a standalone endpoint

**What exists:**
- Histogram computation function
- DB column `color_histogram halfvec(512)` populated for trademarks with images
- pgvector cosine distance works

**What's missing:**
- **Dimension mismatch bug** between query-time (32-dim) and DB (512-dim) must be fixed first
- No HNSW index on `color_histogram`
- No standalone query
- No API endpoint
- No frontend UI for "search by color"

**Feature potential:** A "search by dominant color" feature would be unique and valuable for trademark attorneys looking for visually similar logos. Could accept a color picker input rather than requiring image upload.

**Effort estimate:** MEDIUM — need to fix the dimension bug, add index, build color picker UI.

---

## 5. Semantic Search — MiniLM

### How it works

Encodes trademark names into 384-dimensional semantic vectors using a multilingual sentence transformer. Captures meaning-based similarity (e.g., "APPLE" and "FRUIT" are semantically close even though they share no characters).

### Key code paths

**Model loading** (`ai.py:141-144`):
```python
text_model = SentenceTransformer(
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", device=device
)
```
- Model: **paraphrase-multilingual-MiniLM-L12-v2**
- Output dimension: **384**
- Multilingual (supports Turkish, English, and 50+ languages)
- NOT explicitly set to FP16 (default precision)

**What text is embedded:** The trademark **NAME** field only (`ai.py:675`):
```python
name = rec.get("TRADEMARK", {}).get("NAME", "")
```
Goods descriptions, holder names, etc. are NOT embedded.

**Query-time encoding** (`ai.py:228-251`, `get_text_embedding_cached()`):
```python
text_model.encode(text, show_progress_bar=False).tolist()  # 384-dim
```
- Redis-cached: key `text_emb:{MD5 of UTF-8 text}`, TTL 24h

### SQL queries

**In risk engine** (`risk_engine.py:693`):
```sql
(1 - (t.text_embedding <=> %s::halfvec)) as score_semantic
```
Used inside `calculate_hybrid_risk()` as `score_semantic` — feeds into `score_pair()` as the `semantic_sim` parameter.

**In universal scanner** (`workers/universal_scanner.py:205-224`) — standalone vector search:
```sql
SELECT ..., 1 - (t.text_embedding <=> %s::halfvec) as semantic_sim
FROM trademarks t
WHERE t.text_embedding IS NOT NULL
  AND t.current_status IN ('Registered', 'Published', 'Renewed')
ORDER BY t.text_embedding <=> %s::halfvec
LIMIT 100
```
This is the only place where `text_embedding` is used as the primary ORDER BY for nearest-neighbor search (not combined with trigram).

**In creative suite** (`api/creative.py:226-268`):
```sql
(1 - (t.text_embedding <=> %s::halfvec)) AS semantic_sim
```
Used alongside trigram `similarity()` for name validation.

### How it differs from trigram text search

| Aspect | Trigram (pg_trgm) | Semantic (MiniLM) |
|--------|-------------------|-------------------|
| Matching basis | Character n-grams | Meaning/context |
| "APPLE" vs "ELMA" | 0.0 (no char overlap) | ~0.3 (some semantic overlap) |
| "APPLE" vs "APPEL" | ~0.8 (character similarity) | ~0.95 (nearly identical meaning) |
| Turkish support | Via REPLACE normalization | Native multilingual model |
| Speed | Fast (GiST index) | Fast (HNSW index) |
| Use in scoring | `score_lexical` (pg_trgm) | `score_semantic` (MiniLM) |

In the IDF waterfall, `semantic_sim` is used as a floor/boost in Cases B-F: `base = max(text_sim, semantic_sim, phonetic_sim)`.

### Weight in risk scoring

The semantic score does NOT enter `_dynamic_combine()` directly. Instead, it's incorporated into the IDF waterfall's `base` value, which then becomes the `text_idf_score` component. This means semantic similarity influences the text weight (0.60) rather than being a separate signal.

### Gap analysis: what's needed for a standalone endpoint

**What exists:**
- Model loaded, encoding function with Redis caching
- DB column `text_embedding halfvec(384)` fully populated
- HNSW index exists: `idx_tm_text_vec` with `m=16, ef_construction=200`
- Standalone nearest-neighbor query exists in `universal_scanner.py`

**What's missing:**
- No user-facing API endpoint for pure semantic search
- No frontend UI

**Effort estimate:** VERY LOW — everything exists. Need:
1. Add `/api/search-by-meaning` endpoint (~25 lines, reuse `get_text_embedding_cached()`)
2. Add frontend toggle or tab (~15 lines)

This would be the **easiest win** — the index exists, the function exists, just wire them together.

---

## 6. OCR Text Search

### How it works

EasyOCR extracts text visible in trademark logo images during the ingestion pipeline. This text is stored as a string field (`logo_ocr_text`) and used for text-to-text comparison during risk scoring.

### Key code paths

**OCR extraction** (`ai.py:723-736`):
```python
ocr_reader = easyocr.Reader(['en', 'tr'], gpu=device == 'cuda', verbose=False)
ocr_res = ocr_reader.readtext(str(img_path), detail=0, paragraph=True)
rec["logo_ocr_text"] = " ".join(ocr_res)
```
- Library: **EasyOCR** (not Tesseract, not PaddleOCR)
- Languages: English + Turkish
- GPU-accelerated when available
- `detail=0`: returns text strings only (no bounding boxes)
- `paragraph=True`: merges nearby text into paragraphs
- Output stored as space-joined string in `metadata.json` → DB column `logo_ocr_text TEXT`

**OCR at search time** (`main.py:723`):
```python
query_ocr_text = extract_ocr_text(temp_path)
```
The uploaded query image also has OCR extracted for comparison (in `/api/search-by-image`).

**Similarity comparison** (`risk_engine.py:108-115`):
```python
if ocr_text_a and ocr_text_b:
    ocr_sim = SequenceMatcher(None,
                              ocr_text_a.lower().strip(),
                              ocr_text_b.lower().strip()).ratio()
```
Uses `difflib.SequenceMatcher` — fuzzy string matching, not embedding-based.

### How it's used in risk scoring

OCR text similarity is one of 4 signals in `calculate_visual_similarity()`:

| Signal | Weight |
|--------|--------|
| CLIP | 0.35 |
| DINOv2 | 0.30 |
| Color | 0.15 |
| **OCR** | **0.20** |

**Important limitation:** At query time in `calculate_hybrid_risk()`, `ocr_text_a` (the query's OCR) is always `""` because `RiskEngine` does NOT run EasyOCR on uploaded images. Only the `/api/search-by-image` endpoint extracts query OCR. This means OCR contributes **0.0** to the visual score in the agentic search path.

### Gap analysis: what's needed for a "search by text in logo" endpoint

**What exists:**
- `logo_ocr_text` column populated for ~476 folders with images
- EasyOCR reader loaded at startup
- `SequenceMatcher` comparison logic

**What's missing:**
- No full-text search index on `logo_ocr_text` (no GIN/GiST trigram index)
- No SQL query that searches trademarks by OCR text content
- No API endpoint
- No frontend UI

**Effort estimate:** LOW-MEDIUM:
1. Add trigram index on `logo_ocr_text`: `CREATE INDEX idx_tm_ocr_trgm ON trademarks USING GIN (logo_ocr_text gin_trgm_ops);`
2. Add `/api/search-by-logo-text` endpoint: query `WHERE similarity(logo_ocr_text, $query) > 0.3 ORDER BY similarity DESC`
3. Wire up frontend input

**Use case:** "Find all trademarks whose logos contain the text 'COFFEE'" — highly useful for trademark monitoring.

---

## 7. Unified & Agentic Search

### Orchestration logic

**`AgenticTrademarkSearch.search()`** (`agentic_search.py:122-359`) — 5-step pipeline:

| Step | Action | Condition |
|------|--------|-----------|
| 1 | Search local DB via `risk_engine.assess_brand_risk()` | Always |
| 2a | Decision: need live scrape? | `force_scrape` OR `max_score < 0.75` OR `no candidates` |
| 2b | Scrape TurkPatent live via Playwright | Only if step 2a = yes AND `auto_scrape = True` |
| 3 | Generate AI embeddings for scraped data | Only if scrape happened |
| 4 | Ingest scraped data into PostgreSQL | Only if scrape happened |
| 5 | Re-run `assess_brand_risk()` with updated DB | Only if scrape happened |

**Decision logic** (`agentic_search.py:185-189`):
```python
needs_live_search = (
    force_scrape or
    db_max_score < self.confidence_threshold or  # default 0.75
    len(db_candidates) == 0
)
```

### Result merging strategy

There is **no explicit merge** of DB and scrape results. Instead:
1. Scraped data is ingested INTO the database (Step 4)
2. The entire database is re-queried (Step 5)
3. The risk engine handles ranking via `assess_brand_risk()` → `calculate_hybrid_risk()`
4. Results sorted by `(exact_match DESC, scores.total DESC)` (`risk_engine.py:776`)

This means scrape results compete with existing DB records on equal footing. There is no "boosted because freshly scraped" signal.

### `/api/search/unified` merging

The unified endpoint (`main.py:1011`) handles 3 search types differently:
- **Text-only**: Same SQL as `/api/search`
- **Image-only**: CLIP cosine search, then `calculate_visual_similarity()` with OCR boost
- **Combined**: SQL combines both signals: `0.4 * image_sim + 0.6 * text_sim` as `combined_score`

No re-ranking step exists in any path. Results are scored once and sorted.

### Re-ranking

The `CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2')` is loaded in `risk_engine.py:438` but **NEVER USED** anywhere in the codebase. It was likely intended for a re-ranking step that was never implemented. This wastes ~120MB VRAM.

---

## 8. Risk Scoring Engine

### Full formula

The scoring pipeline has 3 stages:

**Stage 1: Signal Collection** (`risk_engine.py:689-707`)
Single SQL query computes 6 raw signals per candidate:

| Signal | Source | Type |
|--------|--------|------|
| `score_lexical` | `similarity(t.name, $query)` | pg_trgm float |
| `score_semantic` | `1 - (t.text_embedding <=> $vec)` | pgvector cosine |
| `score_clip` | `1 - (t.image_embedding <=> $vec)` | pgvector cosine |
| `score_dinov2` | `1 - (t.dinov2_embedding <=> $vec)` | pgvector cosine |
| `score_color` | `1 - (t.color_histogram <=> $vec)` | pgvector cosine |
| `phonetic_match` | `dmetaphone(t.name) = dmetaphone($query)` | boolean |
| `logo_ocr_text` | `t.logo_ocr_text` | string |
| `name_tr` | `t.name_tr` | string (pre-computed translation) |

**Stage 2: Signal Composition** (`risk_engine.py:88-117, 352-420`)

Visual composite (`calculate_visual_similarity()`):
```
visual_sim = CLIP * 0.35 + DINOv2 * 0.30 + color * 0.15 + OCR * 0.20
```

Text processing (`score_pair()`):
1. `text_sim = max(pg_trgm_score, calculate_turkish_similarity(query, candidate))`
2. `translation_sim = calculate_translation_similarity(query, candidate_name_tr)` (via NLLB-200)
3. `text_idf_score = compute_idf_weighted_score(query, candidate, text_sim, semantic_sim, phonetic_sim)` (Cases A-F)

**Stage 3: Dynamic Combination** (`_dynamic_combine()`, `risk_engine.py:282-349`)

Base weights:
```python
BASE_WEIGHTS = {"text": 0.60, "visual": 0.25, "translation": 0.15}
```

Dynamic boosting formula:
```python
STEEPNESS = 4.0
for signal in [text, visual, translation]:
    if score > 0:
        boosted_weight = base_weight * exp(score * STEEPNESS)
    else:
        boosted_weight = 0  # Dead signals excluded entirely
# Normalize weights to sum to 1.0
total = sum(score * normalized_weight for each active signal)
```

Floor rule: if `translation_sim >= 0.95`, total is floored at **0.90** (guarantees critical risk for near-perfect cross-language matches like APPLE ↔ ELMA).

### Weight breakdown

**Effective weight ranges** (vary by signal strength due to exponential boosting):

| Signal | Base Weight | When Strong (>0.8) | When Weak (<0.3) |
|--------|------------|---------------------|-------------------|
| Text (IDF) | 0.60 | ~50-70% | ~30-40% |
| Visual (composite) | 0.25 | ~20-40% | ~10-15% |
| Translation | 0.15 | ~10-25% | ~5-10% |

The exponential boosting (`STEEPNESS=4.0`) means the strongest signal dominates. If text scores 0.95 but visual scores 0.2, text gets ~85% of the weight rather than the base 60%.

### Thresholds

**Risk engine thresholds** (`risk_engine.py:66-73`) — the authoritative source:

| Level | Threshold | Meaning |
|-------|-----------|---------|
| critical | ≥ 0.90 | Identical or near-identical mark |
| very_high | ≥ 0.80 | Strong conflict likely |
| high | ≥ 0.70 | Significant similarity |
| medium | ≥ 0.50 | Moderate similarity, worth monitoring |
| low | < 0.50 | Low risk of confusion |

**Note:** `config/settings.py` has different thresholds in `MonitoringSettings` (0.90/0.75/0.60) but these are NOT used by the risk engine. The `RISK_THRESHOLDS` dict is the single source of truth.

### How the scoring pipeline is called

- **Sync, not async.** All scoring runs synchronously in the request thread.
- **No Redis caching** of scoring results. Only individual embeddings are cached.
- Models are loaded once at startup (shared via `ai.py` module globals).
- Connection pooling via `db.pool.ThreadedConnectionPool` (min=5, max=20).

---

## 9. Vector Indexes

### Current indexes

| Index Name | Table.Column | Type | Parameters | Partial? |
|------------|-------------|------|------------|----------|
| `idx_tm_image_vec` | `trademarks.image_embedding` | **HNSW** | `m=16, ef_construction=200` | YES (`WHERE IS NOT NULL`) |
| `idx_tm_text_vec` | `trademarks.text_embedding` | **HNSW** | `m=16, ef_construction=200` | NO |

**Non-vector indexes** relevant to search:

| Index Name | Table.Column | Type |
|-----------|-------------|------|
| `idx_tm_name_trgm` | `trademarks.name` | GIST (gist_trgm_ops) |
| `idx_tm_phonetic` | `trademarks.dmetaphone(name)` | btree (expression) |
| `idx_tm_nice_classes_arr` | `trademarks.nice_class_numbers` | GIN |
| `idx_trademarks_name_tr_trgm` | `trademarks.name_tr` | GIN (gin_trgm_ops) |
| `idx_trademarks_name_en_trgm` | `trademarks.name_en` | GIN (gin_trgm_ops) |
| `idx_trademarks_name_ku_trgm` | `trademarks.name_ku` | GIN (gin_trgm_ops) |
| `idx_trademarks_name_fa_trgm` | `trademarks.name_fa` | GIN (gin_trgm_ops) |
| `idx_watchlist_mt_name_trgm` | `watchlist_mt.brand_name` | GIST (gist_trgm_ops) |

### Missing indexes (performance risk)

| Column | Dimensions | Queried? | Impact | Recommended Index |
|--------|-----------|----------|--------|-------------------|
| `trademarks.dinov2_embedding` | 768 | YES — every risk assessment | **HIGH** | HNSW `m=16, ef_construction=200` |
| `trademarks.color_histogram` | 512 | YES — every risk assessment | **MEDIUM** | HNSW `m=16, ef_construction=200` |
| `nice_classes_lookup.description_embedding` | 384 | YES — class suggestion | **NONE** (only 45 rows) | Not needed |
| `watchlist_mt.*` embeddings | various | Used as query source | **NONE** (small table) | Not needed |
| `generated_images.clip_embedding` | 512 | Rarely | **NONE** | Not needed |
| `trademarks.logo_ocr_text` | text | Not searched standalone | **MEDIUM** (if OCR search added) | GIN gin_trgm_ops |

**Critical finding:** `dinov2_embedding` (768-dim) is queried via cosine distance in every `calculate_hybrid_risk()` call but has **NO index**. This forces a sequential scan on the entire trademarks table (~200K+ rows). Adding an HNSW index would dramatically improve risk assessment latency.

**Recommended DDL:**
```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_dinov2_vec
    ON trademarks USING hnsw (dinov2_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE dinov2_embedding IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tm_color_vec
    ON trademarks USING hnsw (color_histogram halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE color_histogram IS NOT NULL;
```

---

## 10. Frontend Search UI

### Current state

**Two search modes exposed:**

| Mode | Button | Auth | Image | Plan |
|------|--------|------|-------|------|
| Quick Search | Indigo solid | Required | No | Any (daily cap) |
| Intelligent/Live | Amber gradient + PRO badge | Required | Optional | Pro/Enterprise |

**Search input elements:**
- Text input with Enter-key shortcut (triggers Quick Search)
- Multi-select Nice class dropdown (classes 1-45)
- Image upload area (PNG/JPEG/WEBP) — only used with Intelligent Search
- Two credit badges (hidden by default, shown when relevant)

**Result card displays:**
- Score badge (percentage, color-coded by risk level)
- Thumbnail image (clickable lightbox)
- Trademark name + status
- Nice class badges (smart truncation)
- TURKPATENT app number (copyable + external link)
- Holder name (clickable for Pro, locked for Free)
- 3-bucket similarity breakdown badges:
  - **Metin** (Text): `max(text_similarity, semantic_similarity)`
  - **Gorsel** (Visual): composite of CLIP+DINOv2+color+OCR
  - **Ceviri** (Translation): translation_similarity
  - Only shown when score > 30%
- Extracted goods indicator (amber badge)
- AI Studio CTA buttons (when score ≥ 70%)
- Watchlist add button

**Pagination:** Server-side, 20 results per page, prev/next buttons

**Sorting:** Client-side toggle: risk ↑↓, date ↑↓

### Gaps

1. **No DINOv2-specific search** — only available as part of composite visual score
2. **No color search** — no color picker or "search by dominant color" feature
3. **No semantic-only search** — MiniLM score is rolled into "Metin" badge alongside trigram
4. **No OCR text search** — no "search trademarks by logo text" input
5. **No individual score breakdown** — users see 3 buckets (Metin/Gorsel/Ceviri) but not the 6 underlying signals (trigram, semantic, CLIP, DINOv2, color, OCR)
6. **Logo Studio detail panel** (`app.js:1340-1396`) DOES show individual CLIP/DINOv2/OCR/Color bars, but this is for AI-generated logos, not for search results

---

## 11. Recommendations

### Priority 1: Quick Wins (expose existing functions)

| # | Feature | Effort | What Exists | What's Needed |
|---|---------|--------|-------------|---------------|
| 1a | **Semantic search endpoint** | 2-3 hrs | MiniLM model loaded, encoding function cached, HNSW index exists, nearest-neighbor query in universal_scanner.py | New `/api/search-by-meaning` endpoint (~25 lines), frontend toggle |
| 1b | **Expose score breakdown** | 1-2 hrs | All 6 signals already returned by `calculate_hybrid_risk()` | Pass individual scores to frontend, add expandable detail section to result card |
| 1c | **Remove unused CrossEncoder** | 15 min | `risk_engine.py:438` loads but never uses it | Delete the load line, free ~120MB VRAM |

### Priority 2: New endpoints needed

| # | Feature | Effort | Dependencies |
|---|---------|--------|--------------|
| 2a | **OCR text search** ("search by logo text") | 4-6 hrs | Need GIN trigram index on `logo_ocr_text`, new endpoint, new UI input |
| 2b | **Enhanced visual search** (CLIP + DINOv2 composite) | 4-6 hrs | Need HNSW index on `dinov2_embedding`, new endpoint combining both signals |
| 2c | **Color-based search** | 8-12 hrs | Fix 32/512 dimension bug in `risk_engine._encode_single_image()`, add HNSW index on `color_histogram`, build color picker UI |
| 2d | **Class suggestion endpoint** | 1 hr | `suggest_classes()` already exists in RiskEngine, just needs an API route |

### Priority 3: Performance improvements

| # | Improvement | Impact | Effort |
|---|------------|--------|--------|
| 3a | **Add HNSW index on `dinov2_embedding`** | HIGH — eliminates sequential scan on 768-dim column for every risk assessment | `CREATE INDEX CONCURRENTLY` (~30 min build time) |
| 3b | **Add HNSW index on `color_histogram`** | MEDIUM — eliminates sequential scan on 512-dim column | `CREATE INDEX CONCURRENTLY` (~20 min build time) |
| 3c | **Fix color histogram dimension mismatch** | MEDIUM — current query-time histograms are 32-dim vs DB's 512-dim | Fix `_encode_single_image()` bins from [8,2,2] to [8,8,8] |
| 3d | **Add GIN trigram index on `logo_ocr_text`** | LOW-MEDIUM — enables OCR text search | Simple DDL |
| 3e | **Cache scoring results in Redis** | MEDIUM — avoid recomputing for repeated queries | Add cache layer in `assess_brand_risk()` |
| 3f | **Remove CrossEncoder from memory** | LOW — frees 120MB VRAM | Delete 1 line |

### Estimated effort per item

| Item | Time | Risk |
|------|------|------|
| 1a Semantic search endpoint | 2-3 hrs | Low — all pieces exist |
| 1b Score breakdown in UI | 1-2 hrs | Low — data already available |
| 1c Remove CrossEncoder | 15 min | None |
| 2a OCR text search | 4-6 hrs | Low — straightforward SQL + UI |
| 2b Enhanced visual search | 4-6 hrs | Medium — need to build HNSW index first |
| 2c Color-based search | 8-12 hrs | Medium — dimension bug, color picker UI |
| 2d Class suggestion endpoint | 1 hr | None — function exists |
| 3a DINOv2 HNSW index | 30 min DDL + build time | Low — standard pgvector operation |
| 3b Color histogram HNSW index | 20 min DDL + build time | Low |
| 3c Fix histogram dimension bug | 30 min code change | Low |
| 3d OCR trigram index | 5 min DDL | None |
| 3e Redis scoring cache | 4-6 hrs | Medium — cache invalidation design |
| 3f Remove CrossEncoder | 15 min | None |
