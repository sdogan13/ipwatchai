"""Browser smoke for the patent free-tier watchlist-quota gate.

The free plan's max_watchlist_items value is stored in the
``subscription_plans`` DB table (the hardcoded fallback in
utils/subscription.py is stale; the live backend reads from the
DB). At authoring time the value is **5**, shared across all four
registries (combined_watchlist_count: marka + design + patent +
cografi). A free-plan user CAN create up to 5 items; on the 6th
attempt the server returns HTTP 403 with::

    {"error": "limit_exceeded",
     "message": "Izleme listesi limitinize ulastiniz (5). ...",
     "current_count": 5, "max_items": 5, "plan_name": "free"}

Patent's gate uses ASCII-only Turkish in the message ("Izleme",
"yukseltin") whereas cografi's uses proper diacritics. Different
message text, same gate behavior.

Flow:
  1. API-side wipe of any leftover BROWSER SMOKE items.
  2. API pre-fill: create FREE_TIER_CAP patent watch items via
     POST /api/v1/patent-watchlist (uses cheap holder watches —
     no embedding round-trip server-side).
  3. Browser login + open Patent watchlist subtab.
  4. Open add modal, fill holder name, submit.
  5. Server returns 403 with structured limit_exceeded body.
  6. Modal stays open (user can read the error / cancel out).
  7. Inline #pwl-add-error banner visible + non-empty + contains
     a quota/limit word.
  8. Failed item does NOT leak into the list.
  9. API-side cleanup in `finally` deletes all 5 pre-fill items.

Run directly:
    python tests/browser/test_patent_free_tier_gate_browser.py
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
    cleanup_smoke_items,
    create_smoke_watch_via_api,
    open_patent_watchlist_subtab,
    patent_config_for_persona,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("free")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_free_tier_gate_browser.py"
)


# Free plan cap = 5 items (DB-stored max_watchlist_items). If this
# changes in the DB, bump the constant + update the test description.
FREE_TIER_CAP = 5
FREE_TIER_FILL_LABELS = [
    slice_label("freetier-fill", f"holder{i}") for i in range(FREE_TIER_CAP)
]
FREE_TIER_OVER_LIMIT_LABEL = slice_label("freetier-over", "holder")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _prefill_quota_to_cap() -> dict:
    """API-create FREE_TIER_CAP patent watchlist items, bringing the
    free persona to its combined_watchlist_count cap. Failing here
    means the persona's plan is wrong (not free) or the cap value
    changed — surface both clearly."""
    created_ids: list[str] = []
    for i, label in enumerate(FREE_TIER_FILL_LABELS):
        item_id = create_smoke_watch_via_api(
            CONFIG,
            label=label,
            watch_type="holder",
            holder_name=f"Prefill Holder {i}",
        )
        if not item_id:
            raise AssertionError(
                f"API pre-fill failed at item {i + 1}/{FREE_TIER_CAP} "
                f"(label {label!r}); cannot exercise the gate without "
                f"hitting the cap. Check that the persona is free and "
                f"that the DB's subscription_plans.max_watchlist_items "
                f"for free is still {FREE_TIER_CAP}."
            )
        created_ids.append(item_id)
    return {"created_ids": created_ids, "count": len(created_ids)}


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _attempt_over_limit_create(page) -> dict:
    """Open the patent watchlist add modal as a free user already at
    cap, fill a holder watch form, submit, and assert the 403
    surfaces inline."""
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    # Default watch_type=holder is pre-selected; fill the holder name
    # field directly (no need to switch radios).
    page.locator("#pwl-add-label").fill(FREE_TIER_OVER_LIMIT_LABEL)
    page.locator("#pwl-add-holder-name").fill("Over-limit Holder")

    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/patent-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#pwl-add-submit").click()
    response = resp_info.value

    if response.status == 200:
        raise AssertionError(
            "free persona over-limit create returned 200 — the quota "
            "gate did NOT fire even at item 6. Check that the pre-"
            "fill items live under THIS persona's organization or "
            "that max_watchlist_items for free hasn't been raised."
        )

    if response.status != 403:
        raise AssertionError(
            f"free persona over-limit create returned {response.status}; "
            f"expected 403 (quota_exceeded). Body: {response.text()[:300]}"
        )

    assert page.locator("#pwl-add-modal").is_visible(), (
        "free persona over-limit modal auto-closed despite the 403; "
        "user has no signal that their submission failed"
    )

    err_el = page.locator("#pwl-add-error")
    assert err_el.is_visible(), (
        "free persona over-limit: inline error banner #pwl-add-error "
        "not visible after the 403 — UI is silent on failure"
    )
    err_text = err_el.inner_text().strip()
    assert err_text, (
        "free persona over-limit: #pwl-add-error visible but empty"
    )
    # Patent's quota message is ASCII-Turkish ("Izleme listesi") whereas
    # cografi's uses diacritics ("İzleme listesi"). Match on case-
    # insensitive substring that covers both + EN/AR fallbacks.
    lower = err_text.lower()
    quota_words = ("izleme listesi", "quota", "limit", "kota", "تجاوز")
    assert any(w in lower for w in quota_words), (
        f"free persona over-limit: error text doesn't look quota-"
        f"related; got {err_text!r}"
    )
    return {"status_code": response.status, "error_text": err_text[:120]}


def _close_add_modal(page) -> None:
    page.locator("#pwl-add-close").click()
    page.wait_for_timeout(300)


def _assert_no_over_limit_leakage(page) -> dict:
    """Verify the failed 6th create did NOT leave a row in the list."""
    open_patent_watchlist_subtab(page)
    page.wait_for_timeout(800)
    rows = page.locator("#pwl-list > div")
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
        f"expected exactly {FREE_TIER_CAP} pre-fill rows visible "
        f"after the 403; got {fill_present}. The 403 should not have "
        f"removed the existing items."
    )
    return {
        "over_limit_leaked": over_limit_leaked,
        "fill_present": fill_present,
        "total_rows": total,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_free_tier_gate_browser_smoke():
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
                "Open patent watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Over-limit create returns 403 with localized quota error",
                REPORTER, page, monitor, CONFIG,
                lambda: _attempt_over_limit_create(page),
                allow_console_errors=("status of 403",),
                allow_request_failures=_TRANSIENT_401S + (
                    f"403 POST {CONFIG.base_url}/api/v1/patent-watchlist",
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

            REPORTER.summary("Patent free-tier gate browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_patent_free_tier_gate_browser_smoke()
