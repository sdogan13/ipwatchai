"""i18n parity test for patent_detail namespace."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_KEYS = (
    "loading", "error", "abstract", "no_abstract",
    "ipc_classes", "holders", "inventors", "attorneys",
    "priorities", "recent_events",
    "application_date", "publication_date", "grant_date", "bulletin",
    "source_format",
    "record_type_granted_patent", "record_type_granted_um",
    "record_type_published_app", "record_type_published_um_app",
    "record_type_ep_fascicle", "record_type_unknown", "record_type_legacy",
)


def _load(lang):
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_detail_block_present(lang):
    assert "patent_detail" in _load(lang), f"{lang}.json missing patent_detail namespace"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_detail_keys_complete(lang):
    block = _load(lang).get("patent_detail", {})
    missing = [k for k in EXPECTED_KEYS if k not in block]
    assert not missing, f"{lang}.json patent_detail missing keys: {missing}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_detail_values_non_empty(lang):
    block = _load(lang).get("patent_detail", {})
    empty = [k for k in EXPECTED_KEYS if k in block and not (block[k] or "").strip()]
    assert not empty, f"{lang}.json patent_detail has empty values: {empty}"
