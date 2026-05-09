"""Unit tests for ``data_collection_tasarim`` helpers.

Mirrors ``tests/test_data_collection.py`` in style. Covers the pure helpers
that the Playwright loop depends on: recency window, issue folder naming,
completeness check, incremental stop logic, and CLI argv parsing.
"""

import shutil
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from data_collection_tasarim import (
    CollectionCounters,
    DEFAULT_INCREMENTAL_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    IncrementalScanTracker,
    build_issue_folder_name,
    check_local_existence,
    extract_primary_issue_number,
    is_recent_issue,
    issue_folder_is_complete,
    parse_argv,
    parse_issue_date,
    safe_filename_keep_text,
    slugify,
)


TEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp_pytest_tasarim"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def temp_dir():
    temp_path = TEST_TEMP_ROOT / f"tmp_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# slugify / filename helpers
# ---------------------------------------------------------------------------

def test_slugify_normalizes_turkish_chars():
    assert slugify("Tasarım Bülteni") == "Tasarim_Bulteni"
    assert slugify("İSTANBUL") == "ISTANBUL"


def test_safe_filename_strips_invalid_chars_and_clamps_length():
    raw = 'Tasarım <2026/04/24>: "Bülten 483"\n\t'
    out = safe_filename_keep_text(raw)
    assert "<" not in out and ">" not in out and ":" not in out
    assert "\n" not in out and "\t" not in out
    assert out  # non-empty

    assert safe_filename_keep_text("a" * 500, max_len=50) == "a" * 50


# ---------------------------------------------------------------------------
# parse_issue_date / extract_primary_issue_number
# ---------------------------------------------------------------------------

def test_parse_issue_date_handles_iso_and_garbage():
    assert parse_issue_date("2026-04-24") == date(2026, 4, 24)
    assert parse_issue_date(None) is None
    assert parse_issue_date("") is None
    assert parse_issue_date("nope") is None


def test_extract_primary_issue_number():
    assert extract_primary_issue_number("483") == 483
    assert extract_primary_issue_number("483_2026") == 483
    assert extract_primary_issue_number("  482  ") == 482
    assert extract_primary_issue_number(None) is None
    assert extract_primary_issue_number("abc") is None


# ---------------------------------------------------------------------------
# is_recent_issue
# ---------------------------------------------------------------------------

def test_is_recent_issue_within_window():
    today = date(2026, 5, 7)
    assert is_recent_issue("2026-05-01", today=today, lookback_days=60) is True
    assert is_recent_issue("2026-04-01", today=today, lookback_days=60) is True


def test_is_recent_issue_outside_window():
    today = date(2026, 5, 7)
    # > 60 days back
    assert is_recent_issue("2025-12-01", today=today, lookback_days=60) is False


def test_is_recent_issue_boundary_is_inclusive():
    today = date(2026, 5, 7)
    # exact cutoff day = today - lookback_days should still count as recent
    assert is_recent_issue("2026-03-08", today=today, lookback_days=60) is True
    # one day past cutoff is not recent
    assert is_recent_issue("2026-03-07", today=today, lookback_days=60) is False


def test_is_recent_issue_unknown_date_treated_as_recent():
    # Cards without parseable dates should not be skipped, so we don't
    # accidentally drop fresh issues with malformed metadata.
    today = date(2026, 5, 7)
    assert is_recent_issue(None, today=today) is True
    assert is_recent_issue("", today=today) is True
    assert is_recent_issue("garbage", today=today) is True


# ---------------------------------------------------------------------------
# build_issue_folder_name / issue_folder_is_complete / check_local_existence
# ---------------------------------------------------------------------------

def test_build_issue_folder_name_with_and_without_date():
    assert build_issue_folder_name("483", "2026-04-24") == "TS_483_2026-04-24"
    assert build_issue_folder_name("483", None) == "TS_483"
    assert build_issue_folder_name(" 482 ", "2026-04-09") == "TS_482_2026-04-09"


def test_build_issue_folder_name_rejects_empty_id():
    with pytest.raises(ValueError):
        build_issue_folder_name("", "2026-04-24")
    with pytest.raises(ValueError):
        build_issue_folder_name(None, None)  # type: ignore[arg-type]


def test_issue_folder_is_complete_requires_nonempty_pdf():
    with temp_dir() as tmp:
        issue = tmp / "TS_483_2026-04-24"
        # missing folder
        assert issue_folder_is_complete(issue) is False

        issue.mkdir()
        # folder exists, no pdf
        assert issue_folder_is_complete(issue) is False

        # empty pdf
        (issue / "bulletin.pdf").write_bytes(b"")
        assert issue_folder_is_complete(issue) is False

        # non-empty pdf
        (issue / "bulletin.pdf").write_bytes(b"%PDF-1.4\n...")
        assert issue_folder_is_complete(issue) is True


def test_check_local_existence_finds_dated_and_undated_folders():
    with temp_dir() as tmp:
        category = tmp / "Tasarim"
        category.mkdir()

        # nothing yet
        assert check_local_existence(str(category), "483", card_date="2026-04-24") is False

        # dated folder with PDF
        dated = category / "TS_483_2026-04-24"
        dated.mkdir()
        (dated / "bulletin.pdf").write_bytes(b"%PDF-1.4")
        assert check_local_existence(str(category), "483", card_date="2026-04-24") is True

        # legacy / undated folder layout still recognized
        legacy = category / "TS_482"
        legacy.mkdir()
        (legacy / "bulletin.pdf").write_bytes(b"%PDF-1.4")
        assert check_local_existence(str(category), "482", card_date="2026-04-09") is True


def test_check_local_existence_returns_false_when_root_missing():
    with temp_dir() as tmp:
        ghost = tmp / "no_such_category"
        assert check_local_existence(str(ghost), "483", card_date="2026-04-24") is False


# ---------------------------------------------------------------------------
# IncrementalScanTracker
# ---------------------------------------------------------------------------

def test_tracker_stops_after_threshold_in_window():
    tracker = IncrementalScanTracker(
        threshold=3, lookback_days=60, today=date(2026, 5, 7)
    )
    assert tracker.observe(card_date="2026-05-01") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-20") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-10") is True
    # threshold reached
    assert tracker.should_stop() is True


def test_tracker_stops_on_cutoff_even_below_threshold():
    tracker = IncrementalScanTracker(
        threshold=10, lookback_days=60, today=date(2026, 5, 7)
    )
    assert tracker.observe(card_date="2026-05-01") is True
    assert tracker.should_stop() is False
    # crossing the cutoff stops the loop even though threshold is far away
    assert tracker.observe(card_date="2025-12-01") is False
    assert tracker.should_stop() is True


def test_tracker_default_constants_match_module_defaults():
    tracker = IncrementalScanTracker()
    assert tracker.threshold == DEFAULT_INCREMENTAL_THRESHOLD
    assert tracker.lookback_days == DEFAULT_LOOKBACK_DAYS
    assert tracker.recent_count == 0
    assert tracker.cutoff_reached is False


# ---------------------------------------------------------------------------
# CLI argv parsing
# ---------------------------------------------------------------------------

def test_parse_argv_defaults():
    args = parse_argv([])
    assert args.full is False
    assert args.limit is None
    assert args.headless is True
    assert args.bulletins_root.name == "Tasarim"


def test_parse_argv_full_and_limit_and_headless_off():
    args = parse_argv(["--full", "--limit", "3", "--headless=false"])
    assert args.full is True
    assert args.limit == 3
    assert args.headless is False


def test_parse_argv_headless_truthy_variants():
    for v in ("true", "1", "yes", "on"):
        assert parse_argv([f"--headless={v}"]).headless is True
    for v in ("false", "0", "no", "off"):
        assert parse_argv([f"--headless={v}"]).headless is False


def test_parse_argv_headless_invalid_raises():
    with pytest.raises(SystemExit):
        parse_argv(["--headless=maybe"])


def test_parse_argv_custom_bulletins_root(tmp_path):
    args = parse_argv(["--bulletins-root", str(tmp_path / "elsewhere")])
    assert args.bulletins_root == tmp_path / "elsewhere"


def test_parse_argv_issue_implies_full():
    """``--issue NNN`` must force --full=True so the incremental tracker
    doesn't stop walking the archive before reaching old bulletins."""
    args = parse_argv(["--issue", "240"])
    assert args.issue == "240"
    assert args.full is True


def test_parse_argv_issue_combined_with_full_explicit():
    """Passing --full alongside --issue is fine (both same effect)."""
    args = parse_argv(["--issue", "240", "--full"])
    assert args.issue == "240"
    assert args.full is True


def test_parse_argv_issue_strips_whitespace():
    """``--issue " 240 "`` -> issue=='240' so users can paste-with-spaces."""
    args = parse_argv(["--issue", "  240  "])
    assert args.issue == "240"


def test_parse_argv_issue_default_is_none():
    """Without --issue, the field is None and full follows its own default."""
    args = parse_argv([])
    assert args.issue is None
    assert args.full is False


# ---------------------------------------------------------------------------
# CollectionCounters
# ---------------------------------------------------------------------------

def test_counters_summary_shape():
    counters = CollectionCounters(downloaded=2, failed=1, skipped=4)
    summary = counters.to_summary(duration_seconds=12.345)
    assert summary == {
        "downloaded": 2,
        "skipped": 4,
        "failed": 1,
        "duration_seconds": 12.3,
    }
