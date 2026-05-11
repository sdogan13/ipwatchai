"""Browser smoke for patent UI locale coverage (EN / TR / AR).

Today's existing dashboard slice checks only the watchlist *panel
title* in TR + AR. This slice broadens locale coverage to the
patent-specific surfaces:

  1. Search subview's query-input placeholder text (en/tr/ar)
  2. Filter panel IPC label text (en/tr/ar) — UNIQUE patent UX
  3. Filter panel holder label text (en/tr/ar)
  4. Watch_type=holder radio label text (en/tr/ar)
  5. Arabic-only: ``html dir="rtl"`` must be set

Each locale switch goes through ``window.AppI18n.setLocale(lang)``
(same path the user's locale toggle uses). setLocale may navigate
the page on RTL switch, which destroys the in-flight evaluate's
execution context — wrapped in try/except as in the cografi
locale smoke.

Run directly:
    python tests/browser/test_patent_locales_browser.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.patent import (
    open_patent_search_subtab,
    open_patent_watchlist_subtab,
    patent_config_for_persona,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_locales_browser.py"
)


LOCALE_EXPECTATIONS = {
    "en": {
        "search_placeholder_substr": "Title, abstract",
        "ipc_label_substr":          "IPC",
        "holder_label_substr":       "Holder",
        "watch_type_holder_substr":  "Holder",
        "expected_dir":              "ltr",
    },
    "tr": {
        "search_placeholder_substr": "Başlık",
        "ipc_label_substr":          "IPC sınıfı",
        "holder_label_substr":       "Hak sahibi",
        "watch_type_holder_substr":  "Hak sahibi",
        "expected_dir":              "ltr",
    },
    "ar": {
        "search_placeholder_substr": "العنوان",
        "ipc_label_substr":          "تصنيف IPC",
        "holder_label_substr":       "المالك",
        "watch_type_holder_substr":  "المالك",
        "expected_dir":              "rtl",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_locale(page, lang: str) -> None:
    try:
        page.evaluate(
            f"() => window.AppI18n && window.AppI18n.setLocale && "
            f"window.AppI18n.setLocale('{lang}')"
        )
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    page.wait_for_timeout(800)


def _verify_search_placeholder(page, *, expected_substr: str) -> None:
    placeholder = page.locator("#patent-search-input").get_attribute("placeholder") or ""
    assert expected_substr in placeholder, (
        f"search placeholder doesn't contain {expected_substr!r}; "
        f"got {placeholder!r}"
    )


def _verify_filter_panel_labels(page, *, ipc_substr: str, holder_substr: str) -> None:
    """Open the patent filters panel + read the IPC and holder labels."""
    # Toggle if not open; idempotent — if already open, click just
    # collapses it. So check first.
    panel_visible = page.locator("#patent-search-ipc").is_visible()
    if not panel_visible:
        page.locator('[x-data*="patentFiltersOpen"] > button').first.click()
        page.wait_for_selector("#patent-search-ipc", state="visible", timeout=3000)
    # IPC label is the <label> just before #patent-search-ipc input
    ipc_label = page.evaluate(
        """() => {
            const inp = document.getElementById('patent-search-ipc');
            if (!inp) return '';
            const label = inp.previousElementSibling;
            return label ? label.innerText.trim() : '';
        }"""
    )
    assert ipc_substr in ipc_label, (
        f"IPC label doesn't contain {ipc_substr!r}; got {ipc_label!r}"
    )
    holder_label = page.evaluate(
        """() => {
            const inp = document.getElementById('patent-search-holder');
            if (!inp) return '';
            const label = inp.previousElementSibling;
            return label ? label.innerText.trim() : '';
        }"""
    )
    assert holder_substr in holder_label, (
        f"Holder label doesn't contain {holder_substr!r}; got {holder_label!r}"
    )


def _verify_watch_type_holder_label(page, *, expected_substr: str) -> None:
    """Open the patent watchlist add modal + assert the holder radio's
    label text. Close the modal after."""
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    radio = page.locator('input[name="pwl-watch-type"][value="holder"]').first
    block_text = radio.evaluate("el => el.closest('label').innerText").strip()
    assert expected_substr in block_text, (
        f"watch_type=holder label doesn't contain {expected_substr!r}; "
        f"got {block_text!r}"
    )
    page.locator("#pwl-add-close").click()
    page.wait_for_timeout(300)


def _verify_html_dir(page, *, expected: str) -> None:
    html_dir = page.evaluate(
        "() => document.documentElement.getAttribute('dir') || ''"
    )
    assert html_dir == expected, (
        f"html dir mismatch: expected {expected!r}, got {html_dir!r}"
    )


def _exercise_locale(page, lang: str) -> dict:
    exp = LOCALE_EXPECTATIONS[lang]
    _set_locale(page, lang)

    open_patent_search_subtab(page)
    _verify_search_placeholder(page, expected_substr=exp["search_placeholder_substr"])
    _verify_filter_panel_labels(
        page,
        ipc_substr=exp["ipc_label_substr"],
        holder_substr=exp["holder_label_substr"],
    )

    open_patent_watchlist_subtab(page)
    _verify_watch_type_holder_label(
        page, expected_substr=exp["watch_type_holder_substr"],
    )

    _verify_html_dir(page, expected=exp["expected_dir"])
    return {"locale": lang}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_locales_browser_smoke():
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as paid persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )

            for lang in ("en", "tr", "ar"):
                run_browser_step(
                    f"Locale={lang}: placeholder + IPC label + holder label + watch_type + dir",
                    REPORTER, page, monitor, CONFIG,
                    lambda lang=lang: _exercise_locale(page, lang),
                    allow_request_failures=_TRANSIENT_401S,
                )

            REPORTER.summary("Patent locales browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_patent_locales_browser_smoke()
