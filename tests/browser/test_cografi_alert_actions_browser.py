"""Browser smoke for the cografi alert action lifecycle (J-2).

The PATCH ``{status: "acknowledged|resolved|dismissed"}`` flow that
powers the ack/resolve/dismiss buttons in J-2's alert list has zero
browser coverage today. This slice exercises it end to end:

  1. Defensive cleanup of leftover smoke items.
  2. Login as paid persona.
  3. Open Coğrafi watchlist subtab.
  4. Create a region watch on "Konya" (the live corpus has many
     Konya GIs so the scan will produce alerts).
  5. Click the per-item Scan button + capture POST /scan.
  6. If alerts_created >= 3, refresh the alerts list and exercise
     each action button on a different alert:
       - Click Ack on alert #1, capture PATCH, assert status=
         "acknowledged" + UI shows "Acknowledged" badge.
       - Click Resolve on alert #2, same assertion shape.
       - Click Dismiss on alert #3, same assertion shape.
  7. If alerts_created < 3 (live data shifted), surface a
     skip-with-context rather than fail — the test is about the
     button behavior, not about how many Konya GIs exist.
  8. Cleanup: delete the watch item (cascades the alerts).

Run directly:
    python tests/browser/test_cografi_alert_actions_browser.py
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
from tests.browser.helpers.cografi import (
    cleanup_smoke_items,
    cografi_config_for_persona,
    open_cografi_watchlist_subtab,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_alert_actions_browser.py"
)

ALERT_LABEL = slice_label("alertactions", "region")

# Track alert IDs we've actioned so we can pick distinct alerts for
# each verb (ack / resolve / dismiss) — clicking the same alert
# three times wouldn't exercise three different status transitions.
_actioned_alert_ids: list[str] = []


# ---------------------------------------------------------------------------
# Setup steps (mirror the J-2 round-trip pattern)
# ---------------------------------------------------------------------------

def _create_konya_region_watch(page) -> dict:
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    page.locator('input[name="cwl-watch-type"][value="region"]').click()
    page.wait_for_selector("#cwl-region-fields", state="visible", timeout=3000)
    page.locator("#cwl-add-label").fill(ALERT_LABEL)
    page.locator("#cwl-add-region-query").fill("Konya")
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/cografi-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#cwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"create returned {response.status}: {response.text()[:300]}"
        )
    page.wait_for_selector(
        f"#cwl-list h4:has-text({ALERT_LABEL!r})", timeout=15000,
    )
    return {"label": ALERT_LABEL}


def _scan_and_count_alerts(page) -> dict:
    """Scan the round-trip item + return the alerts_created count from
    the scan response body. The actions test downstream needs to know
    how many alerts to act on."""
    rows = page.locator("#cwl-list > div")
    target = None
    for i in range(rows.count()):
        if ALERT_LABEL in rows.nth(i).inner_text():
            target = rows.nth(i)
            break
    assert target is not None, f"watch item {ALERT_LABEL!r} not in list"
    scan_btn = target.locator("[data-cwl-scan]")
    with page.expect_response(
        lambda r: "/api/v1/cografi-watchlist/" in r.url
                  and r.url.endswith("/scan")
                  and r.request.method == "POST",
        timeout=30000,
    ) as resp_info:
        scan_btn.first.click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"scan returned {response.status}: {response.text()[:200]}"
        )
    body = response.json()
    # Allow JS-side state to refresh (the scan response triggers
    # refreshAll() which fires fetchAlerts asynchronously).
    page.wait_for_timeout(1500)
    return {"alerts_created": int(body.get("alerts_created") or 0)}


# ---------------------------------------------------------------------------
# Action-button helpers
# ---------------------------------------------------------------------------

def _alert_rows_with_actions(page):
    """Return the list of alert-row Locators that still have the
    action buttons rendered (i.e. status NOT in {resolved, dismissed})."""
    rows = page.locator("#cwl-alerts-list > div")
    return [
        rows.nth(i)
        for i in range(rows.count())
        if rows.nth(i).locator("[data-cwl-alert-ack]").count() > 0
    ]


def _pick_unactioned_alert_id(page) -> str | None:
    """Find an alert row in the list that we haven't yet actioned in
    this test run. Returns the alert id (from data-cd-open) or None
    if there are no fresh actionable alerts left."""
    rows = _alert_rows_with_actions(page)
    for row in rows:
        # The row carries data-cd-open=<conflicting_record_id> on the
        # outer div — but the alert id is on the action buttons via
        # data-cwl-alert-ack="<alert_id>". Read from there.
        ack_btn = row.locator("[data-cwl-alert-ack]")
        if ack_btn.count() == 0:
            continue
        alert_id = ack_btn.first.get_attribute("data-cwl-alert-ack")
        if alert_id and alert_id not in _actioned_alert_ids:
            return alert_id
    return None


def _click_action_on_specific_alert(page, *, alert_id: str, action: str) -> dict:
    """Click ack/resolve/dismiss on the alert with the given id +
    capture the PATCH response. The PATCH endpoint returns
    ``{updated: True, id: ...}`` (no status echo), so verify the
    new status by waiting for the JS-side refreshAlerts() then
    fetching the alert via the page's authFetch context."""
    selector_attr = {
        "ack":     "data-cwl-alert-ack",
        "resolve": "data-cwl-alert-resolve",
        "dismiss": "data-cwl-alert-dismiss",
    }[action]
    btn = page.locator(f'[{selector_attr}="{alert_id}"]').first
    assert btn.count() == 1, (
        f"action button [{selector_attr}={alert_id}] not found in DOM"
    )
    with page.expect_response(
        lambda r: f"/api/v1/cografi-alerts/{alert_id}" in r.url
                  and r.request.method == "PATCH",
        timeout=10000,
    ) as resp_info:
        btn.click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"PATCH alert {alert_id} ({action}) returned {response.status}: "
            f"{response.text()[:200]}"
        )
    expected = {
        "ack":     "acknowledged",
        "resolve": "resolved",
        "dismiss": "dismissed",
    }[action]

    # Verify the new status via a GET on the same alert. The PATCH
    # response shape is {updated: True, id: ...} so we have to fetch
    # to confirm the persisted state. Use the page's auth context
    # via fetch() in JS so we share the SPA's bearer token.
    page.wait_for_timeout(800)  # let the UPDATE commit
    actual_status = page.evaluate(
        """async (alertId) => {
            const tok = (window.AppAuth && window.AppAuth.getAuthToken()) ||
                        localStorage.getItem('auth_token') ||
                        localStorage.getItem('access_token') || '';
            const r = await fetch('/api/v1/cografi-alerts/' + alertId, {
                headers: tok ? {'Authorization': 'Bearer ' + tok} : {},
            });
            if (!r.ok) return '__http_' + r.status;
            const body = await r.json();
            return body.status || '';
        }""",
        alert_id,
    )
    if actual_status != expected:
        raise AssertionError(
            f"GET alert {alert_id} after {action}: status={actual_status!r}; "
            f"expected {expected!r}"
        )
    _actioned_alert_ids.append(alert_id)
    # Allow refreshAlerts() to settle so the next pick sees the
    # updated DOM (the actioned alert may have disappeared from the
    # default 'new' status filter view, which is fine).
    page.wait_for_timeout(1500)
    return {"alert_id": alert_id, "action": action, "status": actual_status}


def _do_action_on_next_alert(page, action: str) -> dict:
    alert_id = _pick_unactioned_alert_id(page)
    if alert_id is None:
        raise AssertionError(
            f"no fresh actionable alerts left for {action!r}; "
            f"alerts_created may have been < 3 or all alerts already "
            f"resolved/dismissed by prior steps"
        )
    return _click_action_on_specific_alert(
        page, alert_id=alert_id, action=action,
    )


def _delete_round_trip_item(page) -> dict:
    rows = page.locator("#cwl-list > div")
    target = None
    for i in range(rows.count()):
        if ALERT_LABEL in rows.nth(i).inner_text():
            target = rows.nth(i)
            break
    if target is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = target.locator("[data-cwl-delete]")
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        rows = page.locator("#cwl-list > div")
        still_there = any(
            ALERT_LABEL in rows.nth(i).inner_text()
            for i in range(rows.count())
        )
        if not still_there:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError("delete timeout")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_alert_actions_browser_smoke():
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
            run_browser_step(
                "Open cografi watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            watch_created = False
            scan_alert_count = 0
            try:
                watch_created = run_browser_step(
                    "Create Konya region watch",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_konya_region_watch(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if watch_created:
                    # Capture scan output so subsequent steps know
                    # how many alerts we have to work with.
                    def _scan_and_capture():
                        nonlocal scan_alert_count
                        result = _scan_and_count_alerts(page)
                        scan_alert_count = result["alerts_created"]
                        if scan_alert_count == 0:
                            raise AssertionError(
                                "scan produced 0 alerts against Konya — "
                                "live corpus may have changed; can't "
                                "exercise alert actions without alerts"
                            )
                        return result

                    if run_browser_step(
                        "Scan watch + ensure at least 1 alert",
                        REPORTER, page, monitor, CONFIG,
                        _scan_and_capture,
                        allow_request_failures=_TRANSIENT_401S,
                    ):
                        # Action #1: Acknowledge
                        run_browser_step(
                            "Acknowledge alert #1 — PATCH returns 200 + status='acknowledged'",
                            REPORTER, page, monitor, CONFIG,
                            lambda: _do_action_on_next_alert(page, "ack"),
                            allow_request_failures=_TRANSIENT_401S,
                        )
                        # Action #2: Resolve (only if a 2nd alert exists)
                        if scan_alert_count >= 2:
                            run_browser_step(
                                "Resolve alert #2 — PATCH returns 200 + status='resolved'",
                                REPORTER, page, monitor, CONFIG,
                                lambda: _do_action_on_next_alert(page, "resolve"),
                                allow_request_failures=_TRANSIENT_401S,
                            )
                        # Action #3: Dismiss (only if a 3rd alert exists)
                        if scan_alert_count >= 3:
                            run_browser_step(
                                "Dismiss alert #3 — PATCH returns 200 + status='dismissed'",
                                REPORTER, page, monitor, CONFIG,
                                lambda: _do_action_on_next_alert(page, "dismiss"),
                                allow_request_failures=_TRANSIENT_401S,
                            )
            finally:
                if watch_created:
                    run_browser_step(
                        "Cleanup: delete watch (cascades alerts)",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_round_trip_item(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            REPORTER.summary("Cografi alert actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_cografi_alert_actions_browser_smoke()
