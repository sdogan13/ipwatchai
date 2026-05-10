"""Smoke tests for the patent_watchlist + patent_alerts migration SQL.

Static SQL file (no Python runner). These tests verify file structure,
expected table/index/constraint coverage, and idempotency markers.
"""
from __future__ import annotations

from pathlib import Path

import pytest


MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "patent_watchlist.sql"


@pytest.fixture(scope="module")
def sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists(sql):
    assert sql, "patent_watchlist.sql is empty"


def test_creates_both_tables(sql):
    assert "CREATE TABLE IF NOT EXISTS patent_watchlist_mt" in sql
    assert "CREATE TABLE IF NOT EXISTS patent_alerts_mt" in sql


def test_idempotency_markers(sql):
    # Every CREATE statement should be IF NOT EXISTS
    assert "CREATE INDEX " not in sql.replace("CREATE INDEX IF NOT EXISTS", "")
    assert "CREATE TABLE " not in sql.replace("CREATE TABLE IF NOT EXISTS", "")


def test_watch_type_discriminator_with_check(sql):
    assert "watch_type" in sql
    assert "CHECK (watch_type IN ('holder', 'reference'))" in sql


def test_holder_watch_constraint(sql):
    assert "chk_holder_watch_has_holder" in sql


def test_reference_watch_constraint(sql):
    assert "chk_reference_watch_has_reference" in sql


def test_holder_fk_to_holders(sql):
    assert "REFERENCES holders(id)" in sql


def test_reference_patent_fk(sql):
    assert "REFERENCES patents(id)" in sql


def test_alerts_dedup_unique_index(sql):
    assert "uq_patent_alerts_pair" in sql
    assert "watchlist_item_id, conflicting_patent_id" in sql


def test_match_type_check_covers_holder_and_reference(sql):
    assert "match_type" in sql
    for v in ("'holder'", "'reference_text'", "'reference_embedding'", "'reference_hybrid'"):
        assert v in sql, f"match_type CHECK missing {v}"


def test_severity_and_status_checks(sql):
    assert "severity IN ('low','medium','high','critical')" in sql
    assert "status IN ('new','seen','acknowledged','resolved','dismissed')" in sql


def test_hnsw_index_on_reference_embedding(sql):
    assert "hnsw (reference_embedding halfvec_cosine_ops)" in sql


def test_gin_index_on_ipc_classes(sql):
    assert "USING GIN (ipc_classes)" in sql


def test_organization_cascade(sql):
    # Watchlist + alerts both cascade on organization delete (multi-tenant cleanup)
    assert sql.count("REFERENCES organizations(id) ON DELETE CASCADE") >= 2
