"""Test vector search stage (Stage 3) in pre-screening pipeline."""
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

# Load just the text model (lightweight)
print("Loading MiniLM text model...")
t0 = time.time()
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
print(f"Model loaded in {(time.time()-t0)*1000:.0f}ms")

DATE_FILTER = "AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"

print("\n" + "=" * 90)
print("TEST 1: Vector search — semantic matches")
print("=" * 90)
queries = [
    ("APPLE", "Should find apple/elma-related marks"),
    ("STAR", "Should find star/yıldız-related marks"),
    ("DOĞAN", "Should find doğan and similar"),
    ("SAMSUNG", "Should find samsung variants"),
    ("golden eagle", "Should find altın kartal / eagle brands"),
    ("red lion", "Should find kırmızı aslan / lion brands"),
]

for query, desc in queries:
    vec = model.encode(query).tolist()
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name, nice_class_numbers,
               (1 - (text_embedding <=> %s::halfvec)) as sim
        FROM trademarks
        WHERE text_embedding IS NOT NULL
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY text_embedding <=> %s::halfvec
        LIMIT 20
    """, (str(vec), str(vec)))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    print(f"\n  '{query}' ({desc}) → {len(rows)} matches ({elapsed:.0f}ms)")
    for r in rows[:8]:
        print(f"    - {r[1]:<35s} (sim: {r[3]:.3f})")

print("\n" + "=" * 90)
print("TEST 2: Cross-language discovery")
print("=" * 90)
cross_lang = [
    ("apple", "Will vector search find Turkish 'elma'?"),
    ("star", "Will vector search find Turkish 'yıldız'?"),
    ("sun", "Will vector search find Turkish 'güneş'?"),
    ("water", "Will vector search find Turkish 'su'?"),
]
for query, desc in cross_lang:
    vec = model.encode(query).tolist()
    t0 = time.time()
    cur.execute(f"""
        SELECT name, (1 - (text_embedding <=> %s::halfvec)) as sim
        FROM trademarks
        WHERE text_embedding IS NOT NULL {DATE_FILTER}
        ORDER BY text_embedding <=> %s::halfvec
        LIMIT 10
    """, (str(vec), str(vec)))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    names = [r[0] for r in rows]
    print(f"\n  '{query}' ({desc}) → {elapsed:.0f}ms")
    for r in rows[:6]:
        print(f"    - {r[0]:<30s} (sim: {r[1]:.3f})")

print("\n" + "=" * 90)
print("TEST 3: Performance benchmark — 10 queries")
print("=" * 90)
bench = ["APPLE", "NIKE", "STAR", "ZARA", "DOĞAN", "SAMSUNG", "COCA COLA", "MERCEDES", "red bull", "golden eagle"]
times = []
for q in bench:
    vec = model.encode(q).tolist()
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name, (1 - (text_embedding <=> %s::halfvec)) as sim
        FROM trademarks
        WHERE text_embedding IS NOT NULL
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY text_embedding <=> %s::halfvec
        LIMIT 50
    """, (str(vec), str(vec)))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    print(f"  '{q}': {len(rows)} results in {elapsed:.0f}ms")

print(f"\n  Average: {sum(times)/len(times):.0f}ms")
print(f"  Min:     {min(times):.0f}ms")
print(f"  Max:     {max(times):.0f}ms")

conn.close()
print("\n✓ All vector search tests complete!")
