"""Live smoke: design search service against the populated DB."""
import sys, io, psycopg2
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
from config.settings import settings
from services.design_search_service import search_designs

conn = psycopg2.connect(
    host=settings.database.host, port=settings.database.port,
    database=settings.database.name,
    user=settings.database.user, password=settings.database.password,
)


def show(label, res):
    print(f"=== {label} ===")
    print(f"  total={res['total']}  duration_ms={res.get('duration_ms', '?')}")
    for r in res["results"]:
        bd = r.get("similarity_breakdown") or {}
        name = r.get("product_name_tr") or r.get("product_name_en") or ""
        print(
            f"  {(r.get('application_no') or r.get('registration_no') or '?'):16s} "
            f"d{r.get('design_index') or '-'} "
            f"sim={r['similarity']:5.1f}  "
            f"text={bd.get('text', 0):.3f} dino={bd.get('dinov2', 0):.3f} "
            f"clip={bd.get('clip', 0):.3f} color={bd.get('color', 0):.3f}  "
            f"name={name!r}"
        )
    print()


# 1. Text query
show("text query 'Lamba' limit 5", search_designs(conn, query="Lamba", limit=5))

# 2. Locarno-filtered text
show(
    "text 'Sandalye' + locarno 06-01 limit 5",
    search_designs(conn, query="Sandalye", locarno_classes=["06-01"], limit=5),
)

# 3. Public mode (capped at 10)
public = search_designs(conn, query="Tepsi", limit=50, public=True)
print(f"public 'Tepsi' returned {public['total']} (cap=10): "
      f"{'✓' if public['total'] <= 10 else 'FAIL'}")
print()

# 4. Image-led query: reuse the stored DINOv2 + CLIP of a known design
cur = conn.cursor()
cur.execute("""
    SELECT dinov2_vitl14_mean::text, clip_vitb32_mean::text
    FROM designs
    WHERE application_no = '2024/007254' AND design_index = 1
    LIMIT 1
""")
row = cur.fetchone()
if row and row[0]:
    dino = [float(x) for x in row[0].strip("[]").split(",")]
    clip = [float(x) for x in row[1].strip("[]").split(",")]
    show(
        "image query (using stored vector of 2024/007254 d1) limit 5",
        search_designs(
            conn,
            image_embeddings={"dinov2_vitl14": dino, "clip_vitb32": clip},
            limit=5,
        ),
    )
else:
    print("image-led smoke skipped: 2024/007254 d1 not in DB")

conn.close()
