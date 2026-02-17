"""Quick OCR perf retest after GIN index."""
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

print("OCR LIKE search with GIN index — performance")
print("=" * 60)
bench = ["star", "apple", "gold", "coffee", "pharma", "tech", "medical", "food", "nike", "samsung"]
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
print("✓ Done!")
