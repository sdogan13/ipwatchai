"""Unit tests for ``data_collection_cografi`` helpers.

Mirrors ``tests/test_data_collection_patent.py`` in style, trimmed to the
PDF-only single-track surface this collector actually exposes. Covers the
pure helpers the Playwright loop depends on: card-id normalization,
recency window, PDF filename construction, completeness check,
incremental stop logic, direct-href validation, and CLI argv parsing.
"""

import shutil
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from data_collection_cografi import (
    BULLETIN_FILENAME,
    BUNDLE_SUFFIX,
    CATEGORY_FOLDER_NAME,
    CLIArgs,
    CollectionCounters,
    DEFAULT_INCREMENTAL_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    IncrementalScanTracker,
    SUBFOLDER_PREFIX,
    _looks_like_download_href,
    bulletin_path,
    bulletin_subfolder_name,
    card_is_complete,
    existing_bulletin,
    is_rar_archive,
    is_recent_issue,
    normalize_card_id,
    parse_argv,
    parse_issue_date,
    safe_filename_keep_text,
    slugify,
)


TEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp_pytest_cografi"
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
# Category & slug normalization
# ---------------------------------------------------------------------------

def test_category_folder_slug_matches_existing_disk_layout():
    """The slug of the dropdown label MUST match the on-disk folder name we
    already have provisioned for cografi bulletins. Regression guard against
    any rename.
    """
    assert (
        slugify("Coğrafi İşaret ve Geleneksel Ürün Adı")
        == CATEGORY_FOLDER_NAME
        == "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
    )


def test_slugify_normalizes_turkish_chars():
    assert slugify("Coğrafi İşaret") == "Cografi_Isaret"
    assert slugify("İSTANBUL") == "ISTANBUL"


def test_safe_filename_strips_invalid_chars_and_clamps_length():
    raw = 'Coğrafi <220>: "Bülten 220"\n\t'
    out = safe_filename_keep_text(raw)
    assert "<" not in out and ">" not in out and ":" not in out
    assert "\n" not in out and "\t" not in out
    assert out

    assert safe_filename_keep_text("a" * 500, max_len=50) == "a" * 50


# ---------------------------------------------------------------------------
# normalize_card_id — cografi cards are bare issue numbers (220, 219, ...)
# ---------------------------------------------------------------------------

def test_normalize_card_id_handles_bare_issue_numbers():
    assert normalize_card_id("220") == "220"
    assert normalize_card_id("  219  ") == "219"
    assert normalize_card_id("3") == "3"


def test_normalize_card_id_returns_none_for_unparseable():
    assert normalize_card_id(None) is None
    assert normalize_card_id("") is None
    assert normalize_card_id("   ") is None
    assert normalize_card_id("abc") is None


# ---------------------------------------------------------------------------
# parse_issue_date / is_recent_issue
# ---------------------------------------------------------------------------

def test_parse_issue_date_handles_iso_and_garbage():
    assert parse_issue_date("2026-04-15") == date(2026, 4, 15)
    assert parse_issue_date(None) is None
    assert parse_issue_date("") is None
    assert parse_issue_date("nope") is None


def test_is_recent_issue_within_window():
    today = date(2026, 5, 10)
    # Card 220, dated 2026-05-04 (4 days back) — well inside window.
    assert is_recent_issue("2026-05-04", today=today, lookback_days=60) is True
    # Card 215, dated 2026-02-16 (~12 weeks back) — also inside the 60-day
    # window when lookback is generous.
    assert is_recent_issue("2026-04-01", today=today, lookback_days=60) is True


def test_is_recent_issue_outside_window():
    today = date(2026, 5, 10)
    # 2025-12-01 is well outside a 60-day window from May 2026.
    assert is_recent_issue("2025-12-01", today=today, lookback_days=60) is False


def test_is_recent_issue_unknown_date_treated_as_recent():
    today = date(2026, 5, 10)
    assert is_recent_issue(None, today=today) is True
    assert is_recent_issue("", today=today) is True
    assert is_recent_issue("garbage", today=today) is True


# ---------------------------------------------------------------------------
# Subfolder layout helpers
# ---------------------------------------------------------------------------

def test_bulletin_subfolder_name_matches_tasarim_style():
    """Mirror tasarım's ``TS_{N}_{date}`` naming with a ``CI_`` prefix."""
    assert bulletin_subfolder_name("220", "2026-05-04") == "CI_220_2026-05-04"
    assert bulletin_subfolder_name("3", "2017-04-15") == "CI_3_2017-04-15"


def test_bulletin_subfolder_name_rejects_bad_inputs():
    with pytest.raises(ValueError):
        bulletin_subfolder_name("", "2026-05-04")
    with pytest.raises(ValueError):
        bulletin_subfolder_name("220", "04.05.2026")
    with pytest.raises(ValueError):
        bulletin_subfolder_name("220", "")


def test_bulletin_path_assembles_subfolder_and_filename(tmp_path):
    p = bulletin_path(tmp_path, "220", "2026-05-04")
    assert p.parent.name == "CI_220_2026-05-04"
    assert p.name == BULLETIN_FILENAME == "bulletin.pdf"


# ---------------------------------------------------------------------------
# existing_bulletin / card_is_complete
# ---------------------------------------------------------------------------

def test_existing_bulletin_finds_subfolder_layout():
    with temp_dir() as tmp:
        sub = tmp / "CI_220_2026-05-04"
        sub.mkdir()
        (sub / BULLETIN_FILENAME).write_bytes(b"%PDF-1.6")
        path = existing_bulletin(tmp, "220")
        assert path is not None
        assert path.parent.name == "CI_220_2026-05-04"
        assert path.name == BULLETIN_FILENAME


def test_existing_bulletin_treats_zero_byte_as_missing():
    """An empty ``bulletin.pdf`` from a partial download must not pass."""
    with temp_dir() as tmp:
        sub = tmp / "CI_220_2026-05-04"
        sub.mkdir()
        (sub / BULLETIN_FILENAME).write_bytes(b"")
        assert existing_bulletin(tmp, "220") is None


def test_existing_bulletin_is_date_tolerant():
    """A subfolder for the same card_id matches regardless of date so
    a re-run with a different site-reported date does not re-download."""
    with temp_dir() as tmp:
        sub = tmp / "CI_220_2025-12-31"
        sub.mkdir()
        (sub / BULLETIN_FILENAME).write_bytes(b"%PDF-1.6")
        # Looking for card 220 should still hit, despite a different date.
        assert existing_bulletin(tmp, "220") is not None


def test_existing_bulletin_returns_none_when_root_missing():
    with temp_dir() as tmp:
        ghost = tmp / "no_such_folder"
        assert existing_bulletin(ghost, "220") is None


def test_card_is_complete_uses_subfolder_layout():
    with temp_dir() as tmp:
        assert card_is_complete(tmp, "220") is False
        sub = tmp / "CI_220_2026-05-04"
        sub.mkdir()
        (sub / BULLETIN_FILENAME).write_bytes(b"%PDF-1.6")
        assert card_is_complete(tmp, "220") is True


def test_card_is_complete_does_not_match_substring_neighbour():
    """Subfolder ``CI_2200_*`` must not be treated as a hit for card 220.
    Sequential issue numbers can collide with longer ones once we cross
    999, so the match must be on exact stem boundaries.
    """
    with temp_dir() as tmp:
        sub = tmp / "CI_2200_2099-01-01"
        sub.mkdir()
        (sub / BULLETIN_FILENAME).write_bytes(b"%PDF-1.6")
        assert card_is_complete(tmp, "220") is False
        assert existing_bulletin(tmp, "220") is None


# ---------------------------------------------------------------------------
# is_rar_archive — file-magic detection of mis-named bundles
# ---------------------------------------------------------------------------

def test_is_rar_archive_detects_rar_v5_magic(tmp_path):
    p = tmp_path / "fake_bundle.pdf"
    p.write_bytes(b"Rar!\x1a\x07\x01\x00rest_of_file_does_not_matter")
    assert is_rar_archive(p) is True


def test_is_rar_archive_returns_false_for_real_pdf(tmp_path):
    p = tmp_path / "real.pdf"
    p.write_bytes(b"%PDF-1.6\n%random pdf bytes")
    assert is_rar_archive(p) is False


def test_is_rar_archive_returns_false_for_missing_file(tmp_path):
    assert is_rar_archive(tmp_path / "nope") is False


def test_bundle_suffix_constant_matches_documented_layout():
    assert BUNDLE_SUFFIX == "_bundle.rar"
    assert SUBFOLDER_PREFIX == "CI_"


# ---------------------------------------------------------------------------
# _looks_like_download_href — direct-href fast-path gating
# ---------------------------------------------------------------------------

def test_looks_like_download_href_accepts_real_turkpatent_url():
    """Lock in the regression: the cografi UI on 2026-05-10 surfaced
    anchors of this exact shape (captured live for card 220), and the
    collector must accept them as direct download targets.
    """
    href = "https://webim.turkpatent.gov.tr/file/13726d52-088a-4689-bb9a-78ce323df552?name=220&download"
    assert _looks_like_download_href(href) is True


def test_looks_like_download_href_rejects_empty_and_placeholder():
    assert _looks_like_download_href(None) is False
    assert _looks_like_download_href("") is False
    assert _looks_like_download_href("   ") is False
    assert _looks_like_download_href("#") is False


def test_looks_like_download_href_rejects_javascript_void():
    assert _looks_like_download_href("javascript:void(0)") is False
    assert _looks_like_download_href("JavaScript:doSomething()") is False
    assert _looks_like_download_href("  javascript:void(0)  ") is False


# ---------------------------------------------------------------------------
# Date regex divergence from patent: cografi cards use single-digit days
# ---------------------------------------------------------------------------

def test_card_date_regex_accepts_single_digit_day():
    """Card 220 raw text on 2026-05-10 reads ``4.05.2026 tarih`` — single-
    digit day. The patent collector's regex (`\\d{2}`) misses this and
    falls through to a parent-walk that picks up a sibling card's date.
    The cografi regex (`\\d{1,2}`) must match directly and pad to two
    digits, yielding ``2026-05-04``.

    This test inlines the JS regex source from
    ``extract_card_metadata`` (we can't run JS in pytest), and asserts
    its capture groups yield the correctly padded ISO date.
    """
    import re as _re
    # Mirror of the JS regex inside extract_card_metadata in the cografi
    # collector. The JS uses /\d{1,2}[./]\d{1,2}[./]\d{4}/; Python /-flavour
    # is identical for this subset.
    date_re = _re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")

    raw = "220\n\n4.05.2026 tarih ve 220 sayılı Resmi Coğrafi İşaret Bülteni\n\nİNDİR"
    m = date_re.search(raw)
    assert m is not None, "regex must match single-digit day"
    day, month, year = m.group(1), m.group(2), m.group(3)
    iso = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    assert iso == "2026-05-04"

    # And still works for two-digit days (cards 217 / 218 on the live UI).
    raw_two = "217\n\n16.03.2026 tarih ve 217 sayılı Resmi Bülteni\n\nİNDİR"
    m2 = date_re.search(raw_two)
    assert m2 is not None
    iso2 = f"{m2.group(3)}-{m2.group(2).zfill(2)}-{m2.group(1).zfill(2)}"
    assert iso2 == "2026-03-16"


# ---------------------------------------------------------------------------
# IncrementalScanTracker
# ---------------------------------------------------------------------------

def test_tracker_stops_after_threshold_in_window():
    tracker = IncrementalScanTracker(
        threshold=3, lookback_days=60, today=date(2026, 5, 10)
    )
    assert tracker.observe(card_date="2026-05-04") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-15") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-01") is True
    assert tracker.should_stop() is True


def test_tracker_stops_on_cutoff_even_below_threshold():
    """When scrolling reaches an old issue, stop early even if we've only
    seen one recent issue. This is what makes incremental cheap.
    """
    tracker = IncrementalScanTracker(
        threshold=10, lookback_days=60, today=date(2026, 5, 10)
    )
    assert tracker.observe(card_date="2026-04-15") is True
    assert tracker.should_stop() is False
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
    assert args.bulletins_root.name == CATEGORY_FOLDER_NAME
    assert args.force is False


def test_parse_argv_force_flag():
    """`--force` is a re-download override; default stays idempotent."""
    assert parse_argv([]).force is False
    assert parse_argv(["--force"]).force is True


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


# ---------------------------------------------------------------------------
# CollectionCounters / CLIArgs dataclass shape
# ---------------------------------------------------------------------------

def test_counters_summary_shape():
    counters = CollectionCounters(downloaded=4, failed=1, skipped=12)
    summary = counters.to_summary(duration_seconds=12.345)
    assert summary == {
        "downloaded": 4,
        "skipped": 12,
        "failed": 1,
        "duration_seconds": 12.3,
    }


def test_cliargs_dataclass_fields_are_exposed():
    args = CLIArgs(
        full=True,
        limit=5,
        headless=False,
        bulletins_root=Path("/tmp/x"),
    )
    assert args.full is True
    assert args.limit == 5
    assert args.headless is False
    assert args.bulletins_root == Path("/tmp/x")
    assert args.force is False
