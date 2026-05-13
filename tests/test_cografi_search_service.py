"""Unit tests for ``services.cografi_search_service`` pure helpers.

Avoids any DB-touching code so the suite runs without Postgres. The
end-to-end search path (SQL builder + retrieval + scoring) is exercised
by the integration smoke at the end of the file, gated on the local
DB being reachable.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.cografi_search_service import (
    CANDIDATE_POOL,
    DEFAULT_EXCLUDED_SECTIONS,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    PUBLIC_RESULT_CAP,
    TRIGRAM_THRESHOLD,
    WEIGHTS_HYBRID,
    WEIGHTS_IMAGE_ONLY,
    WEIGHTS_TEXT_ONLY,
    cap_limit,
    cografi_image_url,
    combine_scores,
    normalize_record_types,
    normalize_section_keys,
    parse_id_query,
    to_halfvec_literal,
)


# ---------------------------------------------------------------------------
# Constants — pinned so accidental edits break loudly
# ---------------------------------------------------------------------------

def test_score_weights_sum_to_one_per_mode():
    assert sum(WEIGHTS_TEXT_ONLY.values()) == pytest.approx(1.0)
    assert sum(WEIGHTS_HYBRID.values()) == pytest.approx(1.0)
    assert sum(WEIGHTS_IMAGE_ONLY.values()) == pytest.approx(1.0)


def test_default_excluded_sections_block_admin_records():
    """Default search must exclude administrative section_keys so name
    searches don't surface corrections / gazette-only announcements."""
    assert "corrections" in DEFAULT_EXCLUDED_SECTIONS
    assert "gazette_only_announcements" in DEFAULT_EXCLUDED_SECTIONS


def test_limit_caps_match_documented_values():
    assert DEFAULT_LIMIT == 20
    assert MAX_LIMIT == 100
    assert PUBLIC_RESULT_CAP == 10
    assert 0 < TRIGRAM_THRESHOLD < 1
    assert CANDIDATE_POOL >= MAX_LIMIT


# ---------------------------------------------------------------------------
# parse_id_query
# ---------------------------------------------------------------------------

def test_parse_id_query_recognises_application_no():
    """Cografi application numbers look like C{YYYY}/{NNNNNN}."""
    assert parse_id_query("C2022/000469") == {"application_no": "C2022/000469"}
    assert parse_id_query("c2025/000485") == {"application_no": "C2025/000485"}
    assert parse_id_query("  C2024/000120  ") == {"application_no": "C2024/000120"}


def test_parse_id_query_handles_spaces_around_slash():
    """Some PDFs render the appno with spaces (we saw it in B1.5
    extraction). The exact-ID shortcut should still recognise it so
    operators can copy-paste from the bulletin."""
    out = parse_id_query("C2023 / 000109")
    assert out == {"application_no": "C2023/000109"}


def test_parse_id_query_recognises_registration_no():
    """A bare integer is treated as a registration_no lookup."""
    assert parse_id_query("1838") == {"registration_no": 1838}
    assert parse_id_query(" 268 ") == {"registration_no": 268}


def test_parse_id_query_returns_none_for_text_queries():
    assert parse_id_query("Karapınar Halısı") is None
    assert parse_id_query("kebap") is None
    assert parse_id_query("Konya bölgesinden") is None
    assert parse_id_query("") is None
    assert parse_id_query(None) is None


def test_parse_id_query_does_not_match_arbitrary_codes():
    """Patent / trademark IDs (different shapes) must NOT collide with
    cografi's C{YYYY}/{N} pattern."""
    assert parse_id_query("2025/15048") is None      # patent shape
    assert parse_id_query("TR 2017 15048 U3") is None  # patent publication
    assert parse_id_query("C") is None
    assert parse_id_query("XYZ") is None


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------

def test_to_halfvec_literal_serialises_floats_for_pgvector_cast():
    out = to_halfvec_literal([0.1, -0.25, 1.0])
    assert out is not None and out.startswith("[") and out.endswith("]")
    parts = out[1:-1].split(",")
    assert len(parts) == 3


def test_to_halfvec_literal_returns_none_for_empty():
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


# ---------------------------------------------------------------------------
# combine_scores
# ---------------------------------------------------------------------------

def test_combine_scores_text_only_weights_text_and_embedding():
    score = combine_scores(text=1.0, embedding=1.0, has_image=False, has_text_query=True)
    expected = WEIGHTS_TEXT_ONLY["text"] + WEIGHTS_TEXT_ONLY["embedding"]
    assert score == pytest.approx(min(1.0, expected))


def test_combine_scores_hybrid_uses_three_signals():
    score = combine_scores(text=1.0, embedding=1.0, figure=1.0, has_image=True, has_text_query=True)
    assert score == pytest.approx(min(1.0, sum(WEIGHTS_HYBRID.values())))


def test_combine_scores_image_only_ignores_text_signals():
    score = combine_scores(text=1.0, embedding=1.0, figure=0.7, has_image=True, has_text_query=False)
    assert score == pytest.approx(WEIGHTS_IMAGE_ONLY["figure"] * 0.7)


def test_combine_scores_clamps_to_one():
    """Even if all signals max out and weights sum > 1.0 numerically,
    cap at 1.0 to keep similarity comparable across modes."""
    score = combine_scores(text=2.0, embedding=2.0, figure=2.0, has_image=True, has_text_query=True)
    assert score == 1.0


def test_combine_scores_floors_negatives_to_zero():
    """Cosine distance can be slightly negative due to halfvec rounding;
    don't subtract from the score in that case."""
    score = combine_scores(text=-0.1, embedding=0.5, has_image=False, has_text_query=True)
    expected = WEIGHTS_TEXT_ONLY["embedding"] * 0.5
    assert score == pytest.approx(expected)


# ---------------------------------------------------------------------------
# cap_limit
# ---------------------------------------------------------------------------

def test_cap_limit_uses_default_when_unparseable():
    assert cap_limit(None) == DEFAULT_LIMIT
    assert cap_limit("twenty") == DEFAULT_LIMIT


def test_cap_limit_floors_at_one():
    assert cap_limit(0) == 1
    assert cap_limit(-5) == 1


def test_cap_limit_caps_at_max_for_authenticated():
    assert cap_limit(500, public=False) == MAX_LIMIT
    assert cap_limit(50, public=False) == 50


def test_cap_limit_caps_at_public_cap_for_public():
    assert cap_limit(500, public=True) == PUBLIC_RESULT_CAP
    assert cap_limit(5, public=True) == 5


# ---------------------------------------------------------------------------
# normalize_section_keys / normalize_record_types
# ---------------------------------------------------------------------------

def test_normalize_section_keys_lowercases_and_dedupes():
    assert normalize_section_keys(["Examined", "EXAMINED", "registered"]) == [
        "examined", "registered",
    ]


def test_normalize_section_keys_returns_none_for_empty():
    assert normalize_section_keys(None) is None
    assert normalize_section_keys([]) is None
    assert normalize_section_keys(["", " "]) is None


def test_normalize_record_types_uppercases_and_dedupes():
    assert normalize_record_types(["gi", "GI", "tpn"]) == ["GI", "TPN"]


def test_normalize_record_types_returns_none_for_empty():
    assert normalize_record_types(None) is None
    assert normalize_record_types([]) is None


# ---------------------------------------------------------------------------
# cografi_image_url
# ---------------------------------------------------------------------------

def test_cografi_image_url_builds_relative_url():
    url = cografi_image_url("C2022_000469/1.jpeg", "CI_220_2026-05-04")
    assert url == "/api/v1/cografi-image/CI_220_2026-05-04/C2022_000469/1.jpeg"


def test_cografi_image_url_strips_leading_slash():
    url = cografi_image_url("/C2022_000469/1.jpeg", "CI_220_2026-05-04")
    assert url == "/api/v1/cografi-image/CI_220_2026-05-04/C2022_000469/1.jpeg"


def test_cografi_image_url_returns_none_when_path_or_folder_missing():
    assert cografi_image_url(None, "CI_220_2026-05-04") is None
    assert cografi_image_url("C2022_000469/1.jpeg", None) is None
    assert cografi_image_url("", "CI_220_2026-05-04") is None


# ---------------------------------------------------------------------------
# Integration smoke (skipped if local DB unreachable)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_conn():
    """Open a Postgres connection from the repo's .env. Skip the
    integration tests when the connection can't be established (the
    test runner is fine without a DB; the smoke is for local/CI runs
    where the cografi schema is migrated and ingested)."""
    import os
    try:
        from dotenv import load_dotenv
        import psycopg2
    except Exception as exc:
        pytest.skip(f"DB integration deps missing: {exc}")
    # tests/conftest.py sets DB_PASSWORD to a test stub before fixtures
    # run; override=True makes the real .env take precedence so the
    # integration smoke can talk to the developer's local DB.
    load_dotenv(override=True)
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "trademark_db"),
            user=os.getenv("DB_USER", "turk_patent"),
            password=os.getenv("DB_PASSWORD", ""),
            connect_timeout=5,
        )
    except Exception as exc:
        pytest.skip(f"local DB unreachable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('cografi_records')")
            if cur.fetchone()[0] is None:
                pytest.skip("cografi_records table missing — run migration first")
            cur.execute("SELECT count(*) FROM cografi_records")
            if cur.fetchone()[0] == 0:
                pytest.skip("cografi_records empty — run ingest first")
        yield conn
    finally:
        conn.close()


def test_integration_search_finds_karapinar_halisi(db_conn):
    """End-to-end smoke: a literal name query hits the trigram index
    and returns the named record at the top of the ranking.

    Note: text-only mode (no query embedding passed in) caps similarity
    at WEIGHTS_TEXT_ONLY['text'] = 0.4 even on a perfect-match trigram,
    so we assert top-1 placement rather than an absolute threshold."""
    from services.cografi_search_service import search_cografi
    result = search_cografi(db_conn, query="Karapınar Halısı", limit=10)
    names = [r["name"] for r in result["results"]]
    assert "Karapınar Halısı" in names
    top = result["results"][0]
    assert top["name"] == "Karapınar Halısı"
    # text trigram only -> at most 0.4 weight * 1.0 sim = 0.4 (40%)
    assert top["similarity"] >= 30  # text weight floor


def test_integration_id_lookup_short_circuits_to_application_no(db_conn):
    from services.cografi_search_service import search_cografi
    result = search_cografi(db_conn, query="C2022/000469", limit=10)
    # Karapınar Halısı's application_no — must surface as a result.
    names = [r["name"] for r in result["results"]]
    assert any(n == "Karapınar Halısı" for n in names)
    assert result["filters"]["id_lookup"] is True


def test_integration_filter_only_returns_recent_browse(db_conn):
    """Filter-only mode (no query) returns the newest matching rows."""
    from services.cografi_search_service import search_cografi
    result = search_cografi(
        db_conn, gi_type="Mahreç işareti", limit=5,
    )
    assert len(result["results"]) > 0
    # Recency ordering — the first row's bulletin_date is >= the last row's.
    dates = [r["bulletin_date"] for r in result["results"] if r["bulletin_date"]]
    assert dates == sorted(dates, reverse=True)
