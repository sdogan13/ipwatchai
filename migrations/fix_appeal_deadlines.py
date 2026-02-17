"""
Fix Appeal Deadlines Migration
===============================
Recalculates all appeal_deadline values using the correct +2 calendar months
formula (per KHK m.42), replacing the old +60 days approximation.

Also recalculates opposition_deadline in universal_conflicts.

Usage: python migrations/fix_appeal_deadlines.py [--dry-run]
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import argparse
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Import the single source of truth
from utils.deadline import calculate_appeal_deadline


def run_migration(dry_run: bool = False):
    """Recalculate all appeal deadlines using correct +2 months formula."""

    conn_str = (
        f"host={os.getenv('DB_HOST', '127.0.0.1')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'trademark_db')} "
        f"user={os.getenv('DB_USER', 'turk_patent')} "
        f"password={os.getenv('DB_PASSWORD', '')}"
    )

    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        print("=" * 60)
        print("Fix Appeal Deadlines Migration")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
        print("=" * 60)

        # ============================================
        # Part 1: Fix trademarks.appeal_deadline
        # ============================================
        print("\n--- Part 1: trademarks.appeal_deadline ---")

        cur.execute("""
            SELECT id, bulletin_date, appeal_deadline
            FROM trademarks
            WHERE bulletin_date IS NOT NULL
        """)
        rows = cur.fetchall()
        print(f"Found {len(rows)} trademarks with bulletin_date")

        updated = 0
        max_diff_days = 0
        needs_update = []

        for row in rows:
            old_deadline = row['appeal_deadline']
            new_deadline = calculate_appeal_deadline(row['bulletin_date'])

            if new_deadline is None:
                continue

            if old_deadline != new_deadline:
                diff = abs((new_deadline - old_deadline).days) if old_deadline else 0
                if diff > max_diff_days:
                    max_diff_days = diff
                needs_update.append((str(new_deadline), str(row['id'])))
                updated += 1

        print(f"  Rows needing update: {updated}")
        print(f"  Max difference from old value: {max_diff_days} days")

        if needs_update and not dry_run:
            # Batch update in chunks of 1000
            BATCH_SIZE = 1000
            for i in range(0, len(needs_update), BATCH_SIZE):
                batch = needs_update[i:i + BATCH_SIZE]
                cur.executemany(
                    "UPDATE trademarks SET appeal_deadline = %s::date WHERE id = %s::uuid",
                    batch
                )
                print(f"  Updated batch {i // BATCH_SIZE + 1} ({len(batch)} rows)")
            conn.commit()
            print(f"  Committed {updated} trademarks updates")
        elif needs_update:
            print(f"  [DRY RUN] Would update {updated} rows")

        # Also set appeal_deadline for rows that have bulletin_date but NULL deadline
        cur.execute("""
            SELECT id, bulletin_date
            FROM trademarks
            WHERE bulletin_date IS NOT NULL AND appeal_deadline IS NULL
        """)
        null_rows = cur.fetchall()
        print(f"\n  Trademarks with bulletin_date but NULL appeal_deadline: {len(null_rows)}")

        null_updates = []
        for row in null_rows:
            new_deadline = calculate_appeal_deadline(row['bulletin_date'])
            if new_deadline:
                null_updates.append((str(new_deadline), str(row['id'])))

        if null_updates and not dry_run:
            for i in range(0, len(null_updates), BATCH_SIZE):
                batch = null_updates[i:i + BATCH_SIZE]
                cur.executemany(
                    "UPDATE trademarks SET appeal_deadline = %s::date WHERE id = %s::uuid",
                    batch
                )
            conn.commit()
            print(f"  Filled {len(null_updates)} NULL appeal_deadlines")
        elif null_updates:
            print(f"  [DRY RUN] Would fill {len(null_updates)} NULL appeal_deadlines")

        # ============================================
        # Part 2: Fix universal_conflicts.opposition_deadline
        # ============================================
        print("\n--- Part 2: universal_conflicts.opposition_deadline ---")

        cur.execute("""
            SELECT id, bulletin_date, opposition_deadline
            FROM universal_conflicts
            WHERE bulletin_date IS NOT NULL
        """)
        uc_rows = cur.fetchall()
        print(f"Found {len(uc_rows)} universal_conflicts with bulletin_date")

        uc_updated = 0
        uc_max_diff = 0
        uc_needs_update = []

        for row in uc_rows:
            old_deadline = row['opposition_deadline']
            new_deadline = calculate_appeal_deadline(row['bulletin_date'])

            if new_deadline is None:
                continue

            if old_deadline != new_deadline:
                diff = abs((new_deadline - old_deadline).days) if old_deadline else 0
                if diff > uc_max_diff:
                    uc_max_diff = diff
                uc_needs_update.append((str(new_deadline), str(row['id'])))
                uc_updated += 1

        print(f"  Rows needing update: {uc_updated}")
        print(f"  Max difference from old value: {uc_max_diff} days")

        if uc_needs_update and not dry_run:
            for i in range(0, len(uc_needs_update), BATCH_SIZE):
                batch = uc_needs_update[i:i + BATCH_SIZE]
                cur.executemany(
                    "UPDATE universal_conflicts SET opposition_deadline = %s::date WHERE id = %s::uuid",
                    batch
                )
                print(f"  Updated batch {i // BATCH_SIZE + 1} ({len(batch)} rows)")
            conn.commit()
            print(f"  Committed {uc_updated} universal_conflicts updates")
        elif uc_needs_update:
            print(f"  [DRY RUN] Would update {uc_updated} rows")

        # ============================================
        # Part 3: Fix alerts_mt.opposition_deadline
        # ============================================
        print("\n--- Part 3: alerts_mt.opposition_deadline (backfill from trademarks) ---")

        cur.execute("""
            SELECT a.id, t.appeal_deadline
            FROM alerts_mt a
            JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE t.appeal_deadline IS NOT NULL
              AND (a.opposition_deadline IS NULL OR a.opposition_deadline != t.appeal_deadline)
        """)
        alert_rows = cur.fetchall()
        print(f"  Alerts needing deadline sync: {len(alert_rows)}")

        if alert_rows and not dry_run:
            alert_updates = [(str(row['appeal_deadline']), str(row['id'])) for row in alert_rows]
            for i in range(0, len(alert_updates), BATCH_SIZE):
                batch = alert_updates[i:i + BATCH_SIZE]
                cur.executemany(
                    "UPDATE alerts_mt SET opposition_deadline = %s::date WHERE id = %s::uuid",
                    batch
                )
            conn.commit()
            print(f"  Synced {len(alert_rows)} alert deadlines")
        elif alert_rows:
            print(f"  [DRY RUN] Would sync {len(alert_rows)} alert deadlines")

        # ============================================
        # Summary
        # ============================================
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"  trademarks updated:          {updated}")
        print(f"  trademarks NULL filled:       {len(null_updates)}")
        print(f"  universal_conflicts updated:  {uc_updated}")
        print(f"  alerts_mt synced:             {len(alert_rows)}")
        print(f"  Max date diff (trademarks):   {max_diff_days} days")
        print(f"  Max date diff (conflicts):    {uc_max_diff} days")
        if dry_run:
            print("\n  *** DRY RUN — no changes made ***")
        print("=" * 60)

        cur.close()
        conn.close()
        return True

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix appeal deadlines (+60 days → +2 months)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    success = run_migration(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
