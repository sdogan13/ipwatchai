"""Unit tests for the design search service + route helpers.

Pure helpers and SQL-builder behavior are tested here. End-to-end DB
queries are exercised by manual smoke runs against the live Postgres
instance (covered in the verification report, not pytest).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.design_search_service import (
    DEFAULT_LIMIT,
    INACTIVE_STATUSES,
    MAX_LIMIT,
    PUBLIC_RESULT_CAP,
    WEIGHTS_IMAGE_QUERY,
    WEIGHTS_TEXT_QUERY,
    cap_limit,
    combine_scores,
    design_image_url,
    normalize_locarno_filter,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_basic():
    assert to_halfvec_literal([1.0, 2.5, -3.7]) == "[1.000000,2.500000,-3.700000]"


def test_to_halfvec_literal_none_or_empty():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# combine_scores
# ---------------------------------------------------------------------------

def test_combine_scores_image_weights_dominant():
    # All-DINOv2 perfect match should land near the dinov2 weight.
    score = combine_scores(text=0, dinov2=1.0, clip=0, color=0, has_image=True)
    assert abs(score - WEIGHTS_IMAGE_QUERY["dinov2"]) < 1e-9


def test_combine_scores_text_weights_dominant_for_text_query():
    score = combine_scores(text=1.0, dinov2=0, clip=0, color=0, has_image=False)
    assert abs(score - WEIGHTS_TEXT_QUERY["text"]) < 1e-9


def test_combine_scores_caps_at_one():
    # Even with all signals at 1.0, score caps at 1.0.
    score = combine_scores(text=1.0, dinov2=1.0, clip=1.0, color=1.0, has_image=True)
    assert score == 1.0


def test_combine_scores_negative_signals_treated_as_zero():
    # Cosine-similarity rarely goes negative but be defensive.
    score = combine_scores(text=-0.5, dinov2=1.0, clip=0, color=0, has_image=True)
    assert abs(score - WEIGHTS_IMAGE_QUERY["dinov2"]) < 1e-9


def test_combine_scores_image_query_uses_color_signal_text_query_does_not():
    image_score = combine_scores(text=0, dinov2=0, clip=0, color=1.0, has_image=True)
    assert image_score > 0
    text_score = combine_scores(text=0, dinov2=0, clip=0, color=1.0, has_image=False)
    assert text_score == 0  # color weight is 0 in text-query mode


# ---------------------------------------------------------------------------
# normalize_locarno_filter
# ---------------------------------------------------------------------------

def test_normalize_locarno_filter_basic():
    assert normalize_locarno_filter(["06-01", "06.02"]) == ["06-01", "06-02"]


def test_normalize_locarno_filter_dedupes_and_strips_blanks():
    assert normalize_locarno_filter([" 06-01 ", "", "06-01", None]) == ["06-01"]


def test_normalize_locarno_filter_empty_returns_none():
    assert normalize_locarno_filter(None) is None
    assert normalize_locarno_filter([]) is None
    assert normalize_locarno_filter(["", "  "]) is None


# ---------------------------------------------------------------------------
# cap_limit
# ---------------------------------------------------------------------------

def test_cap_limit_within_range():
    assert cap_limit(20) == 20
    assert cap_limit("50") == 50


def test_cap_limit_caps_at_max():
    assert cap_limit(500) == MAX_LIMIT
    assert cap_limit(MAX_LIMIT + 1) == MAX_LIMIT


def test_cap_limit_caps_lower_for_public():
    assert cap_limit(50, public=True) == PUBLIC_RESULT_CAP
    assert cap_limit(5, public=True) == 5


def test_cap_limit_handles_garbage_with_default():
    assert cap_limit(None) == DEFAULT_LIMIT
    assert cap_limit("not-a-number") == DEFAULT_LIMIT


def test_cap_limit_minimum_one():
    assert cap_limit(0) == 1
    assert cap_limit(-5) == 1


# ---------------------------------------------------------------------------
# design_image_url
# ---------------------------------------------------------------------------

def test_design_image_url_builds_route_path():
    url = design_image_url("images/2024_007254_1_1.jpg", "TS_483_2026-04-24")
    assert url == "/api/v1/design-image/TS_483_2026-04-24/images/2024_007254_1_1.jpg"


def test_design_image_url_handles_leading_slash():
    url = design_image_url("/images/foo.jpg", "TS_483_2026-04-24")
    assert url == "/api/v1/design-image/TS_483_2026-04-24/images/foo.jpg"


def test_design_image_url_returns_none_when_either_part_missing():
    assert design_image_url(None, "TS_483") is None
    assert design_image_url("images/foo.jpg", None) is None
    assert design_image_url("", "TS_483") is None


# ---------------------------------------------------------------------------
# Inactive status set sanity
# ---------------------------------------------------------------------------

def test_inactive_statuses_includes_iptal_and_hukumsuz():
    assert "İptal Edildi" in INACTIVE_STATUSES
    assert "Hükümsüz" in INACTIVE_STATUSES


# ---------------------------------------------------------------------------
# Image route resolver — directory traversal protection
# ---------------------------------------------------------------------------

def test_resolve_design_image_rejects_traversal(tmp_path, monkeypatch):
    from app_design_search_routes import _resolve_design_image, DESIGN_BULLETINS_ROOT
    # Craft a path that tries to escape the root
    assert _resolve_design_image("../../../etc/passwd") is None
    assert _resolve_design_image("..\\windows\\win.ini") is None


def test_resolve_design_image_returns_none_for_missing_file():
    from app_design_search_routes import _resolve_design_image
    assert _resolve_design_image("TS_999_does_not_exist/images/foo.jpg") is None


def test_resolve_design_image_returns_path_when_file_exists(tmp_path, monkeypatch):
    """Place a real file under the design bulletins root and resolve it."""
    from app_design_search_routes import _resolve_design_image
    import app_design_search_routes as routes
    # Redirect DESIGN_BULLETINS_ROOT to a temp folder for this test
    fake_root = tmp_path / "Tasarim"
    fake_root.mkdir()
    issue_dir = fake_root / "TS_test/images"
    issue_dir.mkdir(parents=True)
    target = issue_dir / "x.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic
    monkeypatch.setattr(routes, "DESIGN_BULLETINS_ROOT", fake_root)
    resolved = _resolve_design_image("TS_test/images/x.jpg")
    assert resolved is not None
    assert Path(resolved).is_file()


# ---------------------------------------------------------------------------
# Smoke: search_designs with mocked cursor returns a sensible response shape
# ---------------------------------------------------------------------------

def test_search_designs_empty_query_returns_error():
    from services.design_search_service import search_designs
    fake_conn = MagicMock()
    res = search_designs(fake_conn, query=None, image_embeddings=None)
    assert res["total"] == 0
    assert res["results"] == []
    assert res["error"] == "design_search.empty_query"
