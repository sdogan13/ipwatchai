"""
Scheduled Tasks — APScheduler integration for daily watchlist auto-scan.

Usage (standalone):
    python -m workers.scheduler

Usage (integrated via main.py lifespan):
    Automatically started when FastAPI app boots.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime, timedelta
from uuid import UUID

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# Singleton scheduler
_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Get or create the singleton scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            job_defaults={'coalesce': True, 'max_instances': 1}
        )
    return _scheduler


def start_scheduler():
    """Start the scheduler with all registered jobs (idempotent)."""
    scheduler = get_scheduler()
    if scheduler.running:
        logger.info("Scheduler already running")
        return scheduler

    # Daily watchlist scan at 03:00
    scheduler.add_job(
        daily_watchlist_scan,
        trigger=CronTrigger(hour=3, minute=0),
        id='daily_watchlist_scan',
        name='Daily Watchlist Auto-Scan',
        replace_existing=True,
    )

    scheduler.start()
    next_run = scheduler.get_job('daily_watchlist_scan').next_run_time
    logger.info(f"Scheduler started — next watchlist scan: {next_run}")
    return scheduler


def shutdown_scheduler():
    """Gracefully stop the scheduler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None


def get_next_scan_time() -> str | None:
    """Return ISO string of next scheduled scan (for status API)."""
    scheduler = get_scheduler()
    if not scheduler.running:
        return None
    job = scheduler.get_job('daily_watchlist_scan')
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def daily_watchlist_scan():
    """
    Scan active watchlist items, gated by organization plan.

    Plan gating:
    - Free (auto_scan_max_items=0): Skip entirely
    - Starter (auto_scan_frequency="weekly"): Only on Mondays, up to 25 items
    - Professional (auto_scan_frequency="daily"): Daily, up to 50 items
    - Enterprise (auto_scan_frequency="daily"): Daily, up to 500 items

    Also respects each item's alert_frequency and last_scan_at.
    """
    logger.info("=== Daily Watchlist Auto-Scan starting ===")

    try:
        from database.crud import Database, WatchlistCRUD
        from watchlist.scanner import get_scanner
        from utils.subscription import get_plan_limit
        from psycopg2.extras import RealDictCursor

        scanner = get_scanner()
        now = datetime.utcnow()
        is_monday = now.weekday() == 0  # 0 = Monday

        with Database() as db:
            all_items = WatchlistCRUD.get_all_active(db)

        if not all_items:
            logger.info("No active watchlist items — nothing to scan")
            return

        # Group items by organization
        org_items: dict = {}
        for item in all_items:
            org_id = str(item.get('organization_id', ''))
            if org_id not in org_items:
                org_items[org_id] = []
            org_items[org_id].append(item)

        # Look up each org's plan
        org_plans: dict = {}
        with Database() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            for org_id in org_items:
                cur.execute("""
                    SELECT COALESCE(sp.name, 'free') as plan_name
                    FROM organizations o
                    LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
                    WHERE o.id = %s
                """, (org_id,))
                row = cur.fetchone()
                org_plans[org_id] = row['plan_name'] if row else 'free'

        scanned = 0
        skipped = 0
        skipped_plan = 0
        total_alerts = 0

        for org_id, items in org_items.items():
            plan_name = org_plans.get(org_id, 'free')
            max_scan_items = get_plan_limit(plan_name, 'auto_scan_max_items')
            scan_frequency = get_plan_limit(plan_name, 'auto_scan_frequency')

            # Skip orgs with no auto-scan (free)
            if max_scan_items == 0 or scan_frequency is None:
                logger.info(
                    f"Skipping org {org_id} (plan: {plan_name}) — auto-scan not included"
                )
                skipped_plan += len(items)
                continue

            # Weekly plans only scan on Mondays
            if scan_frequency == 'weekly' and not is_monday:
                logger.info(
                    f"Skipping org {org_id} (plan: {plan_name}) — weekly scan, not Monday"
                )
                skipped += len(items)
                continue

            # Sort by most recently added (newest first) and cap at plan limit
            items_sorted = sorted(
                items,
                key=lambda x: x.get('created_at', datetime.min),
                reverse=True,
            )[:max_scan_items]

            if len(items) > max_scan_items:
                logger.info(
                    f"  Org {org_id} ({plan_name}): {len(items)} items, "
                    f"capped to {max_scan_items}"
                )

            for item in items_sorted:
                freq = (item.get('alert_frequency') or 'daily').lower()
                last_scan = item.get('last_scan_at')

                # Determine minimum interval
                if freq == 'weekly':
                    min_interval = timedelta(days=6)
                else:  # daily (default)
                    min_interval = timedelta(hours=20)

                # Skip if scanned recently
                if last_scan and (now - last_scan) < min_interval:
                    skipped += 1
                    continue

                try:
                    item_id = UUID(item['id'])
                    alerts_count = scanner.scan_single_watchlist(item_id)
                    scanned += 1
                    total_alerts += alerts_count
                    if alerts_count > 0:
                        logger.info(
                            f"  [{item['brand_name']}] {alerts_count} new alerts"
                        )
                except Exception as e:
                    logger.error(f"  [{item.get('brand_name', '?')}] scan failed: {e}")

        logger.info(
            f"=== Auto-Scan complete: {scanned} scanned, {skipped} skipped, "
            f"{skipped_plan} skipped (plan), {total_alerts} alerts ==="
        )

    except Exception as e:
        logger.error(f"Daily watchlist scan failed: {e}", exc_info=True)


# ==========================================
# CLI Entry Point
# ==========================================
if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    parser = argparse.ArgumentParser(description="Watchlist scheduler")
    parser.add_argument("--run-now", action="store_true",
                        help="Run daily scan immediately (don't wait for schedule)")
    parser.add_argument("--daemon", action="store_true",
                        help="Start scheduler daemon (blocks forever)")
    args = parser.parse_args()

    if args.run_now:
        logger.info("Running daily scan immediately...")
        daily_watchlist_scan()
    elif args.daemon:
        import time
        scheduler = start_scheduler()
        logger.info("Scheduler daemon running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except (KeyboardInterrupt, SystemExit):
            shutdown_scheduler()
    else:
        print("Usage: python -m workers.scheduler --run-now | --daemon")
