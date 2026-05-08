"""Unit tests for ``watchlist.design_scanner`` — pure helpers + flow."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from watchlist import design_scanner as scanner


# ---------------------------------------------------------------------------
# combine_scores
# ---------------------------------------------------------------------------

class TestCombineScores:
    def test_image_query_weights(self):
        # 0.55*1 + 0.30*0 + 0.10*0 + 0.05*0 = 0.55
        assert scanner.combine_scores(text=0, dinov2=1.0, clip=0, color=0, has_image=True) == pytest.approx(0.55)

    def test_text_only_query_weights(self):
        # text-only: 0.70*1 + 0.20*0 + 0.10*0 = 0.70
        assert scanner.combine_scores(text=1.0, dinov2=0, clip=0, color=0, has_image=False) == pytest.approx(0.70)

    def test_clamps_to_one(self):
        # All ones with image weights: 0.55+0.30+0.10+0.05 = 1.0
        assert scanner.combine_scores(text=1, dinov2=1, clip=1, color=1, has_image=True) == 1.0

    def test_negative_clamped_to_zero(self):
        # Negative raw cosine (rare with halfvec, but possible) clamps to 0
        assert scanner.combine_scores(text=-0.5, dinov2=0.5, clip=0, color=0, has_image=True) == pytest.approx(0.55 * 0.5)

    def test_none_inputs_treated_as_zero(self):
        assert scanner.combine_scores(text=None, dinov2=None, clip=None, color=None, has_image=True) == 0.0


class TestOverlapLocarno:
    def test_basic(self):
        assert scanner.overlap_locarno(["06", "26"], ["26", "32"]) == ["26"]

    def test_case_insensitive(self):
        assert scanner.overlap_locarno(["06-01"], ["06-01"]) == ["06-01"]

    def test_empty_inputs(self):
        assert scanner.overlap_locarno([], ["06"]) == []
        assert scanner.overlap_locarno(None, None) == []

    def test_sorted_output(self):
        assert scanner.overlap_locarno(["32", "06"], ["32", "06"]) == ["06", "32"]


# ---------------------------------------------------------------------------
# scan_new_designs flow (mocked DB)
# ---------------------------------------------------------------------------

class TestScanNewDesignsFlow:
    def test_no_design_ids_returns_zero(self):
        assert scanner.scan_new_designs(
            design_ids=[],
            source_type="bulletin",
            source_reference="BLT_500",
            db_factory=lambda: MagicMock(),
        ) == 0

    def test_no_active_watchlist_returns_zero(self, monkeypatch):
        # Mock the active list to empty
        monkeypatch.setattr(scanner, "get_active_design_watchlist_items", lambda **kw: [])

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return MagicMock()
            def commit(self):
                pass

        result = scanner.scan_new_designs(
            design_ids=[uuid4(), uuid4()],
            source_type="bulletin",
            source_reference="BLT_500",
            db_factory=FakeConn,
        )
        assert result == 0

    def test_inserts_one_alert_per_candidate(self, monkeypatch):
        wl_item = {
            "id": uuid4(),
            "user_id": uuid4(),
            "organization_id": uuid4(),
            "product_name": "Sandalye",
            "locarno_classes": ["06"],
            "dinov2_embedding": None,
            "clip_embedding": None,
            "color_histogram": None,
            "customer_application_no": None,
            "customer_registration_no": None,
            "reference_design_id": None,
        }
        monkeypatch.setattr(
            scanner, "get_active_design_watchlist_items", lambda **kw: [wl_item]
        )

        candidate = {
            "id": uuid4(),
            "application_no": "2024/00077",
            "product_name": "Sandalye",
            "locarno_classes": ["06"],
            "scores": {"overall": 0.78, "dinov2": 0, "clip": 0, "color": 0, "text": 0.9,
                       "details": {"has_image_signal": False}},
            "overlapping_classes": ["06"],
        }
        monkeypatch.setattr(
            scanner, "_select_candidates_for_item", lambda *a, **kw: [candidate]
        )

        insert_calls = []
        def fake_insert(*, db, watchlist_item, conflicting_design, scores, overlapping_classes,
                        source_type, source_reference):
            insert_calls.append({"design": conflicting_design, "scores": scores})
            return uuid4()
        monkeypatch.setattr(scanner, "insert_alert_row", fake_insert)
        monkeypatch.setattr(scanner, "update_last_scan_at", lambda **kw: None)

        class FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def cursor(self, *a, **kw):
                return MagicMock()
            def commit(self):
                pass

        result = scanner.scan_new_designs(
            design_ids=[uuid4()],
            source_type="bulletin",
            source_reference="BLT_500",
            db_factory=FakeConn,
        )
        assert result == 1
        assert insert_calls[0]["scores"]["overall"] == 0.78


class TestTriggerWrapperSwallowsExceptions:
    def test_returns_zero_on_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("DB down")
        monkeypatch.setattr(scanner, "scan_new_designs", boom)

        result = scanner.trigger_design_watchlist_scan(
            [uuid4()],
            source_type="bulletin",
            source_reference="BLT_500",
        )
        assert result == 0

    def test_returns_zero_when_no_ids(self):
        assert scanner.trigger_design_watchlist_scan([]) == 0


class TestPostIngestEvaluatesAllCandidates:
    """Regression: scanner caps post-ingest candidate evaluation by limit.

    Bulletins routinely contain >100 designs; capping at the default 100 means
    most candidates aren't scored, and the matching design can be skipped
    (verified live against TS_473 / "Cerrahi kafa lambası").
    """

    def test_select_called_with_limit_at_least_design_id_count(self, monkeypatch):
        wl_item = {
            "id": uuid4(),
            "user_id": uuid4(),
            "organization_id": uuid4(),
            "product_name": "Lamba",
            "locarno_classes": [],
            "dinov2_embedding": None,
            "clip_embedding": None,
            "color_histogram": None,
            "customer_application_no": None,
            "customer_registration_no": None,
            "reference_design_id": None,
        }
        monkeypatch.setattr(scanner, "get_active_design_watchlist_items", lambda **kw: [wl_item])
        monkeypatch.setattr(scanner, "update_last_scan_at", lambda **kw: None)
        monkeypatch.setattr(scanner, "insert_alert_row", lambda **kw: None)

        captured_kwargs = {}
        def fake_select(cur, **kwargs):
            captured_kwargs.update(kwargs)
            return []
        monkeypatch.setattr(scanner, "_select_candidates_for_item", fake_select)

        class FakeConn:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def cursor(self, *a, **kw): return MagicMock()
            def commit(self): pass

        # Simulate a bulletin batch larger than the default limit
        big_batch = [uuid4() for _ in range(1500)]
        scanner.scan_new_designs(
            design_ids=big_batch,
            source_type="bulletin",
            source_reference="BLT_BIG",
            db_factory=FakeConn,
        )

        assert "limit" in captured_kwargs, "scan_new_designs must pass an explicit limit"
        assert captured_kwargs["limit"] >= len(big_batch), (
            f"limit {captured_kwargs['limit']} truncates a {len(big_batch)}-design batch; "
            "post-ingest must evaluate every candidate, not just the top-N"
        )
