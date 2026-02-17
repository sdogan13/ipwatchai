"""
Creative Suite Monthly Credit Reset Worker
============================================
Resets monthly logo generation credits for all organizations
at the start of each calendar month.

The lazy reset in utils/subscription.py handles per-request resets,
but this worker ensures ALL orgs are reset even if they don't log in.

Usage:
    # Run once (manual reset)
    python -m workers.credit_reset

    # Run as daemon (checks every hour, resets on 1st of month)
    python -m workers.credit_reset --daemon

    # Dry run (show what would be reset)
    python -m workers.credit_reset --dry-run
"""

import os
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [CREDIT-RESET] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Check interval in daemon mode (seconds)
CHECK_INTERVAL = 3600  # 1 hour


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', ''),
        connect_timeout=30
    )


def reset_monthly_credits(dry_run: bool = False) -> dict:
    """
    Reset monthly logo credits for all organizations whose reset date
    is from a previous month.

    Returns:
        dict with reset_count, skipped_count, errors
    """
    conn = get_db_connection()
    now = datetime.utcnow()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Find orgs that need reset (reset_at is NULL or from a previous month)
        cur.execute("""
            SELECT
                o.id,
                o.name,
                o.logo_credits_monthly,
                o.logo_credits_reset_at,
                COALESCE(sp.logo_runs_per_month, 1) as plan_limit,
                COALESCE(sp.name, 'free') as plan_name
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.logo_credits_reset_at IS NULL
               OR (
                   EXTRACT(YEAR FROM o.logo_credits_reset_at) < %s
                   OR (
                       EXTRACT(YEAR FROM o.logo_credits_reset_at) = %s
                       AND EXTRACT(MONTH FROM o.logo_credits_reset_at) < %s
                   )
               )
        """, (now.year, now.year, now.month))

        orgs = cur.fetchall()
        logger.info(f"Found {len(orgs)} organizations needing credit reset")

        reset_count = 0
        skipped_count = 0
        errors = []

        for org in orgs:
            try:
                org_id = org['id']
                plan_limit = org['plan_limit']
                old_credits = org['logo_credits_monthly']

                if dry_run:
                    logger.info(
                        f"  [DRY RUN] Would reset org '{org['name']}' "
                        f"({org['plan_name']}): {old_credits} -> {plan_limit}"
                    )
                    reset_count += 1
                    continue

                cur.execute("""
                    UPDATE organizations
                    SET logo_credits_monthly = %s,
                        logo_credits_reset_at = %s
                    WHERE id = %s
                """, (plan_limit, now, org_id))

                reset_count += 1
                logger.info(
                    f"  Reset org '{org['name']}' ({org['plan_name']}): "
                    f"{old_credits} -> {plan_limit}"
                )

            except Exception as e:
                errors.append(f"Org {org['id']}: {str(e)}")
                logger.error(f"  Error resetting org {org['id']}: {e}")

        if not dry_run:
            conn.commit()

        result = {
            'timestamp': now.isoformat(),
            'reset_count': reset_count,
            'skipped_count': skipped_count,
            'errors': errors,
            'dry_run': dry_run,
        }

        logger.info(
            f"Credit reset complete: {reset_count} reset, "
            f"{len(errors)} errors"
        )
        return result

    except Exception as e:
        logger.error(f"Credit reset failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def run_daemon(check_interval: int = CHECK_INTERVAL):
    """Run as a daemon, checking hourly and resetting on the 1st of month."""
    logger.info("Starting Credit Reset daemon...")
    logger.info(f"  Check interval: {check_interval}s")

    last_reset_month = None

    while True:
        try:
            now = datetime.utcnow()
            current_month = (now.year, now.month)

            # Only reset if we haven't already reset this month
            if current_month != last_reset_month:
                logger.info(f"Running monthly credit reset for {now.strftime('%Y-%m')}...")
                result = reset_monthly_credits(dry_run=False)

                if result['reset_count'] > 0 or not result['errors']:
                    last_reset_month = current_month
                    logger.info(f"Monthly reset done: {result['reset_count']} orgs reset")
                else:
                    logger.warning(f"Reset had errors, will retry next check")
            else:
                logger.debug(f"Already reset for {now.strftime('%Y-%m')}, sleeping...")

            time.sleep(check_interval)

        except KeyboardInterrupt:
            logger.info("\nShutting down Credit Reset daemon...")
            break
        except Exception as e:
            logger.error(f"Daemon error: {e}")
            time.sleep(check_interval)


def main():
    parser = argparse.ArgumentParser(
        description="Creative Suite Monthly Credit Reset Worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run once (manual reset)
    python -m workers.credit_reset

    # Run as daemon (checks hourly)
    python -m workers.credit_reset --daemon

    # Dry run (preview what would be reset)
    python -m workers.credit_reset --dry-run
        """
    )

    parser.add_argument('--daemon', '-d', action='store_true',
                        help='Run as daemon (continuous hourly checks)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be reset without making changes')
    parser.add_argument('--check-interval', type=int, default=CHECK_INTERVAL,
                        help=f'Check interval in seconds for daemon mode (default: {CHECK_INTERVAL})')

    args = parser.parse_args()

    if args.daemon:
        run_daemon(check_interval=args.check_interval)
    else:
        result = reset_monthly_credits(dry_run=args.dry_run)
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n{'=' * 50}")
        print(f"{prefix}Credit Reset Results")
        print(f"{'=' * 50}")
        print(f"  Organizations reset: {result['reset_count']}")
        print(f"  Errors: {len(result['errors'])}")
        if result['errors']:
            for err in result['errors']:
                print(f"    - {err}")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
