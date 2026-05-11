"""Browser smoke for the Applications dashboard tab.

Covers the 4-registry switcher: Marka / Tasarım / Patent / Coğrafi.
All four registries now back a real applications workflow against
the polymorphic /api/v1/applications endpoint with a registry_kind
discriminator.

  * Marka renders the existing trademark applications list+form,
  * **Tasarım** workflow (Phase 1) — Locarno chips + design title,
  * **Patent** workflow (Phase 2) — IPC chips + patent_kind select,
  * **Coğrafi** workflow (Phase 3) — gi_type + region + product_type
    + production_method (no classification field),
  * localStorage('applicationsView') is written by the $watch.

Each registry round trip writes one draft DB row and cleans it up
in a finally block; safe to re-run.

Run directly:
    python tests/browser/test_applications_browser.py

Uses the managed-professional persona (Pro+ unlocks Applications).
The persona auto-provisions on first run.
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
from tests.browser.helpers.cografi import cografi_config_for_persona
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("professional")
REPORTER = LiveReporter()

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_applications_browser.py"
)


_TRANSIENT_401S = (
    f"401 GET {CONFIG.base_url}/api/v1/applications",
    f"401 GET {CONFIG.base_url}/api/v1/usage/credits",
)


# Per-registry timestamped titles so re-runs don't collide and the
# cleanup step can find this run's row even on partial failure.
_TS = int(time.time())
_TASARIM_ROUND_TRIP_TITLE = f"BROWSER SMOKE tasarim {_TS}"
_PATENT_ROUND_TRIP_TITLE = f"BROWSER SMOKE patent {_TS}"
_COGRAFI_ROUND_TRIP_TITLE = f"BROWSER SMOKE cografi {_TS}"
_COGRAFI_REGION = "Karapınar"


def _registry_switcher_button(page, label: str):
    return page.locator(
        f"#tab-content-applications > div.inline-flex button:has-text('{label}')"
    ).first


def _open_applications_tab(page) -> dict:
    page.evaluate("window.showDashboardTab('applications')")
    page.wait_for_selector("#tab-content-applications:not(.hidden)", timeout=5000)
    return {"ok": True}


def _assert_default_marka_visible(page) -> dict:
    # Marka registry shows the list view container.
    page.wait_for_selector("#applications-list-view", state="visible", timeout=5000)
    return {"marka_visible": True}


def _switch_to_tasarim_subview(page) -> dict:
    """Switch to the Tasarım registry and confirm the design subview
    renders the real form shell (list view default, with the New
    Application button)."""
    _registry_switcher_button(page, "Tasarım").click()
    page.wait_for_selector("#da-list-view", state="visible", timeout=5000)
    assert not page.locator("#applications-list-view").is_visible(), (
        "Marka applications list still visible after switching to Tasarım"
    )
    return {"da_list_view_visible": True}


def _open_tasarim_form_and_fill(page) -> dict:
    """Click 'Yeni Başvuru' in the Tasarım subview, fill the minimum
    fields needed to save a draft."""
    # Click the new-application button in the design subview header.
    new_btn = page.locator("#da-list-view button:has-text('Yeni Başvuru')").first
    new_btn.click()
    page.wait_for_selector("#da-form-view", state="visible", timeout=5000)
    page.locator("#da-design-title").fill(_TASARIM_ROUND_TRIP_TITLE)
    page.locator("#da-design-description").fill("Smoke test design description.")
    # Add one Locarno class via the chip control.
    page.locator("#da-locarno-class-select").select_option("6")
    page.locator("#da-form-view button:has-text('Ekle')").first.click()
    page.wait_for_timeout(200)
    return {"filled_title": _TASARIM_ROUND_TRIP_TITLE}


def _save_tasarim_draft_via_post(page) -> dict:
    """Click 'Taslak Kaydet' and capture the POST response so 4xx
    surfaces with the server's detail message."""
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/applications/")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#da-btn-save-draft").click()
    response = resp_info.value
    if response.status != 200:
        try:
            body = response.text()[:400]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /api/v1/applications/ returned {response.status}: {body}"
        )
    body = response.json()
    assert body.get("registry_kind") == "design", (
        f"saved row registry_kind={body.get('registry_kind')!r}, expected 'design'"
    )
    assert body.get("brand_name") == _TASARIM_ROUND_TRIP_TITLE, (
        f"saved title mismatch: {body.get('brand_name')!r}"
    )
    return {"id": body.get("id"), "registry_kind": body.get("registry_kind")}


def _confirm_tasarim_in_list(page) -> dict:
    """After save the JS calls showDesignApplicationsList() — wait for
    our timestamped title to render in the list."""
    page.wait_for_selector("#da-list-view", state="visible", timeout=5000)
    page.wait_for_selector(
        f"#da-list h4:has-text({_TASARIM_ROUND_TRIP_TITLE!r})",
        timeout=10000,
    )
    return {"found": _TASARIM_ROUND_TRIP_TITLE}


def _find_tasarim_row(page):
    """Locate the rendered list row containing our timestamped title."""
    rows = page.locator("#da-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if _TASARIM_ROUND_TRIP_TITLE in row.inner_text():
            return row
    return None


def _delete_tasarim_round_trip_row(page) -> dict:
    """Cleanup: delete the smoke-created draft via the inline button.
    Accepts the native confirm() dialog automatically."""
    row = _find_tasarim_row(page)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("button:has-text('Sil')")
    if del_btn.count() == 0:
        return {"deleted": False, "reason": "delete button not on row"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _find_tasarim_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(400)
    raise AssertionError(
        f"Tasarım round-trip row not removed after delete: {_TASARIM_ROUND_TRIP_TITLE!r}"
    )


def _switch_to_patent_subview(page) -> dict:
    """Switch to the Patent registry and confirm the patent subview's
    list shell renders."""
    _registry_switcher_button(page, "Patent").click()
    page.wait_for_selector("#pa-list-view", state="visible", timeout=5000)
    assert not page.locator("#applications-list-view").is_visible(), (
        "Marka applications list still visible after switching to Patent"
    )
    assert not page.locator("#da-list-view").is_visible(), (
        "Tasarım list still visible after switching to Patent"
    )
    return {"pa_list_view_visible": True}


def _open_patent_form_and_fill(page) -> dict:
    """Click 'Yeni Başvuru' in the Patent subview, fill the form
    including IPC chip + patent_kind=utility_model."""
    new_btn = page.locator("#pa-list-view button:has-text('Yeni Başvuru')").first
    new_btn.click()
    page.wait_for_selector("#pa-form-view", state="visible", timeout=5000)
    page.locator("#pa-invention-title").fill(_PATENT_ROUND_TRIP_TITLE)
    page.locator("#pa-patent-kind").select_option("utility_model")
    page.locator("#pa-abstract").fill("Smoke abstract for browser regression.")
    page.locator("#pa-claims").fill("1. A method for round-trip testing.\n2. The method of claim 1 wherein the test passes.")
    page.locator("#pa-inventors").fill("Test Smoke")
    # Add one IPC code via the chip input + Ekle button.
    page.locator("#pa-ipc-class-input").fill("G06F 17/30")
    page.locator("#pa-form-view button:has-text('Ekle')").first.click()
    page.wait_for_timeout(200)
    return {"filled_title": _PATENT_ROUND_TRIP_TITLE}


def _save_patent_draft_via_post(page) -> dict:
    """Click 'Taslak Kaydet' and capture the POST response."""
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/applications/")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#pa-btn-save-draft").click()
    response = resp_info.value
    if response.status != 200:
        try:
            body = response.text()[:400]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /api/v1/applications/ (patent) returned {response.status}: {body}"
        )
    body = response.json()
    assert body.get("registry_kind") == "patent", (
        f"saved row registry_kind={body.get('registry_kind')!r}, expected 'patent'"
    )
    assert body.get("brand_name") == _PATENT_ROUND_TRIP_TITLE, (
        f"saved title mismatch: {body.get('brand_name')!r}"
    )
    details = body.get("details") or {}
    assert details.get("patent_kind") == "utility_model", (
        f"details.patent_kind expected 'utility_model', got {details.get('patent_kind')!r}"
    )
    return {"id": body.get("id"), "registry_kind": body.get("registry_kind")}


def _confirm_patent_in_list(page) -> dict:
    page.wait_for_selector("#pa-list-view", state="visible", timeout=5000)
    page.wait_for_selector(
        f"#pa-list h4:has-text({_PATENT_ROUND_TRIP_TITLE!r})",
        timeout=10000,
    )
    return {"found": _PATENT_ROUND_TRIP_TITLE}


def _find_patent_row(page):
    rows = page.locator("#pa-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if _PATENT_ROUND_TRIP_TITLE in row.inner_text():
            return row
    return None


def _delete_patent_round_trip_row(page) -> dict:
    row = _find_patent_row(page)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("button:has-text('Sil')")
    if del_btn.count() == 0:
        return {"deleted": False, "reason": "delete button not on row"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _find_patent_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(400)
    raise AssertionError(
        f"Patent round-trip row not removed after delete: {_PATENT_ROUND_TRIP_TITLE!r}"
    )


def _switch_to_cografi_subview(page) -> dict:
    """Switch to the Coğrafi registry and confirm the GI subview's
    list shell renders."""
    _registry_switcher_button(page, "Coğrafi").click()
    page.wait_for_selector("#ca-list-view", state="visible", timeout=5000)
    assert not page.locator("#applications-list-view").is_visible(), (
        "Marka applications list still visible after switching to Coğrafi"
    )
    return {"ca_list_view_visible": True}


def _open_cografi_form_and_fill(page) -> dict:
    """Click 'Yeni Başvuru' in the Coğrafi subview, fill the form
    including gi_type=mahrec + region + product_type."""
    new_btn = page.locator("#ca-list-view button:has-text('Yeni Başvuru')").first
    new_btn.click()
    page.wait_for_selector("#ca-form-view", state="visible", timeout=5000)
    page.locator("#ca-gi-name").fill(_COGRAFI_ROUND_TRIP_TITLE)
    page.locator("#ca-gi-type").select_option("mahrec")
    page.locator("#ca-region").fill(_COGRAFI_REGION)
    page.locator("#ca-product-type").fill("Smoke product")
    page.locator("#ca-production-method").fill("Smoke production method description.")
    return {"filled_title": _COGRAFI_ROUND_TRIP_TITLE}


def _save_cografi_draft_via_post(page) -> dict:
    """Click 'Taslak Kaydet' and capture the POST response."""
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/applications/")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#ca-btn-save-draft").click()
    response = resp_info.value
    if response.status != 200:
        try:
            body = response.text()[:400]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /api/v1/applications/ (cografi) returned {response.status}: {body}"
        )
    body = response.json()
    assert body.get("registry_kind") == "cografi", (
        f"saved row registry_kind={body.get('registry_kind')!r}, expected 'cografi'"
    )
    assert body.get("brand_name") == _COGRAFI_ROUND_TRIP_TITLE, (
        f"saved GI name mismatch: {body.get('brand_name')!r}"
    )
    details = body.get("details") or {}
    assert details.get("gi_type") == "mahrec", (
        f"details.gi_type expected 'mahrec', got {details.get('gi_type')!r}"
    )
    assert details.get("region") == _COGRAFI_REGION, (
        f"details.region expected {_COGRAFI_REGION!r}, got {details.get('region')!r}"
    )
    return {"id": body.get("id"), "registry_kind": body.get("registry_kind")}


def _confirm_cografi_in_list(page) -> dict:
    page.wait_for_selector("#ca-list-view", state="visible", timeout=5000)
    page.wait_for_selector(
        f"#ca-list h4:has-text({_COGRAFI_ROUND_TRIP_TITLE!r})",
        timeout=10000,
    )
    return {"found": _COGRAFI_ROUND_TRIP_TITLE}


def _find_cografi_row(page):
    rows = page.locator("#ca-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if _COGRAFI_ROUND_TRIP_TITLE in row.inner_text():
            return row
    return None


def _delete_cografi_round_trip_row(page) -> dict:
    row = _find_cografi_row(page)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("button:has-text('Sil')")
    if del_btn.count() == 0:
        return {"deleted": False, "reason": "delete button not on row"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _find_cografi_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(400)
    raise AssertionError(
        f"Coğrafi round-trip row not removed after delete: {_COGRAFI_ROUND_TRIP_TITLE!r}"
    )


def _switch_back_to_marka(page) -> dict:
    _registry_switcher_button(page, "Marka").click()
    page.wait_for_selector("#applications-list-view", state="visible", timeout=5000)
    return {"marka_visible_again": True}


def _assert_localstorage_applicationsview_marka(page) -> dict:
    value = page.evaluate("() => localStorage.getItem('applicationsView')")
    assert value == "trademark", (
        f"localStorage.applicationsView expected 'trademark', got {value!r}"
    )
    return {"applicationsView": value}


def test_applications_browser_smoke():
    REPORTER.print_heading("Applications browser smoke", server=CONFIG.base_url)
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(
                "Login as managed-professional persona",
                REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
            )
            run_browser_step(
                "Open Applications tab",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_applications_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Default registry is Marka (applications list visible)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_default_marka_visible(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            # --- Tasarım workflow (Phase 1) -------------------------
            run_browser_step(
                "Switch to Tasarım registry (real workflow subview)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_tasarim_subview(page),
            )

            tasarim_created = False
            try:
                run_browser_step(
                    "Open Tasarım form + fill title/desc/Locarno",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _open_tasarim_form_and_fill(page),
                )
                tasarim_created = run_browser_step(
                    "Save Tasarım draft via POST + assert registry_kind",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _save_tasarim_draft_via_post(page),
                )
                if tasarim_created:
                    run_browser_step(
                        "Tasarım draft appears in registry-scoped list",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _confirm_tasarim_in_list(page),
                    )
            finally:
                if tasarim_created:
                    run_browser_step(
                        "Cleanup: delete the Tasarım round-trip draft",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_tasarim_round_trip_row(page),
                    )

            # --- Patent workflow (Phase 2) --------------------------
            run_browser_step(
                "Switch to Patent registry (real workflow subview)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_patent_subview(page),
            )

            patent_created = False
            try:
                run_browser_step(
                    "Open Patent form + fill title/abstract/IPC + utility_model",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _open_patent_form_and_fill(page),
                )
                patent_created = run_browser_step(
                    "Save Patent draft via POST + assert registry_kind + patent_kind",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _save_patent_draft_via_post(page),
                )
                if patent_created:
                    run_browser_step(
                        "Patent draft appears in registry-scoped list",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _confirm_patent_in_list(page),
                    )
            finally:
                if patent_created:
                    run_browser_step(
                        "Cleanup: delete the Patent round-trip draft",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_patent_round_trip_row(page),
                    )

            # --- Coğrafi workflow (Phase 3) -------------------------
            run_browser_step(
                "Switch to Coğrafi registry (real workflow subview)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_cografi_subview(page),
            )

            cografi_created = False
            try:
                run_browser_step(
                    "Open Coğrafi form + fill name/region/type/method",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _open_cografi_form_and_fill(page),
                )
                cografi_created = run_browser_step(
                    "Save Coğrafi draft via POST + assert registry_kind + gi_type + region",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _save_cografi_draft_via_post(page),
                )
                if cografi_created:
                    run_browser_step(
                        "Coğrafi draft appears in registry-scoped list",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _confirm_cografi_in_list(page),
                    )
            finally:
                if cografi_created:
                    run_browser_step(
                        "Cleanup: delete the Coğrafi round-trip draft",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_cografi_round_trip_row(page),
                    )
            run_browser_step(
                "Switch back to Marka registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_back_to_marka(page),
            )
            run_browser_step(
                "localStorage.applicationsView persists last registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_localstorage_applicationsview_marka(page),
            )

            failures = REPORTER.summary("Applications browser smoke")
            if failures:
                raise AssertionError(f"{failures} Applications smoke step(s) failed")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        test_applications_browser_smoke()
    except AssertionError as exc:
        print(exc)
        sys.exit(1)
    sys.exit(0)
