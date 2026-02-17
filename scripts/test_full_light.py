"""Lightweight full pipeline test — SQL only, no heavy model loading."""
import sys, os, time, ast
sys.path.insert(0, '/app')
import psycopg2

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "postgres"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "trademark_db"),
    user=os.getenv("DB_USER", "turk_patent"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()
DATE_FILTER = "AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"
NORM = """LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""

# Get test data: a trademark with all embeddings
cur.execute("""
    SELECT name, image_embedding::text, text_embedding::text, logo_ocr_text
    FROM trademarks
    WHERE image_embedding IS NOT NULL AND text_embedding IS NOT NULL
      AND logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
      AND name ILIKE 'samsung'
    LIMIT 1
""")
row = cur.fetchone()
if not row:
    cur.execute("""
        SELECT name, image_embedding::text, text_embedding::text, logo_ocr_text
        FROM trademarks WHERE image_embedding IS NOT NULL AND text_embedding IS NOT NULL
        AND logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
        ORDER BY random() LIMIT 1
    """)
    row = cur.fetchone()

name, clip_str, text_str, ocr_text = row
print(f"Test: '{name}' (OCR: '{ocr_text[:40]}')")

# ══════════════════════════════════════════════════════════════
# TEST 1: Text-only vs Text+Image pool comparison
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 1: Text-only vs Text+Image candidate pool")
print("=" * 90)

query = name.lower()
escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

# Text stages
text_ids = set()
t_total = time.time()

# Stage 1: Exact
t0 = time.time()
cur.execute(f"SELECT id FROM trademarks WHERE {NORM} = %s {DATE_FILTER} LIMIT 20", (query,))
for r in cur.fetchall(): text_ids.add(r[0])
t_s1 = (time.time() - t0) * 1000

# Stage 2: Containment
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks WHERE {NORM} LIKE %s ESCAPE '\\'
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY length(name) ASC LIMIT 30
""", (f'%{escaped}%',))
for r in cur.fetchall(): text_ids.add(r[0])
t_s2 = (time.time() - t0) * 1000

# Stage 3: Text vector
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks WHERE text_embedding IS NOT NULL
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY text_embedding <=> %s::halfvec LIMIT 50
""", (text_str,))
for r in cur.fetchall(): text_ids.add(r[0])
t_s3 = (time.time() - t0) * 1000

# Stage 5: Trigram
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks WHERE current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    AND GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) >= 0.3
    ORDER BY GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) DESC
    LIMIT 200
""", (name, name, name, name))
for r in cur.fetchall(): text_ids.add(r[0])
t_s5 = (time.time() - t0) * 1000

t_text_total = (time.time() - t_total) * 1000
print(f"  Text-only pool: {len(text_ids)} unique candidates ({t_text_total:.0f}ms)")

# Now add image + OCR stages
combined_ids = set(text_ids)
t_img_total = time.time()

# Stage 4: Image vector (CLIP)
t0 = time.time()
cur.execute(f"""
    SELECT id FROM trademarks WHERE image_embedding IS NOT NULL
    AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
    ORDER BY image_embedding <=> %s::halfvec LIMIT 50
""", (clip_str,))
img_results = cur.fetchall()
for r in img_results: combined_ids.add(r[0])
t_s4 = (time.time() - t0) * 1000

# Stage 4.5: OCR text
t0 = time.time()
ocr_q = ocr_text.lower().split()[0] if ocr_text else ""
if len(ocr_q) >= 2:
    ocr_escaped = ocr_q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    cur.execute(f"""
        SELECT id FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
        AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 20
    """, (f'%{ocr_escaped}%',))
    for r in cur.fetchall(): combined_ids.add(r[0])
t_s45 = (time.time() - t0) * 1000

new_visual = combined_ids - text_ids

print(f"  Combined pool:  {len(combined_ids)} unique candidates (+{t_s4+t_s45:.0f}ms for img+ocr)")
print(f"  NEW from image/OCR: {len(new_visual)} candidates")

# ══════════════════════════════════════════════════════════════
# TEST 2: Per-stage timing table
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 2: Per-stage timing breakdown")
print("=" * 90)
print(f"  ┌──────────────────────────────┬──────────┬──────────┐")
print(f"  │ Stage                        │ Results  │ Time     │")
print(f"  ├──────────────────────────────┼──────────┼──────────┤")
print(f"  │ 1    Exact match             │     ~20  │ {t_s1:>6.0f}ms │")
print(f"  │ 2    Containment             │     ~30  │ {t_s2:>6.0f}ms │")
print(f"  │ 3    Text vector (HNSW)      │      50  │ {t_s3:>6.0f}ms │")
print(f"  │ 4    Image vector (CLIP)     │      50  │ {t_s4:>6.0f}ms │")
print(f"  │ 4.5  OCR text (GIN)          │     ~20  │ {t_s45:>6.0f}ms │")
print(f"  │ 5    Trigram (≥0.3)          │    ~200  │ {t_s5:>6.0f}ms │")
print(f"  ├──────────────────────────────┼──────────┼──────────┤")
total_t = t_s1+t_s2+t_s3+t_s4+t_s45+t_s5
print(f"  │ TOTAL                        │ {len(combined_ids):>6}   │ {total_t:>6.0f}ms │")
print(f"  └──────────────────────────────┴──────────┴──────────┘")

# ══════════════════════════════════════════════════════════════
# TEST 3: Multiple queries — text+image combined
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 3: Multiple queries — text+image combined pool")
print("=" * 90)

test_queries = ["apple", "nike", "star", "zara", "doğan"]
for q in test_queries:
    cur.execute("""
        SELECT name, image_embedding::text, text_embedding::text, COALESCE(logo_ocr_text,'')
        FROM trademarks WHERE LOWER(name) = %s
        AND image_embedding IS NOT NULL AND text_embedding IS NOT NULL
        LIMIT 1
    """, (q,))
    r = cur.fetchone()
    if not r:
        print(f"  '{q}': no test data, skipping")
        continue

    q_name, q_clip, q_text, q_ocr = r
    q_lower = q_name.lower()
    q_esc = q_lower.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')

    t0 = time.time()
    pool = set()

    # Text stages
    cur.execute(f"SELECT id FROM trademarks WHERE {NORM} = %s {DATE_FILTER} LIMIT 20", (q_lower,))
    for rr in cur.fetchall(): pool.add(rr[0])

    cur.execute(f"""SELECT id FROM trademarks WHERE {NORM} LIKE %s ESCAPE '\\'
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 30""", (f'%{q_esc}%',))
    for rr in cur.fetchall(): pool.add(rr[0])

    text_count = len(pool)

    cur.execute(f"""SELECT id FROM trademarks WHERE text_embedding IS NOT NULL
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY text_embedding <=> %s::halfvec LIMIT 50""", (q_text,))
    for rr in cur.fetchall(): pool.add(rr[0])

    # Image stage
    cur.execute(f"""SELECT id FROM trademarks WHERE image_embedding IS NOT NULL
        AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        ORDER BY image_embedding <=> %s::halfvec LIMIT 50""", (q_clip,))
    for rr in cur.fetchall(): pool.add(rr[0])

    # OCR stage
    if q_ocr and len(q_ocr.split()[0]) >= 2:
        ocr_word = q_ocr.lower().split()[0]
        ocr_esc = ocr_word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        cur.execute(f"""SELECT id FROM trademarks
            WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
            AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
            AND current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
            ORDER BY length(name) ASC LIMIT 20""", (f'%{ocr_esc}%',))
        for rr in cur.fetchall(): pool.add(rr[0])

    # Trigram
    cur.execute(f"""SELECT id FROM trademarks
        WHERE current_status NOT IN ('Refused','Withdrawn') {DATE_FILTER}
        AND similarity(name, %s) >= 0.3
        ORDER BY similarity(name, %s) DESC LIMIT 200""", (q_name, q_name))
    for rr in cur.fetchall(): pool.add(rr[0])

    elapsed = (time.time() - t0) * 1000
    visual_added = len(pool) - text_count
    print(f"  '{q_name}': {len(pool)} total ({visual_added} from image/OCR) in {elapsed:.0f}ms")

# ══════════════════════════════════════════════════════════════
# TEST 4: Verify 11-year filter across ALL stages
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 4: 11-year filter verification on combined pool")
print("=" * 90)
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=11*365)).date()

# Use combined pool from Samsung
expired = 0
valid = 0
null_date = 0
for cid in list(combined_ids)[:200]:
    cur.execute("SELECT application_date FROM trademarks WHERE id = %s", (cid,))
    d = cur.fetchone()
    if d and d[0]:
        if d[0] < cutoff:
            expired += 1
        else:
            valid += 1
    else:
        null_date += 1

print(f"  Checked {valid+null_date+expired} candidates from Samsung combined pool:")
print(f"  Valid (within 11yr): {valid}")
print(f"  NULL date (kept):    {null_date}")
print(f"  Expired (FAIL):      {expired}")
print(f"  {'PASS ✓' if expired == 0 else 'FAIL ✗ — expired marks found!'}")

conn.close()
print("\n✓ All tests complete!")
