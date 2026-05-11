"""Browser smoke for the patent alert action lifecycle.

The PATCH ack/resolve/dismiss flow in patent_watchlist.js (lines
~394-402 — alertAction()) drives the per-alert action buttons. The
JS unit tests cover the PATCH payload shape but no test confirms
the UI buttons -> server -> persisted-state path actually works
end to end.

Patent's alert PATCH endpoint shape differs from cografi's:
  - Cografi: PATCH /api/v1/cografi-alerts/{id} body {status: "..."}
  - Patent: POST /api/v1/patent-alerts/{id}/{action} where action
    is the literal verb (acknowledge / resolve / dismiss). This is
    a separate endpoint per action, not a single PATCH.

Flow:
  1. Login as paid persona, open patent watchlist subtab.
  2. Create a holder watch on a common name (Arçelik) — should
     produce alerts against the live patent corpus.
  3. Scan via per-item button.
  4. For each of ack / resolve / dismiss, click the right button
     on a distinct alert and assert the POST returns 200 + a
     follow-up GET shows the new persisted status.
  5. Cleanup: delete the watch (cascades the alerts).

Run directly:
    python tests/browser/test_patent_alert_actions_browser.py
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
from tests.browser.helpers.patent import (
    cleanup_smoke_items,
    open_patent_watchlist_subtab,
    patent_config_for_persona,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("starter")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_alert_actions_browser.py"
)


ALERT_LABEL = slice_label("alertactions", "holder")
_actioned_alert_ids: list[str] = []


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _create_holder_watch(page) -> dict:
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    page.locator('input[name="pwl-watch-type"][value="holder"]').click()
    page.wait_for_selector("#pwl-holder-fields", state="visible", timeout=3000)
    page.locator("#pwl-add-label").fill(ALERT_LABEL)
    page.locator("#pwl-add-holder-name").fill("Arçelik A.Ş.")
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/patent-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#pwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"create returned {response.status}: {response.text()[:300]}"
        )
    page.wait_for_selector(
        f"#pwl-list h4:has-text({ALERT_LABEL!r})", timeout=15000,
    )
    return {"label": ALERT_LABEL}


def _scan_and_count_alerts(page) -> dict:
    rows = page.locator("#pwl-list > div")
    target = None
    for i in range(rows.count()):
        if ALERT_LABEL in rows.nth(i).inner_text():
            target = rows.nth(i)
            break
    assert target is not None, "round-trip row missing"
    scan_btn = target.locator("[data-pwl-scan]")
    with page.expect_response(
        lambda r: "/api/v1/patent-watchlist/" in r.url
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
    page.wait_for_timeout(1500)
    return {"alerts_created": int(body.get("alerts_created") or 0)}


# ---------------------------------------------------------------------------
# Action-button helpers
# ---------------------------------------------------------------------------

def _pick_unactioned_alert_id(page) -> str | None:
    """Find an alert row whose ack button is still rendered (= status
    not yet resolved/dismissed) and whose id we haven't yet actioned
    in this test run."""
    rows = page.locator("#pwl-alerts-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        ack_btn = row.locator("[data-pwl-alert-ack]")
        if ack_btn.count() == 0:
            continue
        alert_id = ack_btn.first.get_attribute("data-pwl-alert-ack")
        if alert_id and alert_id not in _actioned_alert_ids:
            return alert_id
    return None


def _click_action_on_specific_alert(page, *, alert_id: str, action: str) -> dict:
    """Click ack/resolve/dismiss on the alert with the given id +
    capture the POST response. Patent uses POST verb endpoints
    (different from cografi's PATCH-with-body shape). Verify the
    new status via a follow-up GET."""
    selector_attr = {
        "ack":     "data-pwl-alert-ack",
        "resolve": "data-pwl-alert-resolve",
        "dismiss": "data-pwl-alert-dismiss",
    }[action]
    btn = page.locator(f'[{selector_attr}="{alert_id}"]').first
    assert btn.count() == 1, (
        f"action button [{selector_attr}={alert_id}] not in DOM"
    )
    verb = {"ack": "acknowledge", "resolve": "resolve", "dismiss": "dismiss"}[action]
    with page.expect_response(
        lambda r: f"/api/v1/patent-alerts/{alert_id}/{verb}" in r.url
                  and r.request.method == "POST",
        timeout=10000,
    ) as resp_info:
        btn.click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /patent-alerts/{alert_id}/{verb} returned "
            f"{response.status}: {response.text()[:200]}"
        )
    expected = {
        "ack":     "acknowledged",
        "resolve": "resolved",
        "dismiss": "dismissed",
    }[action]

    page.wait_for_timeout(800)
    actual_status = page.evaluate(
        """async (alertId) => {
            const tok = (window.AppAuth && window.AppAuth.getAuthToken()) ||
                        localStorage.getItem('auth_token') ||
                        localStorage.getItem('access_token') || '';
            const r = await fetch('/api/v1/patent-alerts/' + alertId, {
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
    page.wait_for_timeout(1500)
    return {"alert_id": alert_id, "action": action, "status": actual_status}


def _do_action_on_next_alert(page, action: str) -> dict:
    alert_id = _pick_unactioned_alert_id(page)
    if alert_id is None:
        raise AssertionError(
            f"no fresh actionable alerts left for {action!r}"
        )
    return _click_action_on_specific_alert(page, alert_id=alert_id, action=action)


def _delete_round_trip_item(page) -> dict:
    rows = page.locator("#pwl-list > div")
    target = None
    for i in range(rows.count()):
        if ALERT_LABEL in rows.nth(i).inner_text():
            target = rows.nth(i)
            break
    if target is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = target.locator("[data-pwl-delete]")
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        rows = page.locator("#pwl-list > div")
        still = any(
            ALERT_LABEL in rows.nth(i).inner_text()
            for i in range(rows.count())
        )
        if not still:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError("delete timeout")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_alert_actions_browser_smoke():
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
                "Open patent watchlist subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )

            watch_created = False
            scan_alert_count = 0
            try:
                watch_created = run_browser_step(
                    "Create Arçelik holder watch",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_holder_watch(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if watch_created:
                    def _scan_and_capture():
                        nonlocal scan_alert_count
                        result = _scan_and_count_alerts(page)
                        scan_alert_count = result["alerts_created"]
                        if scan_alert_count == 0:
                            raise AssertionError(
                                "scan produced 0 alerts; live corpus may "
                                "have shifted away from Arçelik. Can't "
                                "exercise alert actions without alerts."
                            )
                        return result

                    if run_browser_step(
                        "Scan + ensure at least 1 alert",
                        REPORTER, page, monitor, CONFIG,
                        _scan_and_capture,
                        allow_request_failures=_TRANSIENT_401S,
                    ):
                        run_browser_step(
                            "Acknowledge alert #1 — POST 200 + status=acknowledged",
                            REPORTER, page, monitor, CONFIG,
                            lambda: _do_action_on_next_alert(page, "ack"),
                            allow_request_failures=_TRANSIENT_401S,
                        )
                        if scan_alert_count >= 2:
                            run_browser_step(
                                "Resolve alert #2 — POST 200 + status=resolved",
                                REPORTER, page, monitor, CONFIG,
                                lambda: _do_action_on_next_alert(page, "resolve"),
                                allow_request_failures=_TRANSIENT_401S,
                            )
                        if scan_alert_count >= 3:
                            run_browser_step(
                                "Dismiss alert #3 — POST 200 + status=dismissed",
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

            REPORTER.summary("Patent alert actions browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_patent_alert_actions_browser_smoke()
