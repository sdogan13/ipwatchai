import json
import os
import sys
from datetime import date

from scripts import run_live_repair_until_done as runner


def test_runner_exits_cleanly_on_safety_stop(monkeypatch, tmp_path):
    log_file = tmp_path / "live_repair.jsonl"
    conn = object()

    for key in runner.AGGRESSIVE_REPAIR_SAFETY_DEFAULTS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(runner, "get_connection", lambda: conn)
    monkeypatch.setattr(runner, "release_connection", lambda value: None)
    monkeypatch.setattr(runner, "close_pool", lambda: None)
    def unexpected_status_repair(**kwargs):
        raise AssertionError("status repair should not run after class safety stop")

    monkeypatch.setattr(runner, "run_live_status_repair", unexpected_status_repair)
    monkeypatch.setattr(
        runner,
        "run_live_class_repair",
        lambda **kwargs: {
            "checked": 0,
            "repaired": 0,
            "failed": 0,
            "safety_stopped": True,
            "safety_reason": "safety_rate_limited",
            "next_allowed_at": "2026-04-30T12:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_live_repair_until_done.py",
            "--log-file",
            str(log_file),
            "--empty-cycles-to-stop",
            "99",
        ],
    )

    assert runner.main() == 0

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    stop_event = events[-1]
    assert stop_event["event"] == "stop"
    assert stop_event["reason"] == "safety_stop"
    assert stop_event["safety_reason"] == "safety_rate_limited"
    assert stop_event["next_allowed_at"] == "2026-04-30T12:00:00+00:00"


def test_runner_runs_classes_first_and_applies_aggressive_profile(monkeypatch, tmp_path):
    log_file = tmp_path / "live_repair.jsonl"
    conn = object()
    order = []

    for key in runner.AGGRESSIVE_REPAIR_SAFETY_DEFAULTS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(runner, "get_connection", lambda: conn)
    monkeypatch.setattr(runner, "release_connection", lambda value: None)
    monkeypatch.setattr(runner, "close_pool", lambda: None)

    def class_repair(**kwargs):
        order.append(("classes", kwargs["include_older_than_11_years"]))
        assert os.environ["SCRAPER_SAFETY_MIN_INTERVAL_SECONDS"] == "5"
        assert os.environ["SCRAPER_SAFETY_HOURLY_BUDGET"] == "500"
        return {"checked": 1, "repaired": 0, "failed": 0, "safety_stopped": False}

    def status_repair(**kwargs):
        order.append(("status", kwargs["include_older_than_11_years"]))
        return {"checked": 1, "repaired": 0, "failed": 0, "safety_stopped": False}

    monkeypatch.setattr(runner, "run_live_class_repair", class_repair)
    monkeypatch.setattr(runner, "run_live_status_repair", status_repair)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_live_repair_until_done.py",
            "--log-file",
            str(log_file),
            "--max-checks",
            "1",
            "--include-older-than-11-years",
        ],
    )

    assert runner.main() == 0
    assert order == [("classes", True), ("status", True)]
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert events[0]["safety_settings"]["SCRAPER_SAFETY_DAILY_BUDGET"] == "8000"
    assert events[0]["include_older_than_11_years"] is True


def test_runner_freezes_status_cutoff_for_all_cycles(monkeypatch, tmp_path):
    log_file = tmp_path / "live_repair.jsonl"
    conn = object()
    status_cutoffs = []

    for key in runner.AGGRESSIVE_REPAIR_SAFETY_DEFAULTS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(runner, "get_connection", lambda: conn)
    monkeypatch.setattr(runner, "release_connection", lambda value: None)
    monkeypatch.setattr(runner, "close_pool", lambda: None)
    monkeypatch.setattr(
        runner,
        "run_live_class_repair",
        lambda **kwargs: {"checked": 0, "repaired": 0, "failed": 0, "safety_stopped": False},
    )

    def status_repair(**kwargs):
        status_cutoffs.append(kwargs["max_bulletin_date_exclusive"])
        return {"checked": 0, "repaired": 0, "failed": 0, "safety_stopped": False}

    monkeypatch.setattr(runner, "run_live_status_repair", status_repair)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_live_repair_until_done.py",
            "--log-file",
            str(log_file),
            "--status-max-bulletin-date",
            "2026-01-13",
        ],
    )

    assert runner.main() == 0
    assert status_cutoffs == [date(2026, 1, 13)]
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert events[0]["status_max_bulletin_date"] == "2026-01-13"


def test_runner_default_status_cutoff_uses_current_four_month_boundary():
    assert runner._default_status_max_bulletin_date(date(2026, 5, 13)) == date(2026, 1, 13)
