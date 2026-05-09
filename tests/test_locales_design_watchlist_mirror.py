"""Locale parity test for the Tasarım Takibi mirror of the Marka watchlist.

The Tasarım watchlist sub-view was rebuilt to mirror the Trademark watchlist
toolbar/stats/filters/edit-modal. Per CLAUDE.md's Localization Rule, every
new label must exist non-empty in en/tr/ar.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")

DESIGN_WATCHLIST_KEYS = (
    "monitoring_active",
    "filter_threshold",
    "filter_sort",
    "search_placeholder",
    "sort_newest",
    "sort_oldest",
    "sort_most_conflicts",
    "sort_name_az",
    "stat_total_items",
    "stat_threatened",
    "stat_critical",
    "stat_new_alerts",
    "scan_all",
    "scan_all_title",
    "scan_all_empty",
    "scan_all_confirm",
    "scan_all_in_progress",
    "scan_all_done",
    "bulk_upload",
    "delete_all_title",
    "delete_all_empty",
    "delete_all_confirm",
    "delete_all_done",
    "edit_button",
    "edit_title",
    "save_changes",
    "similarity_threshold",
    "threshold_50",
    "threshold_60",
    "threshold_70",
    "threshold_80",
    "threshold_90",
    "description",
    "alert_frequency_label",
    "freq_daily",
    "freq_weekly",
    "monitoring_scope",
    "scope_text",
    "scope_visual",
    # Phase 3 — bulk CSV upload
    "upload_title",
    "upload_description",
    "upload_download_template",
    "upload_choose_file",
    "upload_detect_columns",
    "upload_map_columns_desc",
    "upload_now",
    "upload_pick_file_first",
    "upload_product_name_required",
    "upload_row_count",
    "upload_result_summary",
    "upload_skipped_label",
    "upload_errors_label",
    "upload_limit_reached",
    "upload_field_customer_reg_no",
    "upload_field_priority",
    "upload_field_tags",
    "upload_field_alert_email",
)

COMMON_KEYS = ("coming_soon", "page_x_of_y")


def _load_locale(lang: str) -> dict:
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("key", DESIGN_WATCHLIST_KEYS)
def test_design_watchlist_key_present(lang, key):
    block = _load_locale(lang)["design_watchlist"]
    assert key in block, f"{lang}.json missing design_watchlist.{key}"
    value = block[key]
    assert isinstance(value, str) and value.strip(), (
        f"{lang}.json design_watchlist.{key} empty"
    )


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("key", COMMON_KEYS)
def test_common_helper_key_present(lang, key):
    block = _load_locale(lang)["common"]
    assert key in block, f"{lang}.json missing common.{key}"
    assert block[key].strip()


def test_design_watchlist_keys_consistent_across_languages():
    needles = set(DESIGN_WATCHLIST_KEYS)
    sets = {lang: needles & set(_load_locale(lang)["design_watchlist"].keys())
            for lang in SUPPORTED_LANGUAGES}
    assert all(s == needles for s in sets.values()), f"divergent: {sets}"
