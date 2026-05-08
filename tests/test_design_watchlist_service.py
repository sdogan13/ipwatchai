"""Unit tests for ``services.design_watchlist_service``.

Exercises the pure helpers and the service entry points with a mocked
``Database`` factory. Live DB integration is covered separately by the
manual live-smoke (see test.md).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from services import design_watchlist_service as svc


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestNormalizeLocarno:
    def test_zero_pads_single_digit(self):
        assert svc._normalize_locarno(["6", "06-1"]) == ["06", "06-01"]

    def test_dot_to_dash(self):
        assert svc._normalize_locarno(["06.02"]) == ["06-02"]

    def test_dedupes_preserving_order(self):
        assert svc._normalize_locarno(["06", "06.0", "06"]) == ["06", "06-00"]

    def test_handles_empty_and_none(self):
        assert svc._normalize_locarno(None) == []
        assert svc._normalize_locarno([]) == []
        assert svc._normalize_locarno(["", "  ", None]) == []

    def test_strips_non_digit(self):
        assert svc._normalize_locarno(["06A-01x"]) == ["06-01"]


class TestToHalfvecLiteral:
    def test_simple(self):
        assert svc.to_halfvec_literal([0.1, 0.2, 0.3]) == "[0.100000,0.200000,0.300000]"

    def test_none(self):
        assert svc.to_halfvec_literal(None) is None

    def test_empty(self):
        assert svc.to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# Quota / create
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_user():
    user = MagicMock()
    user.id = uuid4()
    user.organization_id = uuid4()
    return user


def _fake_db(returns):
    """Return a context-manager factory whose cursor returns the rows in order."""
    cur = MagicMock()
    rows_iter = iter(returns)

    def fetchone():
        try:
            return next(rows_iter)
        except StopIteration:
            return None
    cur.fetchone = fetchone
    cur.execute = MagicMock()

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def cursor(self, *a, **kw):
            return cur
        def commit(self):
            pass

    factory = MagicMock(side_effect=FakeConn)
    return factory, cur


class TestQuota:
    def test_combined_count_below_limit_passes(self, fake_user, monkeypatch):
        monkeypatch.setattr(
            svc,
            "_combined_watchlist_count",
            lambda cur, oid: 4,
        )
        # patch get_user_plan + get_plan_limit through utils.subscription
        import utils.subscription as sub_mod
        monkeypatch.setattr(sub_mod, "get_user_plan", lambda db, uid: {"plan_name": "starter"})
        monkeypatch.setattr(sub_mod, "get_plan_limit", lambda plan, feat: 15)
        db = MagicMock()
        # should not raise
        svc._check_watchlist_quota(db, fake_user)

    def test_combined_count_at_limit_raises_403(self, fake_user, monkeypatch):
        monkeypatch.setattr(svc, "_combined_watchlist_count", lambda cur, oid: 5)
        import utils.subscription as sub_mod
        monkeypatch.setattr(sub_mod, "get_user_plan", lambda db, uid: {"plan_name": "free"})
        monkeypatch.setattr(sub_mod, "get_plan_limit", lambda plan, feat: 5)
        db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            svc._check_watchlist_quota(db, fake_user)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error"] == "limit_exceeded"
        assert exc_info.value.detail["max_items"] == 5


class TestCreate:
    def test_rejects_empty_product_name(self, fake_user):
        with pytest.raises(HTTPException) as exc_info:
            svc.create_design_watchlist_item(
                data={"product_name": "  "},
                current_user=fake_user,
                db_factory=lambda: MagicMock(),
            )
        assert exc_info.value.status_code == 400

    def test_dedupe_rejects_existing_app_no(self, fake_user, monkeypatch):
        # Quota OK, customer_application_no exists
        monkeypatch.setattr(svc, "_check_watchlist_quota", lambda db, u: None)

        cur = MagicMock()
        # First fetchone for dedupe check returns truthy
        cur.fetchone = MagicMock(return_value={"?column?": 1})
        cur.execute = MagicMock()

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return cur
            def commit(self):
                pass

        with pytest.raises(HTTPException) as exc_info:
            svc.create_design_watchlist_item(
                data={"product_name": "Sandalye", "customer_application_no": "2024/12345"},
                current_user=fake_user,
                db_factory=FakeConn,
            )
        assert exc_info.value.status_code == 409

    def test_inserts_with_normalized_locarno(self, fake_user, monkeypatch):
        monkeypatch.setattr(svc, "_check_watchlist_quota", lambda db, u: None)

        captured = {}

        cur = MagicMock()

        def fetchone_side():
            # 1) dedupe check (no app_no, so this is skipped)
            # 2) no reference_design_id, so this is skipped
            # 3) RETURNING * after INSERT
            return {
                "id": uuid4(),
                "product_name": "Sandalye",
                "locarno_classes": ["06"],
                "image_path": None,
                "dinov2_embedding": None,
            }
        cur.fetchone = MagicMock(side_effect=fetchone_side)

        def execute_capture(sql, params=None):
            if "INSERT INTO design_watchlist_mt" in sql:
                captured["params"] = params
        cur.execute = MagicMock(side_effect=execute_capture)

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return cur
            def commit(self):
                pass

        result = svc.create_design_watchlist_item(
            data={"product_name": "Sandalye", "locarno_classes": ["6", "06.02"]},
            current_user=fake_user,
            db_factory=FakeConn,
        )
        assert result["product_name"] == "Sandalye"
        assert captured["params"]["locarno_classes"] == ["06", "06-02"]


# ---------------------------------------------------------------------------
# Update / delete
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_returns_existing_when_no_fields(self, fake_user, monkeypatch):
        monkeypatch.setattr(
            svc,
            "get_design_watchlist_item",
            lambda **kw: {"id": "x", "product_name": "Lamba"},
        )
        result = svc.update_design_watchlist_item(
            item_id=uuid4(),
            data={},
            current_user=fake_user,
            db_factory=lambda: MagicMock(),
        )
        assert result["product_name"] == "Lamba"

    def test_404_when_no_match(self, fake_user):
        cur = MagicMock()
        cur.fetchone = MagicMock(return_value=None)
        cur.execute = MagicMock()

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return cur
            def commit(self):
                pass

        with pytest.raises(HTTPException) as exc_info:
            svc.update_design_watchlist_item(
                item_id=uuid4(),
                data={"product_name": "Yeni"},
                current_user=fake_user,
                db_factory=FakeConn,
            )
        assert exc_info.value.status_code == 404


class TestDelete:
    def test_404_when_not_found(self, fake_user):
        cur = MagicMock()
        rows = [
            {"c": 0},   # alert count
            None,        # DELETE ... RETURNING id
        ]
        rows_iter = iter(rows)
        cur.fetchone = lambda: next(rows_iter)
        cur.execute = MagicMock()

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return cur
            def commit(self):
                pass

        with pytest.raises(HTTPException) as exc_info:
            svc.delete_design_watchlist_item(
                item_id=uuid4(),
                current_user=fake_user,
                db_factory=FakeConn,
            )
        assert exc_info.value.status_code == 404

    def test_reports_deleted_alert_count(self, fake_user):
        cur = MagicMock()
        item_id = uuid4()
        rows = [
            {"c": 3},          # alert count
            {"id": str(item_id)},  # DELETE ... RETURNING id
        ]
        rows_iter = iter(rows)
        cur.fetchone = lambda: next(rows_iter)
        cur.execute = MagicMock()

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return cur
            def commit(self):
                pass

        result = svc.delete_design_watchlist_item(
            item_id=item_id,
            current_user=fake_user,
            db_factory=FakeConn,
        )
        assert result["success"] is True
        assert result["removed_alerts"] == 3
        assert "3" in result["message"]
