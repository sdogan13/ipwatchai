import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest


def _load_real_scrapper(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_BULLETINS_ROOT", str(tmp_path / "bulletins" / "Marka"))
    module_name = f"_real_scrapper_{uuid.uuid4().hex}"
    module_path = Path(__file__).resolve().parents[1] / "scrapper.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _policy(module, state_path, **overrides):
    values = {
        "enabled": True,
        "state_path": state_path,
        "min_interval_seconds": 0.0,
        "jitter_min_seconds": 0.0,
        "jitter_max_seconds": 0.0,
        "hourly_budget": 50,
        "daily_budget": 500,
        "block_cooldown_seconds": 24 * 60 * 60,
        "max_wait_seconds": 120.0,
        "stale_lock_seconds": 120.0,
    }
    values.update(overrides)
    return module.ScraperSafetyPolicy(**values)


def test_safety_gate_allows_first_request(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    guard = module.ScraperSafetyGuard(
        _policy(module, state_path),
        now_fn=lambda: 1000.0,
        sleep_fn=lambda seconds: None,
    )

    event = guard.request_permission(operation="search", query="alpha")

    assert event["safety_stop"] is False
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["hour_count"] == 1
    assert state["day_count"] == 1


def test_safety_gate_waits_when_interval_is_small_enough(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    clock = {"now": 1000.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    guard = module.ScraperSafetyGuard(
        _policy(module, state_path, min_interval_seconds=10.0, max_wait_seconds=20.0),
        now_fn=lambda: clock["now"],
        sleep_fn=sleep,
    )

    assert guard.request_permission(operation="search", query="alpha")["safety_stop"] is False
    assert guard.request_permission(operation="search", query="beta")["safety_stop"] is False

    assert sleeps == [10.0]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["day_count"] == 2


def test_safety_gate_soft_stops_when_daily_budget_is_exhausted(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    guard = module.ScraperSafetyGuard(
        _policy(module, state_path, daily_budget=1),
        now_fn=lambda: 1000.0,
        sleep_fn=lambda seconds: None,
    )

    assert guard.request_permission(operation="search", query="alpha")["safety_stop"] is False
    event = guard.request_permission(operation="search", query="beta")

    assert event["safety_stop"] is True
    assert event["reason"] == "safety_rate_limited"
    assert event["next_allowed_at"]


def test_safety_gate_sets_cooldown_on_block_response(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    guard = module.ScraperSafetyGuard(
        _policy(module, state_path, block_cooldown_seconds=3600.0),
        now_fn=lambda: 1000.0,
        sleep_fn=lambda seconds: None,
    )

    event = guard.record_block(reason="http_429", operation="search", query="alpha")
    blocked = guard.request_permission(operation="search", query="beta")

    assert event["reason"] == "safety_blocked"
    assert blocked["safety_stop"] is True
    assert blocked["reason"] == "safety_blocked"


def test_safe_goto_sets_cooldown_on_http_429(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    scraper = module.TurkPatentScraper(
        headless=True,
        safety_policy=_policy(module, state_path),
    )

    class Response:
        status = 429

    class Page:
        def goto(self, url, wait_until):
            return Response()

    scraper.page = Page()

    with pytest.raises(module.ScraperSafetyStop):
        scraper._safe_goto("https://example.test", operation="search", query="alpha")

    blocked = scraper.safety.request_permission(operation="search", query="beta")
    assert blocked["safety_stop"] is True
    assert blocked["reason"] == "safety_blocked"


def test_blocking_text_detector_handles_captcha_and_verification(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)

    assert module._looks_like_blocking_content("Captcha guvenlik dogrulama gerekiyor")
    assert module._looks_like_blocking_content("Erisim engellendi")


def test_row_registration_no_reads_search_grid_tescil_no(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    row = ["1", "2021/160894", "ip", "IBIS INC.", "06.09.2021", "2021 160894", ""]

    assert module.TurkPatentScraper._row_registration_no(row) == "2021 160894"


def test_search_and_ingest_does_not_write_app_metadata_on_safety_stop(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    state_path = tmp_path / "safety" / "state.json"
    scraper = module.TurkPatentScraper(
        headless=True,
        safety_policy=_policy(module, state_path, daily_budget=0),
    )
    saved = []

    scraper.start_browser = lambda: setattr(scraper, "page", object())
    scraper.save_to_json = lambda rows: saved.append(rows)

    rows = scraper.search_and_ingest("alpha", limit=1, max_scroll_seconds=1)

    assert rows == []
    assert saved == []
    assert scraper.last_safety_event["reason"] == "safety_rate_limited"
    assert not scraper.active_metadata_file.exists()


def test_save_to_json_upserts_canonical_app_metadata_and_exposes_save_info(monkeypatch, tmp_path):
    module = _load_real_scrapper(monkeypatch, tmp_path)
    scraper = module.TurkPatentScraper(
        headless=True,
        safety_policy=_policy(module, tmp_path / "safety" / "state.json"),
    )

    rows = [
        ["1", "2024/001", "alpha", "Holder A", "01.01.2024", "", "", "09 / 42 /"],
        ["2", "2024/002", "beta", "Holder B", "02.01.2024", "2024 002", "", "35 /"],
    ]
    first = scraper.save_to_json(rows)

    assert first["folder_name"] == "APP_1"
    assert first["added_count"] == 2
    assert first["updated_count"] == 0
    assert Path(first["metadata_path"]).name == "metadata.json"

    updated_record = {
        "APPLICATIONNO": "2024/001",
        "STATUS": "tescil edildi",
        "TRADEMARK": {"NAME": "alpha", "REGISTERNO": "2024 001"},
        "text_embedding": [0.1, 0.2],
    }
    second = scraper.save_to_json([updated_record], target_file=first["metadata_path"])
    payload = json.loads(Path(first["metadata_path"]).read_text(encoding="utf-8"))

    assert second["added_count"] == 0
    assert second["updated_count"] == 1
    assert second["saved_application_numbers"] == ["2024/001"]
    assert len(payload) == 2
    alpha = next(item for item in payload if item["APPLICATIONNO"] == "2024/001")
    assert alpha["STATUS"] == "tescil edildi"
    assert alpha["TRADEMARK"]["REGISTERNO"] == "2024 001"
    assert alpha["TRADEMARK"]["NICECLASSES_LIST"] == ["09", "42"]
    assert alpha["text_embedding"] == [0.1, 0.2]
    assert scraper.last_save_info == second
