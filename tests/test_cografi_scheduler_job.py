"""Unit tests for the cografi weekly scan addition in
``workers.scheduler``.

DB-touching code paths are exercised manually via
``python -m workers.scheduler --run-cografi``; these tests pin the
pure-Python pieces:

  * Constants + schedule label helper
  * ``start_scheduler`` registers the cografi job with the expected
    cron trigger (Wed 02:00 Europe/London)
  * ``weekly_cografi_watchlist_scan`` plan-gating + per-item
    frequency-window behavior with a mocked DB and scanner
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Constants + label helper
# ---------------------------------------------------------------------------

def test_constants_pick_wed_02_to_avoid_existing_collisions():
    from workers import scheduler
    # Wed 02:00 sits clear of the trademark Mon 00:00 and universal Tue 00:00
    # slots, leaving the rest of the week free for any future per-registry
    # sweep.
    assert scheduler.COGRAFI_SCAN_DAY == "wed"
    assert scheduler.COGRAFI_SCAN_HOUR == 2
    assert scheduler.COGRAFI_SCAN_MINUTE == 0
    assert scheduler.COGRAFI_SCAN_DAY != scheduler.WATCHLIST_SCAN_DAY
    assert scheduler.COGRAFI_SCAN_DAY != scheduler.UNIVERSAL_SCAN_DAY


def test_schedule_label_helper_renders_expected_string():
    from workers.scheduler import get_cografi_scan_schedule_label
    assert get_cografi_scan_schedule_label() == "Weekly on Wednesday at 02:00"


# ---------------------------------------------------------------------------
# start_scheduler registers the job
# ---------------------------------------------------------------------------

def test_start_scheduler_registers_cografi_job():
    """Confirm ``start_scheduler()`` adds a job with the cografi ID +
    function. APScheduler is stubbed under conftest as MagicMocks, so
    we inject a pre-built fake scheduler (with ``running=False`` so the
    early-return guard doesn't fire) and inspect its ``add_job`` calls."""
    from workers import scheduler as sched_mod

    fake_scheduler = MagicMock()
    fake_scheduler.running = False
    # The function logs next_run_time at the end; make .get_job(...) return
    # something whose .next_run_time is a string-coercible value.
    fake_scheduler.get_job.return_value = MagicMock(next_run_time="next")

    # Pre-seed the singleton so get_scheduler returns our fake.
    prev = sched_mod._scheduler
    sched_mod._scheduler = fake_scheduler
    try:
        sched_mod.start_scheduler()
        all_calls = fake_scheduler.add_job.call_args_list
        cografi_call = next(
            (c for c in all_calls
             if c.kwargs.get("id") == sched_mod.COGRAFI_SCAN_JOB_ID),
            None,
        )
        assert cografi_call is not None, (
            "weekly_cografi_watchlist_scan was not registered with "
            f"id={sched_mod.COGRAFI_SCAN_JOB_ID}"
        )
        assert cografi_call.args[0] is sched_mod.weekly_cografi_watchlist_scan
        assert cografi_call.kwargs.get("name") == \
            'Weekly Cografi Watchlist Auto-Scan'
        assert cografi_call.kwargs.get("replace_existing") is True
    finally:
        sched_mod._scheduler = prev


# ---------------------------------------------------------------------------
# weekly_cografi_watchlist_scan — plan + frequency gating
# ---------------------------------------------------------------------------

def _make_item(
    *, item_id: str, org_id: str,
    alert_frequency: str = "daily",
    last_scan_at=None,
    label: str = "watch",
) -> Dict[str, Any]:
    return {
        "id": item_id,
        "organization_id": org_id,
        "label": label,
        "alert_frequency": alert_frequency,
        "last_scan_at": last_scan_at,
        "created_at": datetime(2026, 5, 1),
    }


class _FakeCursor:
    """Minimal cursor that returns a plan_name lookup row."""
    def __init__(self, plan_lookup: Dict[str, str]):
        self.plan_lookup = plan_lookup
        self._last_org = None

    def execute(self, sql, params):
        # The plan lookup query passes (org_id,) as params.
        if "organizations" in sql and "subscription_plan_id" in sql:
            self._last_org = params[0]

    def fetchone(self):
        plan = self.plan_lookup.get(self._last_org, 'free')
        return {"plan_name": plan}


class _FakeDB:
    """Stand-in for ``database.crud.Database`` context manager."""
    def __init__(self, plan_lookup: Dict[str, str]):
        self.plan_lookup = plan_lookup

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self.plan_lookup)


def _patch_dependencies(items, plan_lookup, scan_results=None):
    """Build the patcher chain used by every scan-function test."""
    fake_db_factory = lambda: _FakeDB(plan_lookup)

    if scan_results is None:
        scan_results = {}
    captured_calls: List[Dict[str, Any]] = []

    def fake_scan_and_store(db, item):
        captured_calls.append({"item_id": item["id"]})
        return scan_results.get(item["id"], {"alerts_created": 0})

    def fake_get_plan_limit(plan_name, feature):
        if feature != 'auto_scan_max_items':
            return None
        return {
            'free': 0,
            'starter': 25,
            'professional': 50,
            'enterprise': 500,
        }.get(plan_name, 0)

    return (
        patch("database.crud.Database", side_effect=fake_db_factory),
        patch("services.cografi_watchlist_service.get_active_cografi_watchlist_items",
              return_value=items),
        patch("services.cografi_scanner_service.scan_and_store",
              side_effect=fake_scan_and_store),
        patch("utils.subscription.get_plan_limit",
              side_effect=fake_get_plan_limit),
        captured_calls,
    )


def test_scan_skips_free_org_entirely():
    from workers.scheduler import weekly_cografi_watchlist_scan

    items = [
        _make_item(item_id="i1", org_id="org-free"),
        _make_item(item_id="i2", org_id="org-free"),
    ]
    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items, plan_lookup={"org-free": "free"},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()
    # Free plan -> auto_scan_max_items=0 -> all items skipped, scan_and_store
    # never called.
    assert captured == []


def test_scan_runs_for_paid_org():
    from workers.scheduler import weekly_cografi_watchlist_scan

    items = [
        _make_item(item_id="i1", org_id="org-paid"),
        _make_item(item_id="i2", org_id="org-paid"),
    ]
    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items,
        plan_lookup={"org-paid": "starter"},
        scan_results={"i1": {"alerts_created": 3}, "i2": {"alerts_created": 0}},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()
    assert sorted(c["item_id"] for c in captured) == ["i1", "i2"]


def test_scan_respects_weekly_frequency_window():
    """Item with alert_frequency='weekly' and last_scan_at=2 days ago
    must be skipped (window is 6 days)."""
    from workers.scheduler import weekly_cografi_watchlist_scan

    two_days_ago = datetime.utcnow() - timedelta(days=2)
    items = [
        _make_item(item_id="recent", org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=two_days_ago),
        _make_item(item_id="never", org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=None),
    ]
    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items, plan_lookup={"org-paid": "professional"},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert "recent" not in ids  # skipped — within 6-day window
    assert "never" in ids       # scanned — no last_scan_at


def test_scan_respects_daily_frequency_window():
    """Item with alert_frequency='daily' and last_scan_at=10h ago must
    be skipped (window is 20 hours)."""
    from workers.scheduler import weekly_cografi_watchlist_scan

    ten_hours_ago = datetime.utcnow() - timedelta(hours=10)
    twentyfive_hours_ago = datetime.utcnow() - timedelta(hours=25)
    items = [
        _make_item(item_id="recent", org_id="org-paid",
                   alert_frequency="daily", last_scan_at=ten_hours_ago),
        _make_item(item_id="stale", org_id="org-paid",
                   alert_frequency="daily", last_scan_at=twentyfive_hours_ago),
    ]
    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items, plan_lookup={"org-paid": "starter"},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert "recent" not in ids  # skipped — within 20-hour window
    assert "stale" in ids       # scanned — outside 20-hour window


def test_scan_caps_items_per_org_at_plan_limit():
    """Starter plan caps at 25 items per org. Items beyond the cap
    should not be scanned."""
    from workers.scheduler import weekly_cografi_watchlist_scan

    # Build 30 items for the same org. Older created_at should be the
    # ones dropped (sorted-by-created_at-desc, then capped).
    items = []
    for i in range(30):
        items.append({
            "id": f"i{i}",
            "organization_id": "org-paid",
            "label": f"watch-{i}",
            "alert_frequency": "daily",
            "last_scan_at": None,
            # i=0 has created_at 1 day ago, i=29 has created_at 30 days ago.
            "created_at": datetime.utcnow() - timedelta(days=i + 1),
        })

    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items, plan_lookup={"org-paid": "starter"},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()
    # Starter cap = 25.
    assert len(captured) == 25
    # The 25 most recently created items should be the ones scanned —
    # i.e. i0..i24, not i25..i29.
    scanned_ids = {c["item_id"] for c in captured}
    for i in range(25):
        assert f"i{i}" in scanned_ids
    for i in range(25, 30):
        assert f"i{i}" not in scanned_ids


def test_scan_no_active_items_returns_cleanly():
    from workers.scheduler import weekly_cografi_watchlist_scan
    p_db, p_get_items, p_scan, p_limit, captured = _patch_dependencies(
        items=[], plan_lookup={},
    )
    with p_db, p_get_items, p_scan, p_limit:
        weekly_cografi_watchlist_scan()  # should not raise
    assert captured == []


def test_scan_individual_failure_does_not_abort_remaining_items():
    from workers.scheduler import weekly_cografi_watchlist_scan

    items = [
        _make_item(item_id="boom", org_id="org-paid"),
        _make_item(item_id="ok", org_id="org-paid"),
    ]

    captured: List[str] = []

    def fake_scan_and_store(db, item):
        captured.append(item["id"])
        if item["id"] == "boom":
            raise RuntimeError("simulated scan failure")
        return {"alerts_created": 1}

    fake_db_factory = lambda: _FakeDB({"org-paid": "starter"})

    def fake_get_plan_limit(plan_name, feature):
        return 25 if feature == "auto_scan_max_items" else None

    with patch("database.crud.Database", side_effect=fake_db_factory), \
         patch("services.cografi_watchlist_service.get_active_cografi_watchlist_items",
               return_value=items), \
         patch("services.cografi_scanner_service.scan_and_store",
               side_effect=fake_scan_and_store), \
         patch("utils.subscription.get_plan_limit",
               side_effect=fake_get_plan_limit):
        weekly_cografi_watchlist_scan()

    assert captured == ["boom", "ok"]
