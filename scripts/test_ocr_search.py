"""Test OCR text search stage (Stage 4.5) in pre-screening pipeline."""
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

# Test 1: OCR search for common brand names
print("=" * 90)
print("TEST 1: OCR text search — find logos containing brand text")
print("=" * 90)
queries = ["star", "apple", "gold", "nike", "samsung", "doğan"]
for q in queries:
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name, logo_ocr_text,
               similarity(name, %s) as name_sim
        FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY length(name) ASC
        LIMIT 10
    """, (q, f'%{escaped}%'))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  OCR search '{q}' → {len(rows)} matches ({elapsed:.0f}ms)")
    for r in rows[:5]:
        ocr_text = r[2][:50] if r[2] else ""
        print(f"    - name: {r[1]:<30s} | OCR: {ocr_text:<50s}")

# Test 2: OCR catches what name search misses
print("\n" + "=" * 90)
print("TEST 2: OCR finds trademarks with text IN the logo but NOT in the name")
print("=" * 90)
for q in ["star", "gold", "royal"]:
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    t0 = time.time()
    cur.execute(f"""
        SELECT name, logo_ocr_text
        FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
          AND LOWER(name) NOT LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY length(name) ASC
        LIMIT 5
    """, (f'%{escaped}%', f'%{escaped}%'))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  '{q}' in OCR but NOT in name → {len(rows)} matches ({elapsed:.0f}ms)")
    for r in rows:
        ocr_text = r[1][:60] if r[1] else ""
        print(f"    - name: {r[0]:<30s} | OCR: {ocr_text}")

# Test 3: OCR from uploaded logo simulation
print("\n" + "=" * 90)
print("TEST 3: Simulating uploaded logo OCR text search")
print("=" * 90)
# Grab a real OCR text from a trademark and search for similar
cur.execute("""
    SELECT name, logo_ocr_text FROM trademarks
    WHERE logo_ocr_text IS NOT NULL AND length(logo_ocr_text) > 5
    AND logo_ocr_text NOT LIKE '%&%'
    ORDER BY random() LIMIT 3
""")
samples = cur.fetchall()
for s_name, s_ocr in samples:
    # Use first meaningful word from OCR as search term
    words = [w for w in s_ocr.lower().split() if len(w) > 2]
    if not words:
        continue
    search_word = words[0]
    escaped = search_word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    t0 = time.time()
    cur.execute(f"""
        SELECT name, logo_ocr_text
        FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
          {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 5
    """, (f'%{escaped}%',))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  Source: '{s_name}' → OCR: '{s_ocr[:50]}' → search '{search_word}'")
    print(f"  Found {len(rows)} matches ({elapsed:.0f}ms)")
    for r in rows[:3]:
        print(f"    - {r[0]:<30s} | OCR: {r[1][:50]}")

# Test 4: Performance
print("\n" + "=" * 90)
print("TEST 4: Performance benchmark")
print("=" * 90)
bench = ["star", "apple", "gold", "coffee", "pharma", "tech", "medical", "food"]
times = []
for q in bench:
    escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
          AND LOWER(logo_ocr_text) LIKE %s ESCAPE '\\'
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
print("\n✓ All OCR search tests complete!")
