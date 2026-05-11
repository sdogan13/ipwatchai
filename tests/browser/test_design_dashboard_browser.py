"""Browser smoke for the design (Tasarım) dashboard tabs.

Foundational slice for the design UI coverage suite. Covers the
end-to-end happy path: search subview + result-card render +
watchlist subview + add form + per-item scan + per-item delete +
EN/TR/AR locale switching with RTL on AR.

**Supersedes** ``test_design_search_browser.py`` and
``test_design_watchlist_browser.py`` (both deleted in the same
commit). Those two pre-existing tests used a stale
``run_browser_step(REPORTER, name, action)`` signature that
mismatches the current ``run_browser_step(name, reporter, page,
monitor, config, action)`` shape — they would AssertionError at
runtime if invoked. Coverage in this slice is strictly broader.

Mirrors the patent + cografi foundational smokes but adapted for
design's specifics:
  - No detail modal — design results render inline; clicking a
    card doesn't open a modal (unlike patent/cografi)
  - No watch_type concept — design watches are identified by
    product_name + locarno_classes
  - Stats bar = 4 cells (total / threatened / critical / new_
    alerts) — different metrics from patent's (total / holder /
    reference / new_alerts)
  - Per-item buttons use ``data-action="<verb>" data-item-id=
    "<uuid>"`` pattern (not patent/cografi's ``data-pwl-scan=
    "<uuid>"``)
  - No CSV-export-of-alerts button in the UI (design has a CSV
    TEMPLATE download but no per-alert export — out of scope here)

Run directly:
    python tests/browser/test_design_dashboard_browser.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.design import (
    cleanup_smoke_items,
    design_config_for_persona,
    open_design_search_subtab,
    open_design_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


# Foundational dashboard slice fires a text search. Use managed-
# professional (2000/day search quota) rather than starter (50/day)
# — starter exhausts under cross-registry dev runs.
CONFIG = design_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_dashboard_browser.py"
)


ROUND_TRIP_NAME = slice_label("dashboard", "design")


# ---------------------------------------------------------------------------
# Search subview steps
# ---------------------------------------------------------------------------

def _run_search_via_enter(page) -> dict:
    """Pick a stable common term — 'Lamba' (lamp) appears in many
    Locarno 26-class designs."""
    page.locator("#design-search-input").click()
    page.locator("#design-search-input").fill("Lamba")
    page.wait_for_timeout(200)
    page.locator("#design-search-input").press("Enter")
    page.wait_for_selector(
        "#design-search-grid > article, #design-search-empty:not(.hidden)",
        timeout=45000,
    )
    grid = page.locator("#design-search-grid > article")
    return {"card_count": grid.count()}


def _assert_at_least_one_result_card_renders(page) -> dict:
    """Design search result cards render as <article> children of
    #design-search-grid. Verify ≥1 + non-empty content. There's no
    detail modal — clicking a card doesn't open a separate view,
    so we just assert the card renders with text."""
    cards = page.locator("#design-search-grid > article")
    count = cards.count()
    assert count >= 1, f"expected ≥1 result card; got {count}"
    first_text = cards.first.inner_text().strip()
    assert first_text, "first result card is empty"
    return {"card_count": count, "first_card_first_120": first_text[:120]}


# ---------------------------------------------------------------------------
# Watchlist subview steps
# ---------------------------------------------------------------------------

def _verify_4_cell_stats_bar(page) -> dict:
    stat_cells = page.locator("#dwl-stats-bar > div").count()
    assert stat_cells == 4, (
        f"design watchlist stats bar should have 4 cells (total + "
        f"threatened + critical + new_alerts); got {stat_cells}"
    )
    return {"stat_cells": stat_cells}


def _open_add_form_and_create(page) -> dict:
    """Click the add-toggle, fill product_name + locarno_classes,
    submit, and verify the new item appears in the rendered list."""
    page.locator("#design-watchlist-add-toggle").click()
    page.wait_for_selector(
        "#design-watchlist-add-card", state="visible", timeout=5000,
    )
    page.locator("#design-watchlist-product-name").fill(ROUND_TRIP_NAME)
    page.locator("#design-watchlist-locarno").fill("06-01")
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/design-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#design-watchlist-submit").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /design-watchlist returned {response.status}: "
            f"{response.text()[:300]}"
        )
    body = response.json()
    item_id = body.get("id")
    if not item_id:
        raise AssertionError(
            f"POST /design-watchlist 200 but body has no id: {body}"
        )
    # Wait for the JS to re-render the list with the new item.
    page.wait_for_function(
        f"() => document.body.innerText.includes({ROUND_TRIP_NAME!r})",
        timeout=10000,
    )
    return {"product_name": ROUND_TRIP_NAME, "item_id": item_id}


def _find_per_item_button(page, *, action: str, item_id: str):
    """Per-item action buttons use ``data-action="<verb>"
    data-item-id="<uuid>"`` selectors."""
    return page.locator(
        f'button[data-action="{action}"][data-item-id="{item_id}"]'
    )


def _scan_round_trip_item(page, *, item_id: str) -> dict:
    btn = _find_per_item_button(page, action="scan-now", item_id=item_id)
    if btn.count() == 0:
        raise AssertionError(
            f"per-item scan button missing for item_id={item_id} — "
            f"the list may not have re-rendered with the new row"
        )
    with page.expect_response(
        lambda r: (
            "/api/v1/design-watchlist/" in r.url
            and r.url.endswith("/scan")
            and r.request.method == "POST"
        ),
        timeout=30000,
    ) as resp_info:
        btn.first.click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /scan returned {response.status}: "
            f"{response.text()[:200]}"
        )
    page.wait_for_timeout(1500)
    return {"item_id": item_id, "status": response.status}


def _delete_round_trip_item(page, *, item_id: str) -> dict:
    btn = _find_per_item_button(page, action="delete", item_id=item_id)
    if btn.count() == 0:
        return {"deleted": False, "reason": "delete button not found"}
    page.once("dialog", lambda d: d.accept())
    btn.first.click()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        still_visible = page.evaluate(
            f"""() => document.body.innerText.includes({ROUND_TRIP_NAME!r})"""
        )
        if not still_visible:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError("delete timeout — row didn't disappear")


# ---------------------------------------------------------------------------
# Locale switching
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


def _switch_locale_and_assert_panel_title(
    page,
    *,
    lang: str,
    expected_title_substr: str,
    expected_dir: str,
) -> dict:
    _set_locale(page, lang)
    open_design_watchlist_subtab(page)
    page.wait_for_timeout(500)
    # The design watchlist panel title is rendered via
    # x-text="t('design_watchlist.panel_title')" in the subview;
    # locate the element by its localized text content.
    title_count = page.evaluate(
        f"""() => {{
            const root = document.getElementById('tab-content-watchlist');
            if (!root) return 0;
            const headings = Array.from(
                root.querySelectorAll('h1, h2, h3')
            );
            return headings.filter(h => h.innerText.includes(
                {expected_title_substr!r}
            )).length;
        }}"""
    )
    assert title_count >= 1, (
        f"locale={lang}: panel title with substring "
        f"{expected_title_substr!r} not rendered"
    )
    html_dir = page.evaluate(
        "() => document.documentElement.getAttribute('dir') || ''"
    )
    assert html_dir == expected_dir, (
        f"locale={lang}: expected html dir={expected_dir!r}, "
        f"got {html_dir!r}"
    )
    return {"locale": lang, "html_dir": html_dir}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_dashboard_browser_smoke():
    cleanup_smoke_items(CONFIG)

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

            # --- Search subview ----------------------------------------
            run_browser_step(
                "Open design search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_search_subtab(page),
            )
            run_browser_step(
                "Run search 'Lamba' via Enter key",
                REPORTER, page, monitor, CONFIG,
                lambda: _run_search_via_enter(page),
            )
            run_browser_step(
                "At least one result card renders with text",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_at_least_one_result_card_renders(page),
            )

            # --- Watchlist subview -------------------------------------
            run_browser_step(
                "Open design watchlist subtab + 4-cell stats bar",
                REPORTER, page, monitor, CONFIG,
                lambda: (open_design_watchlist_subtab(page),
                         _verify_4_cell_stats_bar(page))[1],
                allow_request_failures=_TRANSIENT_401S,
            )

            # --- Round-trip lifecycle ----------------------------------
            round_trip_info: dict = {}
            try:
                if run_browser_step(
                    "Open add form + create design watch via UI",
                    REPORTER, page, monitor, CONFIG,
                    lambda: round_trip_info.update(
                        _open_add_form_and_create(page)
                    ) or {"created": True, **round_trip_info},
                    allow_request_failures=_TRANSIENT_401S,
                ):
                    item_id = round_trip_info.get("item_id")
                    if item_id:
                        run_browser_step(
                            "Click per-item Scan button (POST returns 200)",
                            REPORTER, page, monitor, CONFIG,
                            lambda: _scan_round_trip_item(page, item_id=item_id),
                            allow_request_failures=_TRANSIENT_401S,
                        )
            finally:
                item_id = round_trip_info.get("item_id")
                if item_id:
                    run_browser_step(
                        "Cleanup: delete round-trip item via UI",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_round_trip_item(page, item_id=item_id),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            # --- Locale switching --------------------------------------
            run_browser_step(
                "Switch to TR locale + watchlist panel title in Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Tasarım Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to AR locale + RTL + Arabic panel title",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="ar",
                    expected_title_substr="متابعة التصاميم",
                    expected_dir="rtl",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch back to TR locale (cleanup)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Tasarım Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Design dashboard browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_design_dashboard_browser_smoke()
