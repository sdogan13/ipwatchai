"""Unit tests for ``pipeline.ingest_patents``.

Pure helpers tested without a DB. Holder resolution + upserts run
against a mock cursor recording executed SQL. Live integration is
verified separately at the bottom (gated on a scratch DB).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from pipeline.ingest_patents import (
    PATENT_UPSERT_COLS,
    _patent_row,
    figure_source,
    parse_date_safe,
    resolve_holder_id,
    to_halfvec_literal,
    upsert_patent,
)


# ---------------------------------------------------------------------------
# to_halfvec_literal
# ---------------------------------------------------------------------------


def test_to_halfvec_literal_formats_floats() -> None:
    assert to_halfvec_literal([0.1, 0.2, -0.3]) == "[0.100000,0.200000,-0.300000]"


def test_to_halfvec_literal_returns_none_on_empty() -> None:
    assert to_halfvec_literal(None) is None
    assert to_halfvec_literal([]) is None


def test_to_halfvec_literal_handles_iterables() -> None:
    """Tolerates generators / arrays — not just lists."""
    assert to_halfvec_literal((1.0, 2.0)) == "[1.000000,2.000000]"


# ---------------------------------------------------------------------------
# parse_date_safe
# ---------------------------------------------------------------------------


def test_parse_date_safe_iso() -> None:
    assert parse_date_safe("2025-08-21") == date(2025, 8, 21)


def test_parse_date_safe_handles_missing() -> None:
    assert parse_date_safe(None) is None
    assert parse_date_safe("") is None
    assert parse_date_safe("not-a-date") is None
    assert parse_date_safe("21/08/2025") is None    # DD/MM/YYYY not accepted


# ---------------------------------------------------------------------------
# figure_source
# ---------------------------------------------------------------------------


def test_figure_source_cd_for_tif() -> None:
    """CD TIFFs land at figures/{year}_{appno}.tif post the unified
    folder refactor. .tif extension is the discriminator."""
    assert figure_source("figures/2017_15048.tif") == "CD"
    assert figure_source("figures/2023_018085.tif") == "CD"
    # Case-insensitive
    assert figure_source("figures/X.TIF") == "CD"


def test_figure_source_pdf_for_png_and_unknown() -> None:
    assert figure_source("figures/2017_15048_p120_2.png") == "PDF"
    assert figure_source("figures/X.jpg") == "PDF"
    # Unknown / None defaults to PDF (recoverable on re-ingest if wrong)
    assert figure_source(None) == "PDF"
    assert figure_source("") == "PDF"


# ---------------------------------------------------------------------------
# Holder resolution (mock cursor)
# ---------------------------------------------------------------------------


class _MockCursor:
    """Records executed SQL + returns canned rows by FIFO."""

    def __init__(self, rows: Optional[List[Any]] = None) -> None:
        self.rows = list(rows or [])
        self.executed: List[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if not self.rows:
            return None
        v = self.rows.pop(0)
        # Sentinel: None in the rows list means "no row" (psycopg2's
        # fetchone() returns None when SELECT matches nothing).
        if v is None:
            return None
        return (v,) if not isinstance(v, tuple) else v


def test_resolve_holder_id_returns_existing_uuid() -> None:
    """Existing name match → return the UUID, no INSERT."""
    cur = _MockCursor(rows=["existing-uuid"])
    out = resolve_holder_id(cur, {"name": "ACME"})
    assert out == "existing-uuid"
    assert len(cur.executed) == 1
    sql = cur.executed[0][0]
    assert "SELECT id FROM holders" in sql


def test_resolve_holder_id_inserts_new_when_missing() -> None:
    """No existing row → insert + return the new UUID."""
    cur = _MockCursor(rows=[None, "new-uuid"])
    out = resolve_holder_id(cur, {
        "name": "FOREIGN HOLDER",
        "address": "X St 1",
        "city": "Berlin",
        "country": "DE",
        "postal_code": "10115",
    })
    assert out == "new-uuid"
    assert len(cur.executed) == 2
    select_sql, _ = cur.executed[0]
    assert "SELECT" in select_sql
    insert_sql, params = cur.executed[1]
    assert "INSERT INTO holders" in insert_sql
    assert params == ("FOREIGN HOLDER", "X St 1", "Berlin", "DE", "10115")


def test_resolve_holder_id_returns_none_on_empty() -> None:
    """Defensive: blank/None holder shouldn't insert garbage rows."""
    cur = _MockCursor()
    assert resolve_holder_id(cur, None) is None
    assert resolve_holder_id(cur, {}) is None
    assert resolve_holder_id(cur, {"name": ""}) is None
    assert resolve_holder_id(cur, {"name": "   "}) is None
    assert cur.executed == []


def test_resolve_holder_id_matches_case_insensitively() -> None:
    cur = _MockCursor(rows=["existing-uuid"])
    resolve_holder_id(cur, {"name": "acme"})
    sql, params = cur.executed[0]
    assert "LOWER(name)" in sql
    assert params == ("acme",)


# ---------------------------------------------------------------------------
# _patent_row (record + doc → patents-row dict)
# ---------------------------------------------------------------------------


def test_patent_row_full_shape() -> None:
    """Exercises every column at least once."""
    record = {
        "application_no": "2017/15048",
        "publication_no": "TR 2017 15048 U3",
        "kind_code": "U3",
        "record_type": "PUBLISHED_UM_APP",
        "application_date": "2017-10-05",
        "publication_date": "2025-12-22",
        "grant_date": None,
        "title": "EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ",
        "abstract": "Test abstract.",
        "ipc_classes": ["A61M 5/31", "A61J 1/14"],
        "patent_type": "2",
        "title_abstract_embedding": [0.1] * 1024,
        "primary_figure_embedding": [0.2] * 1024,
        "source_format": "BOTH",
        "page_range": [120, 121],
    }
    doc = {
        "bulletin_no": "2025/12",
        "bulletin_date": "2025-12-22",
        "source_archive": "2025_12_CD.rar",
        "source_pdf": "2025_12.pdf",
    }

    row = _patent_row(record, doc, bulletin_folder="PT_2025_12_2025-12-22")

    assert row["registry_type"] == "patent"
    assert row["application_no"] == "2017/15048"
    assert row["publication_no"] == "TR 2017 15048 U3"
    assert row["kind_code"] == "U3"
    assert row["record_type"] == "PUBLISHED_UM_APP"
    assert row["application_date"] == date(2017, 10, 5)
    assert row["publication_date"] == date(2025, 12, 22)
    assert row["grant_date"] is None
    assert row["title"].startswith("EMNİYET")
    assert row["abstract"] == "Test abstract."
    assert row["ipc_classes"] == ["A61M 5/31", "A61J 1/14"]
    assert row["patent_type"] == "2"
    # halfvec literals are formatted strings, not lists
    assert row["title_abstract_embedding"].startswith("[0.100000,")
    assert row["primary_figure_embedding"].startswith("[0.200000,")
    assert row["source_format"] == "BOTH"
    assert row["source_archive"] == "2025_12_CD.rar"
    assert row["source_pdf"] == "2025_12.pdf"
    assert row["bulletin_folder"] == "PT_2025_12_2025-12-22"
    assert row["page_range_start"] == 120
    assert row["page_range_end"] == 121


def test_patent_row_unknown_record_type_collapses_to_unknown() -> None:
    """Defensive: any record_type the schema enum doesn't accept
    becomes 'UNKNOWN' so INSERT doesn't fail downstream."""
    row = _patent_row(
        {"application_no": "X", "record_type": "MYSTERY_KIND"},
        {"bulletin_no": "2025/8", "bulletin_date": "2025-08-21"},
        bulletin_folder="PT_x",
    )
    assert row["record_type"] == "UNKNOWN"


def test_patent_row_no_embeddings_set_to_none() -> None:
    """Records without embeddings (Stage 6 hasn't run yet) ingest
    cleanly with NULL halfvec values."""
    row = _patent_row(
        {"application_no": "X", "record_type": "GRANTED_PATENT"},
        {"bulletin_no": "2025/8", "bulletin_date": "2025-08-21"},
        bulletin_folder="PT_x",
    )
    assert row["title_abstract_embedding"] is None
    assert row["primary_figure_embedding"] is None


def test_patent_row_no_page_range() -> None:
    row = _patent_row(
        {"application_no": "X", "record_type": "UNKNOWN"},
        {"bulletin_no": "2025/8", "bulletin_date": "2025-08-21"},
        bulletin_folder="PT_x",
    )
    assert row["page_range_start"] is None
    assert row["page_range_end"] is None


# ---------------------------------------------------------------------------
# upsert_patent (mock cursor)
# ---------------------------------------------------------------------------


def _full_row() -> Dict[str, Any]:
    """Test fixture matching PATENT_UPSERT_COLS exactly."""
    return {
        "registry_type": "patent",
        "application_no": "2017/15048",
        "publication_no": "TR 2017 15048 U3",
        "kind_code": "U3",
        "record_type": "PUBLISHED_UM_APP",
        "application_date": date(2017, 10, 5),
        "publication_date": date(2025, 12, 22),
        "grant_date": None,
        "bulletin_no": "2025/12",
        "bulletin_date": date(2025, 12, 22),
        "title": "X",
        "abstract": "Y",
        "ipc_classes": ["A61M 5/31"],
        "patent_type": "2",
        "title_abstract_embedding": "[0.100000,0.200000]",
        "primary_figure_embedding": None,
        "source_format": "BOTH",
        "source_archive": "2025_12_CD.rar",
        "source_pdf": "2025_12.pdf",
        "bulletin_folder": "PT_2025_12_2025-12-22",
        "page_range_start": 120,
        "page_range_end": 121,
    }


def test_upsert_patent_inserts_when_publication_no_missing() -> None:
    """No existing row for this publication_no → INSERT new + return UUID."""
    cur = _MockCursor(rows=[None, "new-uuid"])
    out = upsert_patent(cur, _full_row())

    assert out == "new-uuid"
    select_sql, _ = cur.executed[0]
    insert_sql, params = cur.executed[1]
    assert "SELECT id FROM patents WHERE publication_no" in select_sql
    assert "INSERT INTO patents" in insert_sql
    # halfvec columns must be cast in the INSERT
    assert "title_abstract_embedding)s::halfvec" in insert_sql
    assert "primary_figure_embedding)s::halfvec" in insert_sql


def test_upsert_patent_updates_when_publication_no_exists() -> None:
    """Existing row found → UPDATE in place + return same UUID."""
    cur = _MockCursor(rows=["existing-uuid", "existing-uuid"])
    out = upsert_patent(cur, _full_row())

    assert out == "existing-uuid"
    select_sql, _ = cur.executed[0]
    update_sql, _ = cur.executed[1]
    assert "SELECT id FROM patents WHERE publication_no" in select_sql
    assert "UPDATE patents SET" in update_sql
    assert "updated_at = NOW()" in update_sql
    # halfvec casts in the UPDATE too
    assert "title_abstract_embedding = %(title_abstract_embedding)s::halfvec" in update_sql


def test_upsert_patent_falls_back_to_app_no_when_publication_no_blank() -> None:
    """Records with empty publication_no (142 such in bulletin 2019/11)
    dedupe on (application_no, kind_code, bulletin_no) so re-ingest
    doesn't duplicate them."""
    row = _full_row()
    row["publication_no"] = None

    cur = _MockCursor(rows=[None, "new-uuid"])
    upsert_patent(cur, row)

    select_sql, params = cur.executed[0]
    assert "WHERE application_no" in select_sql
    assert "kind_code" in select_sql
    assert params == (row["application_no"], row["kind_code"], row["bulletin_no"])


def test_patent_upsert_cols_match_schema_columns() -> None:
    """Defensive: every column listed in PATENT_UPSERT_COLS must exist
    in migrations/patents.sql so the INSERT doesn't fail at runtime."""
    sql = (
        Path(__file__).resolve().parent.parent / "migrations" / "patents.sql"
    ).read_text(encoding="utf-8")
    for col in PATENT_UPSERT_COLS:
        assert col in sql, f"PATENT_UPSERT_COLS contains '{col}' but it's not in patents.sql"
