"""Backfill ocr_text_embedding for all trademarks with logo_ocr_text.
Uses MiniLM (paraphrase-multilingual-MiniLM-L12-v2) in batches of 256.
"""
import sys, os, time
sys.path.insert(0, '/app')

import psycopg2
from sentence_transformers import SentenceTransformer
import numpy as np

BATCH_SIZE = 256
COMMIT_EVERY = 5000  # commit every N records

conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "postgres"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "trademark_db"),
    user=os.getenv("DB_USER", "turk_patent"),
    password=os.getenv("DB_PASSWORD"),
)
conn.autocommit = False
cur = conn.cursor()

# Count total
cur.execute("""
    SELECT COUNT(*) FROM trademarks
    WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != '' AND length(logo_ocr_text) > 2
      AND ocr_text_embedding IS NULL
""")
total = cur.fetchone()[0]
print(f"Records to process: {total:,}")

if total == 0:
    print("Nothing to do!")
    sys.exit(0)

# Load model
print("Loading MiniLM model...")
model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
print("Model ready.")

# Process in batches using server-side cursor
cur.execute("DECLARE ocr_cur CURSOR FOR "
            "SELECT id, logo_ocr_text FROM trademarks "
            "WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != '' "
            "AND length(logo_ocr_text) > 2 AND ocr_text_embedding IS NULL")

processed = 0
t_start = time.time()
update_cur = conn.cursor()

while True:
    cur.execute(f"FETCH {BATCH_SIZE} FROM ocr_cur")
    rows = cur.fetchall()
    if not rows:
        break

    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]

    # Encode batch
    embeddings = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False)

    # Update DB
    for rid, emb in zip(ids, embeddings):
        update_cur.execute(
            "UPDATE trademarks SET ocr_text_embedding = %s::halfvec WHERE id = %s",
            (str(emb.tolist()), rid)
        )

    processed += len(rows)

    # Commit periodically
    if processed % COMMIT_EVERY < BATCH_SIZE:
        conn.commit()

    # Progress
    elapsed = time.time() - t_start
    rate = processed / elapsed if elapsed > 0 else 0
    eta = (total - processed) / rate if rate > 0 else 0
    print(f"  {processed:>8,} / {total:,} ({processed/total*100:.1f}%) | {rate:.0f} rec/s | ETA: {eta:.0f}s", flush=True)

# Final commit
conn.commit()
cur.execute("CLOSE ocr_cur")

elapsed = time.time() - t_start
print(f"\nDone! Processed {processed:,} records in {elapsed:.1f}s ({processed/elapsed:.0f} rec/s)")

# Verify
cur.execute("SELECT COUNT(*) FROM trademarks WHERE ocr_text_embedding IS NOT NULL")
embedded = cur.fetchone()[0]
print(f"Total with ocr_text_embedding: {embedded:,}")

conn.close()
