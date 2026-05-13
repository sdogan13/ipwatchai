"""Unit tests for the cross-registry unified search service + routes.

Pure helpers, merge-and-rank logic, and route registration are tested
here. Live DB queries are exercised by manual smoke (covered in the
verification report).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from services.registry_search_service import (
    _normalize_design_result,
    _trademark_image_url,
    normalize_nice_filter,
    parse_registries,
    search_unified,
)


# ---------------------------------------------------------------------------
# normalize_nice_filter
# ---------------------------------------------------------------------------

def test_normalize_nice_filter_basic():
    assert normalize_nice_filter([35, 41]) == [35, 41]
    assert normalize_nice_filter([35, "41", "99"]) == [35, 41, 99]


def test_normalize_nice_filter_drops_garbage_and_dedupes():
    assert normalize_nice_filter([35, None, "", "abc", 35, "41"]) == [35, 41]


def test_normalize_nice_filter_empty_returns_none():
    assert normalize_nice_filter(None) is None
    assert normalize_nice_filter([]) is None
    assert normalize_nice_filter(["", None]) is None


# ---------------------------------------------------------------------------
# parse_registries
# ---------------------------------------------------------------------------

def test_parse_registries_default_both():
    assert parse_registries(None) == ["trademark", "design"]
    assert parse_registries([]) == ["trademark", "design"]


def test_parse_registries_single():
    assert parse_registries(["trademark"]) == ["trademark"]
    assert parse_registries(["design"]) == ["design"]


def test_parse_registries_dedupes_and_drops_unknown():
    assert parse_registries(["trademark", "design", "trademark", "patent"]) == [
        "trademark", "design",
    ]


def test_parse_registries_unknown_only_falls_back_to_default():
    assert parse_registries(["patent", "garbage"]) == ["trademark", "design"]


# ---------------------------------------------------------------------------
# _trademark_image_url
# ---------------------------------------------------------------------------

def test_trademark_image_url_strips_leading_slash():
    assert _trademark_image_url("bulletins/Marka/BLT_500/images/x.jpg") == \
        "/api/trademark-image/bulletins/Marka/BLT_500/images/x.jpg"
    assert _trademark_image_url("/foo.jpg") == "/api/trademark-image/foo.jpg"


# ---------------------------------------------------------------------------
# _normalize_design_result — converts design service shape to unified shape
# ---------------------------------------------------------------------------

def test_normalize_design_result_carries_registry_type_and_nests_design_block():
    raw = {
        "id": "uuid-d-1",
        "application_no": "2024/007254",
        "registration_no": "2024 007254",
        "product_name_tr": "Lamba",
        "design_index": 1,
        "locarno_classes": ["26-05"],
        "section": "tr_native",
        "current_status": "Yayında",
        "designers": ["A", "B"],
        "bulletin_no": "483",
        "bulletin_date": "2026-04-24",
        "holder": {"name": "Holder Co", "tpe_client_id": "1234567"},
        "image_url": "/api/v1/design-image/foo.jpg",
        "similarity": 71.5,
        "similarity_breakdown": {"text": 1.0, "dinov2": 0.0, "clip": 0.0, "color": 0.0},
        "hague_reference": None,
        "deferred_publication": None,
    }
    out = _normalize_design_result(raw)
    assert out["registry_type"] == "design"
    assert out["title"] == "Lamba"
    assert out["application_no"] == "2024/007254"
    assert out["holder"] == {"name": "Holder Co", "tpe_client_id": "1234567"}
    assert out["image_url"] == "/api/v1/design-image/foo.jpg"
    assert out["similarity"] == 71.5
    assert out["trademark"] is None
    assert out["design"]["design_index"] == 1
    assert out["design"]["locarno_classes"] == ["26-05"]
    assert out["design"]["section"] == "tr_native"
    assert out["design"]["designers"] == ["A", "B"]


def test_normalize_design_result_falls_back_to_english_name_for_hague():
    raw = {
        "id": "uuid-d-2",
        "product_name_tr": None,
        "product_name_en": "Jewelry for swim wear",
        "section": "hague",
        "registration_no": "DM 244882",
    }
    out = _normalize_design_result(raw)
    assert out["title"] == "Jewelry for swim wear"


# ---------------------------------------------------------------------------
# search_unified — merge + rank logic with mocked components
# ---------------------------------------------------------------------------

def test_search_unified_empty_input_returns_error():
    fake_conn = MagicMock()
    res = search_unified(fake_conn, query=None, image_embeddings_design=None,
                         image_embeddings_trademark=None)
    assert res["total"] == 0
    assert res["results"] == []
    assert res["error"] == "registry_search.empty_query"


def test_search_unified_merges_by_similarity_descending(monkeypatch):
    """Mock both retrieval branches; assert global ranking by similarity."""
    fake_design_results = [
        {
            "id": "d1", "application_no": "D-1", "product_name_tr": "Lamba",
            "section": "tr_native", "similarity": 60.0,
            "similarity_breakdown": {"text": 0.6, "dinov2": 0, "clip": 0, "color": 0},
            "locarno_classes": [], "designers": [], "holder": None,
            "image_url": None, "current_status": "Yayında",
        },
        {
            "id": "d2", "application_no": "D-2", "product_name_tr": "Lamba",
            "section": "tr_native", "similarity": 80.0,
            "similarity_breakdown": {"text": 0.8, "dinov2": 0, "clip": 0, "color": 0},
            "locarno_classes": [], "designers": [], "holder": None,
            "image_url": None, "current_status": "Yayında",
        },
    ]
    fake_tm_results = [
        {
            "registry_type": "trademark", "id": "t1", "application_no": "T-1",
            "registration_no": None, "title": "TMARK", "holder": None,
            "image_url": None, "similarity": 70.0,
            "similarity_breakdown": {"text": 0.7, "dinov2": 0, "clip": 0, "color": 0},
            "trademark": {"nice_classes": [35], "current_status": "Yayında",
                          "application_date": None},
            "design": None,
        },
    ]

    monkeypatch.setattr(
        "services.registry_search_service.search_designs",
        lambda *a, **kw: {"results": fake_design_results},
    )
    monkeypatch.setattr(
        "services.registry_search_service._retrieve_trademark_candidates",
        lambda *a, **kw: fake_tm_results,
    )

    fake_conn = MagicMock()
    res = search_unified(fake_conn, query="Lamba", limit=5)
    assert res["total"] == 3
    sims = [r["similarity"] for r in res["results"]]
    assert sims == sorted(sims, reverse=True), f"results not sorted desc: {sims}"
    assert sims[0] == 80.0  # design d2 wins
    assert res["by_registry"] == {"trademark": 1, "design": 2}


def test_search_unified_respects_limit(monkeypatch):
    fake_design_results = [
        {"id": f"d{i}", "application_no": f"D-{i}", "product_name_tr": "x",
         "section": "tr_native", "similarity": 100 - i,
         "similarity_breakdown": {}, "locarno_classes": [], "designers": [],
         "holder": None, "image_url": None}
        for i in range(20)
    ]
    monkeypatch.setattr(
        "services.registry_search_service.search_designs",
        lambda *a, **kw: {"results": fake_design_results},
    )
    monkeypatch.setattr(
        "services.registry_search_service._retrieve_trademark_candidates",
        lambda *a, **kw: [],
    )

    fake_conn = MagicMock()
    res = search_unified(fake_conn, query="Lamba", limit=5)
    assert res["total"] == 5
    assert len(res["results"]) == 5


def test_search_unified_public_caps_at_ten(monkeypatch):
    """``public=True`` caps the limit at 10 even when 50 is requested."""
    fake_design_results = [
        {"id": f"d{i}", "application_no": f"D-{i}", "product_name_tr": "x",
         "section": "tr_native", "similarity": 100 - i,
         "similarity_breakdown": {}, "locarno_classes": [], "designers": [],
         "holder": None, "image_url": None}
        for i in range(50)
    ]
    monkeypatch.setattr(
        "services.registry_search_service.search_designs",
        lambda *a, **kw: {"results": fake_design_results},
    )
    monkeypatch.setattr(
        "services.registry_search_service._retrieve_trademark_candidates",
        lambda *a, **kw: [],
    )

    fake_conn = MagicMock()
    res = search_unified(fake_conn, query="Lamba", limit=50, public=True)
    assert res["total"] == 10


def test_search_unified_filters_to_chosen_registry(monkeypatch):
    """When ``registries=['design']``, the trademark branch isn't called."""
    tm_call_count = {"n": 0}

    def fake_tm_retrieve(*a, **kw):
        tm_call_count["n"] += 1
        return []

    monkeypatch.setattr(
        "services.registry_search_service.search_designs",
        lambda *a, **kw: {"results": []},
    )
    monkeypatch.setattr(
        "services.registry_search_service._retrieve_trademark_candidates",
        fake_tm_retrieve,
    )

    fake_conn = MagicMock()
    res = search_unified(fake_conn, query="Lamba", registries=["design"])
    assert tm_call_count["n"] == 0
    assert res["filters"]["registries"] == ["design"]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def test_register_registry_search_routes_attaches_three_paths():
    from fastapi import FastAPI
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from app_registry_search_routes import register_registry_search_routes

    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    register_registry_search_routes(app, limiter)

    paths = {(getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
             for r in app.routes}
    expected = {
        ("/api/v1/registry-search/public", ("GET",)),
        ("/api/v1/registry-search/public", ("POST",)),
        ("/api/v1/registry-search", ("POST",)),
    }
    for path, methods in expected:
        matching = [
            (p, m) for (p, m) in paths
            if p == path and set(methods).issubset(set(m))
        ]
        assert matching, f"expected {path} {methods} not registered; got: {paths}"


# ---------------------------------------------------------------------------
# Quota wiring on /quick
# ---------------------------------------------------------------------------

class _NullDB:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_quick_returns_429_when_over_limit(monkeypatch):
    from app_registry_search_routes import registry_search_quick

    over_limit = {"daily_limit": 5, "used_today": 5, "remaining": 0,
                  "current_plan": "free", "message": "limit"}

    def fake_check(db, user_id):
        return False, "daily_limit_exceeded", over_limit

    monkeypatch.setattr("utils.subscription.check_quick_search_eligibility", fake_check)
    monkeypatch.setattr("database.crud.Database", lambda *a, **kw: _NullDB())

    with pytest.raises(Exception) as excinfo:
        asyncio.run(registry_search_quick(
            query="Lamba", image=None, nice=None, locarno=None,
            registries=None, limit=10, user_id="u-1", organization_id="o-1",
        ))
    assert getattr(excinfo.value, "status_code", None) == 429
    assert getattr(excinfo.value, "detail", None) == over_limit


def test_quick_increments_after_successful_search(monkeypatch):
    from app_registry_search_routes import registry_search_quick

    inc_calls = []

    monkeypatch.setattr(
        "utils.subscription.check_quick_search_eligibility",
        lambda db, uid: (True, "ok", {"daily_limit": 50, "used_today": 1,
                                      "remaining": 49, "current_plan": "starter"}),
    )

    def fake_increment(db, uid, oid=None):
        inc_calls.append((uid, oid))
        return 2

    monkeypatch.setattr("utils.subscription.increment_quick_search_usage", fake_increment)
    monkeypatch.setattr("database.crud.Database", lambda *a, **kw: _NullDB())

    async def fake_do(**_kw):
        return {"results": [], "total": 0, "by_registry": {"trademark": 0, "design": 0},
                "duration_ms": 1, "filters": {}}

    monkeypatch.setattr("app_registry_search_routes._do_unified_search", fake_do)

    result = asyncio.run(registry_search_quick(
        query="Lamba", image=None, nice=None, locarno=None,
        registries=None, limit=10, user_id="u-2", organization_id="o-2",
    ))
    assert result["total"] == 0
    assert inc_calls == [("u-2", "o-2")]
