"""Test image vector search stage (Stage 4) in pre-screening pipeline."""
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

# Pick a real trademark's image embedding to use as a test query
print("=" * 90)
print("TEST 1: Grab a real trademark's CLIP + DINOv2 embeddings as query")
print("=" * 90)
cur.execute("""
    SELECT id, name, image_embedding::text, dinov2_embedding::text
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND dinov2_embedding IS NOT NULL
      AND name ILIKE '%apple%'
    LIMIT 1
""")
row = cur.fetchone()
if not row:
    print("No trademark with embeddings found, trying any...")
    cur.execute("""
        SELECT id, name, image_embedding::text, dinov2_embedding::text
        FROM trademarks
        WHERE image_embedding IS NOT NULL AND dinov2_embedding IS NOT NULL
        LIMIT 1
    """)
    row = cur.fetchone()

if not row:
    print("ERROR: No trademarks with embeddings in DB!")
    sys.exit(1)

test_id, test_name, clip_vec, dino_vec = row
print(f"  Using: '{test_name}' (id={test_id})")
print(f"  CLIP vec length: {len(clip_vec.split(','))}")
print(f"  DINOv2 vec length: {len(dino_vec.split(','))}")

# Test CLIP-only search
print("\n" + "=" * 90)
print("TEST 2: CLIP image vector search (Stage 4)")
print("=" * 90)
t0 = time.time()
cur.execute(f"""
    SELECT id, name, nice_class_numbers,
           (1 - (image_embedding <=> %s::halfvec)) as clip_sim
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND current_status NOT IN ('Refused', 'Withdrawn')
      {DATE_FILTER}
      AND id != %s
    ORDER BY image_embedding <=> %s::halfvec
    LIMIT 20
""", (clip_vec, test_id, clip_vec))
rows = cur.fetchall()
elapsed = (time.time() - t0) * 1000
print(f"  CLIP search for '{test_name}' → {len(rows)} matches ({elapsed:.0f}ms)")
for r in rows[:8]:
    print(f"    - {r[1]:<35s} (clip_sim: {r[3]:.3f})")

# Test DINOv2-only search
print("\n" + "=" * 90)
print("TEST 3: DINOv2 image vector search")
print("=" * 90)
t0 = time.time()
cur.execute(f"""
    SELECT id, name, nice_class_numbers,
           (1 - (dinov2_embedding <=> %s::halfvec)) as dino_sim
    FROM trademarks
    WHERE dinov2_embedding IS NOT NULL
      AND current_status NOT IN ('Refused', 'Withdrawn')
      {DATE_FILTER}
      AND id != %s
    ORDER BY dinov2_embedding <=> %s::halfvec
    LIMIT 20
""", (dino_vec, test_id, dino_vec))
rows = cur.fetchall()
elapsed = (time.time() - t0) * 1000
print(f"  DINOv2 search for '{test_name}' → {len(rows)} matches ({elapsed:.0f}ms)")
for r in rows[:8]:
    print(f"    - {r[1]:<35s} (dino_sim: {r[3]:.3f})")

# Test combined GREATEST(CLIP, DINOv2)
print("\n" + "=" * 90)
print("TEST 4: Combined CLIP + DINOv2 search")
print("=" * 90)
t0 = time.time()
cur.execute(f"""
    SELECT id, name, nice_class_numbers,
           GREATEST(
               (1 - (image_embedding <=> %s::halfvec)),
               (1 - (dinov2_embedding <=> %s::halfvec))
           ) as visual_sim
    FROM trademarks
    WHERE image_embedding IS NOT NULL
      AND current_status NOT IN ('Refused', 'Withdrawn')
      {DATE_FILTER}
      AND id != %s
    ORDER BY GREATEST(
        (1 - (image_embedding <=> %s::halfvec)),
        (1 - (dinov2_embedding <=> %s::halfvec))
    ) DESC
    LIMIT 20
""", (clip_vec, dino_vec, test_id, clip_vec, dino_vec))
rows = cur.fetchall()
elapsed = (time.time() - t0) * 1000
print(f"  Combined search for '{test_name}' → {len(rows)} matches ({elapsed:.0f}ms)")
for r in rows[:8]:
    print(f"    - {r[1]:<35s} (visual_sim: {r[3]:.3f})")

# Performance benchmark
print("\n" + "=" * 90)
print("TEST 5: Performance benchmark — image search speed")
print("=" * 90)
# Get 5 different trademarks' embeddings
cur.execute("""
    SELECT id, name, image_embedding::text, dinov2_embedding::text
    FROM trademarks
    WHERE image_embedding IS NOT NULL AND dinov2_embedding IS NOT NULL
    ORDER BY random()
    LIMIT 5
""")
samples = cur.fetchall()
times = []
for s_id, s_name, s_clip, s_dino in samples:
    t0 = time.time()
    cur.execute(f"""
        SELECT id, name,
               GREATEST(
                   (1 - (image_embedding <=> %s::halfvec)),
                   (1 - (dinov2_embedding <=> %s::halfvec))
               ) as vis
        FROM trademarks
        WHERE image_embedding IS NOT NULL
          AND current_status NOT IN ('Refused', 'Withdrawn')
          {DATE_FILTER}
        ORDER BY GREATEST(
            (1 - (image_embedding <=> %s::halfvec)),
            (1 - (dinov2_embedding <=> %s::halfvec))
        ) DESC
        LIMIT 50
    """, (s_clip, s_dino, s_clip, s_dino))
    rows = cur.fetchall()
    elapsed = (time.time() - t0) * 1000
    times.append(elapsed)
    print(f"  '{s_name[:30]}': {len(rows)} results in {elapsed:.0f}ms")

print(f"\n  Average: {sum(times)/len(times):.0f}ms")
print(f"  Min:     {min(times):.0f}ms")
print(f"  Max:     {max(times):.0f}ms")

# Count how many trademarks have image embeddings
cur.execute("SELECT COUNT(*) FROM trademarks WHERE image_embedding IS NOT NULL")
total_with_img = cur.fetchone()[0]
cur.execute(f"SELECT COUNT(*) FROM trademarks WHERE image_embedding IS NOT NULL {DATE_FILTER}")
recent_with_img = cur.fetchone()[0]
print(f"\n  Trademarks with image embeddings: {total_with_img:,} total, {recent_with_img:,} within 11yr")

conn.close()
print("\n✓ All image vector tests complete!")
