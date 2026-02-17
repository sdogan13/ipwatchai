"""Quick test: Stage 4 CLIP-only image search via HNSW."""
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

# Grab 5 random trademarks with CLIP embeddings
cur.execute("""
    SELECT id, name, image_embedding::text
    FROM trademarks
    WHERE image_embedding IS NOT NULL
    ORDER BY random() LIMIT 5
""")
samples = cur.fetchall()

print("CLIP-only image search (Stage 4 — HNSW indexed)")
print("=" * 70)
times = []
for s_id, s_name, s_clip in samples:
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name,
               (1 - (image_embedding <=> %s::halfvec)) as clip_sim
        FROM trademarks
        WHERE image_embedding IS NOT NULL
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY image_embedding <=> %s::halfvec
        LIMIT 50
    """, (s_clip, s_clip))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    # Show top 3 (skip self at #1)
    top3 = [(r[1], r[2]) for r in rows if r[0] != s_id][:3]
    print(f"\n  '{s_name[:35]}' → {len(rows)} results in {elapsed:.0f}ms")
    for name, sim in top3:
        print(f"    - {name:<35s} (sim: {sim:.3f})")

print(f"\n{'='*70}")
print(f"  Average: {sum(times)/len(times):.0f}ms")
print(f"  Min:     {min(times):.0f}ms | Max: {max(times):.0f}ms")

# Stats
cur.execute(f"SELECT COUNT(*) FROM trademarks WHERE image_embedding IS NOT NULL {DATE_FILTER}")
cnt = cur.fetchone()[0]
print(f"  Searchable images (11yr): {cnt:,}")

conn.close()
print("\n✓ Done!")
