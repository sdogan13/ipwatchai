"""i18n test — verify the ``design_watchlist`` block and the
``tabs.design_watchlist`` label are present and consistent across en/tr/ar.

Per CLAUDE.md's Localization Rule, every new user-facing string must be
present in en/tr/ar in the same task.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_DESIGN_WATCHLIST_KEYS = (
    "panel_title", "panel_description",
    "add_button", "add_form_title",
    "product_name_label", "product_name_placeholder",
    "locarno_label", "locarno_placeholder", "locarno_hint",
    "customer_app_no_label", "customer_app_no_placeholder",
    "submit", "cancel",
    "list_title", "loading",
    "empty_title", "empty_body",
    "last_scan_label", "no_image",
    "alerts_count_label",
    "view_alerts_button", "upload_image_button", "scan_now_button", "delete_button",
    "delete_confirm",
    "alerts_empty_title",
    "alert_severity_low", "alert_severity_medium", "alert_severity_high", "alert_severity_critical",
    "alert_status_new", "alert_status_seen", "alert_status_acknowledged",
    "alert_status_resolved", "alert_status_dismissed",
    "alert_action_acknowledge", "alert_action_resolve", "alert_action_dismiss",
    "scan_queued",
    "error_quota", "error_auth", "error_network", "error_generic",
    "error_image_too_large", "error_invalid_input",
)


def _load_locale(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_locale_file_parses(lang):
    data = _load_locale(lang)
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_design_watchlist_block_present(lang):
    data = _load_locale(lang)
    assert "design_watchlist" in data, f"{lang}.json missing 'design_watchlist' block"
    assert isinstance(data["design_watchlist"], dict)


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_tabs_design_watchlist_label_present(lang):
    data = _load_locale(lang)
    tabs = data.get("tabs") or {}
    assert "design_watchlist" in tabs, f"{lang}.json missing tabs.design_watchlist label"
    assert isinstance(tabs["design_watchlist"], str) and tabs["design_watchlist"].strip()


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("key", EXPECTED_DESIGN_WATCHLIST_KEYS)
def test_design_watchlist_key_present_and_non_empty(lang, key):
    block = _load_locale(lang)["design_watchlist"]
    assert key in block, f"{lang}.json missing design_watchlist.{key}"
    value = block[key]
    assert isinstance(value, str), f"{lang}.json design_watchlist.{key} must be a string"
    assert value.strip(), f"{lang}.json design_watchlist.{key} is empty"


def test_design_watchlist_keys_consistent_across_languages():
    """Same key set in every locale — frontend t() lookups never fall back
    in one language but hit a real value in another."""
    key_sets = {lang: set(_load_locale(lang)["design_watchlist"].keys())
                for lang in SUPPORTED_LANGUAGES}
    reference = key_sets["en"]
    for lang, keys in key_sets.items():
        assert keys == reference, (
            f"{lang}.json design_watchlist keys diverge from en.json: "
            f"{lang}-only={keys - reference} en-only={reference - keys}"
        )


def test_arabic_strings_use_arabic_script():
    block = _load_locale("ar")["design_watchlist"]
    for key, value in block.items():
        has_arabic = any("؀" <= ch <= "ۿ" for ch in value)
        assert has_arabic, f"ar.json design_watchlist.{key} = {value!r} has no Arabic characters"


def test_turkish_strings_distinct_from_english():
    """Sanity: at least the panel title and submit label should be Turkish-distinct
    from the English values (catches accidental copy-paste from en.json)."""
    en_block = _load_locale("en")["design_watchlist"]
    tr_block = _load_locale("tr")["design_watchlist"]
    for key in ("panel_title", "submit", "delete_button"):
        assert tr_block[key] != en_block[key], (
            f"tr.json design_watchlist.{key} ({tr_block[key]!r}) is identical to en.json "
            f"({en_block[key]!r}) — likely an untranslated copy"
        )
