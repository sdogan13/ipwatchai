"""Post-drop smoke test for the MiniLM + color histogram removal.

Verifies, after migrations/drop_trademark_minilm_color_columns.sql has been applied:

  1. The four target columns are gone from the live schema.
  2. The Python modules that used to read/write them still import cleanly
     (catches NameError, ImportError, missing-helper-attribute regressions).
  3. A representative SELECT against `trademarks` succeeds without those
     columns and returns a sensible row count.
  4. A representative SELECT against `watchlist_mt` succeeds without
     text_embedding / logo_color_histogram.
  5. `nice_classes_lookup` is readable without description_embedding.

Designed to be cheap: no ingest run, no heavy AI model load (uses
AI_SKIP_MODEL_LOAD), no live HTTP calls.

Usage:
    AI_SKIP_MODEL_LOAD=1 python scripts/smoke_trademark_minilm_color_removal.py
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Belt-and-suspenders: don't try to download MiniLM if a module is unguarded.
os.environ.setdefault("AI_SKIP_MODEL_LOAD", "1")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.pool import close_pool, get_connection, release_connection  # noqa: E402

DROPPED = [
    ("trademarks", "color_histogram"),
    ("watchlist_mt", "text_embedding"),
    ("watchlist_mt", "logo_color_histogram"),
    ("nice_classes_lookup", "description_embedding"),
]

# Modules I touched in PR 1. Import alone is the cheapest signal that the
# removals didn't leave dangling references (NameError on `SentenceTransformer`,
# `text_model`, etc.) or missing helpers.
MODULES_TO_IMPORT = [
    "pipeline.ai",
    "pipeline.parallel",
    "pipeline.ingest_rules",
    "pipeline.ingest_helpers",
    "pipeline.ingest_runtime",
    "pipeline.ingest_bootstrap",
    "pipeline.repair",
    "services.search_service",
    "services.watchlist_service",
    "services.creative_service",
    "watchlist.scanner",
    "risk_engine",
    "database.repositories.watchlist_repository",
    "agentic_search",
    "app_enhanced_search_routes",
    "workers.pipeline_worker",
]


def check_columns_gone(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN %s
        """,
        (tuple(DROPPED),),
    )
    return [f"{t}.{c}" for t, c in cur.fetchall()]


def check_imports() -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for mod in MODULES_TO_IMPORT:
        try:
            __import__(mod)
        except Exception as exc:
            failures.append((mod, f"{type(exc).__name__}: {exc}"))
    return failures


def check_trademark_select(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT count(*)
        FROM trademarks
        WHERE image_embedding IS NOT NULL OR dinov2_embedding IS NOT NULL
        """
    )
    return int(cur.fetchone()[0])


def check_watchlist_select(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT count(*) FROM watchlist_mt
        WHERE logo_embedding IS NOT NULL OR logo_ocr_text IS NOT NULL
        """
    )
    return int(cur.fetchone()[0])


def check_nice_classes(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM nice_classes_lookup")
    return int(cur.fetchone()[0])


def main() -> int:
    failures: list[str] = []

    print("=" * 72)
    print("Post-drop smoke test")
    print("=" * 72)
    print()

    conn = get_connection()
    try:
        # 1. Schema check.
        still_present = check_columns_gone(conn)
        if still_present:
            failures.append(f"Columns still present: {still_present}")
            print(f"  [FAIL] schema: {still_present}")
        else:
            print(f"  [PASS] schema: all 4 deprecated columns absent")

        # 2. Module imports.
        bad = check_imports()
        if bad:
            for mod, err in bad:
                failures.append(f"Import {mod}: {err}")
                print(f"  [FAIL] import {mod}: {err}")
        else:
            print(f"  [PASS] imports: {len(MODULES_TO_IMPORT)} modules load cleanly")

        # 3. Trademark SELECT.
        try:
            n = check_trademark_select(conn)
            print(f"  [PASS] trademarks SELECT: {n:,} rows with image/dinov2 embeddings")
        except Exception as exc:
            failures.append(f"trademarks SELECT: {type(exc).__name__}: {exc}")
            print(f"  [FAIL] trademarks SELECT: {exc}")

        # 4. Watchlist SELECT.
        try:
            n = check_watchlist_select(conn)
            print(f"  [PASS] watchlist_mt SELECT: {n:,} rows with logo features")
        except Exception as exc:
            failures.append(f"watchlist_mt SELECT: {type(exc).__name__}: {exc}")
            print(f"  [FAIL] watchlist_mt SELECT: {exc}")

        # 5. Nice classes.
        try:
            n = check_nice_classes(conn)
            print(f"  [PASS] nice_classes_lookup SELECT: {n} rows")
        except Exception as exc:
            failures.append(f"nice_classes_lookup SELECT: {type(exc).__name__}: {exc}")
            print(f"  [FAIL] nice_classes_lookup SELECT: {exc}")

        print()
        if failures:
            print(f"FAIL: {len(failures)} issue(s)")
            for f in failures:
                print(f"  - {f}")
            return 1
        print("OK: all checks passed.")
        return 0
    finally:
        release_connection(conn)
        close_pool()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
