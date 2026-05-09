"""Unit tests for ``data_collection_patent`` helpers.

Mirrors ``tests/test_data_collection_tasarim.py`` in style. Covers the pure
helpers that the Playwright loop depends on: card-id normalization, recency
window, per-track filename construction, completeness check, incremental
stop logic, menu-item classification, and CLI argv parsing.
"""

import shutil
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from data_collection_patent import (
    CATEGORY_FOLDER_NAME,
    CLIArgs,
    CollectionCounters,
    DEFAULT_INCREMENTAL_THRESHOLD,
    DEFAULT_LOOKBACK_DAYS,
    IncrementalScanTracker,
    Track,
    _looks_like_download_href,
    build_cd_filename,
    build_pdf_filename,
    card_is_complete,
    classify_menu_item_text,
    existing_legacy_parts,
    existing_track_file,
    is_multi_uuid_href,
    is_recent_issue,
    legacy_part_filename,
    normalize_card_id,
    parse_argv,
    parse_issue_date,
    safe_filename_keep_text,
    slugify,
    split_multi_uuid_href,
    track_filename,
    tracks_missing,
)


TEST_TEMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp_pytest_patent"
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
    already have 184 files in. Regression guard against any rename.
    """
    assert slugify("Patent / Faydalı Model") == CATEGORY_FOLDER_NAME == "Patent__Faydali_Model"


def test_slugify_normalizes_turkish_chars():
    assert slugify("Patent Faydalı Model") == "Patent_Faydali_Model"
    assert slugify("İSTANBUL") == "ISTANBUL"


def test_safe_filename_strips_invalid_chars_and_clamps_length():
    raw = 'Patent <2025/12/22>: "Bülten 2025_12"\n\t'
    out = safe_filename_keep_text(raw)
    assert "<" not in out and ">" not in out and ":" not in out
    assert "\n" not in out and "\t" not in out
    assert out

    assert safe_filename_keep_text("a" * 500, max_len=50) == "a" * 50


# ---------------------------------------------------------------------------
# normalize_card_id — patents render `2025/12` or `2025_12`
# ---------------------------------------------------------------------------

def test_normalize_card_id_handles_slash_and_underscore():
    assert normalize_card_id("2025/12") == "2025_12"
    assert normalize_card_id("2025_12") == "2025_12"
    assert normalize_card_id("  2025/12  ") == "2025_12"
    # Range form (legacy) preserved
    assert normalize_card_id("1996_6-1997_12") == "1996_6-1997_12"


def test_normalize_card_id_returns_none_for_unparseable():
    assert normalize_card_id(None) is None
    assert normalize_card_id("") is None
    assert normalize_card_id("   ") is None
    assert normalize_card_id("abc") is None


# ---------------------------------------------------------------------------
# parse_issue_date / is_recent_issue
# ---------------------------------------------------------------------------

def test_parse_issue_date_handles_iso_and_garbage():
    assert parse_issue_date("2025-12-22") == date(2025, 12, 22)
    assert parse_issue_date(None) is None
    assert parse_issue_date("") is None
    assert parse_issue_date("nope") is None


def test_is_recent_issue_within_window():
    today = date(2026, 5, 8)
    assert is_recent_issue("2026-05-01", today=today, lookback_days=60) is True
    assert is_recent_issue("2026-04-01", today=today, lookback_days=60) is True


def test_is_recent_issue_outside_window():
    today = date(2026, 5, 8)
    # 2025_12 dated 2025-12-22 is well outside a 60-day window from May 2026
    assert is_recent_issue("2025-12-22", today=today, lookback_days=60) is False


def test_is_recent_issue_unknown_date_treated_as_recent():
    today = date(2026, 5, 8)
    assert is_recent_issue(None, today=today) is True
    assert is_recent_issue("", today=today) is True
    assert is_recent_issue("garbage", today=today) is True


# ---------------------------------------------------------------------------
# Per-track filename construction
# ---------------------------------------------------------------------------

def test_build_cd_filename_matches_existing_disk_convention():
    # Existing files include 2025_12_CD.rar, 2024_07_CD.rar, etc.
    assert build_cd_filename("2025_12") == "2025_12_CD.rar"
    assert build_cd_filename("2024_07") == "2024_07_CD.rar"


def test_build_pdf_filename_matches_existing_disk_convention():
    # Existing files include 2025_08.pdf, 2018_11.pdf, etc.
    assert build_pdf_filename("2025_08") == "2025_08.pdf"
    assert build_pdf_filename("2018_11") == "2018_11.pdf"


def test_filename_builders_reject_empty_id():
    with pytest.raises(ValueError):
        build_cd_filename("")
    with pytest.raises(ValueError):
        build_pdf_filename("")


def test_track_filename_dispatches_by_track():
    assert track_filename("2025_12", Track.CD) == "2025_12_CD.rar"
    assert track_filename("2025_12", Track.PDF) == "2025_12.pdf"


# ---------------------------------------------------------------------------
# existing_track_file / tracks_missing / card_is_complete
# ---------------------------------------------------------------------------

def test_existing_track_file_finds_exact_filename():
    with temp_dir() as tmp:
        (tmp / "2025_12_CD.rar").write_bytes(b"RAR content")
        (tmp / "2025_12.pdf").write_bytes(b"%PDF-1.6")

        cd_path = existing_track_file(tmp, "2025_12", Track.CD)
        pdf_path = existing_track_file(tmp, "2025_12", Track.PDF)
        assert cd_path is not None and cd_path.name == "2025_12_CD.rar"
        assert pdf_path is not None and pdf_path.name == "2025_12.pdf"


def test_existing_track_file_treats_zero_byte_as_missing():
    """Critical: a leftover .part-style zero-byte file must not be mistaken
    for a complete download. 2023_11_CD.bin.part is a real artefact.
    """
    with temp_dir() as tmp:
        (tmp / "2025_12_CD.rar").write_bytes(b"")  # zero-byte
        assert existing_track_file(tmp, "2025_12", Track.CD) is None


def test_existing_track_file_ignores_partial_extensions():
    with temp_dir() as tmp:
        # Mimic the real 2023_11_CD.bin.part orphan
        (tmp / "2023_11_CD.bin.part").write_bytes(b"some bytes")
        # Must NOT match — extension is .part, not .rar
        assert existing_track_file(tmp, "2023_11", Track.CD) is None


def test_existing_track_file_returns_none_when_root_missing():
    with temp_dir() as tmp:
        ghost = tmp / "no_such_folder"
        assert existing_track_file(ghost, "2025_12", Track.CD) is None


def test_tracks_missing_reports_only_what_is_absent():
    with temp_dir() as tmp:
        (tmp / "2025_12.pdf").write_bytes(b"%PDF-1.6")
        # CD missing, PDF present
        missing = tracks_missing(tmp, "2025_12", {Track.CD, Track.PDF})
        assert missing == {Track.CD}


def test_card_is_complete_requires_all_wanted_tracks():
    with temp_dir() as tmp:
        (tmp / "2025_12.pdf").write_bytes(b"%PDF-1.6")
        # only PDF wanted -> complete
        assert card_is_complete(tmp, "2025_12", {Track.PDF}) is True
        # both wanted -> incomplete
        assert card_is_complete(tmp, "2025_12", {Track.CD, Track.PDF}) is False
        # add CD -> complete
        (tmp / "2025_12_CD.rar").write_bytes(b"RAR")
        assert card_is_complete(tmp, "2025_12", {Track.CD, Track.PDF}) is True


def test_card_is_complete_does_not_match_legacy_multi_month_bundle():
    """The pre-existing legacy bundle ``Patent Bülteni 1996_6-1997_12.rar``
    contains months 1996_6..1997_12 in a single file. The collector must
    NOT treat ``1996_7`` as already-present just because the bundle's name
    contains a date range that spans 1996_7.
    """
    with temp_dir() as tmp:
        (tmp / "Patent Bülteni 1996_6-1997_12.rar").write_bytes(b"RAR")
        # 1996_7 is NOT in any individual file — must be missing
        assert card_is_complete(tmp, "1996_7", {Track.CD}) is False
        assert existing_track_file(tmp, "1996_7", Track.CD) is None


# ---------------------------------------------------------------------------
# classify_menu_item_text — CD vs PDF
# ---------------------------------------------------------------------------

def test_classify_menu_item_recognizes_cd_label_variants():
    assert classify_menu_item_text("CD İçeriği") is Track.CD
    assert classify_menu_item_text("CD İçerigi") is Track.CD
    assert classify_menu_item_text("CD_Icerigi") is Track.CD
    assert classify_menu_item_text("cd icerigi") is Track.CD


def test_classify_menu_item_treats_anything_else_as_pdf():
    assert classify_menu_item_text("2025_12") is Track.PDF
    assert classify_menu_item_text("PDF") is Track.PDF
    assert classify_menu_item_text("") is Track.PDF


# ---------------------------------------------------------------------------
# _looks_like_download_href — direct-href fast-path gating
# ---------------------------------------------------------------------------

def test_looks_like_download_href_accepts_real_turkpatent_url():
    """Lock in the regression: the Patent UI on 2026-05-08 surfaced anchors
    of this exact shape, and the collector must accept them as download
    targets so it stream-downloads the PDF instead of waiting for a
    (non-existent) dropdown menu.
    """
    href = "https://webim.turkpatent.gov.tr/file/a383bb6a-a74f-4fd0-8218-6401e7641de1?name=2026_04&download"
    assert _looks_like_download_href(href) is True


def test_looks_like_download_href_rejects_empty_and_placeholder():
    assert _looks_like_download_href(None) is False
    assert _looks_like_download_href("") is False
    assert _looks_like_download_href("   ") is False
    assert _looks_like_download_href("#") is False


def test_looks_like_download_href_rejects_javascript_void():
    """Many React/PrimeNG anchors render href='javascript:void(0)' for
    items that need a click handler. Those are not direct downloads.
    """
    assert _looks_like_download_href("javascript:void(0)") is False
    assert _looks_like_download_href("JavaScript:doSomething()") is False
    assert _looks_like_download_href("  javascript:void(0)  ") is False


# ---------------------------------------------------------------------------
# IncrementalScanTracker
# ---------------------------------------------------------------------------

def test_tracker_stops_after_threshold_in_window():
    tracker = IncrementalScanTracker(
        threshold=3, lookback_days=60, today=date(2026, 5, 8)
    )
    assert tracker.observe(card_date="2026-05-01") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-20") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2026-04-10") is True
    assert tracker.should_stop() is True


def test_tracker_stops_on_cutoff_even_below_threshold():
    """When scrolling reaches an old issue (e.g. 2025_12), stop early even if
    we've only seen one recent issue. This is what makes incremental cheap.
    """
    tracker = IncrementalScanTracker(
        threshold=10, lookback_days=60, today=date(2026, 5, 8)
    )
    assert tracker.observe(card_date="2026-04-20") is True
    assert tracker.should_stop() is False
    assert tracker.observe(card_date="2025-12-22") is False
    assert tracker.should_stop() is True


def test_tracker_default_constants_match_module_defaults():
    tracker = IncrementalScanTracker()
    assert tracker.threshold == DEFAULT_INCREMENTAL_THRESHOLD
    assert tracker.lookback_days == DEFAULT_LOOKBACK_DAYS
    assert tracker.recent_count == 0
    assert tracker.cutoff_reached is False


# ---------------------------------------------------------------------------
# CLI argv parsing — including --pdf-only / --cd-only mutex
# ---------------------------------------------------------------------------

def test_parse_argv_defaults_both_tracks():
    args = parse_argv([])
    assert args.full is False
    assert args.limit is None
    assert args.headless is True
    assert args.bulletins_root.name == CATEGORY_FOLDER_NAME
    assert args.tracks == {Track.CD, Track.PDF}
    assert args.no_legacy is False


def test_parse_argv_no_legacy_flag():
    args = parse_argv(["--no-legacy"])
    assert args.no_legacy is True


def test_parse_argv_pdf_only_restricts_tracks():
    args = parse_argv(["--pdf-only"])
    assert args.tracks == {Track.PDF}


def test_parse_argv_cd_only_restricts_tracks():
    args = parse_argv(["--cd-only"])
    assert args.tracks == {Track.CD}


def test_parse_argv_pdf_only_and_cd_only_are_mutex():
    with pytest.raises(SystemExit):
        parse_argv(["--pdf-only", "--cd-only"])


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
# CollectionCounters
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
        tracks={Track.PDF},
    )
    assert args.full is True
    assert args.limit == 5
    assert args.headless is False
    assert args.bulletins_root == Path("/tmp/x")
    assert args.tracks == {Track.PDF}


# ---------------------------------------------------------------------------
# Legacy multi-UUID href detection + splitting
# ---------------------------------------------------------------------------

# A real href captured from the live UI on 2026-05-08 for card 1996_6.
LEGACY_1996_6_HREF = (
    "https://webim.turkpatent.gov.tr/file/"
    "5a72e006-8735-4a80-8f8e-a55bca25c34d,00a60abf-0368-4db6-a652-6e573343b780,"
    "4d886d0b-d59f-4a52-8e33-aa1d99ed6f0f,cfa16ba7-4cdb-4b97-b2aa-ad0a964203f6,"
    "c363935e-b0f9-438f-97b1-066d48ce8293,751e4385-65f4-4f1c-9955-dbbd15d593ba,"
    "d273182d-0a6a-45ba-8b47-89b208b9d1a5,a60df88b-f4dc-4e11-9785-7f176707d823,"
    "efc337f2-6a95-4b34-9641-c7ff4baa21a1,4ed267b6-2227-4931-89b5-41e779b5ac38"
    "?name=1996_6&download"
)
SINGLE_UUID_HREF = (
    "https://webim.turkpatent.gov.tr/file/"
    "a383bb6a-a74f-4fd0-8218-6401e7641de1?name=2026_04&download"
)


def test_is_multi_uuid_href_detects_legacy_pattern():
    assert is_multi_uuid_href(LEGACY_1996_6_HREF) is True


def test_is_multi_uuid_href_rejects_single_uuid_url():
    """The normal monthly PDF anchors must NOT be classified as multi-part."""
    assert is_multi_uuid_href(SINGLE_UUID_HREF) is False


def test_is_multi_uuid_href_handles_none_and_empty():
    assert is_multi_uuid_href(None) is False
    assert is_multi_uuid_href("") is False
    assert is_multi_uuid_href("https://example.com/x") is False


def test_is_multi_uuid_href_ignores_commas_in_query_only():
    """A comma in the query string alone is not a multi-UUID path."""
    url = "https://webim.turkpatent.gov.tr/file/a383bb6a-a74f-4fd0-8218-6401e7641de1?name=a,b&download"
    assert is_multi_uuid_href(url) is False


def test_split_multi_uuid_href_expands_to_single_uuid_urls():
    parts = split_multi_uuid_href(LEGACY_1996_6_HREF)
    assert len(parts) == 10
    for url in parts:
        assert "?name=1996_6&download" in url
        assert url.count(",") == 0
        path = url.split("?", 1)[0]
        last = path.rsplit("/", 1)[-1]
        assert len(last) == 36
        assert last.count("-") == 4


def test_split_multi_uuid_href_preserves_order():
    parts = split_multi_uuid_href(LEGACY_1996_6_HREF)
    assert parts[0].rsplit("/", 1)[-1].split("?", 1)[0] == "5a72e006-8735-4a80-8f8e-a55bca25c34d"
    assert parts[-1].rsplit("/", 1)[-1].split("?", 1)[0] == "4ed267b6-2227-4931-89b5-41e779b5ac38"


def test_split_multi_uuid_href_handles_single_uuid_passthrough():
    """A single-UUID URL split yields exactly one URL identical to input."""
    parts = split_multi_uuid_href(SINGLE_UUID_HREF)
    assert parts == [SINGLE_UUID_HREF]


def test_split_multi_uuid_href_rejects_empty():
    with pytest.raises(ValueError):
        split_multi_uuid_href("")


# ---------------------------------------------------------------------------
# legacy_part_filename / existing_legacy_parts
# ---------------------------------------------------------------------------

def test_legacy_part_filename_pads_for_sort_order():
    # 10 parts -> 2-digit pad
    assert legacy_part_filename("1996_6", 1, 10) == "1996_6_legacy_part01.pdf"
    assert legacy_part_filename("1996_6", 10, 10) == "1996_6_legacy_part10.pdf"
    # 100 parts -> 3-digit pad
    assert legacy_part_filename("X", 7, 100) == "X_legacy_part007.pdf"
    # Tiny totals still get min 2-digit pad
    assert legacy_part_filename("Y", 1, 3) == "Y_legacy_part01.pdf"


def test_legacy_part_filename_validates_inputs():
    with pytest.raises(ValueError):
        legacy_part_filename("", 1, 10)
    with pytest.raises(ValueError):
        legacy_part_filename("X", 0, 10)
    with pytest.raises(ValueError):
        legacy_part_filename("X", 5, 4)
    with pytest.raises(ValueError):
        legacy_part_filename("X", 1, 0)


def test_existing_legacy_parts_finds_present_pieces():
    with temp_dir() as tmp:
        (tmp / "1996_6_legacy_part01.pdf").write_bytes(b"%PDF-1.4 part 1")
        (tmp / "1996_6_legacy_part02.pdf").write_bytes(b"%PDF-1.4 part 2")
        (tmp / "1996_6_legacy_part05.pdf").write_bytes(b"%PDF-1.4 part 5")
        (tmp / "1996_6.pdf").write_bytes(b"unrelated")  # NOT a legacy part
        (tmp / "2025_12_legacy_part01.pdf").write_bytes(b"different card")

        found = existing_legacy_parts(tmp, "1996_6")
        names = sorted(p.name for p in found)
        assert names == [
            "1996_6_legacy_part01.pdf",
            "1996_6_legacy_part02.pdf",
            "1996_6_legacy_part05.pdf",
        ]


def test_existing_legacy_parts_skips_zero_byte_files():
    with temp_dir() as tmp:
        (tmp / "1996_6_legacy_part01.pdf").write_bytes(b"")
        assert existing_legacy_parts(tmp, "1996_6") == []


def test_existing_legacy_parts_returns_empty_when_root_missing():
    with temp_dir() as tmp:
        ghost = tmp / "no_such_folder"
        assert existing_legacy_parts(ghost, "1996_6") == []
