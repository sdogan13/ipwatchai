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
    _normalise_bulletin_no,
    _patent_row,
    _resolve_patent_id_for_event,
    figure_source,
    find_bulletin_folders,
    ingest_bulletin,
    main,
    parse_argv,
    parse_date_safe,
    replace_attorneys,
    replace_events,
    replace_figures,
    replace_holders,
    replace_inventors,
    replace_priorities,
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


def test_scrub_nul_removes_nulls_from_strings() -> None:
    """PostgreSQL TEXT rejects NUL bytes. PyMuPDF occasionally surfaces
    a NUL when a glyph fails to map (saw it on bulletin 2025/2 in a
    record's abstract field). _scrub_nul walks the parsed payload and
    strips NULs so every downstream row builder sees clean strings."""
    from pipeline.ingest_patents import _scrub_nul
    payload = {
        "bulletin_no": "2025/2",
        "records": [
            {
                "application_no": "2025/000880",
                "abstract": "yangına dayanıklı\x00 sistemleri",
                "title": "fine title",
                "ipc_classes": ["A47\x00B"],
            },
        ],
    }
    cleaned = _scrub_nul(payload)
    assert cleaned["records"][0]["abstract"] == "yangına dayanıklı sistemleri"
    assert cleaned["records"][0]["title"] == "fine title"
    assert cleaned["records"][0]["ipc_classes"] == ["A47B"]
    # Non-string values pass through untouched.
    assert cleaned["bulletin_no"] == "2025/2"
    # Empty / non-NUL strings unchanged (no allocation).
    assert _scrub_nul("plain") == "plain"
    assert _scrub_nul(None) is None
    assert _scrub_nul(42) == 42


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


# ---------------------------------------------------------------------------
# Child-table upserts (replace-style)
# ---------------------------------------------------------------------------


def test_replace_holders_deletes_then_inserts() -> None:
    """Re-ingest must DELETE existing rows then INSERT fresh — no
    stale rows lingering."""
    # First fetchone: holder name lookup misses → INSERT new holder
    # Second fetchone: returns the new holder UUID
    # Third fetchone: second holder name lookup misses
    # Fourth fetchone: returns second holder UUID
    cur = _MockCursor(rows=[None, "h1-uuid", None, "h2-uuid"])

    inserted = replace_holders(cur, "p-uuid", [
        {"name": "ACME", "address": "X", "country": "TR"},
        {"name": "FOREIGN INC", "country": "US"},
    ])

    assert inserted == 2
    # First exec is the DELETE
    delete_sql, delete_params = cur.executed[0]
    assert "DELETE FROM patent_holders WHERE patent_id" in delete_sql
    assert delete_params == ("p-uuid",)
    # Then alternating SELECT (holder lookup) + INSERT (holders) + INSERT (patent_holders)
    assert any("INSERT INTO patent_holders" in s for s, _ in cur.executed)


def test_replace_holders_skips_blank_name() -> None:
    """Empty-name holder shouldn't insert a useless row."""
    cur = _MockCursor()
    inserted = replace_holders(cur, "p-uuid", [{"name": "", "country": "TR"}])
    assert inserted == 0
    # Only the DELETE executes
    assert len(cur.executed) == 1


def test_replace_holders_handles_empty_list() -> None:
    cur = _MockCursor()
    inserted = replace_holders(cur, "p-uuid", [])
    assert inserted == 0
    # Still does the DELETE so any prior rows are removed
    assert len(cur.executed) == 1
    assert "DELETE" in cur.executed[0][0]


def test_replace_inventors_inserts_with_seq() -> None:
    cur = _MockCursor()
    inserted = replace_inventors(cur, "p-uuid", [
        {"name": "JANE DOE"},
        {"name": "JOHN ROE", "address": "Y", "city": "İzmir"},
    ])
    assert inserted == 2
    inv_inserts = [s for s, _ in cur.executed if "patent_inventors" in s and "INSERT" in s]
    assert len(inv_inserts) == 2
    # Verify seq increments
    seqs = [p[1] for s, p in cur.executed if "INSERT INTO patent_inventors" in s]
    assert seqs == [1, 2]


def test_replace_attorneys_uses_agent_no_column() -> None:
    """JSON ships 'no'; schema column is 'agent_no' (avoids SQL
    keyword collision)."""
    cur = _MockCursor()
    inserted = replace_attorneys(cur, "p-uuid", [
        {"no": "361", "name": "ERDEM KAYA", "firm": "ERDEM KAYA PATENT VE DAN."},
    ])
    assert inserted == 1
    insert_sql = next(s for s, _ in cur.executed if "INSERT INTO patent_attorneys" in s)
    assert "agent_no" in insert_sql
    # Params: (patent_id, seq, agent_no, name, firm, address)
    insert_params = next(p for s, p in cur.executed if "INSERT INTO patent_attorneys" in s)
    assert insert_params == ("p-uuid", 1, "361", "ERDEM KAYA",
                              "ERDEM KAYA PATENT VE DAN.", None)


def test_replace_priorities_parses_iso_dates() -> None:
    cur = _MockCursor()
    inserted = replace_priorities(cur, "p-uuid", [
        {"priority_no": "2020/05105", "priority_date": "2020-03-31",
         "country": "TR"},
        {"priority_no": "X", "priority_date": "garbage", "country": "JP"},
    ])
    assert inserted == 2
    # First row's priority_date parsed; second's stays None (parse_date_safe
    # returns None on garbage)
    p1_params = cur.executed[1][1]
    assert p1_params == ("p-uuid", 1, "2020/05105", date(2020, 3, 31), "TR")
    p2_params = cur.executed[2][1]
    assert p2_params == ("p-uuid", 2, "X", None, "JP")


def test_replace_figures_includes_embeddings_with_halfvec_cast() -> None:
    """patent_figures.dinov2_vitl14 + clip_vitb32 use ::halfvec cast."""
    cur = _MockCursor()
    inserted = replace_figures(cur, "p-uuid", [
        {
            "image_path": "figures/2017_15048.tif",
            "embeddings": {
                "dinov2_vitl14": [0.1] * 1024,
                "clip_vitb32":   [0.2] * 512,
            },
        },
    ])
    assert inserted == 1
    insert_sql = next(s for s, _ in cur.executed if "INSERT INTO patent_figures" in s)
    assert "::halfvec, %s::halfvec" in insert_sql

    insert_params = next(p for s, p in cur.executed if "INSERT INTO patent_figures" in s)
    # (patent_id, seq, source, image_path, page, image_xref, bbox,
    #  width, height, dinov2_vitl14, clip_vitb32)
    assert insert_params[0] == "p-uuid"
    assert insert_params[1] == 1
    assert insert_params[2] == "CD"   # .tif → CD
    assert insert_params[3] == "figures/2017_15048.tif"
    assert insert_params[9].startswith("[0.100000,")
    assert insert_params[10].startswith("[0.200000,")


def test_replace_figures_handles_dedup_dropped_metadata_only() -> None:
    """A figure with only ``page`` (image_path=None after CD-first
    dedup) lands as a PDF row with halfvec NULLs; bookkeeping intact."""
    cur = _MockCursor()
    inserted = replace_figures(cur, "p-uuid", [
        {"page": 1847},
    ])
    assert inserted == 1
    params = next(p for s, p in cur.executed if "INSERT INTO patent_figures" in s)
    assert params[2] == "PDF"          # default source
    assert params[3] is None           # image_path
    assert params[4] == 1847           # page
    assert params[9] is None           # dinov2_vitl14
    assert params[10] is None          # clip_vitb32


def test_replace_figures_handles_xref_synonym() -> None:
    """JSON sometimes ships 'xref' (PyMuPDF native); schema column is
    image_xref. replace_figures accepts either."""
    cur = _MockCursor()
    replace_figures(cur, "p-uuid", [
        {"image_path": "figures/X_p1_2.png", "page": 1, "xref": 4204},
    ])
    params = next(p for s, p in cur.executed if "INSERT INTO patent_figures" in s)
    assert params[5] == 4204           # image_xref column gets the value


# ---------------------------------------------------------------------------
# ingest_bulletin (per-bulletin orchestrator)
# ---------------------------------------------------------------------------


class _MockConn:
    """psycopg2-shaped conn supporting ``with cursor()`` + commit/rollback."""

    def __init__(self, cursor: _MockCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        # Cursor as context manager (psycopg2 supports it).
        cur = self._cursor

        class _Ctx:
            def __enter__(self_inner):
                return cur
            def __exit__(self_inner, *exc):
                return False
        return _Ctx()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_ingest_bulletin_no_metadata_returns_status(tmp_path) -> None:
    parent = tmp_path / "PT_X"
    parent.mkdir()
    out = ingest_bulletin(parent)
    assert out == {"status": "no_metadata", "bulletin": "PT_X"}


def test_ingest_bulletin_empty_records_returns_status(tmp_path) -> None:
    import json
    parent = tmp_path / "PT_X"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [],
    }), encoding="utf-8")

    out = ingest_bulletin(parent)
    assert out["status"] == "empty"


def test_ingest_bulletin_processes_records_and_commits(tmp_path) -> None:
    """Happy path: one record → patent + child tables inserted, commit
    fires, no rollback."""
    import json
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "source_archive": "2025_07_CD.rar",
        "source_pdf": "2025_08.pdf",
        "records": [{
            "application_no": "2017/15048",
            "publication_no": "TR 2017 15048 U3",
            "kind_code": "U3",
            "record_type": "PUBLISHED_UM_APP",
            "title": "T", "abstract": "A",
            "ipc_classes": ["A61M 5/31"],
            "holders": [{"name": "ACME", "country": "TR"}],
            "inventors": [{"name": "JANE DOE"}],
            "attorneys": [{"no": "361", "name": "ERDEM KAYA"}],
            "priorities": [],
            "figures": [
                {"image_path": "figures/2017_15048.tif"},
            ],
        }],
    }), encoding="utf-8")

    # Mock cursor returns canned UUIDs in the order the orchestrator
    # asks for them. Each record needs:
    #   1 SELECT (publication_no lookup) + 1 INSERT (patents) RETURNING id
    #   1 DELETE patent_holders + (per holder: 1 SELECT + 1 INSERT
    #   holders + 1 INSERT patent_holders)
    #   1 DELETE patent_inventors + (per inventor: 1 INSERT)
    #   1 DELETE patent_attorneys + (per attorney: 1 INSERT)
    #   1 DELETE patent_priorities (no rows here)
    #   1 DELETE patent_figures + 1 INSERT
    cur = _MockCursor(rows=[
        None,             # publication_no SELECT — no existing
        "p-uuid",         # patents INSERT RETURNING
        None,             # holder name lookup — none
        "h-uuid",         # holders INSERT RETURNING
    ])
    conn = _MockConn(cur)

    out = ingest_bulletin(parent, conn=conn, force=True)

    assert out["status"] == "ok"
    assert out["records_processed"] == 1
    assert out["holders_inserted"] == 1
    assert out["inventors_inserted"] == 1
    assert out["attorneys_inserted"] == 1
    assert out["priorities_inserted"] == 0
    assert out["figures_inserted"] == 1
    assert conn.committed is True
    assert conn.rolled_back is False
    # Caller-supplied conn → not closed by ingest_bulletin
    assert conn.closed is False


def test_ingest_bulletin_skips_records_with_no_keys(tmp_path) -> None:
    """A record with neither publication_no nor application_no can't
    be reliably deduped — skip rather than create unindexable rows."""
    import json
    parent = tmp_path / "PT_X"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [
            {"title": "no keys"},
            {"application_no": "X", "publication_no": "TR X",
             "record_type": "GRANTED_PATENT", "ipc_classes": [],
             "holders": [], "inventors": [], "attorneys": [],
             "priorities": [], "figures": []},
        ],
    }), encoding="utf-8")

    cur = _MockCursor(rows=[None, "p-uuid"])
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn, force=True)
    assert out["records_processed"] == 1
    assert out["skipped"] == 1


def test_ingest_bulletin_rolls_back_on_error(tmp_path) -> None:
    """If any record raises, the whole bulletin transaction rolls back —
    don't leave a half-ingested bulletin."""
    import json
    parent = tmp_path / "PT_X"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [{
            "application_no": "X", "publication_no": "TR X",
            "record_type": "GRANTED_PATENT", "ipc_classes": [],
            "holders": [], "inventors": [], "attorneys": [],
            "priorities": [], "figures": [],
        }],
    }), encoding="utf-8")

    class _BoomCursor(_MockCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("simulated DB error")

    cur = _BoomCursor()
    conn = _MockConn(cur)
    with pytest.raises(RuntimeError, match="simulated DB error"):
        # ingest_bulletin only owns the connection (and rolls back) when
        # we don't pass one — pass None to test that path.
        # But we need to inject the broken cursor. Easier: monkey-patch
        # _connect in this test.
        import pipeline.ingest_patents as ip
        original_connect = ip._connect
        try:
            ip._connect = lambda: conn
            ingest_bulletin(parent)
        finally:
            ip._connect = original_connect

    assert conn.rolled_back is True
    assert conn.committed is False
    assert conn.closed is True


# ---------------------------------------------------------------------------
# CLI: find_bulletin_folders + parse_argv + main
# ---------------------------------------------------------------------------


def test_find_bulletin_folders_all_mode_skips_non_pt(tmp_path) -> None:
    (tmp_path / "PT_2025_8_2025-08-21").mkdir()
    (tmp_path / "PT_2024_6_2024-06-21").mkdir()
    (tmp_path / "scratch").mkdir()             # ignored
    (tmp_path / "stray.txt").write_text("x")   # ignored
    folders = find_bulletin_folders(tmp_path)
    assert {p.name for p in folders} == {
        "PT_2025_8_2025-08-21", "PT_2024_6_2024-06-21",
    }


def test_find_bulletin_folders_only_filter(tmp_path) -> None:
    folders = find_bulletin_folders(
        tmp_path, only=["PT_2025_8_2025-08-21"],
    )
    assert folders == [tmp_path / "PT_2025_8_2025-08-21"]


def test_parse_argv_all_mode() -> None:
    ns = parse_argv(["--all", "--bulletins-dir", "/data"])
    assert ns.all_mode is True
    assert ns.bulletins_dir == Path("/data")


def test_parse_argv_specific_bulletin_repeatable() -> None:
    ns = parse_argv([
        "--bulletin", "PT_2025_8_2025-08-21",
        "--bulletin", "PT_2024_6_2024-06-21",
    ])
    assert ns.bulletin == ["PT_2025_8_2025-08-21", "PT_2024_6_2024-06-21"]


def test_parse_argv_no_args_errors() -> None:
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_all_and_bulletin_mutex() -> None:
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletin", "PT_x"])


def test_main_returns_one_when_no_folders(tmp_path) -> None:
    """--all on an empty bulletins-dir is a hard fail."""
    rc = main(["--all", "--bulletins-dir", str(tmp_path)])
    assert rc == 1


def test_main_calls_ingest_bulletin_for_each(monkeypatch, tmp_path) -> None:
    """--all loops through every PT_ folder and tallies results."""
    (tmp_path / "PT_a").mkdir()
    (tmp_path / "PT_b").mkdir()

    called: List[Path] = []

    def _fake_ingest(folder, *, conn=None, force=False):
        called.append(folder)
        return {
            "status": "ok", "bulletin": folder.name,
            "records_processed": 1, "holders_inserted": 0,
            "inventors_inserted": 0, "attorneys_inserted": 0,
            "priorities_inserted": 0, "figures_inserted": 0,
            "events_inserted": 0, "skipped": 0,
        }

    import pipeline.ingest_patents as ip
    monkeypatch.setattr(ip, "ingest_bulletin", _fake_ingest)

    rc = main(["--all", "--bulletins-dir", str(tmp_path)])
    assert rc == 0
    assert {p.name for p in called} == {"PT_a", "PT_b"}


# ---------------------------------------------------------------------------
# Event ingest (Stage 7 patent_events)
# ---------------------------------------------------------------------------


def test_normalise_bulletin_no_canonicalises_both_formats() -> None:
    """Same canonicalisation rule as reconcile_patent — '2025/8' and
    '2025-08' both produce '2025/8' so patents.bulletin_no matches
    patent_events.bulletin_no across CD/PDF sources."""
    assert _normalise_bulletin_no("2025/8") == "2025/8"
    assert _normalise_bulletin_no("2025-08") == "2025/8"
    assert _normalise_bulletin_no("2025/12") == "2025/12"
    assert _normalise_bulletin_no("2025-12") == "2025/12"
    assert _normalise_bulletin_no(None) is None
    assert _normalise_bulletin_no("") is None


def test_resolve_patent_id_returns_uuid_when_unambiguous() -> None:
    """Single patents row matching application_no → return its UUID."""
    cur = _MockCursor(rows=[("p-uuid",)])     # fetchall() returns [(uuid,)]

    # Override fetchone since _resolve uses fetchall
    class _C(_MockCursor):
        def fetchall(self):
            rows = self.rows
            self.rows = []
            return rows
    cur = _C(rows=[("p-uuid",)])
    assert _resolve_patent_id_for_event(cur, "2017/15048") == "p-uuid"


def test_resolve_patent_id_returns_none_when_ambiguous() -> None:
    """Multiple patents rows for one application_no (e.g. B grant +
    A1 republication in same bulletin) → NULL FK, event still ingests."""
    class _C(_MockCursor):
        def fetchall(self):
            rows = self.rows
            self.rows = []
            return rows
    cur = _C(rows=[("p-uuid-1",), ("p-uuid-2",)])
    assert _resolve_patent_id_for_event(cur, "2024/000746") is None


def test_resolve_patent_id_returns_none_when_no_match() -> None:
    """Event for an application not yet in the patents table → NULL.
    Common when events.json mentions an older app whose bulletin
    hasn't been ingested yet."""
    class _C(_MockCursor):
        def fetchall(self):
            return []
    cur = _C()
    assert _resolve_patent_id_for_event(cur, "9999/99999") is None


def test_resolve_patent_id_returns_none_for_blank_app_no() -> None:
    cur = _MockCursor()
    assert _resolve_patent_id_for_event(cur, None) is None
    assert _resolve_patent_id_for_event(cur, "") is None
    assert len(cur.executed) == 0   # no SQL fired


def test_replace_events_deletes_then_inserts_with_canonical_bulletin() -> None:
    """Replace pattern: DELETE WHERE bulletin_no = canonical, then
    INSERT each event. event_date inherits from bulletin_date."""
    class _C(_MockCursor):
        def fetchall(self):
            return []
    cur = _C()
    events_doc = {
        "bulletin_no": "2025-08",       # PDF format
        "bulletin_date": "2025-08-21",
        "events": [
            {"application_no": "2024/011609", "event_type": "GRANT_FINALIZED",
             "page": 50, "free_text": "x", "fingerprint": "abc1234567890def"},
            {"application_no": "2024/011610", "event_type": "GRANT_ANNOUNCED",
             "page": 51, "free_text": "y", "fingerprint": "def1234567890abc"},
        ],
    }
    inserted = replace_events(cur, events_doc)
    assert inserted == 2
    delete_sql, delete_params = cur.executed[0]
    assert "DELETE FROM patent_events WHERE bulletin_no" in delete_sql
    assert delete_params == ("2025/8",)            # canonicalised
    # Both INSERTs use the canonical bulletin_no + parsed event_date
    insert_params_list = [p for s, p in cur.executed if "INSERT INTO patent_events" in s]
    assert len(insert_params_list) == 2
    p0 = insert_params_list[0]
    # tuple shape: (patent_id, application_no, event_type, event_date,
    #               bulletin_no, bulletin_date, page, free_text, fingerprint)
    assert p0[2] == "GRANT_FINALIZED"
    assert p0[3] == date(2025, 8, 21)               # event_date inherits
    assert p0[4] == "2025/8"
    assert p0[8] == "abc1234567890def"


def test_replace_events_skips_when_bulletin_no_missing() -> None:
    """Defensive: malformed events.json without bulletin_no → return 0,
    no DELETE-FROM-WHERE-NULL no-op pollution."""
    class _C(_MockCursor):
        def fetchall(self):
            return []
    cur = _C()
    inserted = replace_events(cur, {"events": [
        {"application_no": "X", "event_type": "GRANT_FINALIZED",
         "page": 1, "free_text": "x", "fingerprint": "fp"},
    ]})
    assert inserted == 0
    # NO sql executes when bulletin_no is missing
    assert len(cur.executed) == 0


def test_ingest_bulletin_loads_events_json_and_inserts(tmp_path) -> None:
    """End-to-end JSON path: metadata.json + events.json both present
    → both upsert paths fire in one transaction."""
    import json
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "source_archive": "x.rar",
        "source_pdf": "y.pdf",
        "records": [{
            "application_no": "X1", "publication_no": "TR X1 B",
            "kind_code": "B", "record_type": "GRANTED_PATENT",
            "title": "T", "abstract": "A", "ipc_classes": [],
            "holders": [], "inventors": [], "attorneys": [],
            "priorities": [], "figures": [],
        }],
    }), encoding="utf-8")
    (parent / "events.json").write_text(json.dumps({
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "events": [
            {"application_no": "X1", "event_type": "GRANT_FINALIZED",
             "page": 50, "free_text": "x", "fingerprint": "abc1234567890def"},
            {"application_no": "X2", "event_type": "GRANT_ANNOUNCED",
             "page": 51, "free_text": "y", "fingerprint": "def1234567890abc"},
        ],
    }), encoding="utf-8")

    class _C(_MockCursor):
        def fetchall(self):
            rows = self.rows
            self.rows = []
            return rows
    # Sequence of fetches required:
    #   1 patents publication_no SELECT (returns None → INSERT path)
    #   1 patents INSERT RETURNING (returns p-uuid)
    #   then SELECTs for app_no FK resolution per event (use fetchall)
    cur = _C(rows=[None, "p-uuid"])
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn, force=True)

    assert out["status"] == "ok"
    assert out["records_processed"] == 1
    assert out["events_inserted"] == 2
    assert conn.committed is True


def test_ingest_bulletin_no_events_json_skips_silently(tmp_path) -> None:
    """metadata.json present, events.json absent → ingest succeeds
    with events_inserted=0. Stage 7 hasn't run yet for this bulletin."""
    import json
    parent = tmp_path / "PT_x"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [],
    }), encoding="utf-8")

    cur = _MockCursor()
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn, force=True)
    assert out["status"] == "empty"   # no records → returns empty
    assert "events_inserted" not in out or out.get("events_inserted", 0) == 0


def test_ingest_bulletin_skips_when_db_max_updated_at_newer(tmp_path) -> None:
    """When ``MAX(updated_at)`` for the bulletin is newer than the source
    metadata.json mtime, ingest is a no-op. Avoids re-upserting hundreds
    of thousands of identical rows on a no-op --all sweep."""
    import json
    import time as _time
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [{
            "application_no": "X1", "publication_no": "TR 2025 000123 B",
            "kind_code": "B", "record_type": "GRANTED_PATENT",
            "page_range": [1, 1],
            "holders": [], "inventors": [], "attorneys": [],
            "priorities": [], "figures": [],
        }],
    }), encoding="utf-8")
    metadata_mtime = (parent / "metadata.json").stat().st_mtime
    # DB MAX(updated_at) is 5 seconds in the future of the file.
    db_epoch = metadata_mtime + 5

    cur = _MockCursor(rows=[(db_epoch,)])
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn)
    assert out["status"] == "fresh_skip"
    # No upsert SQL was issued — the freshness query is the only execute.
    assert any("MAX(updated_at)" in sql for sql, _ in cur.executed)
    assert not any("INSERT INTO patents" in sql for sql, _ in cur.executed)


def test_ingest_bulletin_runs_when_db_max_updated_at_older(tmp_path) -> None:
    """If MAX(updated_at) is older than the metadata.json mtime, the
    source has changed since last ingest → re-upsert without --force."""
    import json
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [{
            "application_no": "X1", "publication_no": "TR 2025 000123 B",
            "kind_code": "B", "record_type": "GRANTED_PATENT",
            "page_range": [1, 1],
            "holders": [], "inventors": [], "attorneys": [],
            "priorities": [], "figures": [],
        }],
    }), encoding="utf-8")
    metadata_mtime = (parent / "metadata.json").stat().st_mtime
    # DB MAX is older than the file → source changed.
    db_epoch = metadata_mtime - 10

    # Freshness query result first; then upsert path:
    #   patents publication_no SELECT (no row) → INSERT RETURNING (uuid)
    cur = _MockCursor(rows=[(db_epoch,), None, "p-uuid"])
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn)
    assert out["status"] == "ok"
    assert out["records_processed"] == 1


def test_ingest_bulletin_force_skips_freshness_check(tmp_path) -> None:
    """force=True bypasses the freshness probe and always upserts."""
    import json
    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8", "bulletin_date": "2025-08-21",
        "records": [{
            "application_no": "X1", "publication_no": "TR 2025 000123 B",
            "kind_code": "B", "record_type": "GRANTED_PATENT",
            "page_range": [1, 1],
            "holders": [], "inventors": [], "attorneys": [],
            "priorities": [], "figures": [],
        }],
    }), encoding="utf-8")

    # No freshness row needed because force=True; jump straight to upsert.
    cur = _MockCursor(rows=[None, "p-uuid"])
    conn = _MockConn(cur)
    out = ingest_bulletin(parent, conn=conn, force=True)
    assert out["status"] == "ok"
    # The MAX(updated_at) probe was NOT run.
    assert not any("MAX(updated_at)" in sql for sql, _ in cur.executed)
