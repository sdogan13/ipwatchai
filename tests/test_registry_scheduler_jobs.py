"""Unit tests for the patent + design weekly scheduled scans in
``workers.scheduler`` (Phase I-spillover).

Mirrors the cografi version (``test_cografi_scheduler_job.py``) but
parameterized across the two new registry jobs:

  * weekly_patent_watchlist_scan   — patent_scanner_service.scan_and_store
  * weekly_design_watchlist_scan   — design_scanner.scan_single_design_watchlist
                                      (different signature: item_id only;
                                       opens its own db_factory)

DB-touching code paths are exercised manually via
``python -m workers.scheduler --run-patent`` / ``--run-design``;
these tests pin the pure-Python pieces:

  * Constants + schedule label helpers
  * ``start_scheduler`` registers both jobs with the expected cron
    triggers (Wed 03:00 patent, Wed 04:00 design)
  * Plan-gating: free orgs are skipped entirely; paid orgs are
    capped by ``auto_scan_max_items``
  * Per-item ``alert_frequency`` window (weekly ≥6 days, daily ≥20h)
  * Empty active-items list returns cleanly
  * Individual scan failures don't abort the remaining loop
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest


# ---------------------------------------------------------------------------
# Constants + label helpers
# ---------------------------------------------------------------------------

def test_patent_constants_pick_wed_03():
    """Wed 03:00 sits 1 hour after cografi (02:00) so the multi-registry
    weekly pass spreads DB load across the night window."""
    from workers import scheduler
    assert scheduler.PATENT_SCAN_DAY == "wed"
    assert scheduler.PATENT_SCAN_HOUR == 3
    assert scheduler.PATENT_SCAN_MINUTE == 0
    # Must not collide with trademark Mon 00:00 or universal Tue 00:00
    assert scheduler.PATENT_SCAN_DAY != scheduler.WATCHLIST_SCAN_DAY
    assert scheduler.PATENT_SCAN_DAY != scheduler.UNIVERSAL_SCAN_DAY
    # And must not collide with cografi (same day, different hour)
    assert (scheduler.PATENT_SCAN_DAY, scheduler.PATENT_SCAN_HOUR) != \
           (scheduler.COGRAFI_SCAN_DAY, scheduler.COGRAFI_SCAN_HOUR)


def test_design_constants_pick_wed_04():
    from workers import scheduler
    assert scheduler.DESIGN_SCAN_DAY == "wed"
    assert scheduler.DESIGN_SCAN_HOUR == 4
    assert scheduler.DESIGN_SCAN_MINUTE == 0
    # Must not collide with any prior cron
    triples = {
        (scheduler.WATCHLIST_SCAN_DAY, scheduler.WATCHLIST_SCAN_HOUR, 0),
        (scheduler.UNIVERSAL_SCAN_DAY, scheduler.UNIVERSAL_SCAN_HOUR, 0),
        (scheduler.COGRAFI_SCAN_DAY,   scheduler.COGRAFI_SCAN_HOUR,
         scheduler.COGRAFI_SCAN_MINUTE),
        (scheduler.PATENT_SCAN_DAY,    scheduler.PATENT_SCAN_HOUR,
         scheduler.PATENT_SCAN_MINUTE),
    }
    assert (scheduler.DESIGN_SCAN_DAY, scheduler.DESIGN_SCAN_HOUR,
            scheduler.DESIGN_SCAN_MINUTE) not in triples


def test_patent_label_helper():
    from workers.scheduler import get_patent_scan_schedule_label
    assert get_patent_scan_schedule_label() == "Weekly on Wednesday at 03:00"


def test_design_label_helper():
    from workers.scheduler import get_design_scan_schedule_label
    assert get_design_scan_schedule_label() == "Weekly on Wednesday at 04:00"


# ---------------------------------------------------------------------------
# start_scheduler registers both jobs
# ---------------------------------------------------------------------------

def _seed_fake_scheduler(sched_mod):
    fake_scheduler = MagicMock()
    fake_scheduler.running = False
    fake_scheduler.get_job.return_value = MagicMock(next_run_time="next")
    sched_mod._scheduler = fake_scheduler
    return fake_scheduler


def test_start_scheduler_registers_patent_job():
    from workers import scheduler as sched_mod
    prev = sched_mod._scheduler
    try:
        fake = _seed_fake_scheduler(sched_mod)
        sched_mod.start_scheduler()
        patent_call = next(
            (c for c in fake.add_job.call_args_list
             if c.kwargs.get("id") == sched_mod.PATENT_SCAN_JOB_ID),
            None,
        )
        assert patent_call is not None
        assert patent_call.args[0] is sched_mod.weekly_patent_watchlist_scan
        assert patent_call.kwargs.get("name") == "Weekly Patent Watchlist Auto-Scan"
        assert patent_call.kwargs.get("replace_existing") is True
    finally:
        sched_mod._scheduler = prev


def test_start_scheduler_registers_design_job():
    from workers import scheduler as sched_mod
    prev = sched_mod._scheduler
    try:
        fake = _seed_fake_scheduler(sched_mod)
        sched_mod.start_scheduler()
        design_call = next(
            (c for c in fake.add_job.call_args_list
             if c.kwargs.get("id") == sched_mod.DESIGN_SCAN_JOB_ID),
            None,
        )
        assert design_call is not None
        assert design_call.args[0] is sched_mod.weekly_design_watchlist_scan
        assert design_call.kwargs.get("name") == "Weekly Design Watchlist Auto-Scan"
        assert design_call.kwargs.get("replace_existing") is True
    finally:
        sched_mod._scheduler = prev


# ---------------------------------------------------------------------------
# Shared fake DB / plan-lookup / cursor
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, plan_lookup: Dict[str, str]):
        self.plan_lookup = plan_lookup
        self._last_org = None

    def execute(self, sql, params):
        if "organizations" in sql and "subscription_plan_id" in sql:
            self._last_org = params[0]

    def fetchone(self):
        plan = self.plan_lookup.get(self._last_org, "free")
        return {"plan_name": plan}


class _FakeDB:
    def __init__(self, plan_lookup: Dict[str, str]):
        self.plan_lookup = plan_lookup

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        return _FakeCursor(self.plan_lookup)


def _make_item(*, item_id, org_id, alert_frequency="daily",
               last_scan_at=None, **extras):
    base = {
        "id": item_id,
        "organization_id": org_id,
        "label": "watch",
        "alert_frequency": alert_frequency,
        "last_scan_at": last_scan_at,
        "created_at": datetime(2026, 5, 1),
    }
    base.update(extras)
    return base


def _fake_plan_limit(plan_name, feature):
    if feature != "auto_scan_max_items":
        return None
    return {
        "free": 0,
        "starter": 25,
        "professional": 50,
        "enterprise": 500,
    }.get(plan_name, 0)


# ---------------------------------------------------------------------------
# Patent scan tests
# ---------------------------------------------------------------------------

def _patent_patchers(items, plan_lookup, scan_results=None):
    captured: List[Dict[str, Any]] = []
    if scan_results is None:
        scan_results = {}

    def fake_scan_and_store(db, item):
        captured.append({"item_id": item["id"]})
        return scan_results.get(item["id"], {"alerts_created": 0})

    return (
        patch("database.crud.Database",
              side_effect=lambda: _FakeDB(plan_lookup)),
        patch("services.patent_watchlist_service.get_active_patent_watchlist_items",
              return_value=items),
        patch("services.patent_scanner_service.scan_and_store",
              side_effect=fake_scan_and_store),
        patch("utils.subscription.get_plan_limit",
              side_effect=_fake_plan_limit),
        captured,
    )


def test_patent_scan_skips_free_org_entirely():
    from workers.scheduler import weekly_patent_watchlist_scan
    items = [_make_item(item_id="i1", org_id="org-free")]
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(items, {"org-free": "free"})
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()
    assert captured == []


def test_patent_scan_runs_for_paid_org():
    from workers.scheduler import weekly_patent_watchlist_scan
    items = [
        _make_item(item_id="i1", org_id="org-paid"),
        _make_item(item_id="i2", org_id="org-paid"),
    ]
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(
        items, {"org-paid": "starter"},
        scan_results={"i1": {"alerts_created": 5}},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()
    assert sorted(c["item_id"] for c in captured) == ["i1", "i2"]


def test_patent_scan_respects_weekly_frequency_window():
    from workers.scheduler import weekly_patent_watchlist_scan
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    items = [
        _make_item(item_id="recent", org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=two_days_ago),
        _make_item(item_id="never", org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=None),
    ]
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(
        items, {"org-paid": "professional"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert "recent" not in ids
    assert "never" in ids


def test_patent_scan_respects_daily_frequency_window():
    from workers.scheduler import weekly_patent_watchlist_scan
    ten_h = datetime.utcnow() - timedelta(hours=10)
    twentyfive_h = datetime.utcnow() - timedelta(hours=25)
    items = [
        _make_item(item_id="recent", org_id="org-paid",
                   alert_frequency="daily", last_scan_at=ten_h),
        _make_item(item_id="stale", org_id="org-paid",
                   alert_frequency="daily", last_scan_at=twentyfive_h),
    ]
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(
        items, {"org-paid": "starter"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert "recent" not in ids
    assert "stale" in ids


def test_patent_scan_caps_items_at_starter_limit_25():
    from workers.scheduler import weekly_patent_watchlist_scan
    items = []
    for i in range(30):
        items.append(_make_item(
            item_id=f"i{i}", org_id="org-paid",
            created_at=datetime.utcnow() - timedelta(days=i + 1),
        ))
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(
        items, {"org-paid": "starter"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()
    assert len(captured) == 25
    scanned_ids = {c["item_id"] for c in captured}
    for i in range(25):
        assert f"i{i}" in scanned_ids
    for i in range(25, 30):
        assert f"i{i}" not in scanned_ids


def test_patent_scan_no_items_returns_cleanly():
    from workers.scheduler import weekly_patent_watchlist_scan
    p_db, p_get, p_scan, p_lim, captured = _patent_patchers(items=[], plan_lookup={})
    with p_db, p_get, p_scan, p_lim:
        weekly_patent_watchlist_scan()  # should not raise
    assert captured == []


def test_patent_scan_individual_failure_does_not_abort():
    from workers.scheduler import weekly_patent_watchlist_scan
    items = [
        _make_item(item_id="boom", org_id="org-paid"),
        _make_item(item_id="ok",   org_id="org-paid"),
    ]
    captured: List[str] = []

    def fake_scan(db, item):
        captured.append(item["id"])
        if item["id"] == "boom":
            raise RuntimeError("boom")
        return {"alerts_created": 1}

    with patch("database.crud.Database",
               side_effect=lambda: _FakeDB({"org-paid": "starter"})), \
         patch("services.patent_watchlist_service.get_active_patent_watchlist_items",
               return_value=items), \
         patch("services.patent_scanner_service.scan_and_store",
               side_effect=fake_scan), \
         patch("utils.subscription.get_plan_limit",
               side_effect=_fake_plan_limit):
        weekly_patent_watchlist_scan()

    assert captured == ["boom", "ok"]


# ---------------------------------------------------------------------------
# Design scan tests (different scanner entry-point signature)
# ---------------------------------------------------------------------------

# Use real-looking UUIDs so design's `UUID(str(item["id"]))` coercion passes.
_DESIGN_UUIDS = [
    UUID("00000000-0000-4000-8000-{:012d}".format(i + 1)) for i in range(30)
]


def _design_patchers(items, plan_lookup, scan_results=None):
    captured: List[Dict[str, Any]] = []
    if scan_results is None:
        scan_results = {}

    def fake_scan_single(*, item_id):
        captured.append({"item_id": str(item_id)})
        # Match by stringified UUID
        return scan_results.get(str(item_id), 0)

    return (
        patch("database.crud.Database",
              side_effect=lambda: _FakeDB(plan_lookup)),
        patch("services.design_watchlist_service.get_active_design_watchlist_items",
              return_value=items),
        patch("watchlist.design_scanner.scan_single_design_watchlist",
              side_effect=fake_scan_single),
        patch("utils.subscription.get_plan_limit",
              side_effect=_fake_plan_limit),
        captured,
    )


def test_design_scan_skips_free_org_entirely():
    from workers.scheduler import weekly_design_watchlist_scan
    items = [_make_item(item_id=str(_DESIGN_UUIDS[0]), org_id="org-free")]
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(items, {"org-free": "free"})
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    assert captured == []


def test_design_scan_runs_for_paid_org():
    from workers.scheduler import weekly_design_watchlist_scan
    items = [
        _make_item(item_id=str(_DESIGN_UUIDS[0]), org_id="org-paid"),
        _make_item(item_id=str(_DESIGN_UUIDS[1]), org_id="org-paid"),
    ]
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(
        items, {"org-paid": "starter"},
        scan_results={str(_DESIGN_UUIDS[0]): 3},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    assert sorted(c["item_id"] for c in captured) == sorted(
        [str(_DESIGN_UUIDS[0]), str(_DESIGN_UUIDS[1])]
    )


def test_design_scan_respects_weekly_frequency_window():
    from workers.scheduler import weekly_design_watchlist_scan
    two_days_ago = datetime.utcnow() - timedelta(days=2)
    items = [
        _make_item(item_id=str(_DESIGN_UUIDS[0]), org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=two_days_ago),
        _make_item(item_id=str(_DESIGN_UUIDS[1]), org_id="org-paid",
                   alert_frequency="weekly", last_scan_at=None),
    ]
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(
        items, {"org-paid": "professional"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert str(_DESIGN_UUIDS[0]) not in ids  # within 6-day window
    assert str(_DESIGN_UUIDS[1]) in ids


def test_design_scan_respects_daily_frequency_window():
    from workers.scheduler import weekly_design_watchlist_scan
    ten_h = datetime.utcnow() - timedelta(hours=10)
    twentyfive_h = datetime.utcnow() - timedelta(hours=25)
    items = [
        _make_item(item_id=str(_DESIGN_UUIDS[0]), org_id="org-paid",
                   alert_frequency="daily", last_scan_at=ten_h),
        _make_item(item_id=str(_DESIGN_UUIDS[1]), org_id="org-paid",
                   alert_frequency="daily", last_scan_at=twentyfive_h),
    ]
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(
        items, {"org-paid": "starter"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    ids = [c["item_id"] for c in captured]
    assert str(_DESIGN_UUIDS[0]) not in ids
    assert str(_DESIGN_UUIDS[1]) in ids


def test_design_scan_caps_items_at_starter_limit_25():
    from workers.scheduler import weekly_design_watchlist_scan
    items = []
    for i in range(30):
        items.append(_make_item(
            item_id=str(_DESIGN_UUIDS[i]), org_id="org-paid",
            created_at=datetime.utcnow() - timedelta(days=i + 1),
        ))
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(
        items, {"org-paid": "starter"},
    )
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    assert len(captured) == 25
    scanned_ids = {c["item_id"] for c in captured}
    for i in range(25):
        assert str(_DESIGN_UUIDS[i]) in scanned_ids
    for i in range(25, 30):
        assert str(_DESIGN_UUIDS[i]) not in scanned_ids


def test_design_scan_no_items_returns_cleanly():
    from workers.scheduler import weekly_design_watchlist_scan
    p_db, p_get, p_scan, p_lim, captured = _design_patchers(items=[], plan_lookup={})
    with p_db, p_get, p_scan, p_lim:
        weekly_design_watchlist_scan()
    assert captured == []


def test_design_scan_individual_failure_does_not_abort():
    from workers.scheduler import weekly_design_watchlist_scan
    boom_id = str(_DESIGN_UUIDS[0])
    ok_id = str(_DESIGN_UUIDS[1])
    items = [
        _make_item(item_id=boom_id, org_id="org-paid"),
        _make_item(item_id=ok_id, org_id="org-paid"),
    ]
    captured: List[str] = []

    def fake_scan(*, item_id):
        captured.append(str(item_id))
        if str(item_id) == boom_id:
            raise RuntimeError("boom")
        return 2

    with patch("database.crud.Database",
               side_effect=lambda: _FakeDB({"org-paid": "starter"})), \
         patch("services.design_watchlist_service.get_active_design_watchlist_items",
               return_value=items), \
         patch("watchlist.design_scanner.scan_single_design_watchlist",
               side_effect=fake_scan), \
         patch("utils.subscription.get_plan_limit",
               side_effect=_fake_plan_limit):
        weekly_design_watchlist_scan()

    assert captured == [boom_id, ok_id]
