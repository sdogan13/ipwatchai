"""Full pipeline test: text + image combined candidate pool."""
import sys, os, time
sys.path.insert(0, '/app')

import psycopg2
from sentence_transformers import SentenceTransformer

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "postgres"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "trademark_db"),
    user=os.getenv("DB_USER", "turk_patent"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()

# Load text model for vector encoding
print("Loading MiniLM...")
text_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
print("Ready.\n")

# Helper: minimal RiskEngine with just DB + text model
from risk_engine import RiskEngine, normalize_turkish, turkish_lower
import logging

engine = RiskEngine.__new__(RiskEngine)
engine.conn = conn
engine.text_model = text_model
engine.logger = logging.getLogger('test')

# Grab a real trademark with image embeddings for testing
cur.execute("""
    SELECT id, name, image_embedding::text, dinov2_embedding::text, logo_ocr_text
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND dinov2_embedding IS NOT NULL
      AND logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
      AND name ILIKE '%star%'
    LIMIT 1
""")
row = cur.fetchone()
if not row:
    cur.execute("""
        SELECT id, name, image_embedding::text, dinov2_embedding::text, logo_ocr_text
        FROM trademarks
        WHERE image_embedding IS NOT NULL AND dinov2_embedding IS NOT NULL
          AND logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
        ORDER BY random() LIMIT 1
    """)
    row = cur.fetchone()

test_id, test_name, clip_vec_str, dino_vec_str, test_ocr = row
# Parse vectors from string
import ast
clip_vec = ast.literal_eval(clip_vec_str)
dino_vec = ast.literal_eval(dino_vec_str)

print(f"Test trademark: '{test_name}' (OCR: '{test_ocr[:50]}')")
print(f"CLIP vec: {len(clip_vec)} dims, DINOv2 vec: {len(dino_vec)} dims")

# ══════════════════════════════════════════════════════════════
# TEST 1: Text-only search (baseline)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 1: Text-only search (no image)")
print("=" * 90)
t0 = time.time()
text_only = engine.pre_screen_candidates(
    test_name, target_classes=None, limit=500,
    q_img_vec=None, q_dino_vec=None, q_ocr_text=None
)
elapsed_text = (time.time() - t0) * 1000
print(f"  '{test_name}' → {len(text_only)} candidates ({elapsed_text:.0f}ms)")
for r in text_only[:5]:
    print(f"    - {r[2]:<35s} (score: {r[5]:.2f})")

# ══════════════════════════════════════════════════════════════
# TEST 2: Text + Image combined search
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 2: Text + Image combined search")
print("=" * 90)
t0 = time.time()
combined = engine.pre_screen_candidates(
    test_name, target_classes=None, limit=500,
    q_img_vec=clip_vec, q_dino_vec=dino_vec, q_ocr_text=test_ocr
)
elapsed_combined = (time.time() - t0) * 1000
print(f"  '{test_name}' + image → {len(combined)} candidates ({elapsed_combined:.0f}ms)")
for r in combined[:5]:
    print(f"    - {r[2]:<35s} (score: {r[5]:.2f})")

# Compare: how many new candidates did image/OCR add?
text_ids = {r[0] for r in text_only}
combined_ids = {r[0] for r in combined}
new_from_visual = combined_ids - text_ids
print(f"\n  Text-only candidates:      {len(text_ids)}")
print(f"  Combined candidates:       {len(combined_ids)}")
print(f"  NEW from image/OCR stages: {len(new_from_visual)}")

# Show some of the new visual candidates
if new_from_visual:
    new_candidates = [r for r in combined if r[0] in new_from_visual][:8]
    print(f"\n  Sample new visual/OCR candidates:")
    for r in new_candidates:
        print(f"    - {r[2]:<35s} (score: {r[5]:.3f})")

# ══════════════════════════════════════════════════════════════
# TEST 3: Different query — "APPLE" with Apple's image
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 3: 'APPLE' search — text-only vs text+image")
print("=" * 90)
cur.execute("""
    SELECT image_embedding::text, dinov2_embedding::text, logo_ocr_text
    FROM trademarks
    WHERE name ILIKE 'apple' AND image_embedding IS NOT NULL
    LIMIT 1
""")
apple_row = cur.fetchone()
if apple_row:
    apple_clip = ast.literal_eval(apple_row[0])
    apple_dino = ast.literal_eval(apple_row[1])
    apple_ocr = apple_row[2] or ""

    t0 = time.time()
    apple_text = engine.pre_screen_candidates("APPLE", limit=500)
    t_text = (time.time() - t0) * 1000

    t0 = time.time()
    apple_both = engine.pre_screen_candidates(
        "APPLE", limit=500,
        q_img_vec=apple_clip, q_dino_vec=apple_dino, q_ocr_text=apple_ocr
    )
    t_both = (time.time() - t0) * 1000

    apple_text_ids = {r[0] for r in apple_text}
    apple_both_ids = {r[0] for r in apple_both}
    new_visual = apple_both_ids - apple_text_ids

    print(f"  Text-only:  {len(apple_text)} candidates ({t_text:.0f}ms)")
    print(f"  Text+Image: {len(apple_both)} candidates ({t_both:.0f}ms)")
    print(f"  NEW from visual: {len(new_visual)}")

    if new_visual:
        new_apple = [r for r in apple_both if r[0] in new_visual][:5]
        print(f"\n  Visual-only candidates (NOT found by text):")
        for r in new_apple:
            print(f"    - {r[2]:<35s} (score: {r[5]:.3f})")
else:
    print("  No Apple trademark with embeddings found, skipping")

# ══════════════════════════════════════════════════════════════
# TEST 4: Stage-by-stage timing breakdown
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 4: Per-stage timing (manual breakdown)")
print("=" * 90)

DATE_FILTER = "AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"
query = "SAMSUNG"

# Stage 1: Exact
t0 = time.time()
name_norm = normalize_turkish(query)
cur.execute(f"""
    SELECT id FROM trademarks
    WHERE LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')) = %s
    {DATE_FILTER} LIMIT 20
""", (name_norm,))
exact = cur.fetchall()
t_exact = (time.time() - t0) * 1000

# Stage 2: Containment
t0 = time.time()
escaped = name_norm.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
cur.execute(f"""
    SELECT id FROM trademarks
    WHERE LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')) LIKE %s ESCAPE '\\'
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY length(name) ASC LIMIT 30
""", (f'%{escaped}%',))
contain = cur.fetchall()
t_contain = (time.time() - t0) * 1000

# Stage 3: Text vector
t0 = time.time()
q_vec = text_model.encode(query).tolist()
cur.execute(f"""
    SELECT id FROM trademarks
    WHERE text_embedding IS NOT NULL
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY text_embedding <=> %s::halfvec LIMIT 50
""", (str(q_vec),))
vec = cur.fetchall()
t_vec = (time.time() - t0) * 1000

# Stage 4: Image vector (using samsung's image)
cur.execute("""
    SELECT image_embedding::text FROM trademarks
    WHERE name ILIKE 'samsung' AND image_embedding IS NOT NULL LIMIT 1
""")
sam_img = cur.fetchone()
if sam_img:
    t0 = time.time()
    cur.execute(f"""
        SELECT id FROM trademarks
        WHERE image_embedding IS NOT NULL
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY image_embedding <=> %s::halfvec LIMIT 50
    """, (sam_img[0],))
    img = cur.fetchall()
    t_img = (time.time() - t0) * 1000
else:
    t_img = 0
    img = []

# Stage 4.5: OCR
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks
    WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
    AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY length(name) ASC LIMIT 20
""", (f'%{escaped}%',))
ocr = cur.fetchall()
t_ocr = (time.time() - t0) * 1000

# Stage 5: Trigram
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks
    WHERE current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    AND GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) >= 0.3
    ORDER BY GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) DESC
    LIMIT 50
""", (query, query, query, query))
trgm = cur.fetchall()
t_trgm = (time.time() - t0) * 1000

print(f"  Query: '{query}'")
print(f"  ┌─────────────────────────────┬──────────┬──────────┐")
print(f"  │ Stage                       │ Results  │ Time     │")
print(f"  ├─────────────────────────────┼──────────┼──────────┤")
print(f"  │ 1a-c Exact match            │ {len(exact):>6}   │ {t_exact:>6.0f}ms │")
print(f"  │ 2    Containment            │ {len(contain):>6}   │ {t_contain:>6.0f}ms │")
print(f"  │ 3    Text vector (MiniLM)   │ {len(vec):>6}   │ {t_vec:>6.0f}ms │")
print(f"  │ 4    Image vector (CLIP)    │ {len(img):>6}   │ {t_img:>6.0f}ms │")
print(f"  │ 4.5  OCR text (GIN)         │ {len(ocr):>6}   │ {t_ocr:>6.0f}ms │")
print(f"  │ 5    Trigram (≥0.3)         │ {len(trgm):>6}   │ {t_trgm:>6.0f}ms │")
print(f"  ├─────────────────────────────┼──────────┼──────────┤")
total_r = len(exact)+len(contain)+len(vec)+len(img)+len(ocr)+len(trgm)
total_t = t_exact+t_contain+t_vec+t_img+t_ocr+t_trgm
print(f"  │ TOTAL (before dedup)        │ {total_r:>6}   │ {total_t:>6.0f}ms │")
print(f"  └─────────────────────────────┴──────────┴──────────┘")

# ══════════════════════════════════════════════════════════════
# TEST 5: Full pre_screen_candidates benchmark
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 5: Full pre_screen_candidates() — 5 queries with image")
print("=" * 90)

# Get 5 different trademarks with embeddings
cur.execute("""
    SELECT name, image_embedding::text, dinov2_embedding::text, COALESCE(logo_ocr_text,'')
    FROM trademarks
    WHERE image_embedding IS NOT NULL AND dinov2_embedding IS NOT NULL
    AND name IN ('apple','samsung','nike','zara','star')
    LIMIT 5
""")
test_marks = cur.fetchall()
if len(test_marks) < 3:
    cur.execute("""
        SELECT name, image_embedding::text, dinov2_embedding::text, COALESCE(logo_ocr_text,'')
        FROM trademarks
        WHERE image_embedding IS NOT NULL AND dinov2_embedding IS NOT NULL
        ORDER BY random() LIMIT 5
    """)
    test_marks = cur.fetchall()

times = []
for tm_name, tm_clip, tm_dino, tm_ocr in test_marks:
    cv = ast.literal_eval(tm_clip)
    dv = ast.literal_eval(tm_dino)
    t0 = time.time()
    results = engine.pre_screen_candidates(
        tm_name, limit=500,
        q_img_vec=cv, q_dino_vec=dv, q_ocr_text=tm_ocr
    )
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    print(f"  '{tm_name[:30]}': {len(results)} candidates in {elapsed:.0f}ms")

print(f"\n  Average: {sum(times)/len(times):.0f}ms")
print(f"  Min: {min(times):.0f}ms | Max: {max(times):.0f}ms")

conn.close()
print("\n✓ All full pipeline tests complete!")
