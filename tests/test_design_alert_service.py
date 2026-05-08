"""Unit tests for ``services.design_alert_service``."""
from __future__ import annotations

import sys
from datetime import datetime, date
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from services import design_alert_service as svc


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestSeverityForScore:
    def test_critical(self):
        assert svc.severity_for_score(0.95) == "critical"
        assert svc.severity_for_score(0.85) == "critical"

    def test_high(self):
        assert svc.severity_for_score(0.84) == "high"
        assert svc.severity_for_score(0.70) == "high"

    def test_medium(self):
        assert svc.severity_for_score(0.69) == "medium"
        assert svc.severity_for_score(0.55) == "medium"

    def test_low(self):
        assert svc.severity_for_score(0.54) == "low"
        assert svc.severity_for_score(0.0) == "low"


class TestFormatDesignAlert:
    def test_minimal_row(self):
        row = {
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": uuid4(),
            "user_id": None,
            "conflicting_design_id": None,
            "conflicting_application_no": "2024/00001",
            "conflicting_locarno_classes": ["06", "06-01"],
            "overall_similarity_score": 0.72,
            "severity": "high",
            "status": "new",
            "alert_type": "conflict",
            "score_details": '{"has_image_signal": true}',
        }
        out = svc.format_design_alert(row)
        assert out["scores"]["overall"] == 0.72
        assert out["severity"] == "high"
        assert out["status"] == "new"
        assert out["conflicting"]["application_no"] == "2024/00001"
        assert out["conflicting"]["locarno_classes"] == ["06", "06-01"]
        assert out["scores"]["details"] == {"has_image_signal": True}

    def test_handles_dict_score_details(self):
        row = {
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": uuid4(),
            "overall_similarity_score": 0.5,
            "score_details": {"foo": "bar"},
        }
        out = svc.format_design_alert(row)
        assert out["scores"]["details"] == {"foo": "bar"}

    def test_handles_invalid_score_details(self):
        row = {
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": uuid4(),
            "overall_similarity_score": 0.5,
            "score_details": "not-json{{",
        }
        out = svc.format_design_alert(row)
        assert out["scores"]["details"] == {}

    def test_serializes_datetime_fields(self):
        row = {
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": uuid4(),
            "overall_similarity_score": 0.6,
            "created_at": datetime(2026, 5, 8, 10, 30, 0),
            "conflicting_bulletin_date": date(2026, 5, 1),
        }
        out = svc.format_design_alert(row)
        assert out["created_at"].startswith("2026-05-08")
        assert out["conflicting"]["bulletin_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_user():
    user = MagicMock()
    user.id = uuid4()
    user.organization_id = uuid4()
    return user


def _conn_with_returning(row_or_none):
    cur = MagicMock()
    cur.fetchone = MagicMock(return_value=row_or_none)
    cur.execute = MagicMock()

    class Conn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def cursor(self, *a, **kw):
            return cur
        def commit(self):
            pass
    return Conn, cur


class TestTransition:
    def test_invalid_status_raises_400(self, fake_user):
        with pytest.raises(HTTPException) as exc_info:
            svc._transition_alert(
                alert_id=uuid4(),
                next_status="bogus",
                notes=None,
                current_user=fake_user,
                db_factory=lambda: MagicMock(),
            )
        assert exc_info.value.status_code == 400

    def test_acknowledge_sets_acknowledged_columns(self, fake_user):
        Conn, cur = _conn_with_returning({
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": fake_user.organization_id,
            "status": "acknowledged",
            "overall_similarity_score": 0.7,
        })

        result = svc.acknowledge_design_alert(
            alert_id=uuid4(),
            notes="reviewing",
            current_user=fake_user,
            db_factory=Conn,
        )
        sql_arg = cur.execute.call_args.args[0]
        assert "acknowledged_at = NOW()" in sql_arg
        assert "acknowledged_by = %s" in sql_arg
        assert result["status"] == "acknowledged"

    def test_resolve_sets_notes_when_provided(self, fake_user):
        Conn, cur = _conn_with_returning({
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": fake_user.organization_id,
            "status": "resolved",
            "overall_similarity_score": 0.7,
        })
        svc.resolve_design_alert(
            alert_id=uuid4(),
            notes="Owner cleared the conflict",
            current_user=fake_user,
            db_factory=Conn,
        )
        sql_arg = cur.execute.call_args.args[0]
        assert "resolution_notes = %s" in sql_arg

    def test_resolve_omits_notes_when_none(self, fake_user):
        Conn, cur = _conn_with_returning({
            "id": uuid4(),
            "watchlist_item_id": uuid4(),
            "organization_id": fake_user.organization_id,
            "status": "resolved",
            "overall_similarity_score": 0.7,
        })
        svc.resolve_design_alert(
            alert_id=uuid4(),
            notes=None,
            current_user=fake_user,
            db_factory=Conn,
        )
        sql_arg = cur.execute.call_args.args[0]
        assert "resolution_notes = %s" not in sql_arg

    def test_404_when_no_row(self, fake_user):
        Conn, cur = _conn_with_returning(None)
        with pytest.raises(HTTPException) as exc_info:
            svc.dismiss_design_alert(
                alert_id=uuid4(),
                notes="false positive",
                current_user=fake_user,
                db_factory=Conn,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Insert (scanner integration)
# ---------------------------------------------------------------------------

class TestInsertAlertRow:
    def test_returns_id_on_success(self):
        cur = MagicMock()
        cur.fetchone = MagicMock(return_value={"id": uuid4()})
        cur.execute = MagicMock()

        class DB:
            def cursor(self, *a, **kw):
                return cur
        wl = {
            "id": uuid4(),
            "user_id": uuid4(),
            "organization_id": uuid4(),
        }
        conf = {
            "id": uuid4(),
            "application_no": "2024/00099",
            "product_name": "Lamba",
            "locarno_classes": ["26"],
            "holder_name": "ACME A.Ş.",
            "image_path": "bulletins/Tasarim/TS_500/page_001/img_002.jpg",
            "bulletin_no": "500",
            "bulletin_date": date(2026, 5, 1),
            "opposition_end": date(2026, 7, 30),
        }
        scores = {"overall": 0.78, "dinov2": 0.81, "clip": 0.7, "color": 0.6, "text": 0.4,
                  "details": {"has_image_signal": True}}

        alert_id = svc.insert_alert_row(
            db=DB(),
            watchlist_item=wl,
            conflicting_design=conf,
            scores=scores,
            overlapping_classes=["26"],
            source_type="bulletin",
            source_reference="BLT_500",
        )
        assert alert_id is not None
        # severity should be 'high' for 0.78
        params = cur.execute.call_args.args[1]
        assert params["severity"] == "high"
        assert params["overall_similarity_score"] == 0.78

    def test_returns_none_when_dedup_skips(self):
        cur = MagicMock()
        cur.fetchone = MagicMock(return_value=None)  # ON CONFLICT DO NOTHING
        cur.execute = MagicMock()

        class DB:
            def cursor(self, *a, **kw):
                return cur

        wl = {"id": uuid4(), "user_id": None, "organization_id": uuid4()}
        conf = {"id": uuid4(), "application_no": "X"}
        scores = {"overall": 0.6}
        alert_id = svc.insert_alert_row(
            db=DB(),
            watchlist_item=wl,
            conflicting_design=conf,
            scores=scores,
            overlapping_classes=[],
            source_type="bulletin",
            source_reference="BLT_500",
        )
        assert alert_id is None
