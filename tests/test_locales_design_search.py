"""i18n test — verify the ``design_search`` and ``registry`` blocks are
present and consistent across all three supported locales (en/tr/ar).

Per CLAUDE.md's Localization Rule, every new user-facing string must be
present in en/tr/ar in the same task.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_DESIGN_SEARCH_KEYS = (
    "panel_title", "query_placeholder",
    "locarno_label", "locarno_placeholder", "locarno_hint",
    "upload_label", "upload_hint", "image_clear",
    "submit", "reset",
    "results_title", "loading", "empty_title", "empty_body", "no_image",
    "appno_label", "holder_label",
    "error_empty", "error_generic", "error_network",
    "error_quota", "error_auth", "error_image_too_large", "error_invalid_input",
    # Polished form additions: drag-drop image zone, optional badge,
    # visual-analysis subtitle, remove button, analysis-types hint line
    "drop_image", "optional", "visual_analysis", "remove_image", "analysis_hint",
    # Locarno class picker: collapsed-bar prompt, count chip, expanded panel
    # header, AI suggest input/button + loading/error/quota messages
    "locarno_finder_toggle", "locarno_classes_selected", "locarno_browse_all",
    "locarno_ai_placeholder", "locarno_ai_button", "locarno_ai_loading",
    "locarno_ai_error", "locarno_ai_no_credits",
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
def test_design_search_block_present(lang):
    data = _load_locale(lang)
    assert "design_search" in data, f"{lang}.json missing 'design_search' block"
    assert isinstance(data["design_search"], dict)


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("key", EXPECTED_DESIGN_SEARCH_KEYS)
def test_design_search_key_present_and_non_empty(lang, key):
    block = _load_locale(lang)["design_search"]
    assert key in block, f"{lang}.json missing design_search.{key}"
    value = block[key]
    assert isinstance(value, str), f"{lang}.json design_search.{key} must be a string"
    assert value.strip(), f"{lang}.json design_search.{key} is empty"


def test_design_search_keys_consistent_across_languages():
    """Same key set in every locale — frontend t() lookups never fall back
    in one language but hit a real value in another."""
    key_sets = {lang: set(_load_locale(lang)["design_search"].keys())
                for lang in SUPPORTED_LANGUAGES}
    reference = key_sets["en"]
    for lang, keys in key_sets.items():
        assert keys == reference, (
            f"{lang}.json design_search keys diverge from en.json: "
            f"{lang}-only={keys - reference} en-only={reference - keys}"
        )


def test_arabic_strings_use_arabic_script():
    block = _load_locale("ar")["design_search"]
    for key, value in block.items():
        has_arabic = any("؀" <= ch <= "ۿ" for ch in value)
        assert has_arabic, f"ar.json design_search.{key} = {value!r} has no Arabic characters"


def test_turkish_strings_use_turkish_diacritics_or_ascii():
    """Sanity: at least the panel title and submit label should be Turkish-distinct
    from the English values (catches accidental copy-paste from en.json)."""
    en_block = _load_locale("en")["design_search"]
    tr_block = _load_locale("tr")["design_search"]
    for key in ("panel_title", "submit"):
        assert tr_block[key] != en_block[key], (
            f"tr.json design_search.{key} ({tr_block[key]!r}) is identical to en.json "
            f"({en_block[key]!r}) — likely an untranslated copy"
        )
