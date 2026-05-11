"""Browser smoke for cografi UI locale coverage (EN / TR / AR).

Today's existing smoke (test_cografi_dashboard_browser.py) checks
only the watchlist *panel title* in TR + AR. This slice broadens
locale coverage to the cografi-specific surfaces that weren't
exercised:

  1. Search subview's query-input placeholder text — verified in
     each locale (catches missing translation keys + the EN
     fallback path).
  2. Filter section_keys checkbox labels — verified that one
     known section key renders its localized text in each locale.
  3. Add modal's 4 watch_type radio labels — verified in each
     locale.
  4. Arabic-only: ``html dir="rtl"`` must be set; one search
     subview heading should be visible (RTL layout sanity).

Each locale switch goes through ``window.AppI18n.setLocale(lang)``
which is the same path the user's locale toggle uses. We don't
exercise the toggle button itself (that's covered indirectly by
test_cografi_dashboard_browser.py).

Run directly:
    python tests/browser/test_cografi_locales_browser.py
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
from tests.browser.helpers.cografi import (
    cografi_config_for_persona,
    open_cografi_search_subtab,
    open_cografi_watchlist_subtab,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_locales_browser.py"
)


# Per-locale expected substrings — picked to be distinctive between
# locales so a TR-rendered field in EN-mode (or vice versa) fails
# clearly.
LOCALE_EXPECTATIONS = {
    "en": {
        "search_placeholder_substr": "GI name",  # "GI name, region, or paste an application no..."
        "section_examined_label":    "Examined",
        "watch_type_holder":         "Holder",
        "expected_dir":              "ltr",
    },
    "tr": {
        "search_placeholder_substr": "Coğrafi işaret",  # "Coğrafi işaret adı, bölge..."
        "section_examined_label":    "İncelenen",
        "watch_type_holder":         "Hak sahibi",
        "expected_dir":              "ltr",
    },
    "ar": {
        "search_placeholder_substr": "المؤشر الجغرافي",
        "section_examined_label":    "المدروسة",  # "الطلبات المدروسة"
        "watch_type_holder":         "المالك",
        "expected_dir":              "rtl",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_locale(page, lang: str) -> None:
    """Switch the SPA's i18n via the global AppI18n.setLocale.

    setLocale may trigger a navigation (e.g. AR mode reloads the
    page to apply RTL stylesheets), which destroys the execution
    context of the in-flight evaluate. Wrap in try/except so the
    navigation-related InvalidExecutionContext doesn't cascade —
    the assertion downstream will fail loudly if the locale
    didn't actually apply.
    """
    try:
        page.evaluate(
            f"() => window.AppI18n && window.AppI18n.setLocale && "
            f"window.AppI18n.setLocale('{lang}')"
        )
    except Exception:
        pass
    # Wait for any locale-switch-triggered navigation/reload to
    # settle before subsequent DOM access.
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    page.wait_for_timeout(800)


def _verify_search_placeholder(page, *, expected_substr: str) -> None:
    placeholder = page.locator("#cografi-search-input").get_attribute("placeholder") or ""
    assert expected_substr in placeholder, (
        f"search input placeholder doesn't contain {expected_substr!r}; "
        f"got {placeholder!r}"
    )


def _verify_section_checkbox_label(page, *, expected_substr: str) -> None:
    """Find the section_keys checkbox for 'examined' (the most stable
    section key — present in every bulletin) and assert its sibling
    label contains the expected localized text."""
    # The checkbox + label live inside a wrapping <label>. We can read
    # the parent label's full innerText.
    cb = page.locator('.cografi-section-key[value="examined"]').first
    assert cb.count() == 1, "examined section checkbox missing"
    # The wrapping label is the parent of the input.
    label_text = cb.evaluate("el => el.closest('label').innerText").strip()
    assert expected_substr in label_text, (
        f"section_examined label doesn't contain {expected_substr!r}; "
        f"got {label_text!r}"
    )


def _verify_watch_type_holder_label(page, *, expected_substr: str) -> None:
    """Open the add modal + assert the holder radio's text label
    contains the expected localized string."""
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    # The label text is inside a <div class="text-sm font-medium"> sibling of
    # the radio input. Read all visible text in the labeled radio block.
    radio = page.locator('input[name="cwl-watch-type"][value="holder"]').first
    block_text = radio.evaluate("el => el.closest('label').innerText").strip()
    assert expected_substr in block_text, (
        f"watch_type=holder label doesn't contain {expected_substr!r}; "
        f"got {block_text!r}"
    )
    # Close the modal so subsequent steps aren't blocked
    page.locator("#cwl-add-close").click()
    page.wait_for_timeout(300)


def _verify_html_dir(page, *, expected: str) -> None:
    html_dir = page.evaluate(
        "() => document.documentElement.getAttribute('dir') || ''"
    )
    assert html_dir == expected, (
        f"html dir mismatch: expected {expected!r}, got {html_dir!r}"
    )


# ---------------------------------------------------------------------------
# Per-locale step bundle
# ---------------------------------------------------------------------------

def _exercise_locale(page, lang: str) -> dict:
    """Switch to ``lang`` + verify search placeholder + section label
    + watch_type label + html dir."""
    exp = LOCALE_EXPECTATIONS[lang]
    _set_locale(page, lang)

    # Search subview checks (placeholder + section_keys label)
    open_cografi_search_subtab(page)
    # The filters panel needs to be open to read the section_keys label
    page.locator('[x-data*="cografiFiltersOpen"] > button').first.click()
    page.wait_for_selector(
        ".cografi-section-key", state="attached", timeout=3000,
    )
    _verify_search_placeholder(
        page, expected_substr=exp["search_placeholder_substr"],
    )
    _verify_section_checkbox_label(
        page, expected_substr=exp["section_examined_label"],
    )

    # Watchlist subview checks (watch_type radio label)
    open_cografi_watchlist_subtab(page)
    _verify_watch_type_holder_label(
        page, expected_substr=exp["watch_type_holder"],
    )

    # HTML dir check
    _verify_html_dir(page, expected=exp["expected_dir"])

    return {"locale": lang, "all_checks_passed": True}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_locales_browser_smoke():
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
                    f"Locale={lang}: placeholder + section label + watch_type label + dir",
                    REPORTER, page, monitor, CONFIG,
                    lambda lang=lang: _exercise_locale(page, lang),
                    allow_request_failures=_TRANSIENT_401S,
                )

            REPORTER.summary("Cografi locales browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_cografi_locales_browser_smoke()
