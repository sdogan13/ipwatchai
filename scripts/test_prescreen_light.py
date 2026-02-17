"""Lightweight pre-screening test — no AI model loading, just SQL."""
import sys, os, time
sys.path.insert(0, '/app')
os.environ.setdefault("DB_HOST", "postgres")
os.environ.setdefault("DB_PORT", "5432")

import psycopg2
from datetime import datetime, timedelta

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "postgres"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "trademark_db"),
    user=os.getenv("DB_USER", "turk_patent"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor()

# Set trigram threshold
cur.execute("SET pg_trgm.similarity_threshold = 0.3;")

NORMALIZE_SQL = """LOWER(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""

DATE_FILTER = "AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

print("=" * 90)
print("TEST 1: Single-word CONTAINMENT (Stage 2)")
print("=" * 90)
for word in ["star", "apple", "dogan", "gold", "nike"]:
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name, nice_class_numbers,
               similarity(name, %s) as sim
        FROM trademarks
        WHERE {NORMALIZE_SQL} LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY length(name) ASC
        LIMIT 30
    """, (word, f'%{word}%'))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  '{word}' → {len(rows)} containment matches ({elapsed:.0f}ms)")
    for r in rows[:5]:
        print(f"    - {r[1]} (classes: {r[2]}, sim: {r[3]:.2f})")

print("\n" + "=" * 90)
print("TEST 2: TRIGRAM with 0.3 threshold (Stage 5)")
print("=" * 90)
for word in ["STAR", "APPLE", "DOĞAN", "SAMSUNG", "NIKE", "ZARA"]:
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name, nice_class_numbers,
               GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) as sim
        FROM trademarks
        WHERE current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
          AND GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) >= 0.3
        ORDER BY GREATEST(similarity(name, %s), COALESCE(similarity(name_tr, %s), 0)) DESC
        LIMIT 50
    """, (word, word, word, word, word, word))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  '{word}' → {len(rows)} trigram matches ({elapsed:.0f}ms)")
    for r in rows[:5]:
        print(f"    - {r[1]} (sim: {r[3]:.2f})")

print("\n" + "=" * 90)
print("TEST 3: 11-year filter — verify no expired marks")
print("=" * 90)
cur.execute(f"""
    SELECT id, name, application_date
    FROM trademarks
    WHERE {NORMALIZE_SQL} LIKE '%samsung%' ESCAPE '\\'
      {DATE_FILTER}
    ORDER BY application_date ASC NULLS LAST
    LIMIT 5
""")
rows = cur.fetchall()
cutoff = (datetime.now() - timedelta(days=11*365)).date()
print(f"  Cutoff date: {cutoff}")
for r in rows:
    status = "OK" if r[2] is None or r[2] >= cutoff else "FAIL - EXPIRED"
    print(f"  - {r[1]} | date: {r[2]} | {status}")

# Check if any expired would have been in old results
cur.execute("""
    SELECT COUNT(*) FROM trademarks
    WHERE LOWER(name) LIKE '%samsung%'
      AND application_date < NOW() - INTERVAL '11 years'
""")
old_count = cur.fetchone()[0]
print(f"\n  Expired 'samsung' marks filtered out: {old_count}")

print("\n" + "=" * 90)
print("TEST 4: Trigram threshold — compare with and without 0.3 floor")
print("=" * 90)
for word in ["NIKE", "STAR"]:
    # Without floor
    t0 = time.time()
    cur.execute(f"""
        SELECT COUNT(*)
        FROM trademarks
        WHERE current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
          AND similarity(name, %s) > 0
        """, (word,))
    no_floor = cur.fetchone()[0]
    t_no = (time.time() - t0) * 1000

    # With 0.3 floor
    t0 = time.time()
    cur.execute(f"""
        SELECT COUNT(*)
        FROM trademarks
        WHERE current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
          AND similarity(name, %s) >= 0.3
        """, (word,))
    with_floor = cur.fetchone()[0]
    t_with = (time.time() - t0) * 1000

    print(f"  '{word}': no floor → {no_floor:,} candidates ({t_no:.0f}ms) | ≥0.3 → {with_floor:,} candidates ({t_with:.0f}ms)")
    print(f"          Junk filtered: {no_floor - with_floor:,} ({(no_floor - with_floor)/max(no_floor,1)*100:.0f}%)")

print("\n" + "=" * 90)
print("TEST 5: Pool size — full pipeline stages combined")
print("=" * 90)
for word in ["STAR", "ZARA", "APPLE"]:
    seen = set()
    total = 0

    # Stage 1: Exact
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE {NORMALIZE_SQL} = %s {DATE_FILTER}
        LIMIT 20
    """, (word.lower(),))
    exact = cur.fetchall()
    for r in exact:
        if r[0] not in seen: seen.add(r[0]); total += 1

    # Stage 2: Containment
    cur.execute(f"""
        SELECT id, name FROM trademarks
        WHERE {NORMALIZE_SQL} LIKE %s ESCAPE '\\'
          AND current_status NOT IN ('Refused', 'Withdrawn') {DATE_FILTER}
        ORDER BY length(name) ASC LIMIT 30
    """, (f'%{word.lower()}%',))
    contain = cur.fetchall()
    for r in contain:
        if r[0] not in seen: seen.add(r[0]); total += 1

    # Stage 5: Trigram
    remaining = 500 - total
    cur.execute(f"""
        SELECT id, name, similarity(name, %s) as sim
        FROM trademarks
        WHERE current_status NOT IN ('Refused', 'Withdrawn') {DATE_FILTER}
          AND similarity(name, %s) >= 0.3
        ORDER BY similarity(name, %s) DESC LIMIT %s
    """, (word, word, word, remaining))
    trgm = cur.fetchall()
    for r in trgm:
        if r[0] not in seen: seen.add(r[0]); total += 1

    elapsed = (time.time() - t0) * 1000
    print(f"  '{word}': exact={len(exact)}, contain={len(contain)}, trigram={len(trgm)} → TOTAL: {total} ({elapsed:.0f}ms)")

conn.close()
print("\n✓ All tests complete!")
