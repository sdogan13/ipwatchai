"""Unit tests for the patent search service helpers.

Pure helpers and SQL-builder behavior are tested here; full DB queries
are covered by manual smoke runs against Postgres (verification report).
Mirrors ``tests/test_design_search.py`` in shape.
"""
from __future__ import annotations

import pytest

from services.patent_search_service import (
    DEFAULT_LIMIT,
    EXCLUDED_RECORD_TYPES,
    HYDRATE_COLS,
    MAX_LIMIT,
    PUBLIC_RESULT_CAP,
    WEIGHTS,
    _result_row,
    cap_limit,
    combine_scores,
    normalize_ipc_filter,
    parse_id_query,
    patent_image_url,
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

def test_combine_scores_text_only():
    score = combine_scores(text=1.0, embedding=0.0)
    assert abs(score - WEIGHTS["text"]) < 1e-9


def test_combine_scores_embedding_only():
    score = combine_scores(text=0.0, embedding=1.0)
    assert abs(score - WEIGHTS["embedding"]) < 1e-9


def test_combine_scores_caps_at_one():
    score = combine_scores(text=1.0, embedding=1.0)
    assert score == 1.0


def test_combine_scores_negative_signals_treated_as_zero():
    score = combine_scores(text=-0.5, embedding=1.0)
    assert abs(score - WEIGHTS["embedding"]) < 1e-9


def test_combine_scores_weights_sum_balances():
    # Both perfect → close to 1.0; both half → close to 0.5
    assert combine_scores(text=0.5, embedding=0.5) == pytest.approx(0.5, abs=1e-9)


# ---------------------------------------------------------------------------
# normalize_ipc_filter
# ---------------------------------------------------------------------------

def test_normalize_ipc_filter_basic():
    assert normalize_ipc_filter(["a61m", " B65D "]) == ["A61M", "B65D"]


def test_normalize_ipc_filter_dedupes_and_strips_blanks():
    assert normalize_ipc_filter([" A61M ", "", "a61m", None]) == ["A61M"]


def test_normalize_ipc_filter_empty_returns_none():
    assert normalize_ipc_filter(None) is None
    assert normalize_ipc_filter([]) is None
    assert normalize_ipc_filter(["", "  "]) is None


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
# parse_id_query — exact-ID shortcut detection
# ---------------------------------------------------------------------------

def test_parse_id_query_application_no_with_slash():
    out = parse_id_query("2017/15048")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_application_no_with_space():
    out = parse_id_query("2017 15048")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_publication_with_kind():
    out = parse_id_query("2017/15048 U3")
    assert out == {"application_no": "2017/15048", "kind_code": "U3"}


def test_parse_id_query_with_country_prefix():
    out = parse_id_query("TR 2017 15048 B")
    assert out == {"application_no": "2017/15048", "kind_code": "B"}


def test_parse_id_query_kind_uppercased():
    out = parse_id_query("2017/15048 a1")
    assert out == {"application_no": "2017/15048", "kind_code": "A1"}


def test_parse_id_query_handles_whitespace():
    out = parse_id_query("  2017/15048  ")
    assert out == {"application_no": "2017/15048"}


def test_parse_id_query_rejects_plain_text():
    assert parse_id_query("solar panel") is None
    assert parse_id_query("widget device") is None


def test_parse_id_query_rejects_empty():
    assert parse_id_query("") is None
    assert parse_id_query(None) is None


def test_parse_id_query_rejects_partial_match():
    # Free-text containing numbers shouldn't accidentally lookup
    assert parse_id_query("solar 2017 widget") is None


# ---------------------------------------------------------------------------
# Sanity: excluded record types
# ---------------------------------------------------------------------------

def test_excluded_record_types_includes_unknown_and_legacy():
    # UNKNOWN catches kind codes the classifier doesn't yet map (A3/U3/T7
    # gap memory) — they must be excluded from default search results.
    # LEGACY rows lack INID-coded fields so titles/abstracts are unreliable.
    assert "UNKNOWN" in EXCLUDED_RECORD_TYPES
    assert "LEGACY" in EXCLUDED_RECORD_TYPES


# ---------------------------------------------------------------------------
# patent_image_url + result-row image wiring
# ---------------------------------------------------------------------------

def test_patent_image_url_builds_route_path():
    url = patent_image_url("figures/2017_15048.tif", "PT_2017_11_2017-11-21")
    assert url == "/api/v1/patent-image/PT_2017_11_2017-11-21/figures/2017_15048.tif"


def test_patent_image_url_handles_leading_slash():
    url = patent_image_url("/figures/foo.png", "PT_2025_8_2025-08-21")
    assert url == "/api/v1/patent-image/PT_2025_8_2025-08-21/figures/foo.png"


def test_patent_image_url_returns_none_when_either_part_missing():
    assert patent_image_url(None, "PT_2025_8") is None
    assert patent_image_url("figures/foo.tif", None) is None
    assert patent_image_url("", "PT_2025_8") is None


def test_hydrate_cols_includes_first_image_path_and_bulletin_folder():
    # Result-row wiring depends on these columns; if either is dropped
    # from HYDRATE_COLS, every result card silently loses its image.
    assert "first_image_path" in HYDRATE_COLS
    assert "bulletin_folder" in HYDRATE_COLS
    assert "FROM patent_figures" in HYDRATE_COLS


def test_result_row_emits_image_url_when_figure_present():
    record = {
        "patent_id": "00000000-0000-0000-0000-000000000001",
        "registry_type": "patent",
        "application_no": "2017/15048",
        "publication_no": None,
        "kind_code": "B",
        "record_type": "GRANT",
        "patent_type": "1",
        "title": "x", "abstract": "y",
        "ipc_classes": ["A61M"],
        "bulletin_no": "2017/11", "bulletin_date": None,
        "bulletin_folder": "PT_2017_11_2017-11-21",
        "application_date": None, "publication_date": None, "grant_date": None,
        "first_holder_name": None, "first_holder_country": None,
        "first_holder_tpe_id": None, "inventors": None,
        "first_attorney_name": None, "first_attorney_firm": None,
        "first_image_path": "figures/2017_15048.tif",
    }
    out = _result_row(record, similarity=0.5, breakdown={})
    assert out["image_url"] == "/api/v1/patent-image/PT_2017_11_2017-11-21/figures/2017_15048.tif"


def test_result_row_image_url_none_when_no_figure():
    record = {
        "patent_id": "00000000-0000-0000-0000-000000000002",
        "registry_type": "patent",
        "application_no": "2020/00001",
        "publication_no": None, "kind_code": "A1", "record_type": "APPLICATION",
        "patent_type": "1", "title": "x", "abstract": "y",
        "ipc_classes": [], "bulletin_no": None, "bulletin_date": None,
        "bulletin_folder": "PT_2020_1_2020-01-01",
        "application_date": None, "publication_date": None, "grant_date": None,
        "first_holder_name": None, "first_holder_country": None,
        "first_holder_tpe_id": None, "inventors": None,
        "first_attorney_name": None, "first_attorney_firm": None,
        "first_image_path": None,
    }
    out = _result_row(record, similarity=0.5, breakdown={})
    assert out["image_url"] is None


# ---------------------------------------------------------------------------
# Image route resolver — directory traversal protection + TIFF conversion
# ---------------------------------------------------------------------------

def test_resolve_patent_image_rejects_traversal():
    from app_patent_search_routes import _resolve_patent_image
    assert _resolve_patent_image("../../../etc/passwd") is None
    assert _resolve_patent_image("..\\windows\\win.ini") is None


def test_resolve_patent_image_returns_none_for_missing_file():
    from app_patent_search_routes import _resolve_patent_image
    assert _resolve_patent_image("PT_doesnotexist/figures/foo.tif") is None


def test_resolve_patent_image_returns_path_when_file_exists(tmp_path, monkeypatch):
    from app_patent_search_routes import _resolve_patent_image
    import app_patent_search_routes as routes
    fake_root = tmp_path / "Patent__Faydali_Model"
    figs = fake_root / "PT_test/figures"
    figs.mkdir(parents=True)
    target = figs / "x.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic
    monkeypatch.setattr(routes, "PATENT_BULLETINS_ROOT", fake_root)
    resolved = _resolve_patent_image("PT_test/figures/x.png")
    assert resolved is not None
    from pathlib import Path
    assert Path(resolved).samefile(target)


def test_tiff_to_jpeg_bytes_produces_valid_jpeg(tmp_path):
    """Crucial path: CD-era figures ship as TIFF; the image route must
    convert them on the fly so the browser can render the card.

    ``conftest.py`` mocks ``PIL`` globally for the ML-light test suite;
    we restore the real package and re-import the route module so
    ``Image.open`` resolves to actual Pillow rather than a MagicMock.
    """
    import sys, importlib
    pil_keys = [k for k in list(sys.modules) if k == "PIL" or k.startswith("PIL.")]
    for k in pil_keys:
        del sys.modules[k]
    sys.modules.pop("app_patent_search_routes", None)
    try:
        import PIL.Image as PILImage
        routes = importlib.import_module("app_patent_search_routes")
        tif_path = tmp_path / "fig.tif"
        PILImage.new("RGB", (10, 10), color=(200, 100, 50)).save(tif_path, format="TIFF")
        out = routes._tiff_to_jpeg_bytes(str(tif_path))
        assert out[:3] == b"\xff\xd8\xff"  # JPEG SOI
        import io as _io
        decoded = PILImage.open(_io.BytesIO(out))
        decoded.verify()
    finally:
        # Re-mock for downstream tests so the harness contract is restored.
        from unittest.mock import MagicMock
        sys.modules["PIL"] = MagicMock()
        sys.modules["PIL.Image"] = MagicMock()
        sys.modules.pop("app_patent_search_routes", None)
