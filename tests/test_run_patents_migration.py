"""Tests for the Patent / Faydalı Model migration runner.

Mock-DB pattern: a fake psycopg2 connection captures executed SQL +
returns canned rows for the existence-check queries. Verifies apply
flow, rollback flow, and the --down CLI plumbing without touching a
real database.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

import pytest


# ---------------------------------------------------------------------------
# Mock psycopg2 plumbing
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Records executed SQL; returns canned rows for fetchone().

    ``rows`` is a deque that fetchone() pops from in order. Each
    existence-check query pops one row; the bulk migration cur.execute(sql)
    doesn't fetch.
    """

    def __init__(self, rows: Optional[List[Any]] = None) -> None:
        self.rows = list(rows or [])
        self.executed: List[str] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        if not self.rows:
            return (False,)
        value = self.rows.pop(0)
        return (value,) if not isinstance(value, tuple) else value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False


def _patch_connect(monkeypatch, conn: _FakeConnection) -> None:
    import migrations.run_patents_migration as migration
    monkeypatch.setattr(migration, "_connect", lambda: conn)


# ---------------------------------------------------------------------------
# apply_up
# ---------------------------------------------------------------------------


def test_apply_up_executes_migration_sql_when_holders_present(monkeypatch):
    """Happy path: holders table exists; apply_up reads patents.sql,
    executes it on the cursor, commits, and writes nothing else."""
    import migrations.run_patents_migration as migration

    # First fetchone() answers "_holders_table_exists" -> True.
    # The next 9 answer the already_up existence checks (any value
    # works; SQL gets executed regardless because CREATE IF NOT EXISTS
    # is idempotent).
    cursor = _FakeCursor(rows=[True] + [False] * 9)
    conn = _FakeConnection(cursor)
    _patch_connect(monkeypatch, conn)

    migration.apply_up(verbose=False)

    # First two executes: holders existence + the bundle of 9
    # _table_exists / _enum_exists checks. The very last execute is
    # the migration SQL itself (which we sniff for the section header).
    assert any(
        "Patent / Faydalı Model Schema Migration" in sql
        for sql in cursor.executed
    ), "migration SQL must be executed"
    assert conn.committed is True


def test_apply_up_raises_when_holders_table_missing(monkeypatch):
    """patents.holder_id FK references the trademark schema's holders
    table. If that bootstrap hasn't run, apply_up must fail loud."""
    import migrations.run_patents_migration as migration

    cursor = _FakeCursor(rows=[False])    # holders existence -> False
    conn = _FakeConnection(cursor)
    _patch_connect(monkeypatch, conn)

    with pytest.raises(RuntimeError, match="requires the existing 'holders' table"):
        migration.apply_up(verbose=False)

    # Migration SQL must NOT have executed.
    assert not any(
        "Patent / Faydalı Model Schema Migration" in sql
        for sql in cursor.executed
    )
    assert conn.committed is False


# ---------------------------------------------------------------------------
# apply_down
# ---------------------------------------------------------------------------


def test_apply_down_executes_drop_in_reverse_fk_order(monkeypatch):
    """DOWN_SQL must drop child tables before parents. Verifies the
    explicit ordering documents the FK dependency graph correctly."""
    import migrations.run_patents_migration as migration

    cursor = _FakeCursor()
    conn = _FakeConnection(cursor)
    _patch_connect(monkeypatch, conn)

    migration.apply_down(verbose=False)

    assert len(cursor.executed) == 1
    sql = cursor.executed[0]
    # Each table appears exactly once.
    expected_order = [
        "patent_events",
        "patent_figures",
        "patent_priorities",
        "patent_attorneys",
        "patent_inventors",
        "patent_holders",
        "patents",
        "ipc_classes_lookup",
    ]
    indices = [sql.index(f"DROP TABLE IF EXISTS {t}") for t in expected_order]
    assert indices == sorted(indices), (
        f"DROP TABLE order must match reverse FK dependency: "
        f"{expected_order}, got at offsets {indices}"
    )
    # Enum drop comes last.
    assert sql.rfind("DROP TYPE IF EXISTS patent_record_type") > indices[-1]
    assert conn.committed is True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_no_args_calls_apply_up(monkeypatch):
    import migrations.run_patents_migration as migration
    called: List[str] = []
    monkeypatch.setattr(migration, "apply_up", lambda: called.append("up"))
    monkeypatch.setattr(migration, "apply_down", lambda: called.append("down"))

    rc = migration.main([])

    assert rc == 0
    assert called == ["up"]


def test_main_down_flag_calls_apply_down(monkeypatch):
    import migrations.run_patents_migration as migration
    called: List[str] = []
    monkeypatch.setattr(migration, "apply_up", lambda: called.append("up"))
    monkeypatch.setattr(migration, "apply_down", lambda: called.append("down"))

    rc = migration.main(["--down"])

    assert rc == 0
    assert called == ["down"]


# ---------------------------------------------------------------------------
# patents.sql content lints
# ---------------------------------------------------------------------------


def _patents_sql() -> str:
    path = Path(__file__).resolve().parent.parent / "migrations" / "patents.sql"
    return path.read_text(encoding="utf-8")


def test_patents_sql_loads_as_text():
    """Smoke: file exists, is UTF-8, has the expected section header."""
    sql = _patents_sql()
    assert "Patent / Faydalı Model Schema Migration" in sql
    assert sql.count("CREATE TABLE IF NOT EXISTS") >= 7    # 7 patent tables + lookup


def test_patents_sql_uses_idempotent_patterns():
    """All CREATE TABLE / CREATE INDEX must be IF NOT EXISTS; enum
    creation must be wrapped in DO $$ ... EXCEPTION WHEN
    duplicate_object pattern."""
    sql = _patents_sql()
    # No bare CREATE TABLE (without IF NOT EXISTS)
    import re
    bare_tables = re.findall(r"CREATE TABLE(?! IF NOT EXISTS)", sql)
    assert not bare_tables, f"non-idempotent CREATE TABLE: {bare_tables}"
    # No bare CREATE INDEX
    bare_indexes = re.findall(r"CREATE (?:UNIQUE )?INDEX(?! IF NOT EXISTS)", sql)
    assert not bare_indexes, f"non-idempotent CREATE INDEX: {bare_indexes}"
    # Enum creation idempotent
    assert "CREATE TYPE patent_record_type" in sql
    assert "EXCEPTION WHEN duplicate_object THEN NULL" in sql


def test_patents_sql_uses_publication_no_as_natural_unique_key():
    """Regression for the natural-key memory: publication_no is the
    unique key, NOT application_no. Same app can ship multiple
    publications in one bulletin."""
    sql = _patents_sql()
    assert "uq_patents_publication_no" in sql
    assert "ON patents (publication_no) WHERE publication_no IS NOT NULL" in sql
    # application_no is indexed but NOT unique.
    assert "idx_pat_app_no" in sql
    assert "uq_patents_application_no" not in sql


def test_patents_sql_has_record_type_enum_with_documented_values():
    """All seven kind-code-based record types ship in the enum."""
    sql = _patents_sql()
    for value in ("GRANTED_PATENT", "GRANTED_UM", "PUBLISHED_APP",
                  "PUBLISHED_UM_APP", "EP_FASCICLE", "LEGACY", "UNKNOWN"):
        assert f"'{value}'" in sql, f"enum value {value} missing from patents.sql"


def test_patents_sql_holder_fk_references_global_holders_table():
    """Locked decision: patent_holders FKs to the existing global
    holders table (TPECLIENT IDs are shared cross-registry)."""
    sql = _patents_sql()
    assert "holder_id    UUID REFERENCES holders(id) ON DELETE SET NULL" in sql


def test_patents_sql_has_hnsw_vector_indexes():
    """Schema must ship HNSW indexes for the embedding columns so
    Stage 6 doesn't have to add them in a follow-up migration."""
    sql = _patents_sql()
    assert "USING hnsw (title_abstract_embedding halfvec_cosine_ops)" in sql
    assert "USING hnsw (primary_figure_embedding halfvec_cosine_ops)" in sql
    assert "USING hnsw (dinov2_vitl14 halfvec_cosine_ops)" in sql
    assert "USING hnsw (clip_vitb32 halfvec_cosine_ops)" in sql
    assert "WITH (m=16, ef_construction=200)" in sql
