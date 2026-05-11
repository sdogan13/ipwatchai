"""Browser smoke for trademark subview locale rendering (TR/EN/AR).

Drives ``window.AppI18n.setLocale()`` directly (locale-agnostic
vs depending on a header dropdown markup that may move) and
verifies the trademark watchlist subview labels render in each
target locale.

Probe labels (from static/locales/{en,tr,ar}.json under the
``watchlist`` namespace — the trademark subview uses
``t('watchlist.X')``):
  - filter_sort:        EN "Sort"      | TR "Sırala"      | AR "ترتيب"
  - stat_total_items:   EN "Total Items" | TR "Toplam Öğe" | AR "إجمالي العناصر"

For AR also asserts ``html[dir]="rtl"`` and ``html.rtl`` class
present (the i18n setLocale switches both).
For EN asserts LTR.

Run directly:
    python tests/browser/test_trademark_locales_browser.py
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
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.browser.helpers.trademark import (
    open_trademark_watchlist_subtab,
    trademark_config_for_persona,
    transient_401_budget,
)
from tests.live.helpers.assertions import LiveReporter


CONFIG = trademark_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_trademark_locales_browser.py"
)


EXPECTED_FILTER_SORT = {
    "en": "Sort",          # tolerates "Sort" or "Sort:"
    "tr": "Sırala",        # tolerates "Sırala" or "Sırala:"
    "ar": "ترتيب",         # Arabic for "order/sort"
}
EXPECTED_STAT_TOTAL = {
    "en": "Total Items",
    "tr": "Toplam Öğe",
    "ar": "إجمالي العناصر",
}


# ---------------------------------------------------------------------------
# Locale switcher
# ---------------------------------------------------------------------------

def _switch_locale(page, locale: str) -> None:
    page.evaluate(
        """async (loc) => {
            if (window.AppI18n && window.AppI18n.setLocale) {
                await window.AppI18n.setLocale(loc);
            }
        }""",
        locale,
    )
    page.wait_for_function(
        "(loc) => document.documentElement.getAttribute('lang') === loc",
        arg=locale,
        timeout=5000,
    )
    # Allow Alpine x-text bindings to re-render on the
    # locale-changed event.
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def _assert_subview_labels_for_locale(page, locale: str) -> dict:
    sort_label = page.evaluate(
        """() => {
            const sel = document.getElementById('wl-sort-select');
            if (!sel) return null;
            const wrap = sel.parentElement;
            if (!wrap) return null;
            const lab = wrap.querySelector('label');
            return lab ? lab.innerText : null;
        }"""
    )
    if sort_label is None:
        raise AssertionError(
            "could not find sort label next to #wl-sort-select"
        )
    expected_sort = EXPECTED_FILTER_SORT[locale]
    if expected_sort not in sort_label:
        raise AssertionError(
            f"sort label for locale {locale!r} doesn't contain "
            f"{expected_sort!r}; got {sort_label!r}"
        )
    stat_label = page.evaluate(
        """() => {
            const val = document.getElementById('wl-stat-total');
            if (!val) return null;
            const card = val.parentElement;
            if (!card) return null;
            const labelDivs = card.querySelectorAll('div');
            for (const d of labelDivs) {
                if (d === val) continue;
                const txt = d.innerText.trim();
                if (txt) return txt;
            }
            return null;
        }"""
    )
    if stat_label is None:
        raise AssertionError(
            "could not find descriptive label next to #wl-stat-total"
        )
    expected_stat = EXPECTED_STAT_TOTAL[locale]
    if expected_stat not in stat_label:
        raise AssertionError(
            f"stat_total label for locale {locale!r} doesn't contain "
            f"{expected_stat!r}; got {stat_label!r}"
        )
    return {
        "locale": locale,
        "sort_label": sort_label,
        "stat_label": stat_label,
    }


def _assert_rtl_when_arabic(page) -> dict:
    dir_attr = page.evaluate(
        "() => document.documentElement.getAttribute('dir')"
    )
    if dir_attr != "rtl":
        raise AssertionError(
            f"html[dir] is {dir_attr!r} after switching to ar; "
            f"expected 'rtl'"
        )
    has_rtl_class = page.evaluate(
        "() => document.documentElement.classList.contains('rtl')"
    )
    if not has_rtl_class:
        raise AssertionError(
            "html.rtl class missing after switching to Arabic"
        )
    return {"dir": dir_attr, "rtl_class_present": True}


def _assert_ltr_when_english(page) -> dict:
    dir_attr = page.evaluate(
        "() => document.documentElement.getAttribute('dir')"
    )
    if dir_attr == "rtl":
        raise AssertionError(
            "html[dir] is still 'rtl' after switching to en"
        )
    has_rtl_class = page.evaluate(
        "() => document.documentElement.classList.contains('rtl')"
    )
    if has_rtl_class:
        raise AssertionError(
            "html.rtl class still present after switching to English"
        )
    return {"dir": dir_attr or "ltr", "rtl_class_present": False}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_trademark_locales_browser_smoke():
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
            run_browser_step(
                "Open trademark watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_trademark_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            run_browser_step(
                "Switch to Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "tr"),
            )
            run_browser_step(
                "TR trademark subview labels render Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_subview_labels_for_locale(page, "tr"),
            )

            run_browser_step(
                "Switch to English",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "en"),
            )
            run_browser_step(
                "EN trademark subview labels render English + LTR",
                REPORTER, page, monitor, CONFIG,
                lambda: (
                    _assert_subview_labels_for_locale(page, "en"),
                    _assert_ltr_when_english(page),
                )[-1],
            )

            run_browser_step(
                "Switch to Arabic",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "ar"),
            )
            run_browser_step(
                "AR trademark subview labels render Arabic",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_subview_labels_for_locale(page, "ar"),
            )
            run_browser_step(
                "AR switches html[dir]=rtl + html.rtl class",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_rtl_when_arabic(page),
            )

            REPORTER.summary("Trademark locales browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_trademark_locales_browser_smoke()
