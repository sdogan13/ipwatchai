"""i18n parity test for patent_leads namespace."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


LOCALES_DIR = Path(__file__).resolve().parent.parent / "static" / "locales"
SUPPORTED_LANGUAGES = ("en", "tr", "ar")
EXPECTED_KEYS = (
    "mode_patent", "title", "subtitle",
    "category_label",
    "cat_lapse", "cat_transfer", "cat_license", "cat_rejected",
    "scope_watchlist", "holder_placeholder", "refresh",
    "loading", "empty", "error", "upgrade_required",
    "bulletin_date", "total_found",
    "event_grant_fee_lapse", "event_application_fee_lapse",
    "event_application_lapsed_or_rejected", "event_application_rejected",
    "event_assignment_recorded", "event_license_offer",
)


def _load(lang):
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_leads_block_present(lang):
    assert "patent_leads" in _load(lang), f"{lang}.json missing patent_leads namespace"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_leads_keys_complete(lang):
    block = _load(lang).get("patent_leads", {})
    missing = [k for k in EXPECTED_KEYS if k not in block]
    assert not missing, f"{lang}.json patent_leads missing keys: {missing}"


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_patent_leads_values_non_empty(lang):
    block = _load(lang).get("patent_leads", {})
    empty = [k for k in EXPECTED_KEYS if k in block and not (block[k] or "").strip()]
    assert not empty, f"{lang}.json patent_leads has empty values: {empty}"
