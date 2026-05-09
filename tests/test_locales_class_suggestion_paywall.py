"""Verify the locale keys added when the Marka/Tasarım class suggesters
were gated behind the ``monthly_ai_credits`` paywall are present in all three
supported locales (en/tr/ar) per the CLAUDE.md Localization Rule.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")


def _load_locale(lang: str) -> dict:
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize(
    "key",
    [
        "f_class_suggestions",          # cross row on the free pricing card
        "f_class_suggestions_n",        # paid rows with {n} months credit count
        "f_unlimited_class_suggestions",  # for plans with unlimited credits
    ],
)
def test_pricing_class_suggestion_key_present(lang, key):
    block = _load_locale(lang)["pricing"]
    assert key in block, f"{lang}.json missing pricing.{key}"
    assert isinstance(block[key], str) and block[key].strip()


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_search_class_suggestion_upgrade_key_present(lang):
    block = _load_locale(lang)["search"]
    assert "class_suggestion_upgrade_required" in block, (
        f"{lang}.json missing search.class_suggestion_upgrade_required"
    )
    assert block["class_suggestion_upgrade_required"].strip()


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize(
    "key",
    [
        # Tailored upgrade-modal copy for the class-suggestion paywall.
        # Consumed by static/js/utils/upgrade-modal.js CONTEXT_COPY entry
        # `class_suggestions` and rendered in the modal eyebrow/title/desc.
        "class_suggestions_eyebrow",
        "class_suggestions_title",
        "class_suggestions_description",
    ],
)
def test_upgrade_modal_class_suggestion_copy_present(lang, key):
    block = _load_locale(lang)["upgrade"]
    assert key in block, f"{lang}.json missing upgrade.{key}"
    assert isinstance(block[key], str) and block[key].strip()


def test_pricing_paywall_keys_consistent_across_languages():
    """Same key set in every locale — frontend t() lookups must never fall
    back to an English value while another locale is missing it."""
    needles = {"f_class_suggestions", "f_class_suggestions_n", "f_unlimited_class_suggestions"}
    sets = {lang: needles & set(_load_locale(lang)["pricing"].keys()) for lang in SUPPORTED_LANGUAGES}
    assert all(s == needles for s in sets.values()), f"divergent: {sets}"
