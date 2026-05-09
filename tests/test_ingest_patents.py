"""Unit tests for ``pipeline.ingest_patents``.

Pure helpers tested without a DB. Holder resolution + upserts run
against a mock cursor recording executed SQL. Live integration is
verified separately at the bottom (gated on a scratch DB).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from pipeline.ingest_patents import (
    figure_source,
    parse_date_safe,
    resolve_holder_id,
    to_halfvec_literal,
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
