"""Shared background tasks for watchlist workflows."""

import asyncio
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def _scan_watchlist_item(item_id: UUID):
    """Background task to scan a watchlist item with the shared scanner."""
    import traceback

    logger.info(f"[SCAN START] Scanning watchlist item {item_id}")
    try:
        from watchlist.scanner import get_scanner

        scanner = get_scanner()
        try:
            scanner.conn.rollback()
        except Exception:
            pass
        alerts_count = scanner.scan_single_watchlist(item_id)
        logger.info(f"[SCAN COMPLETE] Item {item_id}: {alerts_count} alerts created")
    except Exception as exc:
        logger.error(f"[SCAN FAILED] Item {item_id}: {exc}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        from watchlist.scanner import reset_scanner

        reset_scanner()


async def run_watchlist_scan_task(item_id: UUID):
    """Run the watchlist scan in a worker thread so the app stays responsive."""
    await asyncio.to_thread(_scan_watchlist_item, item_id)
