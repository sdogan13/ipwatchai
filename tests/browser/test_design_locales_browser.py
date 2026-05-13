"""Browser smoke for design subview locale rendering (EN/TR/AR).

Slice 7 of the comprehensive design UI browser coverage.

This slice exercises the locale-switch path against the design
watchlist subview specifically (not the global header). It
verifies that:

  1. Switching ``window.AppI18n.setLocale('en'|'tr'|'ar')``
     updates the rendered design-namespace labels.
  2. The design watchlist subview's visible text reflects the
     active locale for at least two distinct keys:
       - ``design_watchlist.filter_sort``  (visible sort label)
       - ``design_watchlist.stat_total_items`` (stats card)
  3. When Arabic is active, the document direction switches to
     RTL: ``html[dir="rtl"]`` and ``html.rtl`` class is applied.

Expected text per locale (from static/locales/{en,tr,ar}.json
under the ``design_watchlist`` namespace):
  - filter_sort: EN "Sort:" | TR "Sırala:" | AR "الترتيب:"
  - stat_total_items: EN "Total" | TR "Toplam" | AR "الإجمالي"

Run directly:
    python tests/browser/test_design_locales_browser.py
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
from tests.browser.helpers.design import (
    design_config_for_persona,
    open_design_watchlist_subtab,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = design_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_locales_browser.py"
)


EXPECTED_FILTER_SORT = {
    "en": "Sort",          # Allows "Sort" or "Sort:"
    "tr": "Sırala",        # Allows "Sırala" or "Sırala:"
    "ar": "الترتيب",       # "order" in Arabic
}
EXPECTED_STAT_TOTAL = {
    "en": "Total",
    "tr": "Toplam",
    "ar": "الإجمالي",
}


# ---------------------------------------------------------------------------
# Locale switcher (drives the i18n API directly — locale-agnostic vs
# clicking a header dropdown whose markup may move)
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
    # Wait for the locale-changed event to have already propagated by
    # observing html[lang] reflect the new locale.
    page.wait_for_function(
        """(loc) => document.documentElement.getAttribute('lang') === loc""",
        arg=locale,
        timeout=5000,
    )
    # Alpine x-text re-renders synchronously on locale-changed; allow
    # one tick for re-render.
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

def _assert_subview_labels_for_locale(page, locale: str) -> dict:
    """Assert the design watchlist subview's two probe labels render
    in the target locale's expected text."""
    # 1. filter_sort label is in the sort bar near #dwl-sort-select
    sort_label = page.evaluate(
        """() => {
            const sel = document.getElementById('dwl-sort-select');
            if (!sel) return null;
            // The label is the immediately preceding <label> element
            const wrap = sel.parentElement;
            if (!wrap) return null;
            const label = wrap.querySelector('label');
            return label ? label.innerText : null;
        }"""
    )
    if sort_label is None:
        raise AssertionError(
            "could not find sort label next to #dwl-sort-select"
        )
    expected_sort = EXPECTED_FILTER_SORT[locale]
    if expected_sort not in sort_label:
        raise AssertionError(
            f"sort label for locale {locale!r} doesn't contain "
            f"{expected_sort!r}; got {sort_label!r}"
        )
    # 2. stat_total_items label is in the stats bar inside #dwl-stats-bar
    #    next to #dwl-stat-total
    stat_label = page.evaluate(
        """() => {
            const val = document.getElementById('dwl-stat-total');
            if (!val) return null;
            // The descriptive label is a sibling div within the stat card
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
            "could not find descriptive label next to #dwl-stat-total"
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
            f"html[dir] is still 'rtl' after switching to en"
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

def test_design_locales_browser_smoke():
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
                "Open design watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            # TR (default for this test environment per i18n init)
            run_browser_step(
                "Switch to Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "tr"),
            )
            run_browser_step(
                "TR design subview labels render Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_subview_labels_for_locale(page, "tr"),
            )

            # EN
            run_browser_step(
                "Switch to English",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "en"),
            )
            run_browser_step(
                "EN design subview labels render English + LTR direction",
                REPORTER, page, monitor, CONFIG,
                lambda: (
                    _assert_subview_labels_for_locale(page, "en"),
                    _assert_ltr_when_english(page),
                )[-1],
            )

            # AR
            run_browser_step(
                "Switch to Arabic",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale(page, "ar"),
            )
            run_browser_step(
                "AR design subview labels render Arabic",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_subview_labels_for_locale(page, "ar"),
            )
            run_browser_step(
                "AR switches html[dir]=rtl + html.rtl class",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_rtl_when_arabic(page),
            )

            REPORTER.summary("Design locales browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_design_locales_browser_smoke()
