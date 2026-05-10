"""i18n parity test for patent_watchlist namespace."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_KEYS = (
    "panel_title", "panel_description",
    "stat_total", "stat_holder", "stat_reference", "stat_new_alerts",
    "section_items", "section_alerts",
    "scan_all_title", "add_button", "add_modal_title",
    "watch_type_label", "watch_type_holder", "watch_type_holder_hint",
    "watch_type_reference", "watch_type_reference_hint",
    "label_field", "label_placeholder",
    "holder_name_field", "holder_name_placeholder", "holder_name_hint",
    "reference_query_field", "reference_query_placeholder", "reference_query_hint",
    "ipc_field", "ipc_placeholder", "threshold_field",
    "cancel", "add_submit",
    "add_success", "delete_btn", "delete_success", "delete_failed",
    "scan_btn", "scan_done", "scan_failed", "scan_all_done", "alerts_created",
    "last_scan", "never_scanned", "empty_body", "alerts_empty",
    "alert_filter_all", "alert_filter_new", "alert_filter_seen",
    "alert_filter_ack", "alert_filter_resolved",
    "severity_critical", "severity_high", "severity_medium", "severity_low",
    "status_new", "status_seen", "status_acknowledged", "status_resolved", "status_dismissed",
    "action_ack", "action_resolve", "action_dismiss",
    "alert_action_failed",
    "confirm_delete", "confirm_scan_all",
    "error_label_required", "error_holder_required", "error_reference_required",
    "error_generic",
    "export_csv", "export_csv_title", "export_failed",
)


def _load(lang):
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_watchlist_block_present(lang):
    data = _load(lang)
    assert "patent_watchlist" in data, f"{lang}.json missing patent_watchlist namespace"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_watchlist_keys_complete(lang):
    block = _load(lang).get("patent_watchlist", {})
    missing = [k for k in EXPECTED_KEYS if k not in block]
    assert not missing, f"{lang}.json patent_watchlist missing keys: {missing}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_watchlist_values_non_empty(lang):
    block = _load(lang).get("patent_watchlist", {})
    empty = [k for k in EXPECTED_KEYS if k in block and not (block[k] or "").strip()]
    assert not empty, f"{lang}.json patent_watchlist has empty values: {empty}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_watchlist_view_patent_label(lang):
    data = _load(lang)
    label = data.get("watchlist", {}).get("view_patent")
    assert label and label.strip(), f"{lang}.json watchlist.view_patent missing/empty"
