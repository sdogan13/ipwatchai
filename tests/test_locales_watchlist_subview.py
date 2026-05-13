"""Locale-key test for the unified Watchlist tab's Marka↔Tasarım toggle.

The Watchlist tab merged the legacy standalone Tasarım Takibi tab into a
single panel with a segmented sub-view toggle. The toggle button labels
must be present in all three supported locales per the CLAUDE.md
Localization Rule.
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
@pytest.mark.parametrize("key", ["view_trademark", "view_design"])
def test_watchlist_subview_toggle_label_present(lang, key):
    """The two segmented-toggle labels (Marka / Tasarım) must exist
    non-empty in every supported locale."""
    block = _load_locale(lang)["watchlist"]
    assert key in block, f"{lang}.json missing watchlist.{key}"
    assert isinstance(block[key], str) and block[key].strip(), (
        f"{lang}.json watchlist.{key} is empty"
    )


def test_watchlist_subview_keys_consistent_across_languages():
    needles = {"view_trademark", "view_design"}
    found = {lang: needles & set(_load_locale(lang)["watchlist"].keys())
             for lang in SUPPORTED_LANGUAGES}
    assert all(s == needles for s in found.values()), (
        f"locale parity broken: {found}"
    )
