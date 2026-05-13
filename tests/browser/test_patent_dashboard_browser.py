"""Browser smoke for the patent (Patent / Faydalı Model) dashboard tabs.

Foundational slice for the patent UI coverage suite. Covers the
end-to-end happy path: search subview + result card + detail
modal + watchlist subview + add modal + 2-watch-type radios + CSV
export button + round-trip create/scan/export/delete lifecycle for
a reference watch + EN/TR/AR locale switching including AR RTL.

Mirrors the cografi foundational smoke
(``test_cografi_dashboard_browser.py``) but adapted for patent:
  - 2 watch types (holder + reference) vs cografi's 4
  - 4-cell stats bar (total + 2 watch_types + new_alerts) vs
    cografi's 6
  - Result-card click → ``[data-pd-open]`` opens
    ``#patent-detail-modal`` (cografi uses ``[data-cd-open]`` →
    ``#cografi-detail-modal``)
  - No autocomplete on the main query input (patent's autocomplete
    is on the IPC field inside the filter panel — covered by
    slice 5)

Run directly:
    python tests/browser/test_patent_dashboard_browser.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from tests.browser.helpers.assertions import run_browser_step
from tests.browser.helpers.patent import (
    cleanup_smoke_items,
    open_patent_search_subtab,
    open_patent_watchlist_subtab,
    patent_config_for_persona,
    slice_label,
    transient_401_budget,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


# Foundational dashboard slice fires several search calls (text +
# locale switch reloads). Use managed-professional (2000/day) rather
# than starter (50/day) — starter is easy to exhaust during dev when
# multiple registries' tests share the same quota bucket.
CONFIG = patent_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_dashboard_browser.py"
)


# Round-trip lifecycle uses a reference watch (free-text query) since
# it's the type the existing patent_search.js docstring describes as
# the more common use case. The other type (holder) is round-tripped
# in slice 3 (test_patent_watch_types_browser.py).
ROUND_TRIP_LABEL = slice_label("dashboard", "reference")


# ---------------------------------------------------------------------------
# Search subview steps
# ---------------------------------------------------------------------------

def _run_search_via_enter(page: Page) -> dict:
    """Pick a stable, common patent term to drive the search.
    'kompozisyon' (composition) appears in many Turkish patent
    abstracts; should reliably return >0 results from the live
    corpus."""
    page.locator("#patent-search-input").click()
    page.locator("#patent-search-input").fill("kompozisyon")
    page.wait_for_timeout(200)
    page.locator("#patent-search-input").press("Enter")
    # Wait for either result cards or empty/error state to settle.
    # patent-search/quick is heavier than text-only — allow 45s.
    page.wait_for_selector(
        "#patent-search-grid > div, #patent-search-empty:not(.hidden)",
        timeout=45000,
    )
    grid = page.locator("#patent-search-grid > div")
    return {"card_count": grid.count()}


def _open_first_result_detail_modal(page: Page) -> dict:
    cards = page.locator("#patent-search-grid > div")
    if cards.count() == 0:
        raise AssertionError(
            "no result cards rendered — can't open detail modal"
        )
    cards.first.click()
    page.wait_for_selector(
        "#patent-detail-modal #pd-body", state="visible", timeout=15000,
    )
    title = page.locator("#pd-title").inner_text().strip()
    assert title and title != "—", f"detail modal title empty: {title!r}"
    return {"title": title[:120]}


def _close_detail_modal(page: Page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Watchlist subview steps
# ---------------------------------------------------------------------------

def _verify_watchlist_subtab(page: Page) -> dict:
    """Assert the 4-cell stats bar rendered (total + holder watches +
    reference watches + new alerts)."""
    stat_cells = page.locator("#pwl-stats-bar > div").count()
    assert stat_cells == 4, (
        f"patent watchlist stats bar should have 4 cells "
        f"(total + 2 watch_types + new_alerts); got {stat_cells}"
    )
    return {"stat_cells": stat_cells}


def _assert_alerts_export_button_present(page: Page) -> dict:
    """Patent already exposes the CSV export button (parallels the
    cografi J-2.5 work). Verify it's wired into the DOM."""
    btn = page.locator("#pwl-alerts-export-csv")
    assert btn.count() == 1, "patent CSV export button missing"
    assert btn.is_visible(), "patent CSV export button not visible"
    return {"present": True}


def _open_add_modal_and_cycle_watch_types(page: Page) -> dict:
    """Patent has 2 watch types (holder + reference). Cycle both
    radios and assert each toggles the right field group."""
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    groups = {
        "holder":    "pwl-holder-fields",
        "reference": "pwl-reference-fields",
    }
    for wt, group_id in groups.items():
        page.locator(f'input[name="pwl-watch-type"][value="{wt}"]').click()
        page.wait_for_timeout(150)
        assert page.locator(f"#{group_id}").is_visible(), (
            f"watch_type={wt} did not reveal {group_id}"
        )
        for other_wt, other_group in groups.items():
            if other_wt == wt:
                continue
            assert not page.locator(f"#{other_group}").is_visible(), (
                f"watch_type={wt}: {other_group} still visible"
            )
    page.locator("#pwl-add-close").click()
    page.wait_for_timeout(300)
    return {"watch_types_cycled": list(groups.keys())}


# ---------------------------------------------------------------------------
# Round-trip lifecycle
# ---------------------------------------------------------------------------

def _create_reference_watch(page: Page) -> dict:
    page.locator("#pwl-btn-add").click()
    page.wait_for_selector("#pwl-add-modal", state="visible", timeout=5000)
    page.locator('input[name="pwl-watch-type"][value="reference"]').click()
    page.wait_for_selector(
        "#pwl-reference-fields", state="visible", timeout=3000,
    )
    page.locator("#pwl-add-label").fill(ROUND_TRIP_LABEL)
    page.locator("#pwl-add-reference-query").fill(
        "Yeni nesil enjeksiyon kalıbı tasarımı; çelik alaşımlı."
    )
    with page.expect_response(
        lambda r: r.url.endswith("/api/v1/patent-watchlist")
                  and r.request.method == "POST",
        timeout=15000,
    ) as resp_info:
        page.locator("#pwl-add-submit").click()
    response = resp_info.value
    if response.status != 200:
        raise AssertionError(
            f"POST /patent-watchlist returned {response.status}: "
            f"{response.text()[:300]}"
        )
    page.wait_for_selector(
        f"#pwl-list h4:has-text({ROUND_TRIP_LABEL!r})", timeout=15000,
    )
    return {"label": ROUND_TRIP_LABEL}


def _find_round_trip_row(page: Page):
    rows = page.locator("#pwl-list > div")
    for i in range(rows.count()):
        row = rows.nth(i)
        if ROUND_TRIP_LABEL in row.inner_text():
            return row
    return None


def _scan_round_trip_item(page: Page) -> dict:
    row = _find_round_trip_row(page)
    assert row is not None, "round-trip item missing from list"
    scan_btn = row.locator("[data-pwl-scan]")
    assert scan_btn.count() == 1, "scan button missing on round-trip row"
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
            f"POST /scan returned {response.status}: {response.text()[:200]}"
        )
    body = response.json()
    page.wait_for_timeout(1500)
    return {"alerts_created": int(body.get("alerts_created") or 0)}


def _click_export_and_capture_download(page: Page) -> dict:
    """Click the CSV export button + intercept the browser download.
    Verify the saved blob starts with the UTF-8 BOM."""
    with page.expect_download(timeout=15000) as dl_info:
        page.locator("#pwl-alerts-export-csv").click()
    download = dl_info.value
    saved_path = Path("tests/browser/artifacts") / "patent_smoke_export.csv"
    saved_path.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(saved_path))
    raw = saved_path.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", (
        f"CSV missing UTF-8 BOM; first bytes: {raw[:6]!r}"
    )
    first_line = raw[3:].split(b"\n", 1)[0].decode("utf-8")
    # Patent CSV headers (Turkish): Uyarı ID / Başvuru No / etc.
    # Match a couple of stable header words.
    assert "Uyarı" in first_line or "ID" in first_line, (
        f"CSV header doesn't look right; first line: {first_line!r}"
    )
    return {"size_bytes": len(raw), "filename": download.suggested_filename}


def _delete_round_trip_item(page: Page) -> dict:
    row = _find_round_trip_row(page)
    if row is None:
        return {"deleted": False, "reason": "row not found"}
    del_btn = row.locator("[data-pwl-delete]")
    if del_btn.count() != 1:
        return {"deleted": False, "reason": "delete button missing"}
    page.once("dialog", lambda d: d.accept())
    del_btn.first.click()
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _find_round_trip_row(page) is None:
            return {"deleted": True}
        page.wait_for_timeout(500)
    raise AssertionError("delete timeout")


# ---------------------------------------------------------------------------
# Locale switching
# ---------------------------------------------------------------------------

def _set_locale(page: Page, lang: str) -> None:
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
    page: Page,
    *,
    lang: str,
    expected_title_substr: str,
    expected_dir: str,
) -> dict:
    _set_locale(page, lang)
    open_patent_watchlist_subtab(page)
    page.wait_for_timeout(500)
    title_count = page.locator(
        f"#tab-content-watchlist h2:has-text('{expected_title_substr}')"
    ).count()
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
    return {"locale": lang, "html_dir": html_dir, "title_count": title_count}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_dashboard_browser_smoke():
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
                "Open patent search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_search_subtab(page),
            )
            run_browser_step(
                "Run search via Enter key",
                REPORTER, page, monitor, CONFIG,
                lambda: _run_search_via_enter(page),
            )
            run_browser_step(
                "First result opens detail modal with hydrated body",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_first_result_detail_modal(page),
            )
            run_browser_step(
                "Close detail modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_detail_modal(page),
            )

            # --- Watchlist subview -------------------------------------
            run_browser_step(
                "Open patent watchlist subtab + 4-cell stats bar",
                REPORTER, page, monitor, CONFIG,
                lambda: (open_patent_watchlist_subtab(page),
                         _verify_watchlist_subtab(page))[1],
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Alerts CSV export button present",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_alerts_export_button_present(page),
            )
            run_browser_step(
                "Add modal: 2 watch_type radios toggle right field groups",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_add_modal_and_cycle_watch_types(page),
            )

            # --- Round-trip lifecycle ----------------------------------
            round_trip_created = False
            try:
                round_trip_created = run_browser_step(
                    "Create reference watch via UI",
                    REPORTER, page, monitor, CONFIG,
                    lambda: _create_reference_watch(page),
                    allow_request_failures=_TRANSIENT_401S,
                )
                if round_trip_created:
                    run_browser_step(
                        "Scan the reference watch (200)",
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
                        "Cleanup: delete round-trip watch via UI",
                        REPORTER, page, monitor, CONFIG,
                        lambda: _delete_round_trip_item(page),
                        allow_request_failures=_TRANSIENT_401S,
                    )

            # --- Locale switching --------------------------------------
            run_browser_step(
                "Switch to TR locale + watchlist panel title in Turkish",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Patent Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to AR locale + RTL + Arabic panel title",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="ar",
                    expected_title_substr="متابعة براءات الاختراع",
                    expected_dir="rtl",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch back to TR locale (cleanup)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_locale_and_assert_panel_title(
                    page, lang="tr",
                    expected_title_substr="Patent Takibi",
                    expected_dir="ltr",
                ),
                allow_request_failures=_TRANSIENT_401S,
            )

            REPORTER.summary("Patent dashboard browser smoke")
        finally:
            context.close()
            browser.close()
            cleanup_smoke_items(CONFIG)


if __name__ == "__main__":
    test_patent_dashboard_browser_smoke()
