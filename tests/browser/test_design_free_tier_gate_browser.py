"""Browser smoke for the design free-tier watchlist-quota gate.

Same 5-item cross-registry cap that patent + cografi enforce. Free
plan users can create up to 5 watchlist items across all 4
registries; on the 6th create the server returns 403 with::

    {"error": "limit_exceeded",
     "message": "Izleme listesi limitinize ulastiniz (5). ...",
     "current_count": 5, "max_items": 5, "plan_name": "free"}

Design's error message uses ASCII-only Turkish (matches patent;
cografi's uses proper diacritics — different code paths, same
gate behavior).

Flow:
  1. API-side wipe of leftover BROWSER SMOKE items.
  2. API pre-fill 5 design watch items (product_name +
     locarno_classes=["06-01"]).
  3. Browser login + open Design watchlist subtab.
  4. Expand the add form, fill product_name + locarno, submit.
  5. Server returns 403 with structured limit_exceeded body.
  6. Form stays open + #design-watchlist-error banner visible +
     non-empty + contains a quota/limit word.
  7. Failed item does NOT leak into the list.
  8. API-side cleanup in `finally` deletes all pre-fill items.

Run directly:
    python tests/browser/test_design_free_tier_gate_browser.py
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
    cleanup_smoke_items,
    create_smoke_watch_via_api,
    design_config_for_persona,
    open_design_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = design_config_for_persona("free")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_design_free_tier_gate_browser.py"
)


FREE_TIER_CAP = 5
FREE_TIER_FILL_NAMES = [
    slice_label("freetier-fill", f"item{i}") for i in range(FREE_TIER_CAP)
]
FREE_TIER_OVER_LIMIT_NAME = slice_label("freetier-over", "item")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _prefill_quota_to_cap() -> dict:
    created_ids: list[str] = []
    for i, name in enumerate(FREE_TIER_FILL_NAMES):
        item_id = create_smoke_watch_via_api(
            CONFIG,
            product_name=name,
            locarno_classes=["06-01"],
        )
        if not item_id:
            raise AssertionError(
                f"API pre-fill failed at item {i + 1}/{FREE_TIER_CAP} "
                f"(product_name {name!r}); cannot exercise the gate. "
                f"Check that the persona is free and that the DB's "
                f"subscription_plans.max_watchlist_items for free is "
                f"still {FREE_TIER_CAP}."
            )
        created_ids.append(item_id)
    return {"created_ids": created_ids, "count": len(created_ids)}


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _attempt_over_limit_create(page) -> dict:
    """Open the add form in the design watchlist subview, fill the
    product_name + locarno_classes, submit, and assert the 403
    surfaces inline."""
    # The add form is collapsed by default; click the toggle to expand
    # it. The toggle id is #design-watchlist-add-toggle (per template).
    page.locator("#design-watchlist-add-toggle").click()
    page.wait_for_selector(
        "#design-watchlist-add-card", state="visible", timeout=5000,
    )
    page.locator("#design-watchlist-product-name").fill(FREE_TIER_OVER_LIMIT_NAME)
    page.locator("#design-watchlist-locarno").fill("06-01")

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/design-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#design-watchlist-submit").click()
    response = resp_info.value

    if response.status == 200:
        raise AssertionError(
            "free persona over-limit create returned 200 — the quota "
            "gate did NOT fire even at item 6. Verify that the pre-"
            "fill items live under this persona's organization or "
            "that max_watchlist_items for free is still 5."
        )

    if response.status != 403:
        raise AssertionError(
            f"free persona over-limit create returned {response.status}; "
            f"expected 403. Body: {response.text()[:300]}"
        )

    # The error banner is #design-watchlist-error (per template).
    err_el = page.locator("#design-watchlist-error")
    assert err_el.is_visible(), (
        "free persona over-limit: inline error banner "
        "#design-watchlist-error not visible after the 403 — UI is "
        "silent on failure"
    )
    err_text = err_el.inner_text().strip()
    assert err_text, (
        "free persona over-limit: #design-watchlist-error visible but empty"
    )
    lower = err_text.lower()
    quota_words = ("izleme listesi", "quota", "limit", "kota", "تجاوز")
    assert any(w in lower for w in quota_words), (
        f"free persona over-limit: error text doesn't look quota-"
        f"related; got {err_text!r}"
    )
    return {"status_code": response.status, "error_text": err_text[:120]}


def _close_add_form(page) -> None:
    page.locator("#design-watchlist-cancel").click()
    page.wait_for_timeout(300)


def _assert_no_over_limit_leakage(page) -> dict:
    """Verify the failed create did NOT leave a row in the rendered
    list, and that the 5 pre-fill rows are still present."""
    open_design_watchlist_subtab(page)
    page.wait_for_timeout(1000)
    # Design watchlist list rows render as cards inside the page,
    # typically with the product_name in a heading or text node.
    # Use page.locator on the visible text to count.
    over_limit_leaked = page.evaluate(
        f"""() => {{
            const search = {FREE_TIER_OVER_LIMIT_NAME!r};
            return Array.from(document.querySelectorAll('*'))
                .filter(el => el.textContent && el.textContent.includes(search))
                .filter(el => el.children.length === 0)
                .length;
        }}"""
    )
    assert over_limit_leaked == 0, (
        f"free persona over-limit create leaked into the DOM "
        f"({over_limit_leaked} matches for {FREE_TIER_OVER_LIMIT_NAME!r}); "
        f"expected 0 (server returned 403)"
    )
    fill_present = 0
    for fname in FREE_TIER_FILL_NAMES:
        count = page.evaluate(
            f"""() => {{
                const search = {fname!r};
                return Array.from(document.querySelectorAll('*'))
                    .filter(el => el.textContent && el.textContent.includes(search))
                    .filter(el => el.children.length === 0)
                    .length;
            }}"""
        )
        if count > 0:
            fill_present += 1
    assert fill_present == FREE_TIER_CAP, (
        f"expected exactly {FREE_TIER_CAP} pre-fill items visible in "
        f"the DOM after the 403; got {fill_present}"
    )
    return {
        "over_limit_leaked": over_limit_leaked,
        "fill_present": fill_present,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_design_free_tier_gate_browser_smoke():
    cleanup_smoke_items(CONFIG)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                f"Pre-fill quota to {FREE_TIER_CAP}-item cap via API",
                REPORTER, page, monitor, CONFIG,
                lambda: _prefill_quota_to_cap(),
            )
            run_browser_step(
                "Login as free persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open design watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_design_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Over-limit create returns 403 with localized quota error",
                REPORTER, page, monitor, CONFIG,
                lambda: _attempt_over_limit_create(page),
                allow_console_errors=("status of 403",),
                allow_request_failures=_TRANSIENT_401S + (
                    f"403 POST {CONFIG.base_url}/api/v1/design-watchlist",
                ),
            )
            run_browser_step(
                "Close the add form", REPORTER, page, monitor, CONFIG,
                lambda: _close_add_form(page),
            )
            run_browser_step(
                f"List shows {FREE_TIER_CAP} pre-fill rows, no over-limit leakage",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_no_over_limit_leakage(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Design free-tier gate browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_design_free_tier_gate_browser_smoke()
