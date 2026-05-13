"""Backup-then-drop runner for migrations/drop_trademark_minilm_color_columns.sql.

Workflow:
    1. Confirm the four target columns exist (skips with a note if any are
       already missing).
    2. Print row + non-NULL counts for each column.
    3. Back up every non-NULL (id, value) pair per column to
       artifacts/migrations/drop_minilm_color/<UTC timestamp>/<column>.csv.gz.
    4. Prompt for explicit confirmation (skip with --yes).
    5. Apply the DROP migration in a single transaction.
    6. Re-verify the columns are gone.

The drop is irreversible at the DB level. The CSV backups let you recreate
the columns + reload the data via `psql \\copy ... FROM` if rollback is
ever needed; halfvec serialization is preserved as the `::text` form
(`[0.1,0.2,...]`) which pgvector accepts directly on cast.

Usage:
    python scripts/apply_drop_trademark_minilm_color.py            # prompts
    python scripts/apply_drop_trademark_minilm_color.py --yes      # no prompt
    python scripts/apply_drop_trademark_minilm_color.py --dry-run  # backup only

Pre-condition: PR 1 (commit 5ffc6181, merged into main) must already be
deployed. The application no longer reads or writes these columns, so the
drop is safe at code level. Verify your deployment is current before
running this.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.pool import close_pool, get_connection, release_connection  # noqa: E402

MIGRATION_FILE = PROJECT_ROOT / "migrations" / "drop_trademark_minilm_color_columns.sql"

# (table, column, primary-key column to capture alongside the value)
TARGET_COLUMNS = [
    ("trademarks", "color_histogram", "id"),
    ("watchlist_mt", "text_embedding", "id"),
    ("watchlist_mt", "logo_color_histogram", "id"),
    ("nice_classes_lookup", "description_embedding", "class_number"),
]


def column_exists(conn, table: str, column: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = %s
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def column_counts(conn, table: str, column: str) -> tuple[int, int]:
    """Return (total_rows, rows_with_non_null_value)."""
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*), COUNT({column}) FROM {table}")
    row = cur.fetchone()
    return int(row[0]), int(row[1])


def backup_column(conn, table: str, column: str, key: str, out_path: Path) -> int:
    """Write every non-NULL (key, value::text) pair to a gzipped CSV. Returns rows written."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cur = conn.cursor("backup_cursor")  # named/server-side cursor for streaming
    cur.itersize = 5000
    cur.execute(
        f"SELECT {key}, {column}::text "
        f"FROM {table} "
        f"WHERE {column} IS NOT NULL "
        f"ORDER BY {key}"
    )

    written = 0
    with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{key},{column}\n")
        for row in cur:
            key_value, vec_value = row
            # CSV quoting: vec text is "[0.1,0.2,...]" — quote and escape inner ".
            escaped = (vec_value or "").replace('"', '""')
            fh.write(f'{key_value},"{escaped}"\n')
            written += 1
    cur.close()
    return written


def apply_migration(conn) -> None:
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--yes", action="store_true", help="Skip the destructive-drop prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Backup + counts only; skip the DROP.")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="Override default backup dir (artifacts/migrations/drop_minilm_color/<ts>).",
    )
    args = parser.parse_args()

    if not MIGRATION_FILE.exists():
        print(f"ERROR: migration file not found: {MIGRATION_FILE}", file=sys.stderr)
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = args.backup_dir or (PROJECT_ROOT / "artifacts" / "migrations" / "drop_minilm_color" / ts)

    conn = get_connection()
    try:
        print("=" * 72)
        print("Drop deprecated MiniLM + color histogram columns")
        print("=" * 72)
        print(f"Migration: {MIGRATION_FILE}")
        print(f"Backup dir: {backup_dir}")
        print()

        # 1. Detect which columns are still present.
        present: list[tuple[str, str, str]] = []
        absent: list[tuple[str, str, str]] = []
        for table, column, key in TARGET_COLUMNS:
            (present if column_exists(conn, table, column) else absent).append(
                (table, column, key)
            )

        if absent:
            print("Already dropped (skipping):")
            for table, column, _ in absent:
                print(f"  - {table}.{column}")
            print()

        if not present:
            print("All target columns are already gone. Nothing to do.")
            return 0

        # 2. Show row counts.
        print("Pre-flight counts:")
        plan: list[tuple[str, str, str, int, int]] = []
        for table, column, key in present:
            total, non_null = column_counts(conn, table, column)
            plan.append((table, column, key, total, non_null))
            print(f"  {table}.{column}: {non_null:,} non-NULL / {total:,} total rows")
        print()

        # 3. Backup every present column.
        print(f"Writing backups to {backup_dir} ...")
        for table, column, key, _, non_null in plan:
            out_path = backup_dir / f"{table}.{column}.csv.gz"
            if non_null == 0:
                # Still write an empty (header-only) marker for completeness.
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as fh:
                    fh.write(f"{key},{column}\n")
                print(f"  {table}.{column}: 0 rows -> {out_path.name} (header only)")
                continue
            written = backup_column(conn, table, column, key, out_path)
            size_bytes = out_path.stat().st_size
            print(
                f"  {table}.{column}: {written:,} rows -> "
                f"{out_path.name} ({size_bytes / (1024 * 1024):.1f} MiB)"
            )
        print()

        if args.dry_run:
            print("--dry-run set; skipping DROP. Backups complete.")
            return 0

        # 4. Confirm before destructive op.
        if not args.yes:
            confirm = input(
                "Apply the DROP migration now? This cannot be undone without the backups. [type 'drop' to confirm] "
            ).strip().lower()
            if confirm != "drop":
                print("Aborted. Backups are at:", backup_dir)
                return 1

        # 5. Apply.
        print("Applying migration ...")
        apply_migration(conn)

        # 6. Re-verify.
        remaining = [
            f"{table}.{column}"
            for table, column, _ in present
            if column_exists(conn, table, column)
        ]
        if remaining:
            print(f"WARNING: still present after migration: {remaining}", file=sys.stderr)
            return 3
        print("Migration applied. All four columns are gone.")
        return 0
    finally:
        release_connection(conn)
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
