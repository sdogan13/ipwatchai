"""Browser smoke for the cografi free-tier watchlist-quota gate.

The free plan's max_watchlist_items value is stored in the
``subscription_plans`` DB table (not the hardcoded fallback in
utils/subscription.py — that file's value is stale; the live
backend reads from the DB). At the time this test was written
the value is **5**, shared across all four registries
(combined_watchlist_count: marka + design + patent + cografi).
A free-plan user CAN create up to 5 items; on the 6th attempt
the server returns HTTP 403 with a structured body::

    {"error": "limit_exceeded",
     "message": "İzleme listesi limitinize ulaştınız (3). ...",
     "current_count": 3, "max_items": 3, "plan_name": "free"}

This slice exercises that gate's UX:

  1. API pre-fill: create N cografi watchlist items where N equals
     the free plan's max_watchlist_items value (read live from
     /api/v1/usage/summary so this test stays correct even if the
     plan limit changes in the DB), bringing the free persona to
     exactly its cap.
  2. Open the watchlist tab and the add modal.
  3. Submit a 4th item.
  4. Server returns 403 (the gate fires).
  5. Modal stays open (so the user can read the error / cancel out
     rather than getting a silent fail).
  6. Inline error banner #cwl-add-error is visible + non-empty.
  7. Failed item did NOT leak into the list (idempotency of failure).
  8. API-side cleanup deletes all 4 BROWSER SMOKE items in
     ``finally`` so the test account is clean for the next run.

Run directly:
    python tests/browser/test_cografi_free_tier_gate_browser.py
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
    cleanup_smoke_items,
    cografi_config_for_persona,
    create_smoke_watch_via_api,
    open_cografi_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("free")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_free_tier_gate_browser.py"
)

# Free plan cap = 5 items (from the subscription_plans DB table; the
# hardcoded fallback in utils/subscription.py is stale at 3 — the DB
# value wins). If this changes in the DB, bump the constant + update
# the test description. The smoke is designed to break loudly on a
# silent plan-config drift rather than mask it.
FREE_TIER_CAP = 5
FREE_TIER_FILL_LABELS = [
    slice_label("freetier-fill", f"region{i}") for i in range(FREE_TIER_CAP)
]
FREE_TIER_OVER_LIMIT_LABEL = slice_label("freetier-over", "region")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _prefill_quota_to_cap() -> dict:
    """API-create FREE_TIER_CAP cografi watchlist items, bringing the
    free persona to its combined_watchlist_count cap. Failing here
    means the persona's plan is wrong (not free), or the cap value
    in the subscription_plans table changed — surface both clearly."""
    created_ids: list[str] = []
    for i, label in enumerate(FREE_TIER_FILL_LABELS):
        item_id = create_smoke_watch_via_api(
            CONFIG,
            label=label,
            watch_type="region",
            region_query=f"PrefillRegion{i}",
        )
        if not item_id:
            raise AssertionError(
                f"API pre-fill failed at item {i + 1}/{FREE_TIER_CAP} "
                f"(label {label!r}); cannot exercise the gate without "
                f"hitting the cap. Check that the persona is actually "
                f"on the free plan and that the DB's "
                f"subscription_plans.max_watchlist_items for free is "
                f"still {FREE_TIER_CAP}."
            )
        created_ids.append(item_id)
    return {"created_ids": created_ids, "count": len(created_ids)}


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _attempt_over_limit_create(page) -> dict:
    """Open the add modal as a free user already at cap, fill a 4th
    region watch, submit, and assert the 403 surfaces inline."""
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    page.locator('input[name="cwl-watch-type"][value="region"]').click()
    page.wait_for_selector("#cwl-region-fields", state="visible", timeout=3000)
    page.locator("#cwl-add-label").fill(FREE_TIER_OVER_LIMIT_LABEL)
    page.locator("#cwl-add-region-query").fill("Konya")

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/cografi-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#cwl-add-submit").click()
    response = resp_info.value

    if response.status == 200:
        raise AssertionError(
            "free persona over-limit create returned 200 — the quota "
            "gate did NOT fire even at the 4th item. Either the "
            "pre-fill didn't actually load the quota (check that the "
            "items live under this persona's organization) or "
            "max_watchlist_items for the free plan changed."
        )

    if response.status != 403:
        raise AssertionError(
            f"free persona over-limit create returned {response.status}; "
            f"expected 403 (quota_exceeded). "
            f"Body: {response.text()[:300]}"
        )

    # Modal must still be open for the user to see + dismiss the
    # error.
    assert page.locator("#cwl-add-modal").is_visible(), (
        "free persona over-limit modal auto-closed despite the 403; "
        "user has no signal that their submission failed"
    )

    err_el = page.locator("#cwl-add-error")
    assert err_el.is_visible(), (
        "free persona over-limit: inline error banner #cwl-add-error "
        "not visible after the 403 — UI is silent on failure"
    )
    err_text = err_el.inner_text().strip()
    assert err_text, (
        "free persona over-limit: #cwl-add-error visible but empty"
    )
    # Localized quota message contains "İzleme listesi" or "quota" or
    # "limit" — accept either the server's TR message OR the JS's
    # i18n fallback ("Cross-registry watchlist quota exceeded ...").
    lower = err_text.lower()
    quota_words = ("izleme listesi", "quota", "limit", "kota", "تجاوز")
    assert any(w in lower for w in quota_words), (
        f"free persona over-limit: error text doesn't look quota-"
        f"related; got {err_text!r}"
    )
    return {"status_code": response.status, "error_text": err_text[:120]}


def _close_add_modal(page) -> None:
    page.locator("#cwl-add-close").click()
    page.wait_for_timeout(300)


def _assert_no_over_limit_leakage(page) -> dict:
    """The pre-fill items should still be in the list (3 rows), but
    the failed over-limit attempt must NOT have leaked a 4th row."""
    # Refresh the tab so the JS rerenders from the latest API state.
    open_cografi_watchlist_subtab(page)
    page.wait_for_timeout(800)
    rows = page.locator("#cwl-list > div")
    total = rows.count()
    over_limit_leaked = 0
    fill_present = 0
    for i in range(total):
        text = rows.nth(i).inner_text()
        if FREE_TIER_OVER_LIMIT_LABEL in text:
            over_limit_leaked += 1
        for fl in FREE_TIER_FILL_LABELS:
            if fl in text:
                fill_present += 1
    assert over_limit_leaked == 0, (
        f"free persona over-limit create leaked into the list "
        f"({over_limit_leaked} rows match {FREE_TIER_OVER_LIMIT_LABEL!r}); "
        f"expected 0 (server returned 403)"
    )
    assert fill_present == FREE_TIER_CAP, (
        f"expected exactly {FREE_TIER_CAP} pre-fill rows visible after "
        f"the 403; got {fill_present}. The 403 should not have removed "
        f"the existing items."
    )
    return {
        "over_limit_leaked": over_limit_leaked,
        "fill_present": fill_present,
        "total_rows": total,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_free_tier_gate_browser_smoke():
    # Wipe any leftover smoke items from prior failed runs before
    # pre-filling, else current_count could already be >= 3 before
    # we begin and the pre-fill would itself 403.
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
                # login_via_modal retries internally on 429 but the
                # transient 429 console error fires before the retry.
                allow_console_errors=("status of 429",),
            )
            run_browser_step(
                "Open cografi watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Over-limit create returns 403 with localized quota error",
                REPORTER, page, monitor, CONFIG,
                lambda: _attempt_over_limit_create(page),
                # The 403 is the whole point — don't surface it as a
                # failure. The browser auto-logs "Failed to load
                # resource: ... status of 403" to console on every
                # 4xx, so we need to allow it through both buckets.
                allow_console_errors=("status of 403",),
                allow_request_failures=_TRANSIENT_401S + (
                    f"403 POST {CONFIG.base_url}/api/v1/cografi-watchlist",
                ),
            )
            run_browser_step(
                "Close the add modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_add_modal(page),
            )
            run_browser_step(
                f"List shows {FREE_TIER_CAP} pre-fill rows, no over-limit leakage",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_no_over_limit_leakage(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Cografi free-tier gate browser smoke")
        finally:
            context.close()
            browser.close()
            # Drop all 3 pre-fill items + any over-limit leak.
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_cografi_free_tier_gate_browser_smoke()
