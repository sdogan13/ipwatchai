"""
Browser journeys for member alert actions.

Run directly:
    python tests/browser/test_alerts_browser.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from database.crud import Database, UserCRUD
from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.cleanup import cleanup_watchlist_items_by_prefix
from tests.live.helpers.personas import PersonaSession, resolve_free_persona_session


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False

WATCHLIST_PREFIX = "BROWSER ALERT WL"
CONFLICT_PREFIX = "BROWSER ALERT TM"
STATUS_PENDING = "\u0042\u0061\u015fvuruldu"
STATUS_REGISTERED = "Tescil Edildi"
STATUS_PUBLISHED = "\u0059\u0061\u0079\u0131nda"

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_alerts_browser.py"
)


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="alerts browser free user")
    return FREE_SESSION


def _ensure_email_verified(session: PersonaSession) -> None:
    if not session.user_id:
        return

    try:
        with Database() as db:
            user = UserCRUD.get_by_id(db, session.user_id)
            if not user or user.get("is_email_verified"):
                return

            UserCRUD.verify_email(db, session.user_id)
            cur = db.cursor()
            cur.execute(
                "UPDATE email_verification_tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                (str(session.user_id),),
            )
            db.commit()
            REPORTER.info(f"{session.label} email verification -> marked verified for browser setup")
    except Exception as exc:
        REPORTER.warn(f"{session.label} email verification -> setup failed ({exc})")


def _cleanup_seeded_alerts(session: PersonaSession) -> None:
    if not session.organization_id:
        return

    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                DELETE FROM alerts_mt a
                USING watchlist_mt w
                WHERE a.watchlist_item_id = w.id
                  AND w.organization_id = %s
                  AND w.brand_name LIKE %s
                """,
                (str(session.organization_id), f"{WATCHLIST_PREFIX}%"),
            )
            deleted = cur.rowcount or 0
            db.commit()
            if deleted:
                REPORTER.info(f"alert browser cleanup -> removed {deleted} seeded alert(s)")
    except Exception as exc:
        REPORTER.warn(f"alert browser cleanup -> alert delete failed ({exc})")


def _create_watchlist_item(
    session: PersonaSession,
    *,
    brand_name: str | None = None,
    application_no: str | None = None,
    description: str = "Browser alert test item",
) -> tuple[str, str]:
    brand_name = brand_name or f"{WATCHLIST_PREFIX} {uuid4().hex[:8].upper()}"
    payload = {
        "brand_name": brand_name,
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": description,
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    if application_no:
        payload["application_no"] = application_no
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code not in (200, 201):
        raise AssertionError(f"unexpected watchlist create status: {response.status_code}")

    data = response.json()
    item_id = data.get("id")
    if not item_id:
        raise AssertionError("watchlist create response missing id")
    return item_id, brand_name


def _load_appeal_seed_data(session: PersonaSession) -> dict[str, dict]:
    if not session.organization_id:
        raise AssertionError("missing organization id for appeal seed setup")

    own_specs = {
        "new": {"status": STATUS_PENDING, "order": "ASC"},
        "acknowledged": {"status": STATUS_REGISTERED, "order": "DESC"},
        "resolved": {"status": STATUS_PUBLISHED, "order": "DESC"},
    }

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT customer_application_no
            FROM watchlist_mt
            WHERE organization_id = %s
              AND customer_application_no IS NOT NULL
            """,
            (str(session.organization_id),),
        )
        used_app_nos = {
            str(row["customer_application_no"]).strip()
            for row in cur.fetchall()
            if row.get("customer_application_no")
        }

        own_rows: dict[str, dict] = {}
        for key, spec in own_specs.items():
            cur.execute(
                f"""
                SELECT application_no, name, final_status, application_date
                FROM trademarks
                WHERE final_status = %s
                  AND application_no IS NOT NULL
                  AND application_date IS NOT NULL
                ORDER BY application_date {spec["order"]}, application_no ASC
                LIMIT 50
                """,
                (spec["status"],),
            )
            rows = [dict(row) for row in cur.fetchall()]
            selected = next(
                (
                    row
                    for row in rows
                    if str(row["application_no"]).strip() not in used_app_nos
                ),
                None,
            )
            if selected is None:
                raise AssertionError(f"missing trademark seed candidate for {key} appeals filter scenario")
            own_rows[key] = selected
            used_app_nos.add(str(selected["application_no"]).strip())

        cur.execute(
            """
            SELECT id, application_no, name, final_status, appeal_deadline
            FROM trademarks
            WHERE appeal_deadline IS NOT NULL
              AND appeal_deadline >= CURRENT_DATE
              AND application_no IS NOT NULL
            ORDER BY appeal_deadline ASC, application_no ASC
            LIMIT 25
            """
        )
        conflicts = []
        for row in cur.fetchall():
            mapped = dict(row)
            app_no = str(mapped["application_no"]).strip()
            if app_no in used_app_nos:
                continue
            conflicts.append(mapped)
            used_app_nos.add(app_no)
            if len(conflicts) == 3:
                break

    if len(conflicts) < 3:
        raise AssertionError("missing conflicting trademark candidates with active appeal deadlines")

    return {
        "own": own_rows,
        "conflicts": {
            "new": conflicts[0],
            "acknowledged": conflicts[1],
            "resolved": conflicts[2],
        },
    }


def _seed_alert(
    session: PersonaSession,
    watchlist_id: str,
    suffix: str,
    *,
    conflict_tm: dict | None = None,
    status: str = "new",
    severity: str = "critical",
) -> str:
    if not session.organization_id:
        raise AssertionError("missing organization id for alert seed")
    alert_id = str(uuid4())
    conflicting_name = (conflict_tm or {}).get("name") or f"{CONFLICT_PREFIX} {suffix}"
    conflicting_application_no = (conflict_tm or {}).get("application_no") or f"ALERT-{uuid4().hex[:8].upper()}"
    conflicting_tm_id = str(conflict_tm["id"]) if conflict_tm and conflict_tm.get("id") else None

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO alerts_mt (
                id, user_id, organization_id, watchlist_item_id, conflicting_trademark_id,
                conflicting_name, conflicting_application_no,
                conflicting_classes, conflicting_holder_name, conflicting_image_path,
                overall_risk_score, text_similarity_score, semantic_similarity_score,
                visual_similarity_score, translation_similarity_score,
                phonetic_match, severity, source_type, alert_type, status,
                overlapping_classes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alert_id,
                str(session.user_id) if session.user_id else None,
                str(session.organization_id),
                str(watchlist_id),
                conflicting_tm_id,
                conflicting_name,
                conflicting_application_no,
                [9, 35],
                "Browser Alert Holder",
                None,
                0.92,
                0.91,
                0.87,
                0.0,
                0.0,
                False,
                severity,
                "browser_seed",
                "similarity",
                status,
                [9],
            ),
        )
        if status == "acknowledged":
            cur.execute(
                "UPDATE alerts_mt SET acknowledged_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (alert_id,),
            )
        elif status == "resolved":
            cur.execute(
                "UPDATE alerts_mt SET resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (alert_id,),
            )
        db.commit()
    return alert_id


def _fetch_alert_status(session: PersonaSession, alert_id: str) -> str:
    response = session.client.get(f"/api/v1/alerts/{alert_id}")
    if response.status_code != 200:
        raise AssertionError(f"unexpected alert fetch status: {response.status_code}")
    return response.json().get("status") or ""


def _wait_for_alert_status(session: PersonaSession, alert_id: str, expected_status: str, timeout_seconds: float = 12.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = _fetch_alert_status(session, alert_id)
        if status == expected_status:
            return
        time.sleep(0.5)
    raise AssertionError(f"alert {alert_id} did not reach status {expected_status}")


def _run_isolated_step(playwright, browser_config, name: str, action) -> None:
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    try:
        run_browser_step(
            name,
            REPORTER,
            page,
            monitor,
            browser_config,
            lambda: action(page, monitor),
        )
    finally:
        context.close()
        browser.close()


def _open_dashboard_with_token(page, browser_config, monitor, session: PersonaSession) -> None:
    token = session.client.token
    if not token:
        raise AssertionError("alerts browser session missing auth token")

    page.add_init_script(
        f"""
        (() => {{
            const token = {token!r};
            localStorage.setItem('auth_token', token);
            localStorage.setItem('access_token', token);
            sessionStorage.setItem('auth_token', token);
            sessionStorage.setItem('access_token', token);
        }})();
        """,
    )
    open_url(page, browser_config, "/dashboard")
    page.locator("#tab-btn-overview").wait_for(state="visible")
    monitor.clear()


def _wait_for_text(page, selector: str, text: str, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        ([targetSelector, targetText]) => {
            const el = document.querySelector(targetSelector);
            return !!(el && el.textContent && el.textContent.includes(targetText));
        }
        """,
        arg=[selector, text],
        timeout=timeout_ms,
    )


def _wait_for_text_absent(page, selector: str, text: str, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        ([targetSelector, targetText]) => {
            const el = document.querySelector(targetSelector);
            return !el || !el.textContent || !el.textContent.includes(targetText);
        }
        """,
        arg=[selector, text],
        timeout=timeout_ms,
    )


def _appeal_row_brands(page) -> list[str]:
    return page.eval_on_selector_all(
        "#portfolio-grid [data-watchlist-appeal-row='true']",
        "els => els.map(el => el.getAttribute('data-watchlist-brand') || '').filter(Boolean)",
    )


def _wait_for_appeal_rows(page, expected_brands: list[str], timeout_ms: int) -> None:
    page.wait_for_function(
        """
        (brands) => {
            const rows = Array.from(document.querySelectorAll("#portfolio-grid [data-watchlist-appeal-row='true']"));
            const names = rows.map(row => row.getAttribute('data-watchlist-brand') || '');
            return brands.every(brand => names.includes(brand));
        }
        """,
        arg=expected_brands,
        timeout=timeout_ms,
    )


def _wait_for_appeal_order(page, expected_brands: list[str], timeout_ms: int) -> None:
    page.wait_for_function(
        """
        (brands) => {
            const rows = Array.from(document.querySelectorAll("#portfolio-grid [data-watchlist-appeal-row='true']"));
            const names = rows.map(row => row.getAttribute('data-watchlist-brand') || '');
            if (names.length !== brands.length) {
                return false;
            }
            return brands.every((brand, index) => names[index] === brand);
        }
        """,
        arg=expected_brands,
        timeout=timeout_ms,
    )


def main() -> None:
    REPORTER.print_heading("ALERTS BROWSER", server=CONFIG.base_url)

    session = ensure_free_session()
    if session is None:
        sys.exit(1)

    _ensure_email_verified(session)
    browser_config = with_live_credentials(CONFIG, session.config)

    watchlist_id = None
    try:
        _cleanup_seeded_alerts(session)
        cleanup_watchlist_items_by_prefix(session.client, REPORTER, WATCHLIST_PREFIX)

        watchlist_id, watchlist_brand = _create_watchlist_item(session)
        ack_alert_id = _seed_alert(session, watchlist_id, "ACK")
        resolve_alert_id = _seed_alert(session, watchlist_id, "RESOLVE")
        dismiss_alert_id = _seed_alert(session, watchlist_id, "DISMISS")

        appeal_seed = _load_appeal_seed_data(session)
        filter_new_brand = f"{WATCHLIST_PREFIX} FILTER ALPHA {uuid4().hex[:6].upper()}"
        filter_ack_brand = f"{WATCHLIST_PREFIX} FILTER ZETA {uuid4().hex[:6].upper()}"
        filter_resolved_brand = f"{WATCHLIST_PREFIX} FILTER OMEGA {uuid4().hex[:6].upper()}"

        filter_new_id, _ = _create_watchlist_item(
            session,
            brand_name=filter_new_brand,
            application_no=str(appeal_seed["own"]["new"]["application_no"]),
            description="Browser alert filter new item",
        )
        filter_ack_id, _ = _create_watchlist_item(
            session,
            brand_name=filter_ack_brand,
            application_no=str(appeal_seed["own"]["acknowledged"]["application_no"]),
            description="Browser alert filter acknowledged item",
        )
        filter_resolved_id, _ = _create_watchlist_item(
            session,
            brand_name=filter_resolved_brand,
            application_no=str(appeal_seed["own"]["resolved"]["application_no"]),
            description="Browser alert filter resolved item",
        )

        _seed_alert(
            session,
            filter_new_id,
            "FILTER-NEW",
            conflict_tm=appeal_seed["conflicts"]["new"],
            status="new",
            severity="high",
        )
        _seed_alert(
            session,
            filter_ack_id,
            "FILTER-ACK",
            conflict_tm=appeal_seed["conflicts"]["acknowledged"],
            status="acknowledged",
            severity="critical",
        )
        _seed_alert(
            session,
            filter_resolved_id,
            "FILTER-RESOLVED",
            conflict_tm=appeal_seed["conflicts"]["resolved"],
            status="resolved",
            severity="medium",
        )

        with sync_playwright() as playwright:
            def acknowledge_alert_detail(page, monitor) -> None:
                _open_dashboard_with_token(page, browser_config, monitor, session)
                page.wait_for_function(
                    "() => !!(document.body._x_dataStack && document.body._x_dataStack[0])",
                    timeout=browser_config.timeout_ms,
                )
                page.evaluate(
                    """async (alertId) => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        if (!state || typeof state.showAlertDetail !== 'function') {
                            throw new Error('dashboard showAlertDetail unavailable');
                        }
                        await state.showAlertDetail(alertId);
                    }""",
                    ack_alert_id,
                )
                page.locator("#alert-detail-modal").wait_for(state="visible")
                page.locator("#alert-detail-content").filter(has_text=f"{CONFLICT_PREFIX} ACK").wait_for()
                page.locator("#alert-detail-actions button").nth(0).click()
                _wait_for_alert_status(session, ack_alert_id, "acknowledged")
                page.locator("#alert-detail-modal").wait_for(state="hidden")

            _run_isolated_step(
                playwright,
                browser_config,
                "member alert detail acknowledge browser journey",
                acknowledge_alert_detail,
            )

            def resolve_inline_alert(page, monitor) -> None:
                _open_dashboard_with_token(page, browser_config, monitor, session)
                page.click("#tab-btn-watchlist")
                page.locator("#tab-content-watchlist").wait_for(state="visible")
                page.fill("#wl-search-input", watchlist_brand)
                page.wait_for_timeout(450)
                card = page.locator("#portfolio-grid .card-base").filter(has_text=watchlist_brand).first
                card.wait_for(state="visible")
                card.click()
                panel = page.locator(f"#wl-alerts-{watchlist_id}")
                panel.wait_for(state="visible")
                panel.locator("text=" + f"{CONFLICT_PREFIX} RESOLVE").wait_for()
                panel.locator(f"div[onclick*=\"toggleInlineAlertDetail('{resolve_alert_id}')\"]").click()
                button = panel.locator(f"button[onclick*=\"inlineResolveAlert('{resolve_alert_id}'\"]")
                button.wait_for(state="visible")
                page.wait_for_function(
                    "() => typeof inlineResolveAlert === 'function'",
                    timeout=browser_config.timeout_ms,
                )
                page.evaluate(
                    """async ([alertId, watchlistItemId]) => {
                        if (typeof inlineResolveAlert !== 'function') {
                            throw new Error('inlineResolveAlert unavailable');
                        }
                        await inlineResolveAlert(alertId, watchlistItemId);
                    }""",
                    [resolve_alert_id, watchlist_id],
                )
                _wait_for_alert_status(session, resolve_alert_id, "resolved")

            _run_isolated_step(
                playwright,
                browser_config,
                "member inline alert resolve browser journey",
                resolve_inline_alert,
            )

            def dismiss_inline_alert(page, monitor) -> None:
                _open_dashboard_with_token(page, browser_config, monitor, session)
                page.click("#tab-btn-watchlist")
                page.locator("#tab-content-watchlist").wait_for(state="visible")
                page.fill("#wl-search-input", watchlist_brand)
                page.wait_for_timeout(450)
                card = page.locator("#portfolio-grid .card-base").filter(has_text=watchlist_brand).first
                card.wait_for(state="visible")
                card.click()
                panel = page.locator(f"#wl-alerts-{watchlist_id}")
                panel.wait_for(state="visible")
                panel.locator("text=" + f"{CONFLICT_PREFIX} DISMISS").wait_for()
                panel.locator(f"div[onclick*=\"toggleInlineAlertDetail('{dismiss_alert_id}')\"]").click()
                button = panel.locator(f"button[onclick*=\"inlineDismissAlert('{dismiss_alert_id}'\"]")
                button.wait_for(state="visible")
                page.wait_for_function(
                    "() => typeof inlineDismissAlert === 'function'",
                    timeout=browser_config.timeout_ms,
                )
                page.evaluate(
                    """async ([alertId, watchlistItemId]) => {
                        if (typeof inlineDismissAlert !== 'function') {
                            throw new Error('inlineDismissAlert unavailable');
                        }
                        await inlineDismissAlert(alertId, watchlistItemId);
                    }""",
                    [dismiss_alert_id, watchlist_id],
                )
                _wait_for_alert_status(session, dismiss_alert_id, "dismissed")

            _run_isolated_step(
                playwright,
                browser_config,
                "member inline alert dismiss browser journey",
                dismiss_inline_alert,
            )

            def appeals_filter_and_sort(page, monitor) -> None:
                _open_dashboard_with_token(page, browser_config, monitor, session)
                page.click("#tab-btn-watchlist")
                page.locator("#tab-content-watchlist").wait_for(state="visible")
                page.click("#wl-view-tab-appeals")
                page.locator("#wl-status-filter-wrap").wait_for(state="visible")
                page.wait_for_function(
                    "() => !!document.querySelector('#wl-status-select option[value=\"resolved\"]')",
                    timeout=browser_config.timeout_ms,
                )

                page.fill("#wl-search-input", f"{WATCHLIST_PREFIX} FILTER")
                page.wait_for_timeout(600)

                _wait_for_appeal_order(page, [filter_ack_brand, filter_new_brand], browser_config.timeout_ms)

                page.select_option("#wl-status-select", "new")
                _wait_for_appeal_order(page, [filter_new_brand], browser_config.timeout_ms)

                page.select_option("#wl-status-select", "acknowledged")
                _wait_for_appeal_order(page, [filter_ack_brand], browser_config.timeout_ms)

                page.select_option("#wl-status-select", "resolved")
                _wait_for_appeal_order(page, [filter_resolved_brand], browser_config.timeout_ms)

                page.select_option("#wl-status-select", "")
                _wait_for_appeal_order(page, [filter_ack_brand, filter_new_brand], browser_config.timeout_ms)

                page.select_option("#wl-tm-status-select", STATUS_REGISTERED)
                _wait_for_appeal_order(page, [filter_ack_brand], browser_config.timeout_ms)

                page.select_option("#wl-tm-status-select", "")
                _wait_for_appeal_order(page, [filter_ack_brand, filter_new_brand], browser_config.timeout_ms)

                page.select_option("#wl-sort-select", "name_asc")
                _wait_for_appeal_order(page, [filter_new_brand, filter_ack_brand], browser_config.timeout_ms)

                page.select_option("#wl-sort-select", "date_desc")
                _wait_for_appeal_order(page, [filter_ack_brand, filter_new_brand], browser_config.timeout_ms)

            _run_isolated_step(
                playwright,
                browser_config,
                "member appeals alert filter and sort browser journey",
                appeals_filter_and_sort,
            )
    finally:
        _cleanup_seeded_alerts(session)
        cleanup_watchlist_items_by_prefix(session.client, REPORTER, WATCHLIST_PREFIX)

    sys.exit(0 if REPORTER.summary("ALERTS BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()
