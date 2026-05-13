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
    from app_design_search_routes import _resolve_design_image
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


def test_resolve_design_image_falls_back_to_cd_images(tmp_path, monkeypatch):
    """DB-stored paths shape ``{source}/{design_id}/{view}.jpg`` need to
    resolve under ``cd_images/{design_id}/{view}.jpg`` because the
    on-disk layout puts CD-sourced views inside the cd_images/ subdir."""
    from app_design_search_routes import _resolve_design_image
    import app_design_search_routes as routes
    fake_root = tmp_path / "Tasarim"
    design_dir = fake_root / "TS_test/cd_images/2024_004342"
    design_dir.mkdir(parents=True)
    target = design_dir / "62_1.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0")
    monkeypatch.setattr(routes, "DESIGN_BULLETINS_ROOT", fake_root)

    # image_path stored as "{source}/{design_id}/{view}.jpg" — resolver
    # should locate it under cd_images/ via fallback.
    resolved = _resolve_design_image("TS_test/2024_004342/62_1.jpg")
    assert resolved is not None
    assert Path(resolved).samefile(target)


def test_resolve_design_image_falls_back_to_images(tmp_path, monkeypatch):
    """Same fallback also covers PDF-sourced files under images/."""
    from app_design_search_routes import _resolve_design_image
    import app_design_search_routes as routes
    fake_root = tmp_path / "Tasarim"
    design_dir = fake_root / "TS_test/images/2024_009999"
    design_dir.mkdir(parents=True)
    target = design_dir / "1_1.jpg"
    target.write_bytes(b"\xff\xd8\xff\xe0")
    monkeypatch.setattr(routes, "DESIGN_BULLETINS_ROOT", fake_root)

    resolved = _resolve_design_image("TS_test/2024_009999/1_1.jpg")
    assert resolved is not None
    assert Path(resolved).samefile(target)


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


# ---------------------------------------------------------------------------
# Route registration — verifies the three paths attach to a FastAPI app
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Quota counting on /quick — eligibility + increment wiring
# ---------------------------------------------------------------------------

import asyncio


class _NullDB:
    """Stand-in for ``database.crud.Database`` in tests — supports the
    context-manager protocol without opening a real connection."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _stub_async_search(*, expected_query=None):
    async def _stub(**_kwargs):
        if expected_query is not None:
            assert _kwargs.get("query") == expected_query
        return {"results": [], "total": 0, "duration_ms": 0}
    return _stub


def test_quick_search_returns_429_when_quota_exceeded(monkeypatch):
    """Over-limit users see the eligibility payload as a 429."""
    from app_design_search_routes import design_search_quick

    over_limit_details = {
        "error": "daily_limit_exceeded",
        "current_plan": "free",
        "daily_limit": 5,
        "used_today": 5,
        "remaining": 0,
        "message": "Limit ulasildi",
        "message_en": "Limit reached",
    }

    eligibility_calls = []
    increment_calls = []

    def fake_check(db, user_id):
        eligibility_calls.append(user_id)
        return False, "daily_limit_exceeded", over_limit_details

    def fake_increment(db, user_id, org_id=None):
        increment_calls.append((user_id, org_id))
        return 6

    monkeypatch.setattr("utils.subscription.check_quick_search_eligibility", fake_check)
    monkeypatch.setattr("utils.subscription.increment_quick_search_usage", fake_increment)
    monkeypatch.setattr("database.crud.Database", lambda *a, **kw: _NullDB())
    monkeypatch.setattr("app_design_search_routes._do_design_search", _stub_async_search())

    with pytest.raises(Exception) as excinfo:
        asyncio.run(design_search_quick(
            query="Lamba", image=None, locarno=None, limit=10,
            user_id="user-123", organization_id="org-1",
        ))

    assert getattr(excinfo.value, "status_code", None) == 429
    assert getattr(excinfo.value, "detail", None) == over_limit_details
    assert eligibility_calls == ["user-123"]
    assert increment_calls == []  # increment NOT called when over limit


def test_quick_search_increments_after_successful_search(monkeypatch):
    """Under-limit users get a 200 and the daily counter goes up by one."""
    from app_design_search_routes import design_search_quick

    eligibility_calls = []
    increment_calls = []

    def fake_check(db, user_id):
        eligibility_calls.append(user_id)
        return True, "ok", {"current_plan": "starter", "daily_limit": 50,
                             "used_today": 3, "remaining": 47}

    def fake_increment(db, user_id, org_id=None):
        increment_calls.append((user_id, org_id))
        return 4

    monkeypatch.setattr("utils.subscription.check_quick_search_eligibility", fake_check)
    monkeypatch.setattr("utils.subscription.increment_quick_search_usage", fake_increment)
    monkeypatch.setattr("database.crud.Database", lambda *a, **kw: _NullDB())
    monkeypatch.setattr("app_design_search_routes._do_design_search",
                        _stub_async_search(expected_query="Lamba"))

    result = asyncio.run(design_search_quick(
        query="Lamba", image=None, locarno=None, limit=10,
        user_id="user-456", organization_id="org-2",
    ))

    assert result == {"results": [], "total": 0, "duration_ms": 0}
    assert eligibility_calls == ["user-456"]
    assert increment_calls == [("user-456", "org-2")]


def test_quick_search_skips_quota_when_no_user_id(monkeypatch):
    """If user_id wasn't resolved (defensive path), neither check nor
    increment runs."""
    from app_design_search_routes import design_search_quick

    eligibility_calls = []
    increment_calls = []

    def fake_check(db, user_id):
        eligibility_calls.append(user_id)
        return False, "x", {}

    def fake_increment(db, user_id, org_id=None):
        increment_calls.append((user_id, org_id))

    monkeypatch.setattr("utils.subscription.check_quick_search_eligibility", fake_check)
    monkeypatch.setattr("utils.subscription.increment_quick_search_usage", fake_increment)
    monkeypatch.setattr("app_design_search_routes._do_design_search", _stub_async_search())

    asyncio.run(design_search_quick(
        query="Lamba", image=None, locarno=None, limit=10,
        user_id=None, organization_id=None,
    ))

    assert eligibility_calls == []
    assert increment_calls == []


# ---------------------------------------------------------------------------
# Route registration — verifies the three paths attach to a FastAPI app
# ---------------------------------------------------------------------------

def test_register_design_search_routes_attaches_three_paths():
    from fastapi import FastAPI
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from app_design_search_routes import register_design_search_routes

    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    register_design_search_routes(app, limiter)

    paths = {(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
             for r in app.routes}

    expected = {
        ("/api/v1/design-image/{image_path:path}", ("GET",)),
        ("/api/v1/design-search/public", ("GET",)),
        ("/api/v1/design-search/public", ("POST",)),
        ("/api/v1/design-search", ("POST",)),
    }
    for path, methods in expected:
        # FastAPI may include HEAD on GET routes; check subset relation
        matching = [
            (p, m) for (p, m) in paths
            if p == path and set(methods).issubset(set(m))
        ]
        assert matching, f"expected {path} {methods} not registered; got: {paths}"
