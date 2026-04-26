import asyncio
from datetime import date
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid
from unittest.mock import AsyncMock

from data_collection import (
    IncrementalScanTracker,
    build_download_plan,
    build_issue_download_stem,
    check_local_existence,
    get_clickable_download_href,
    normalize_card_metadata,
    process_card,
)


TEST_TEMP_ROOT = Path("C:/Users/701693/turk_patent/.tmp_pytest_data_collection")
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextmanager
def temp_dir():
    temp_path = TEST_TEMP_ROOT / f"tmp_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def test_normalize_card_metadata_reclassifies_impossible_gazette():
    meta = normalize_card_metadata(
        {"id": "269", "date": "2026-03-27", "is_gazette": True},
        min_gazette_issue_number=300,
    )

    assert meta == {"id": "269", "date": "2026-03-27", "is_gazette": False}


def test_check_local_existence_requires_metadata_and_events():
    with temp_dir() as tmp_path:
        category_folder = tmp_path / "Marka"
        category_folder.mkdir()

        (category_folder / "GZ_269_2026-03-27.pdf").write_bytes(b"pdf")
        assert (
            check_local_existence(
                str(category_folder),
                "269",
                is_gazette=True,
                card_date="2026-03-27",
                pdf_only=False,
            )
            is False
        )

        issue_folder = category_folder / "BLT_269_2026-03-27"
        issue_folder.mkdir()
        (issue_folder / "metadata.json").write_text("[]", encoding="utf-8")
        assert (
            check_local_existence(
                str(category_folder),
                "269",
                is_gazette=False,
                card_date="2026-03-27",
                pdf_only=False,
            )
            is False
        )

        (issue_folder / "events.json").write_text("[]", encoding="utf-8")
        assert (
            check_local_existence(
                str(category_folder),
                "269",
                is_gazette=False,
                card_date="2026-03-27",
                pdf_only=False,
            )
            is True
        )


def test_check_local_existence_supports_legacy_issue_folder_name():
    with temp_dir() as tmp_path:
        category_folder = tmp_path / "Marka"
        category_folder.mkdir()

        issue_folder = category_folder / "BLT_269"
        issue_folder.mkdir()
        (issue_folder / "metadata.json").write_text("[]", encoding="utf-8")
        (issue_folder / "events.json").write_text("[]", encoding="utf-8")

        assert (
            check_local_existence(
                str(category_folder),
                "269",
                is_gazette=False,
                card_date="2026-03-27",
                pdf_only=False,
            )
            is True
        )


def test_check_local_existence_pdf_only_uses_pdf_artifact():
    with temp_dir() as tmp_path:
        category_folder = tmp_path / "Marka"
        category_folder.mkdir()
        (category_folder / "GZ_300_2026-04-21.pdf").write_bytes(b"pdf")

        assert (
            check_local_existence(
                str(category_folder),
                "300",
                is_gazette=True,
                card_date="2026-04-21",
                pdf_only=True,
            )
            is True
        )


def test_check_local_existence_ignores_scraped_sidecar_only():
    with temp_dir() as tmp_path:
        category_folder = tmp_path / "Marka"
        category_folder.mkdir()

        issue_folder = category_folder / "BLT_490_2026-04-13"
        issue_folder.mkdir()
        (issue_folder / "scraped_metadata.json").write_text("[]", encoding="utf-8")

        assert (
            check_local_existence(
                str(category_folder),
                "490",
                is_gazette=False,
                card_date="2026-04-13",
                pdf_only=False,
            )
            is False
        )


def test_build_issue_download_stem_uses_canonical_prefixes():
    assert build_issue_download_stem("490", "2026-04-13", False) == "BLT_490_2026-04-13"
    assert build_issue_download_stem("500", "2026-03-31", True) == "GZ_500_2026-03-31"


def test_incremental_scan_tracker_stops_after_recent_threshold_or_cutoff():
    tracker = IncrementalScanTracker(
        threshold=5,
        lookback_days=60,
        today=date(2026, 4, 21),
    )

    for _ in range(5):
        assert tracker.observe(card_date="2026-04-20", is_gazette=False) is True

    assert tracker.should_stop() is False

    assert tracker.observe(card_date="2026-01-15", is_gazette=True) is False
    assert tracker.should_stop() is True


def test_build_download_plan_fetches_cd_and_pdf_when_available():
    items = [
        {"text": "CD Icerigi", "is_cd": True, "key": None, "href": "/cd"},
        {"text": "269", "is_cd": False, "key": "269", "href": "/pdf"},
    ]

    plan = build_download_plan(items, pdf_only=False)
    assert [(step["is_cd_file"], step["item"]["href"]) for step in plan] == [
        (True, "/cd"),
        (False, "/pdf"),
    ]

    pdf_only_plan = build_download_plan(items, pdf_only=True)
    assert [(step["is_cd_file"], step["item"]["href"]) for step in pdf_only_plan] == [
        (False, "/pdf"),
    ]


class _FakeClickable:
    def __init__(self, href=None, *, fallback_href=None):
        self._href = href
        self._fallback_href = fallback_href

    async def get_attribute(self, name):
        assert name == "href"
        return self._href

    async def evaluate(self, script):
        return self._fallback_href


class _FakePage:
    url = "https://www.turkpatent.gov.tr/bultenler"

    async def wait_for_timeout(self, ms):
        return None


def test_get_clickable_download_href_uses_anchor_or_fallback():
    direct = asyncio.run(get_clickable_download_href(_FakeClickable(href="/direct-download")))
    assert direct == "/direct-download"

    fallback = asyncio.run(
        get_clickable_download_href(_FakeClickable(href="javascript:void(0)", fallback_href="/fallback"))
    )
    assert fallback == "/fallback"


def test_process_card_prefers_direct_href_stream_over_menu(monkeypatch):
    page = _FakePage()
    clickable = _FakeClickable(href="/file/500?download")
    fake_stream = AsyncMock(return_value=True)
    calls = {"count": 0}

    def fake_has_matching(*args, **kwargs):
        calls["count"] += 1
        return calls["count"] >= 2

    async def fake_scrape(*args, **kwargs):
        return {"available": False, "created": 0, "attempted": False, "result": None}

    monkeypatch.setattr("data_collection.check_local_existence", lambda *args, **kwargs: False)
    monkeypatch.setattr("data_collection.has_matching_download_artifact", fake_has_matching)
    monkeypatch.setattr("data_collection.maybe_scrape_issue", fake_scrape)
    monkeypatch.setattr("data_collection.stream_download_with_browser_session", fake_stream)
    monkeypatch.setattr(
        "data_collection.open_download_menu",
        AsyncMock(side_effect=AssertionError("menu path should not be used for direct href cards")),
    )

    result = asyncio.run(
        process_card(
            page,
            object(),
            clickable,
            "C:/tmp/Marka",
            "500",
            "2026-03-31",
            True,
            pdf_only=False,
        )
    )

    assert result.downloaded_raw == 1
    assert result.download_failed == 0
    fake_stream.assert_awaited_once()
    assert fake_stream.await_args.args[2] == "https://www.turkpatent.gov.tr/file/500?download"


def test_process_card_marks_partial_when_scrape_fails_after_raw_download(monkeypatch):
    page = _FakePage()
    clickable = _FakeClickable(href="/file/490?download")
    fake_stream = AsyncMock(return_value=True)
    calls = {"count": 0}

    def fake_has_matching(*args, **kwargs):
        calls["count"] += 1
        return calls["count"] >= 2

    async def fake_scrape(*args, **kwargs):
        return {"available": False, "created": 0, "attempted": True, "result": {"status": "failed"}}

    monkeypatch.setattr("data_collection.check_local_existence", lambda *args, **kwargs: False)
    monkeypatch.setattr("data_collection.has_matching_download_artifact", fake_has_matching)
    monkeypatch.setattr("data_collection.maybe_scrape_issue", fake_scrape)
    monkeypatch.setattr("data_collection.stream_download_with_browser_session", fake_stream)
    monkeypatch.setattr(
        "data_collection.open_download_menu",
        AsyncMock(side_effect=AssertionError("menu path should not be used for direct href cards")),
    )

    result = asyncio.run(
        process_card(
            page,
            object(),
            clickable,
            "C:/tmp/Marka",
            "490",
            "2026-04-13",
            False,
            pdf_only=False,
        )
    )

    assert result.downloaded_raw == 1
    assert result.scrape_failed == 1
    assert result.partial_issues == 1
    assert result.retry_needed == 0


def test_process_card_marks_partial_when_scrape_only_succeeds(monkeypatch):
    page = _FakePage()
    clickable = _FakeClickable(href=None)

    monkeypatch.setattr("data_collection.check_local_existence", lambda *args, **kwargs: False)
    monkeypatch.setattr("data_collection.get_clickable_download_href", AsyncMock(return_value=None))
    monkeypatch.setattr("data_collection.open_download_menu", AsyncMock(return_value=None))
    monkeypatch.setattr("data_collection.has_matching_download_artifact", lambda *args, **kwargs: False)
    monkeypatch.setattr("data_collection.direct_click_download", AsyncMock(return_value=False))

    async def fake_scrape(*args, **kwargs):
        return {"available": True, "created": 1, "attempted": True, "result": {"status": "success"}}

    monkeypatch.setattr("data_collection.maybe_scrape_issue", fake_scrape)

    result = asyncio.run(
        process_card(
            page,
            object(),
            clickable,
            "C:/tmp/Marka",
            "500",
            "2026-03-31",
            True,
            pdf_only=False,
        )
    )

    assert result.download_failed == 1
    assert result.scraped == 1
    assert result.partial_issues == 1
    assert result.retry_needed == 0
