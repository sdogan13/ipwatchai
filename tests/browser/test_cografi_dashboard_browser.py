"""Browser smoke for the cografi (Coğrafi İşaret) dashboard tabs.

Covers the J-1b + J-2 UI: search subview + result cards + detail
modal + autocomplete + watchlist subview + 4-way watch_type radio
+ add modal + TR/EN/AR locale switching including AR RTL.

Run directly:
    python tests/browser/test_cografi_dashboard_browser.py

Requires the app running. By default uses the managed-starter
persona (``managed-starter-smoke@example.com``) — the round-trip
lifecycle steps (create + scan + export + delete) need cross-
registry watchlist quota that the default free persona doesn't
have. The persona auto-provisions on first run via
``tests/live/helpers/test_accounts.py``. Override with
``TEST_EMAIL`` / ``TEST_PASSWORD`` env vars if you want a
different persona.

Caught one real bug on first run (autocomplete kind-chip reading
"İsimsiz" instead of "İsim") that all server-side smoke missed,
which is the regression value this test is meant to preserve.
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
from tests.browser.helpers.config import BrowserConfig, load_browser_config
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


# This test exercises the full watchlist lifecycle (create -> scan ->
# export -> delete), which requires a persona with cross-registry
# watchlist quota. The default mobiletest@test.com is on the free plan
# (quota=0) so POST /cografi-watchlist returns 403 for that account.
# Use the managed-starter persona instead — it auto-provisions on
# first run via tests/live/helpers/test_accounts.py and persists in
# the DB. Falls back to the default config-resolved persona if the
# managed account env override is set, so a paid persona can be
# wired in by exporting TEST_EMAIL / TEST_PASSWORD.
_BASE_CONFIG = load_browser_config()
_MANAGED_STARTER_EMAIL = "managed-starter-smoke@example.com"
_MANAGED_STARTER_PASSWORD = "Test1234!"

# If TEST_EMAIL is explicitly set in the environment, respect it.
# Otherwise upgrade from the default free persona to the managed-
# starter paid persona so the round-trip lifecycle works.
import os as _os
if _os.environ.get("TEST_EMAIL"):
    CONFIG = _BASE_CONFIG
else:
    CONFIG = BrowserConfig(
        base_url=_BASE_CONFIG.base_url,
        timeout_ms=_BASE_CONFIG.timeout_ms,
        email=_MANAGED_STARTER_EMAIL,
        password=_MANAGED_STARTER_PASSWORD,
        browser_channel=_BASE_CONFIG.browser_channel,
        headless=_BASE_CONFIG.headless,
        artifacts_dir=_BASE_CONFIG.artifacts_dir,
    )

REPORTER = LiveReporter()

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_cografi_dashboard_browser.py"
)


# Filter the brief 401s that fire during page boot on cografi watchlist
# / alert endpoints before the auth header is wired up; these recover
# under normal user flow. Real 4xx/5xx on other paths still fail the run.
_TRANSIENT_401S = (
    "401 GET http://127.0.0.1:8000/api/v1/cografi-watchlist",
    "401 GET http://127.0.0.1:8000/api/v1/cografi-alerts",
)


# ---------------------------------------------------------------------------
# Search subview (J-1b)
# ---------------------------------------------------------------------------

def _open_search_tab(page) -> None:
    page.evaluate("window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)


def _click_cografi_search_subtab(page) -> dict:
    """Click the Coğrafi sub-view button inside the search tab."""
    tab_btn = page.locator(
        "#tab-content-search button:has-text('Coğrafi')"
    )
    assert tab_btn.count() >= 1, "Coğrafi search tab button missing"
    tab_btn.first.click()
    page.wait_for_selector("#cografi-search-input", state="visible", timeout=3000)
    return {"button_count": tab_btn.count()}


def _autocomplete_fires(page, query: str) -> dict:
    """Type into the search input and assert the autocomplete dropdown
    renders at least one entry. Also asserts each visible entry carries
    the right kind chip ("İsim" for names, "Bölge" for regions) — this
    is the regression check for the J-1b autocomplete kind-label bug
    that was caught on the first browser run.
    """
    page.locator("#cografi-search-input").click()
    page.locator("#cografi-search-input").fill(query)
    page.wait_for_timeout(400)  # debounce 180ms + fetch
    dropdown_items = page.locator(
        "#cografi-search-history-list [data-cografi-autocomplete]"
    )
    count = dropdown_items.count()
    assert count >= 1, f"autocomplete dropdown empty for query={query!r}"
    # Pull the rendered kind labels: every entry must be either "İsim"
    # or "Bölge" (TR is the default locale in this session). The bug
    # we caught had names labeled "İsimsiz" — guard against regression.
    rendered_kinds: set[str] = set()
    for i in range(count):
        chip_text = dropdown_items.nth(i).locator(
            "span.text-\\[10px\\]"
        ).inner_text().strip()
        rendered_kinds.add(chip_text)
    forbidden = {"İsimsiz", "Unnamed", "بدون اسم"}
    invalid = rendered_kinds & forbidden
    assert not invalid, (
        f"autocomplete kind chip uses untitled-placeholder text "
        f"{invalid!r} — expected 'İsim'/'Bölge'. All rendered: "
        f"{rendered_kinds!r}"
    )
    return {"item_count": count, "kinds": sorted(rendered_kinds)}


def _run_search_via_enter(page) -> dict:
    """Press Enter on the input (autocomplete dropdown overlays the
    submit button when open; Enter is the equivalent path)."""
    page.locator("#cografi-search-input").press("Enter")
    # Longer wait: cografi-search/quick does a multi-signal cosine +
    # trigram retrieval and the first hit after a cold backend takes
    # several seconds; under per-IP rate-limit budgets it can also
    # queue. 45s is the same safety budget design_search uses.
    page.wait_for_selector("#cografi-search-grid > div", timeout=45000)
    grid = page.locator("#cografi-search-grid > div")
    return {"card_count": grid.count()}


def _open_first_result_detail_modal(page) -> dict:
    page.locator("#cografi-search-grid > div").first.click()
    page.wait_for_selector(
        "#cografi-detail-modal #cd-body", state="visible", timeout=10000
    )
    title = page.locator("#cd-title").inner_text().strip()
    assert title and title != "—", f"detail modal title empty: {title!r}"
    return {"title": title}


def _close_detail_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Watchlist subview (J-2)
# ---------------------------------------------------------------------------

def _open_watchlist_tab(page) -> None:
    page.evaluate("window.showDashboardTab('watchlist')")
    page.wait_for_selector("#tab-content-watchlist:not(.hidden)", timeout=5000)


def _click_cografi_watchlist_subtab(page) -> dict:
    tab_btn = page.locator(
        "#tab-content-watchlist button:has-text('Coğrafi')"
    )
    assert tab_btn.count() >= 1, "Coğrafi watchlist tab button missing"
    tab_btn.first.click()
    page.wait_for_selector("#cwl-stats-bar", state="visible", timeout=5000)
    stat_cells = page.locator("#cwl-stats-bar > div").count()
    assert stat_cells == 6, (
        f"cografi watchlist stats bar should have 6 cells "
        f"(total + 4 watch_types + new_alerts); got {stat_cells}"
    )
    return {"stat_cells": stat_cells}


def _assert_alerts_export_button_present(page) -> dict:
    """The CSV export button (J-2.5) lives next to the alerts status
    filter. Verify it renders and is clickable. We don't actually
    click it here — the click triggers a real browser download dialog
    which is awkward to assert against without an explicit Playwright
    download listener, and the underlying endpoint is exercised by
    the server-side curl smoke. This step just locks in the regression
    that the button is wired into the watchlist DOM."""
    btn = page.locator("#cwl-alerts-export-csv")
    assert btn.count() == 1, "CSV export button missing"
    assert btn.is_visible(), "CSV export button not visible"
    label = btn.locator("span").inner_text().strip()
    assert label == "CSV", f"CSV button label: got {label!r}"
    return {"label": label}


def _open_add_modal_and_cycle_watch_types(page) -> dict:
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    groups = {
        "holder":    "cwl-holder-fields",
        "reference": "cwl-reference-fields",
        "region":    "cwl-region-fields",
        "lifecycle": "cwl-lifecycle-fields",
    }
    for wt, group_id in groups.items():
        page.locator(
            f'input[name="cwl-watch-type"][value="{wt}"]'
        ).click()
        page.wait_for_timeout(150)
        assert page.locator(f"#{group_id}").is_visible(), (
            f"watch_type={wt} did not reveal {group_id}"
        )
        for other_wt, other_group in groups.items():
            if other_wt == wt:
                continue
            assert not page.locator(f"#{other_group}").is_visible(), (
                f"watch_type={wt}: {other_group} still visible "
                f"(should be hidden)"
            )
    # Close the modal afterwards.
    page.locator("#cwl-add-close").click()
    page.wait_for_timeout(300)
    return {"watch_types_cycled": list(groups.keys())}


# ---------------------------------------------------------------------------
# Round-trip lifecycle: create -> scan -> export -> delete
# ---------------------------------------------------------------------------

# Per-run label suffix so re-runs don't collide on the unique
# (organization_id, label) constraint and so the finally-block
# cleanup can find this run's item even if the test fails midway.
ROUND_TRIP_LABEL = f"BROWSER SMOKE region {int(time.time())}"


def _create_region_watch_item(page) -> dict:
    """Open the add modal, switch to watch_type=region, fill the form,
    submit (capturing the POST response so 4xx surfaces with detail),
    and verify the new item appears in the list."""
    page.locator("#cwl-btn-add").click()
    page.wait_for_selector("#cwl-add-modal", state="visible", timeout=5000)
    page.locator('input[name="cwl-watch-type"][value="region"]').click()
    page.wait_for_selector("#cwl-region-fields", state="visible", timeout=3000)
    page.locator("#cwl-add-label").fill(ROUND_TRIP_LABEL)
    page.locator("#cwl-add-region-query").fill("Konya")
    # alert_email defaults to checked + alert_webhook defaults to off;
    # leave the daily frequency default. No need to fill gi_type or
    # section_keys — they're optional narrowing filters.
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/cografi-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#cwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        # Surface the server's error body in the failure message so
        # plan-gate / quota / validation issues are diagnosable.
        try:
            body = response.text()[:400]
        except Exception:
            body = "<unreadable>"
        raise AssertionError(
            f"POST /cografi-watchlist returned {response.status}: {body}"
        )
    # The modal closes on success + the list refreshes; wait for our
    # label to appear in the items list.
    try:
        page.wait_for_selector(
            f"#cwl-list h4:has-text({ROUND_TRIP_LABEL!r})",
            timeout=15000,
        )
    except Exception:
        # Dump diagnostics so the failure is debuggable. The POST
        # returned 200 above, so the item exists server-side; the
        # JS-side refreshAll() must have raced or the label is being
        # transformed before render.
        list_html = page.locator("#cwl-list").inner_html()[:1500]
        item_count = page.locator("#cwl-list > div").count()
        raise AssertionError(
            f"created item not in list after 15s; "
            f"#cwl-list has {item_count} child div(s). "
            f"First 1500 chars of innerHTML: {list_html!r}"
        )
    return {"label": ROUND_TRIP_LABEL}


def _find_round_trip_item_row(page):
    """Locate the rendered list row that contains our timestamped label.
    Returns the row Locator (or None if not found)."""
    rows = page.locator("#cwl-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if ROUND_TRIP_LABEL in row.inner_text():
            return row
    return None


def _scan_round_trip_item(page) -> dict:
    """Click the per-item Scan button on our test item and wait for the
    POST /scan to return. Asserts the response was 200."""
    row = _find_round_trip_item_row(page)
    assert row is not None, f"round-trip item not found in list: {ROUND_TRIP_LABEL!r}"
    scan_btn = row.locator("[data-cwl-scan]")
    assert scan_btn.count() == 1, "scan button missing on round-trip item row"
    with page.expect_response(
        lambda r: "/api/v1/cografi-watchlist/" in r.url
                  and r.url.endswith("/scan")
                  and r.request.method == "POST",
        timeout=20000,
    ) as resp_info:
        scan_btn.first.click()
    response = resp_info.value
    assert response.status == 200, (
        f"scan POST returned {response.status}: {response.text()[:200]}"
    )
    # The scan handler returns {alerts_created: N, ...}
    body = response.json()
    return {"alerts_created": body.get("alerts_created", 0)}


def _click_export_and_capture_download(page) -> dict:
    """Click the CSV export button + intercept the resulting browser
    download. Verify the saved blob starts with the UTF-8 BOM and that
    the first line is the Turkish header row."""
    with page.expect_download(timeout=15000) as dl_info:
        page.locator("#cwl-alerts-export-csv").click()
    download = dl_info.value
    saved_path = Path("tests/browser/artifacts") / "cografi_smoke_export.csv"
    saved_path.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(saved_path))
    raw = saved_path.read_bytes()
    # UTF-8 BOM == EF BB BF
    assert raw[:3] == b"\xef\xbb\xbf", (
        f"CSV missing UTF-8 BOM; first bytes: {raw[:6]!r}"
    )
    first_line = raw[3:].split(b"\n", 1)[0].decode("utf-8")
    # Two expected Turkish header words must appear in the first line
    for needle in ("Uyarı ID", "Coğrafi İşaret Adı"):
        assert needle in first_line, (
            f"CSV header missing {needle!r}; first line: {first_line!r}"
        )
    return {
        "size_bytes": len(raw),
        "filename": download.suggested_filename,
    }


def _delete_round_trip_item(page) -> dict:
    """Delete our test item via its delete button. The JS uses a
    native confirm() dialog — accept it via page.on('dialog'). Wait
    for the item to disappear from the list."""
    row = _find_round_trip_item_row(page)
    if row is None:
        # Item was never created or already cleaned up — nothing to do.
        return {"deleted": False, "reason": "item not found in list"}
    del_btn = row.locator("[data-cwl-delete]")
    assert del_btn.count() == 1, "delete button missing on round-trip item row"

    # Auto-accept the native confirm() prompt the JS fires before DELETE.
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    # Wait for the list to refresh and our label to be gone.
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _find_round_trip_item_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError(
        f"round-trip item not removed from list after delete: {ROUND_TRIP_LABEL!r}"
    )


# ---------------------------------------------------------------------------
# Locale switching
# ---------------------------------------------------------------------------

def _switch_locale_and_assert_panel_title(
    page,
    *,
    lang: str,
    expected_title_substr: str,
    expected_dir: str | None = None,
) -> dict:
    """Switch to the given locale and assert the cografi watchlist
    panel title renders the expected language substring. Optionally
    asserts the html dir attribute (rtl for AR)."""
    page.evaluate(
        f"window.AppI18n && window.AppI18n.setLocale && "
        f"window.AppI18n.setLocale('{lang}')"
    )
    page.wait_for_timeout(500)
    # The Coğrafi watchlist tab button label changes per locale
    # (Coğrafi / Cografi / المؤشر الجغرافي). Re-locate it by the
    # English-stable id of the tab content. The localized tab button
    # text we use only for the click selector.
    locale_button_substr = {
        "tr": "Coğrafi",
        "en": "GI",
        "ar": "المؤشر الجغرافي",
    }[lang]
    page.locator(
        f"#tab-content-watchlist button:has-text('{locale_button_substr}')"
    ).first.click()
    page.wait_for_timeout(500)
    title_count = page.locator(
        f"#tab-content-watchlist h2:has-text('{expected_title_substr}')"
    ).count()
    assert title_count >= 1, (
        f"locale={lang}: panel title with substring {expected_title_substr!r} "
        f"not rendered"
    )
    html_dir = page.evaluate(
        "() => document.documentElement.getAttribute('dir') || ''"
    )
    if expected_dir is not None:
        assert html_dir == expected_dir, (
            f"locale={lang}: expected html dir={expected_dir!r}, got {html_dir!r}"
        )
    return {"locale": lang, "html_dir": html_dir, "title_count": title_count}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_cografi_dashboard_browser_smoke():
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as member persona", REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
            )

            # --- Search subview (J-1b) -------------------------------
            run_browser_step(
                "Open search tab", REPORTER, page, monitor, CONFIG,
                lambda: _open_search_tab(page),
            )
            run_browser_step(
                "Click Coğrafi search subtab", REPORTER, page, monitor, CONFIG,
                lambda: _click_cografi_search_subtab(page),
            )
            run_browser_step(
                "Autocomplete dropdown renders correct kind chips",
                REPORTER, page, monitor, CONFIG,
                lambda: _autocomplete_fires(page, "Karapınar"),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Run search via Enter key",
                REPORTER, page, monitor, CONFIG,
                lambda: _run_search_via_enter(page),
            )
            run_browser_step(
                "First result opens detail modal",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_first_result_detail_modal(page),
            )
            run_browser_step(
                "Close detail modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_detail_modal(page),
            )

            # --- Watchlist subview (J-2) -----------------------------
            run_browser_step(
                "Open watchlist tab", REPORTER, page, monitor, CONFIG,
                lambda: _open_watchlist_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Click Coğrafi watchlist subtab + 6-cell stats bar",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_cografi_watchlist_subtab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Alerts CSV export button present (J-2.5)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_alerts_export_button_present(page),
            )
            run_browser_step(
                "Add modal: 4 watch_type radios toggle right field groups",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_add_modal_and_cycle_watch_types(page),
            )

            # --- Round-trip lifecycle ------------------------------------
            # Create -> scan -> export -> delete. Cleanup runs in the
            # `finally` so even partial failure leaves no test data.
            # The flag is gated on the create step's actual return so
            # cleanup only fires when create succeeded.
            round_trip_created = False
            try:
                round_trip_created = run_browser_step(
                    "Create region watch item",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_region_watch_item(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if round_trip_created:
                    run_browser_step(
                        "Scan the round-trip item",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _scan_round_trip_item(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )
                    run_browser_step(
                        "CSV export downloads with BOM + Turkish headers",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _click_export_and_capture_download(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )
            finally:
                if round_trip_created:
                    run_browser_step(
                        "Cleanup: delete the round-trip item",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_round_trip_item(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            # --- Locale switching ------------------------------------
            run_browser_step(
                "Switch to TR locale + panel title renders Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Coğrafi İşaret Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to AR locale + RTL + Arabic panel title",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="ar",
                    expected_title_substr="مراقبة المؤشرات الجغرافية",
                    expected_dir="rtl",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch back to TR locale (cleanup)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Coğrafi İşaret Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Cografi dashboard browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_cografi_dashboard_browser_smoke()
