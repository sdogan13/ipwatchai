"""
Browser journeys for opposition filing handoff from alerts into applications.

Run directly:
    python tests/browser/test_opposition_browser.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
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
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.cleanup import cleanup_watchlist_items_by_prefix
from tests.live.helpers.personas import PersonaSession, resolve_free_persona_session


CONFIG = load_browser_config()
REPORTER = LiveReporter()
FREE_SESSION: PersonaSession | None = None
FREE_RESOLVED = False

WATCHLIST_PREFIX = "BROWSER OPP WL"
TRADEMARK_APP_PREFIX = "BROWSER-OPP-APP-"
TRADEMARK_NAME_PREFIX = "BROWSER OPP TM"

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_opposition_browser.py"
)


def ensure_free_session() -> PersonaSession | None:
    global FREE_SESSION
    global FREE_RESOLVED
    if FREE_SESSION is None and not FREE_RESOLVED:
        FREE_RESOLVED = True
        FREE_SESSION = resolve_free_persona_session(REPORTER, label="opposition browser free user")
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
                REPORTER.info(f"opposition browser cleanup -> removed {deleted} seeded alert(s)")
    except Exception as exc:
        REPORTER.warn(f"opposition browser cleanup -> alert delete failed ({exc})")


def _cleanup_seeded_trademarks() -> None:
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                DELETE FROM trademarks
                WHERE application_no LIKE %s
                   OR name LIKE %s
                """,
                (f"{TRADEMARK_APP_PREFIX}%", f"{TRADEMARK_NAME_PREFIX}%"),
            )
            deleted = cur.rowcount or 0
            db.commit()
            if deleted:
                REPORTER.info(f"opposition browser cleanup -> removed {deleted} seeded trademark(s)")
    except Exception as exc:
        REPORTER.warn(f"opposition browser cleanup -> trademark delete failed ({exc})")


def _create_watchlist_item(session: PersonaSession) -> tuple[str, str]:
    brand_name = f"{WATCHLIST_PREFIX} {uuid4().hex[:8].upper()}"
    payload = {
        "brand_name": brand_name,
        "nice_class_numbers": [9, 35],
        "similarity_threshold": 0.75,
        "description": "Browser opposition test item",
        "monitor_text": True,
        "monitor_visual": False,
        "monitor_phonetic": False,
        "alert_frequency": "daily",
        "alert_email": False,
    }
    response = session.client.post("/api/v1/watchlist", json_data=payload)
    if response.status_code not in (200, 201):
        raise AssertionError(f"unexpected watchlist create status: {response.status_code}")

    data = response.json()
    item_id = data.get("id")
    if not item_id:
        raise AssertionError("watchlist create response missing id")
    return item_id, brand_name


def _seed_conflicting_trademark() -> dict[str, str]:
    trademark_id = str(uuid4())
    app_no = f"{TRADEMARK_APP_PREFIX}{uuid4().hex[:8].upper()}"
    trademark_name = f"{TRADEMARK_NAME_PREFIX} {uuid4().hex[:6].upper()}"
    bulletin_no = f"2026-{uuid4().hex[:3].upper()}"
    bulletin_date = date.today() - timedelta(days=12)
    opposition_deadline = date.today() + timedelta(days=18)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO trademarks (
                id,
                application_no,
                name,
                nice_class_numbers,
                bulletin_no,
                bulletin_date,
                appeal_deadline,
                holder_name
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trademark_id,
                app_no,
                trademark_name,
                [9, 35],
                bulletin_no,
                bulletin_date,
                opposition_deadline,
                "Browser Opposition Holder",
            ),
        )
        db.commit()

    return {
        "id": trademark_id,
        "app_no": app_no,
        "name": trademark_name,
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date.isoformat(),
        "opposition_deadline": opposition_deadline.isoformat(),
        "holder": "Browser Opposition Holder",
    }


def _seed_alert(session: PersonaSession, watchlist_id: str, trademark: dict[str, str]) -> str:
    if not session.organization_id:
        raise AssertionError("missing organization id for alert seed")

    alert_id = str(uuid4())
    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO alerts_mt (
                id,
                user_id,
                organization_id,
                watchlist_item_id,
                conflicting_trademark_id,
                conflicting_name,
                conflicting_application_no,
                conflicting_classes,
                conflicting_holder_name,
                conflicting_image_path,
                overall_risk_score,
                text_similarity_score,
                semantic_similarity_score,
                visual_similarity_score,
                translation_similarity_score,
                phonetic_match,
                severity,
                source_type,
                alert_type,
                status,
                overlapping_classes,
                opposition_deadline,
                source_bulletin
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                alert_id,
                str(session.user_id) if session.user_id else None,
                str(session.organization_id),
                str(watchlist_id),
                trademark["id"],
                trademark["name"],
                trademark["app_no"],
                [9, 35],
                trademark["holder"],
                None,
                0.92,
                0.91,
                0.88,
                0.0,
                0.0,
                False,
                "critical",
                "browser_seed",
                "similarity",
                "new",
                [9, 35],
                trademark["opposition_deadline"],
                trademark["bulletin_no"],
            ),
        )
        db.commit()
    return alert_id


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


def _login_and_stabilize(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def _open_watchlist_alert_context(page, browser_config, watchlist_id: str, watchlist_brand: str) -> None:
    page.click("#tab-btn-watchlist")
    page.locator("#tab-content-watchlist").wait_for(state="visible")
    page.fill("#wl-search-input", watchlist_brand)
    page.wait_for_timeout(450)
    card = page.locator("#portfolio-grid .card-base").filter(has_text=watchlist_brand).first
    card.wait_for(state="visible")
    card.click()
    page.locator(f"#wl-alerts-{watchlist_id}").wait_for(state="visible", timeout=browser_config.timeout_ms)


def _assert_appeal_form_prefill(page, browser_config, *, watched_brand: str, trademark: dict[str, str], require_classes: bool) -> None:
    page.locator("#tab-content-applications").wait_for(state="visible", timeout=browser_config.timeout_ms)
    page.locator("#applications-form-view").wait_for(state="visible", timeout=browser_config.timeout_ms)
    page.wait_for_function(
        """
        ([watchedBrand, conflictBrand, appNo]) => {
            const formView = document.querySelector('#applications-form-view');
            const type = document.querySelector('#app-application-type');
            const brandInput = document.querySelector('#app-brand-name');
            const appealSection = document.querySelector('#app-appeal-section');
            const conflictBrandEl = document.querySelector('#app-conflict-brand');
            const conflictAppNoEl = document.querySelector('#app-conflict-appno');
            return !!(
                formView && !formView.classList.contains('hidden') &&
                type && type.value === 'appeal' &&
                brandInput && brandInput.value === watchedBrand &&
                appealSection && !appealSection.classList.contains('hidden') &&
                conflictBrandEl && conflictBrandEl.textContent.includes(conflictBrand) &&
                conflictAppNoEl && conflictAppNoEl.textContent.includes(appNo)
            );
        }
        """,
        arg=[watched_brand, trademark["name"], trademark["app_no"]],
        timeout=browser_config.timeout_ms,
    )

    if page.locator("#app-brand-name").input_value() != watched_brand:
        raise AssertionError("appeal form did not preserve watched brand as application brand")
    if page.locator("#app-application-type").input_value() != "appeal":
        raise AssertionError("appeal form did not switch application type to appeal")
    if trademark["name"] not in (page.locator("#app-conflict-brand").text_content() or ""):
        raise AssertionError("appeal conflict card missing conflicting brand")
    if trademark["app_no"] not in (page.locator("#app-conflict-appno").text_content() or ""):
        raise AssertionError("appeal conflict card missing application number")
    if trademark["bulletin_no"] not in (page.locator("#app-conflict-bulletin").text_content() or ""):
        raise AssertionError("appeal conflict card missing bulletin number")

    deadline_text = page.locator("#app-conflict-deadline").text_content() or ""
    if trademark["opposition_deadline"] not in deadline_text:
        raise AssertionError("appeal conflict card missing opposition deadline")

    if require_classes:
        classes_text = page.locator("#app-conflict-classes").text_content() or ""
        if "9" not in classes_text or "35" not in classes_text:
            raise AssertionError("appeal conflict card missing expected overlapping classes")


def _assert_opposition_modal_prefill(page, browser_config, *, watched_brand: str, trademark: dict[str, str]) -> None:
    page.locator("#opposition-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
    page.locator("#opposition-content").wait_for(state="visible", timeout=browser_config.timeout_ms)
    content_text = page.locator("#opposition-content").text_content() or ""

    for expected in (trademark["name"], trademark["app_no"], trademark["opposition_deadline"], "%92"):
        if expected not in content_text:
            raise AssertionError(f"opposition modal missing expected text: {expected}")


def main() -> None:
    REPORTER.print_heading("OPPOSITION BROWSER", server=CONFIG.base_url)

    session = ensure_free_session()
    if session is None:
        sys.exit(1)

    _ensure_email_verified(session)
    browser_config = with_live_credentials(CONFIG, session.config)

    watchlist_id = None
    try:
        _cleanup_seeded_alerts(session)
        cleanup_watchlist_items_by_prefix(session.client, REPORTER, WATCHLIST_PREFIX)
        _cleanup_seeded_trademarks()

        watchlist_id, watchlist_brand = _create_watchlist_item(session)
        trademark = _seed_conflicting_trademark()
        alert_id = _seed_alert(session, watchlist_id, trademark)

        with sync_playwright() as playwright:
            def inline_opposition_handoff(page, monitor) -> None:
                _login_and_stabilize(page, browser_config, monitor)
                _open_watchlist_alert_context(page, browser_config, watchlist_id, watchlist_brand)
                monitor.clear()
                panel = page.locator(f"#wl-alerts-{watchlist_id}")
                panel.locator(f"div[onclick*=\"toggleInlineAlertDetail('{alert_id}')\"]").click()
                button = panel.locator(f"button[onclick*=\"openItirazForm('{alert_id}')\"]")
                button.wait_for(state="visible", timeout=browser_config.timeout_ms)
                button.click()
                _assert_appeal_form_prefill(
                    page,
                    browser_config,
                    watched_brand=watchlist_brand,
                    trademark=trademark,
                    require_classes=True,
                )

            _run_isolated_step(
                playwright,
                browser_config,
                "member inline alert opposition handoff browser journey",
                inline_opposition_handoff,
            )

            def alert_detail_opposition_modal(page, monitor) -> None:
                _login_and_stabilize(page, browser_config, monitor)
                page.wait_for_function(
                    "() => !!(document.body._x_dataStack && document.body._x_dataStack[0])",
                    timeout=browser_config.timeout_ms,
                )
                monitor.clear()
                page.evaluate(
                    """async (targetAlertId) => {
                        const state = document.body._x_dataStack && document.body._x_dataStack[0];
                        if (!state || typeof state.showAlertDetail !== 'function') {
                            throw new Error('dashboard showAlertDetail unavailable');
                        }
                        await state.showAlertDetail(targetAlertId);
                    }""",
                    alert_id,
                )
                page.locator("#alert-detail-modal").wait_for(state="visible", timeout=browser_config.timeout_ms)
                action_button = page.locator("#alert-detail-actions button").first
                action_button.wait_for(state="visible", timeout=browser_config.timeout_ms)
                action_button.click()
                _assert_opposition_modal_prefill(
                    page,
                    browser_config,
                    watched_brand=watchlist_brand,
                    trademark=trademark,
                )

            _run_isolated_step(
                playwright,
                browser_config,
                "member alert detail opposition guidance modal browser journey",
                alert_detail_opposition_modal,
            )
    finally:
        _cleanup_seeded_alerts(session)
        cleanup_watchlist_items_by_prefix(session.client, REPORTER, WATCHLIST_PREFIX)
        _cleanup_seeded_trademarks()

    sys.exit(0 if REPORTER.summary("OPPOSITION BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()
