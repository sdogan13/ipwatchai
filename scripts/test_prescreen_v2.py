"""Test pre-screening pipeline with new enrichments:
1. Containment match for single-word queries
2. Minimum trigram threshold (0.3)
3. 11-year renewal window filter
4. Pool size 500
"""
import sys, os, time
sys.path.insert(0, '/app')

import psycopg2

def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD"),
    )

from risk_engine import RiskEngine

conn = get_conn()
engine = RiskEngine.__new__(RiskEngine)
engine.conn = conn
engine.logger = __import__('logging').getLogger('test')

# ── Test 1: Single-word containment search ──
print("=" * 90)
print("TEST 1: Single-word containment (STAR → STARLIGHT, GOLDSTAR, etc.)")
print("=" * 90)
test_words = ["STAR", "APPLE", "DOĞAN", "GOLD", "MEGA"]
for word in test_words:
    t0 = time.time()
    results = engine.pre_screen_candidates(word, target_classes=None, limit=500)
    elapsed = (time.time() - t0) * 1000
    # Check for containment matches
    containment_matches = [r for r in results if word.lower() in r[2].lower() and r[2].lower() != word.lower()]
    print(f"\n  Query: '{word}' → {len(results)} candidates ({elapsed:.0f}ms)")
    print(f"  Containment matches: {len(containment_matches)}")
    if containment_matches[:5]:
        for m in containment_matches[:5]:
            print(f"    - {m[2]} (score: {m[5]:.2f})")

# ── Test 2: Multi-word token LIKE (still works) ──
print("\n" + "=" * 90)
print("TEST 2: Multi-word token LIKE (DOGAN PATENT, GOLDEN STAR)")
print("=" * 90)
multi_words = ["DOGAN PATENT", "GOLDEN STAR", "KIRMIZI ELMA"]
for phrase in multi_words:
    t0 = time.time()
    results = engine.pre_screen_candidates(phrase, target_classes=None, limit=500)
    elapsed = (time.time() - t0) * 1000
    print(f"\n  Query: '{phrase}' → {len(results)} candidates ({elapsed:.0f}ms)")
    for m in results[:5]:
        print(f"    - {m[2]} (score: {m[5]:.2f})")

# ── Test 3: 11-year filter verification ──
print("\n" + "=" * 90)
print("TEST 3: 11-year filter — no expired marks in results")
print("=" * 90)
cur = conn.cursor()
results = engine.pre_screen_candidates("SAMSUNG", target_classes=None, limit=500)
print(f"  Query: 'SAMSUNG' → {len(results)} candidates")
# Check dates of returned candidates
old_count = 0
null_count = 0
valid_count = 0
for r in results:
    cur.execute("SELECT application_date FROM trademarks WHERE id = %s", (r[0],))
    row = cur.fetchone()
    if row and row[0]:
        from datetime import datetime, timedelta
        cutoff = datetime.now().date() - timedelta(days=11*365)
        if row[0] < cutoff:
            old_count += 1
            print(f"    FAIL: {r[2]} has date {row[0]} (before cutoff {cutoff})")
        else:
            valid_count += 1
    else:
        null_count += 1

print(f"  Valid (within 11yr): {valid_count}")
print(f"  NULL date (kept):    {null_count}")
print(f"  Expired (SHOULD BE 0): {old_count}")

# ── Test 4: Trigram threshold — no junk results ──
print("\n" + "=" * 90)
print("TEST 4: Trigram threshold ≥ 0.3 — no junk in pool")
print("=" * 90)
results = engine.pre_screen_candidates("NIKE", target_classes=None, limit=500)
print(f"  Query: 'NIKE' → {len(results)} candidates")
low_score = [r for r in results if r[5] < 0.3]
print(f"  Results with score < 0.3: {len(low_score)} (from exact/containment stages, OK if present)")
# Show score distribution
scores = [r[5] for r in results]
if scores:
    print(f"  Score range: {min(scores):.2f} — {max(scores):.2f}")
    print(f"  Mean score:  {sum(scores)/len(scores):.2f}")

# ── Test 5: Pool size reaches 500 ──
print("\n" + "=" * 90)
print("TEST 5: Pool size — can we fill 500?")
print("=" * 90)
big_queries = ["A", "MARKA", "GRUP"]
for q in big_queries:
    t0 = time.time()
    results = engine.pre_screen_candidates(q, target_classes=None, limit=500)
    elapsed = (time.time() - t0) * 1000
    print(f"  Query: '{q}' → {len(results)} candidates ({elapsed:.0f}ms)")

# ── Test 6: Class-filtered search ──
print("\n" + "=" * 90)
print("TEST 6: Class-filtered search (class 25 = clothing)")
print("=" * 90)
t0 = time.time()
results = engine.pre_screen_candidates("ZARA", target_classes=[25], limit=500)
elapsed = (time.time() - t0) * 1000
print(f"  Query: 'ZARA' class [25] → {len(results)} candidates ({elapsed:.0f}ms)")
for m in results[:8]:
    print(f"    - {m[2]} (classes: {m[3]}, score: {m[5]:.2f})")

# ── Test 7: Performance benchmark ──
print("\n" + "=" * 90)
print("TEST 7: Performance benchmark (5 queries, avg time)")
print("=" * 90)
bench_queries = ["APPLE", "DOĞAN PATENT", "SAMSUNG", "STAR", "İSTANBUL"]
times = []
for q in bench_queries:
    t0 = time.time()
    results = engine.pre_screen_candidates(q, target_classes=None, limit=500)
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    print(f"  '{q}': {len(results)} candidates in {elapsed:.0f}ms")

print(f"\n  Average: {sum(times)/len(times):.0f}ms")
print(f"  Max:     {max(times):.0f}ms")

conn.close()
print("\n✓ All tests complete!")
