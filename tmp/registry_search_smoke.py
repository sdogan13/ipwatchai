"""Live smoke for registry_search_service.search_unified."""
import os, sys, io, psycopg2
sys.path.insert(0, ".")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
# Load .env from the sibling original worktree where the live DB password lives
from pathlib import Path
env_path = Path("../turk_patent/.env")
if env_path.is_file():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
from services.registry_search_service import search_unified

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", 5433)),
    database=os.environ.get("DB_NAME", "trademark_db"),
    user=os.environ.get("DB_USER", "turk_patent"),
    password=os.environ.get("DB_PASSWORD", ""),
)


def show(label, res):
    print(f"=== {label} ===")
    print(f"  total={res['total']}  by_registry={res.get('by_registry')}  duration_ms={res.get('duration_ms')}")
    for r in res["results"]:
        bd = r.get("similarity_breakdown") or {}
        title = r.get("title") or ""
        print(
            f"  [{r['registry_type']:9s}] {(r.get('application_no') or r.get('registration_no') or '?'):16s} "
            f"sim={r['similarity']:5.1f}  text={bd.get('text', 0):.3f} "
            f"dino={bd.get('dinov2', 0):.3f} clip={bd.get('clip', 0):.3f}  "
            f"{title!r}"
        )
    print()


# 1. Text query that should hit BOTH registries
show("text 'Apple' across both", search_unified(conn, query="Apple", limit=10))

# 2. Text query that's design-heavy
show("text 'Lamba' across both", search_unified(conn, query="Lamba", limit=10))

# 3. Filter to design only
show("design-only 'Sandalye'", search_unified(conn, query="Sandalye", registries=["design"], limit=5))

# 4. Filter to trademark only
show("trademark-only 'Apple'", search_unified(conn, query="Apple", registries=["trademark"], limit=5))

# 5. Public cap (max 10)
public = search_unified(conn, query="Tepsi", limit=50, public=True)
print(f"public 'Tepsi' returned {public['total']} (cap=10): {'OK' if public['total'] <= 10 else 'FAIL'}")
print()

conn.close()
