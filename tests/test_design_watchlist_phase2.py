"""Phase-2 service tests for design watchlist bulk + stats helpers.

Each test injects a fake DB factory that returns canned cursor results,
mirroring the pattern other service tests in this repo use. The intent is
to lock in:
  * the org-scoping clause on every endpoint (no cross-tenant leakage)
  * the {total, threatened, critical, new_alerts} stats shape
  * threshold-bound rejection (422) outside [0.0, 1.0]
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.design_watchlist_service import (
    delete_all_design_watchlist_items,
    get_design_watchlist_stats,
    list_active_item_ids_for_org,
    update_all_design_watchlist_thresholds,
)


# ---------------------------------------------------------------------------
# Fake DB plumbing
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, scripted_responses: List[Any]):
        self._scripted = list(scripted_responses)
        self.executed: List[tuple] = []
        self._next_result: Any = None

    def execute(self, sql: str, params: Optional[Any] = None) -> None:
        self.executed.append((sql, params))
        if self._scripted:
            self._next_result = self._scripted.pop(0)
        else:
            self._next_result = None

    def fetchone(self):
        if isinstance(self._next_result, list):
            return self._next_result[0] if self._next_result else None
        return self._next_result

    def fetchall(self):
        if self._next_result is None:
            return []
        if isinstance(self._next_result, list):
            return self._next_result
        return [self._next_result]


class _FakeDB:
    def __init__(self, scripted_responses: List[Any]):
        self._cur = _FakeCursor(scripted_responses)
        self.committed = False

    def cursor(self, **kwargs):
        return self._cur

    def commit(self) -> None:
        self.committed = True

    @property
    def executed(self):
        return self._cur.executed


def _fake_factory(scripted: List[Any]):
    db = _FakeDB(scripted)

    @contextmanager
    def factory():
        yield db

    factory._db = db
    return factory


class _FakeUser:
    def __init__(self, org_id: str = "11111111-1111-1111-1111-111111111111"):
        self.organization_id = org_id


# ---------------------------------------------------------------------------
# get_design_watchlist_stats
# ---------------------------------------------------------------------------

def test_stats_returns_full_shape_and_org_scopes():
    user = _FakeUser()
    factory = _fake_factory([
        {"total": 7, "threatened": 3, "critical": 2, "new_alerts": 5},
    ])
    out = get_design_watchlist_stats(current_user=user, db_factory=factory)
    assert out == {"total": 7, "threatened": 3, "critical": 2, "new_alerts": 5}
    sql, params = factory._db.executed[0]
    assert "design_watchlist_mt" in sql
    assert "design_alerts_mt" in sql
    # The org id is passed under the %(org)s placeholder.
    assert isinstance(params, dict)
    assert params.get("org") == user.organization_id


def test_stats_handles_null_counts():
    user = _FakeUser()
    factory = _fake_factory([
        {"total": None, "threatened": None, "critical": None, "new_alerts": None},
    ])
    out = get_design_watchlist_stats(current_user=user, db_factory=factory)
    assert out == {"total": 0, "threatened": 0, "critical": 0, "new_alerts": 0}


# ---------------------------------------------------------------------------
# list_active_item_ids_for_org
# ---------------------------------------------------------------------------

def test_list_active_item_ids_returns_strings_and_filters_active_only():
    user = _FakeUser()
    a, b = uuid4(), uuid4()
    factory = _fake_factory([[{"id": a}, {"id": b}]])
    out = list_active_item_ids_for_org(current_user=user, db_factory=factory)
    assert out == [str(a), str(b)]
    sql, params = factory._db.executed[0]
    assert "is_active = TRUE" in sql
    assert params == (user.organization_id,)


def test_list_active_item_ids_empty():
    user = _FakeUser()
    factory = _fake_factory([[]])
    assert list_active_item_ids_for_org(current_user=user, db_factory=factory) == []


# ---------------------------------------------------------------------------
# delete_all_design_watchlist_items
# ---------------------------------------------------------------------------

def test_delete_all_returns_count_and_commits():
    user = _FakeUser()
    factory = _fake_factory([[{"id": uuid4()}, {"id": uuid4()}, {"id": uuid4()}]])
    out = delete_all_design_watchlist_items(current_user=user, db_factory=factory)
    assert out == {"success": True, "deleted": 3}
    assert factory._db.committed is True
    sql, params = factory._db.executed[0]
    assert "DELETE FROM design_watchlist_mt" in sql
    assert "WHERE organization_id = %s" in sql
    assert params == (user.organization_id,)


def test_delete_all_with_no_rows():
    user = _FakeUser()
    factory = _fake_factory([[]])
    out = delete_all_design_watchlist_items(current_user=user, db_factory=factory)
    assert out == {"success": True, "deleted": 0}


# ---------------------------------------------------------------------------
# update_all_design_watchlist_thresholds
# ---------------------------------------------------------------------------

def test_update_all_thresholds_happy_path():
    user = _FakeUser()
    factory = _fake_factory([[{"id": uuid4()}, {"id": uuid4()}]])
    out = update_all_design_watchlist_thresholds(
        current_user=user, threshold=0.75, db_factory=factory,
    )
    assert out == {"success": True, "updated": 2, "threshold": 0.75}
    sql, params = factory._db.executed[0]
    assert "UPDATE design_watchlist_mt" in sql
    assert "is_active = TRUE" in sql
    assert params == (0.75, user.organization_id)
    assert factory._db.committed is True


@pytest.mark.parametrize("bad", [-0.1, 1.5, 2.0, -1.0])
def test_update_all_thresholds_rejects_out_of_range(bad):
    user = _FakeUser()
    factory = _fake_factory([])
    with pytest.raises(HTTPException) as exc_info:
        update_all_design_watchlist_thresholds(
            current_user=user, threshold=bad, db_factory=factory,
        )
    assert exc_info.value.status_code == 422
