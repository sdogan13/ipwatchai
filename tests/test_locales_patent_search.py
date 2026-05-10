"""i18n test — verify the ``patent_search`` block is present and consistent
across all three supported locales (en/tr/ar).

Per CLAUDE.md's Localization Rule, every new user-facing string must be
present in en/tr/ar in the same task.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_PATENT_SEARCH_KEYS = (
    "panel_title", "query_placeholder",
    "ipc_label", "ipc_placeholder",
    "holder_label", "holder_placeholder",
    "date_from_label", "date_to_label",
    "kind_code_label", "kind_any",
    "kind_b", "kind_a1", "kind_u3", "kind_u1", "kind_t4",
    "submit",
    "results_title", "loading",
    "empty_title", "empty_body", "empty_query_status",
    "error_generic", "error_network", "quota_exceeded",
    "analysis_hint",
    "recent_searches", "clear_history",
    "untitled", "filed", "published",
)


def _load_locale(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_search_block_present(lang):
    data = _load_locale(lang)
    assert "patent_search" in data, f"{lang}.json missing patent_search namespace"
    assert isinstance(data["patent_search"], dict)


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_search_keys_complete(lang):
    data = _load_locale(lang)
    block = data.get("patent_search", {})
    missing = [k for k in EXPECTED_PATENT_SEARCH_KEYS if k not in block]
    assert not missing, f"{lang}.json patent_search missing keys: {missing}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_search_values_non_empty(lang):
    data = _load_locale(lang)
    block = data.get("patent_search", {})
    empty = [k for k in EXPECTED_PATENT_SEARCH_KEYS
             if k in block and not (block[k] or "").strip()]
    assert not empty, f"{lang}.json patent_search has empty values: {empty}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_tabs_patent_search_present(lang):
    data = _load_locale(lang)
    tabs = data.get("tabs", {})
    assert "patent_search" in tabs, f"{lang}.json tabs missing patent_search label"
    assert (tabs.get("patent_search") or "").strip(), f"{lang}.json tabs.patent_search empty"
