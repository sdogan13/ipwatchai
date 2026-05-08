"""i18n test — verify the ``registry`` block exists and is consistent across
all three supported locales (English, Turkish, Arabic).

Per CLAUDE.md's Localization Rule, every new user-facing string must be
present in en/tr/ar in the same task. This test guards that the
registry-discriminator labels (added when the `registry_type` column was
introduced on trademarks/designs) stay in sync.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_REGISTRY_KEYS = ("trademark", "design")


def _load_locale(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_locale_file_parses(lang):
    """Locale files must remain valid JSON."""
    data = _load_locale(lang)
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_registry_block_present(lang):
    data = _load_locale(lang)
    assert "registry" in data, f"{lang}.json missing 'registry' top-level key"
    assert isinstance(data["registry"], dict)


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("key", EXPECTED_REGISTRY_KEYS)
def test_registry_key_present_and_non_empty(lang, key):
    """Every supported language must define every expected registry key
    with a non-empty string value."""
    registry = _load_locale(lang)["registry"]
    assert key in registry, f"{lang}.json missing registry.{key}"
    value = registry[key]
    assert isinstance(value, str), f"{lang}.json registry.{key} must be a string, got {type(value)}"
    assert value.strip(), f"{lang}.json registry.{key} is empty"


def test_registry_keys_consistent_across_languages():
    """The set of registry keys must be identical across all locale files
    so a frontend ``t('registry.' + record.registry_type)`` lookup never
    returns a key-missing fallback in one language and a real value in another.
    """
    key_sets = {lang: set(_load_locale(lang)["registry"].keys()) for lang in SUPPORTED_LANGUAGES}
    reference = key_sets["en"]
    for lang, keys in key_sets.items():
        assert keys == reference, (
            f"{lang}.json registry keys diverge from en.json: "
            f"{lang}-only={keys - reference} en-only={reference - keys}"
        )


def test_arabic_registry_strings_use_arabic_script():
    """Sanity-check that the Arabic translations actually contain Arabic
    characters (catches a copy-paste mistake where en or tr text leaked
    into ar.json)."""
    registry = _load_locale("ar")["registry"]
    for key, value in registry.items():
        # Arabic Unicode block: U+0600..U+06FF
        has_arabic = any("؀" <= ch <= "ۿ" for ch in value)
        assert has_arabic, f"ar.json registry.{key} = {value!r} has no Arabic characters"
