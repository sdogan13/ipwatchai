"""Test image-only search — now routes through full pipeline with OCR text."""
import sys, os, time, ast
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

text_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')

from risk_engine import normalize_turkish
import logging

DATE_FILTER = "AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"
NORM_OCR = """LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(logo_ocr_text,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""
NORM_NAME = """LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""

# Get test trademarks with image + OCR
cur.execute("""
    SELECT name, image_embedding::text, text_embedding::text, logo_ocr_text
    FROM trademarks
    WHERE image_embedding IS NOT NULL AND text_embedding IS NOT NULL
      AND logo_ocr_text IS NOT NULL AND length(logo_ocr_text) > 3
      AND name ILIKE 'samsung'
    LIMIT 1
""")
row = cur.fetchone()
if not row:
    cur.execute("""
        SELECT name, image_embedding::text, text_embedding::text, logo_ocr_text
        FROM trademarks
        WHERE image_embedding IS NOT NULL AND text_embedding IS NOT NULL
          AND logo_ocr_text IS NOT NULL AND length(logo_ocr_text) > 3
        ORDER BY random() LIMIT 1
    """)
    row = cur.fetchone()

tm_name, clip_str, text_str, ocr_text = row
clip_vec = ast.literal_eval(clip_str)

print(f"Simulating IMAGE-ONLY search using: '{tm_name}' (OCR: '{ocr_text[:50]}')")
print(f"(User uploads logo, no text query)")

# ══════════════════════════════════════════════════════════════
# TEST 1: OLD path — image vector only (what pre_screen_by_image did)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 1: OLD path — CLIP vector search only (limit=20)")
print("=" * 90)
t0 = time.time()
cur.execute(f"""
    SELECT id, name, (1 - (image_embedding <=> %s::halfvec)) as vis
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND current_status NOT IN ('Refused', 'Withdrawn')
      {DATE_FILTER}
    ORDER BY image_embedding <=> %s::halfvec
    LIMIT 20
""", (clip_str, clip_str))
old_results = cur.fetchall()
t_old = (time.time() - t0) * 1000
old_ids = {r[0] for r in old_results}
print(f"  OLD: {len(old_results)} candidates ({t_old:.0f}ms)")
for r in old_results[:5]:
    print(f"    - {r[1]:<35s} (vis: {r[2]:.3f})")

# ══════════════════════════════════════════════════════════════
# TEST 2: NEW path — full pipeline using OCR text + image vectors
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 2: NEW path — full 7-stage pipeline (OCR as text query)")
print("=" * 90)

# Simulate what the new code does: use OCR text as name_input
ocr_name = ocr_text.strip()
norm_ocr = normalize_turkish(ocr_name)
escaped = norm_ocr.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')

new_ids = set()
t_total = time.time()

# Stage 1: Exact match on OCR text
t0 = time.time()
cur.execute(f"""
    SELECT id, name FROM trademarks
    WHERE {NORM_NAME} = %s {DATE_FILTER} LIMIT 20
""", (norm_ocr,))
s1 = cur.fetchall()
for r in s1: new_ids.add(r[0])
t_s1 = (time.time() - t0) * 1000

# Stage 2: Containment (single word from OCR)
t0 = time.time()
ocr_words = norm_ocr.split()
if len(ocr_words) == 1 and len(ocr_words[0]) >= 2:
    esc_w = ocr_words[0].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE {NORM_NAME} LIKE %s ESCAPE '\\'
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 30
    """, (f'%{esc_w}%',))
    s2 = cur.fetchall()
    for r in s2: new_ids.add(r[0])
else:
    s2 = []
t_s2 = (time.time() - t0) * 1000

# Stage 3: Text vector (encode OCR text)
t0 = time.time()
if ocr_name:
    ocr_vec = text_model.encode(ocr_name).tolist()
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE text_embedding IS NOT NULL
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY text_embedding <=> %s::halfvec LIMIT 50
    """, (str(ocr_vec),))
    s3 = cur.fetchall()
    for r in s3: new_ids.add(r[0])
else:
    s3 = []
t_s3 = (time.time() - t0) * 1000

# Stage 4: Image vector (CLIP)
t0 = time.time()
cur.execute(f"""
    SELECT id, name FROM trademarks
    WHERE image_embedding IS NOT NULL
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY image_embedding <=> %s::halfvec LIMIT 50
""", (clip_str,))
s4 = cur.fetchall()
for r in s4: new_ids.add(r[0])
t_s4 = (time.time() - t0) * 1000

# Stage 4.5: OCR text search
t0 = time.time()
if ocr_name and len(norm_ocr) >= 2:
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
        AND {NORM_OCR} LIKE %s ESCAPE '\\'
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 20
    """, (f'%{escaped}%',))
    s45 = cur.fetchall()
    for r in s45: new_ids.add(r[0])
else:
    s45 = []
t_s45 = (time.time() - t0) * 1000

# Stage 5: Trigram
t0 = time.time()
if ocr_name:
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        AND similarity(name, %s) >= 0.3
        ORDER BY similarity(name, %s) DESC LIMIT 200
    """, (ocr_name, ocr_name))
    s5 = cur.fetchall()
    for r in s5: new_ids.add(r[0])
else:
    s5 = []
t_s5 = (time.time() - t0) * 1000

t_new = (time.time() - t_total) * 1000

print(f"  ┌──────────────────────────────┬──────────┬──────────┐")
print(f"  │ Stage                        │ Results  │ Time     │")
print(f"  ├──────────────────────────────┼──────────┼──────────┤")
print(f"  │ 1    Exact (OCR text)        │ {len(s1):>6}   │ {t_s1:>6.0f}ms │")
print(f"  │ 2    Containment             │ {len(s2):>6}   │ {t_s2:>6.0f}ms │")
print(f"  │ 3    Text vector (OCR→emb)   │ {len(s3):>6}   │ {t_s3:>6.0f}ms │")
print(f"  │ 4    Image vector (CLIP)     │ {len(s4):>6}   │ {t_s4:>6.0f}ms │")
print(f"  │ 4.5  OCR text search         │ {len(s45):>6}   │ {t_s45:>6.0f}ms │")
print(f"  │ 5    Trigram (OCR text)       │ {len(s5):>6}   │ {t_s5:>6.0f}ms │")
print(f"  ├──────────────────────────────┼──────────┼──────────┤")
print(f"  │ TOTAL (deduped)              │ {len(new_ids):>6}   │ {t_new:>6.0f}ms │")
print(f"  └──────────────────────────────┴──────────┴──────────┘")

# Compare old vs new
extra = new_ids - old_ids
print(f"\n  OLD path (vector only):  {len(old_ids)} candidates")
print(f"  NEW path (full pipeline): {len(new_ids)} candidates")
print(f"  Additional candidates:    +{len(extra)}")

# ══════════════════════════════════════════════════════════════
# TEST 3: Multiple image-only searches
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 3: Multiple image-only searches (various logos)")
print("=" * 90)
cur.execute("""
    SELECT name, image_embedding::text, logo_ocr_text
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND logo_ocr_text IS NOT NULL AND length(logo_ocr_text) > 3
    ORDER BY random() LIMIT 5
""")
samples = cur.fetchall()

for s_name, s_clip, s_ocr in samples:
    ocr_n = normalize_turkish(s_ocr.strip())
    esc = ocr_n.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')

    t0 = time.time()
    pool = set()

    # Image stage
    cur.execute(f"""SELECT id FROM trademarks WHERE image_embedding IS NOT NULL
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY image_embedding <=> %s::halfvec LIMIT 50""", (s_clip,))
    img_count = 0
    for r in cur.fetchall(): pool.add(r[0]); img_count += 1

    # OCR stage
    ocr_count = 0
    if len(ocr_n) >= 2:
        cur.execute(f"""SELECT id FROM trademarks
            WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
            AND {NORM_OCR} LIKE %s ESCAPE '\\' {DATE_FILTER}
            ORDER BY length(name) ASC LIMIT 20""", (f'%{esc}%',))
        for r in cur.fetchall():
            if r[0] not in pool: ocr_count += 1
            pool.add(r[0])

    # Text stages using OCR text
    txt_count = 0
    if s_ocr.strip():
        ocr_vec = text_model.encode(s_ocr.strip()).tolist()
        cur.execute(f"""SELECT id FROM trademarks WHERE text_embedding IS NOT NULL
            AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
            ORDER BY text_embedding <=> %s::halfvec LIMIT 50""", (str(ocr_vec),))
        for r in cur.fetchall():
            if r[0] not in pool: txt_count += 1
            pool.add(r[0])

    elapsed = (time.time() - t0) * 1000
    print(f"  '{s_name[:25]}' (OCR: '{s_ocr[:25]}') → {len(pool)} total (img:{img_count} ocr:{ocr_count} txt:{txt_count}) {elapsed:.0f}ms")

conn.close()
print("\n✓ All image-only tests complete!")
