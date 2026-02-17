"""Test Turkish normalization across all stages."""
import sys, os, time
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
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({col},
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""

NORM_NAME = NORM.format(col='name')
NORM_OCR = NORM.format(col='logo_ocr_text')

# ══════════════════════════════════════════════════════════════
# TEST 1: Turkish char equivalence in OCR search
# ══════════════════════════════════════════════════════════════
print("=" * 90)
print("TEST 1: OCR search — Turkish chars normalized (ğ=g, ş=s, ö=o, ü=u, ç=c, ı=i)")
print("=" * 90)

# Search for "dogan" should find OCR text "DOĞAN" and vice versa
pairs = [
    ("dogan", "Should match DOĞAN in OCR"),
    ("güneş", "Should match GUNES in OCR (if exists)"),
    ("şeker", "Should match SEKER in OCR"),
    ("çelik", "Should match CELIK in OCR"),
    ("istanbul", "Should match İSTANBUL in OCR"),
]
for query, desc in pairs:
    # Normalize query same way as Python normalize_turkish
    norm_q = query.replace('ğ','g').replace('Ğ','g').replace('ı','i').replace('İ','i')
    norm_q = norm_q.replace('ö','o').replace('Ö','o').replace('ü','u').replace('Ü','u')
    norm_q = norm_q.replace('ş','s').replace('Ş','s').replace('ç','c').replace('Ç','c').lower()
    escaped = norm_q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')

    t0 = time.time()
    cur.execute(f"""
        SELECT name, logo_ocr_text
        FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND {NORM_OCR} LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 5
    """, (f'%{escaped}%',))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  '{query}' (norm: '{norm_q}') → {len(rows)} matches ({elapsed:.0f}ms)")
    print(f"  {desc}")
    for r in rows[:3]:
        ocr = r[1][:60] if r[1] else ""
        print(f"    - name: {r[0]:<30s} | OCR: {ocr}")

# ══════════════════════════════════════════════════════════════
# TEST 2: Cross-variant matching (search ASCII → find Turkish)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 2: ASCII query → finds Turkish OCR text")
print("=" * 90)

# "dogan" (ASCII) should find "DOĞAN" in OCR
for ascii_q, turkish_expected in [("dogan", "doğan"), ("celik", "çelik"), ("ozturk", "öztürk")]:
    escaped = ascii_q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    cur.execute(f"""
        SELECT name, logo_ocr_text
        FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND {NORM_OCR} LIKE %s ESCAPE '\\'
          AND logo_ocr_text ILIKE %s
          {DATE_FILTER}
        LIMIT 3
    """, (f'%{escaped}%', f'%{turkish_expected}%'))
    rows = cur.fetchall()
    found = "FOUND" if rows else "NOT FOUND"
    print(f"  '{ascii_q}' → matches OCR with '{turkish_expected}': {found} ({len(rows)} rows)")
    for r in rows[:2]:
        print(f"    - name: {r[0]:<30s} | OCR: {r[1][:50]}")

# ══════════════════════════════════════════════════════════════
# TEST 3: Verify GIN index is used for normalized OCR
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 3: GIN index usage for normalized OCR LIKE")
print("=" * 90)
cur.execute(f"""
    EXPLAIN ANALYZE
    SELECT id FROM trademarks
    WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
      AND {NORM_OCR} LIKE '%dogan%' ESCAPE '\\'
      AND current_status NOT IN ('Refused', 'Withdrawn')
      {DATE_FILTER}
    LIMIT 20
""")
plan = cur.fetchall()
uses_index = any('idx_tm_ocr_norm_gin' in str(r) or 'Bitmap Index' in str(r) for r in plan)
for r in plan:
    line = str(r[0])
    if 'Index' in line or 'Scan' in line or 'Execution' in line:
        print(f"  {line}")
print(f"\n  GIN index used: {'YES ✓' if uses_index else 'NO ✗'}")

# ══════════════════════════════════════════════════════════════
# TEST 4: Stage 5 trigram — normalized threshold check
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 4: Stage 5 trigram — normalized threshold catches Turkish variants")
print("=" * 90)

# "dogan" should match "doğan" via normalized similarity
for query, expected in [("dogan", "doğan"), ("celik", "çelik"), ("gunes", "güneş")]:
    # Raw similarity
    cur.execute("SELECT similarity(%s, %s)", (query, expected))
    raw_sim = cur.fetchone()[0]

    # Normalized similarity (both sides ASCII-folded)
    norm_expected = expected.replace('ğ','g').replace('ö','o').replace('ü','u').replace('ş','s').replace('ç','c').replace('ı','i')
    cur.execute("SELECT similarity(%s, %s)", (query, norm_expected))
    norm_sim = cur.fetchone()[0]

    passes_raw = raw_sim >= 0.3
    passes_norm = norm_sim >= 0.3

    print(f"  '{query}' vs '{expected}': raw_sim={raw_sim:.2f} ({'≥0.3 ✓' if passes_raw else '<0.3 ✗'}) | norm_sim={norm_sim:.2f} ({'≥0.3 ✓' if passes_norm else '<0.3 ✗'})")

# ══════════════════════════════════════════════════════════════
# TEST 5: OCR performance with new GIN index
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("TEST 5: OCR search performance with normalized GIN index")
print("=" * 90)
bench = ["star", "apple", "gold", "dogan", "celik", "samsung", "nike", "istanbul"]
times = []
for q in bench:
    escaped = q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND {NORM_OCR} LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 20
    """, (f'%{escaped}%',))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    print(f"  '{q}': {len(rows)} results in {elapsed:.0f}ms")

print(f"\n  Average: {sum(times)/len(times):.0f}ms")
print(f"  Min: {min(times):.0f}ms | Max: {max(times):.0f}ms")

conn.close()
print("\n✓ All normalization tests complete!")
