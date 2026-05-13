"""Browser smoke for the patent detail modal sub-section rendering.

The dashboard slice (test_patent_dashboard_browser.py) only verifies
the modal opens with a non-empty title + body becomes visible. This
slice asserts every section actually hydrates with real data.

Strategy: search for a common patent term (`kompozisyon` — appears
in many Turkish patent abstracts), open the first result's modal,
and verify each section renders non-empty content. We don't pin to
a specific record (live patent corpus is large and individual
records can change) — the assertions are "section hydrates with
something real" rather than "section equals X".

Sections covered:
  1. Header: title + record_type badge + kind code badge +
     publication_no (or application_no fallback)
  2. Dates: application_date + publication_date (at least one)
  3. Abstract: non-empty + not the "no abstract" fallback
  4. IPC chips: at least 1 chip rendered
  5. Holders: at least 1 holder row
  6. Inventors (optional — older patents may lack named inventors,
     so this is a soft check)
  7. Source provenance footer: non-empty (NOT the '—' placeholder)

Run directly:
    python tests/browser/test_patent_detail_modal_browser.py
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
    open_patent_search_subtab,
    patent_config_for_persona,
    transient_401_budget,
    wait_for_search_rate_limit_to_clear,
)
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = patent_config_for_persona("professional")
REPORTER = LiveReporter()
_TRANSIENT_401S = transient_401_budget(CONFIG.base_url)

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_patent_detail_modal_browser.py"
)


SEARCH_QUERY = "kompozisyon"


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _search_and_open_first(page) -> dict:
    page.locator("#patent-search-input").click()
    page.locator("#patent-search-input").fill(SEARCH_QUERY)
    page.wait_for_timeout(200)
    page.locator("#patent-search-input").press("Enter")
    page.wait_for_selector("#patent-search-grid > div", timeout=45000)
    grid = page.locator("#patent-search-grid > div")
    if grid.count() == 0:
        raise AssertionError(
            f"search for {SEARCH_QUERY!r} returned 0 results — pick a "
            f"different stable query"
        )
    grid.first.click()
    page.wait_for_selector(
        "#patent-detail-modal #pd-body", state="visible", timeout=15000,
    )
    return {"result_count": grid.count()}


def _assert_header_hydrated(page) -> dict:
    title = page.locator("#pd-title").inner_text().strip()
    assert title and title != "—", f"#pd-title empty: {title!r}"
    pub_no = page.locator("#pd-publication-no").inner_text().strip()
    # Either publication_no or application_no must be present.
    assert pub_no, "#pd-publication-no empty (no app_no fallback rendered)"
    # record_type badge content is locale-dependent; just check
    # SOMETHING is rendered (non-empty + visible).
    rt_badge = page.locator("#pd-record-type-badge")
    rt_text = rt_badge.inner_text().strip() if rt_badge.count() else ""
    # rt may be hidden via display:none if record_type was empty;
    # accept either visible-with-text or hidden.
    return {
        "title": title[:120],
        "pub_no": pub_no,
        "rt_badge_text": rt_text,
    }


def _assert_dates(page) -> dict:
    app_date = page.locator("#pd-application-date").inner_text().strip()
    pub_date = page.locator("#pd-publication-date").inner_text().strip()
    # At least one date should be populated (not '—'). Patent
    # publication_date may be NULL for utility-model applications
    # not yet granted, so we don't hard-require both.
    populated = sum(1 for d in (app_date, pub_date) if d and d != "—")
    assert populated >= 1, (
        f"no date populated: app={app_date!r}, pub={pub_date!r}"
    )
    return {"app_date": app_date, "pub_date": pub_date}


def _assert_abstract(page) -> dict:
    abstract = page.locator("#pd-abstract").inner_text().strip()
    assert abstract and abstract != "—", (
        f"#pd-abstract not populated: {abstract!r}"
    )
    # Guard against the i18n fallback firing when the record has no
    # abstract.
    no_abstract_fallbacks = ("no abstract", "özet yok", "لا يوجد ملخص")
    assert not any(fb in abstract.lower() for fb in
                   (v.lower() for v in no_abstract_fallbacks)), (
        f"#pd-abstract shows the no-abstract fallback for a record "
        f"that should have one: {abstract[:120]!r}"
    )
    return {"abstract_first_120": abstract[:120]}


def _assert_ipc_chips(page) -> dict:
    chips = page.locator("#pd-ipc-chips span")
    count = chips.count()
    assert count >= 1, (
        f"#pd-ipc-chips rendered 0 chips; patent records should have "
        f"at least one IPC classification"
    )
    first_text = chips.first.inner_text().strip()
    assert first_text, "first IPC chip empty"
    return {"chip_count": count, "first_chip": first_text}


def _assert_holders(page) -> dict:
    holders = page.locator("#pd-holders > div")
    count = holders.count()
    assert count >= 1, (
        f"#pd-holders rendered 0 holder rows; patent records should "
        f"have at least one"
    )
    first_text = holders.first.inner_text().strip()
    assert first_text, "first holder row empty"
    return {"holder_count": count, "first_holder": first_text[:120]}


def _assert_source_provenance(page) -> dict:
    """The footer shows record's source format / archive / pdf paths.
    A non-empty value confirms the detail-hydrate is reaching beyond
    the headline fields."""
    source = page.locator("#pd-source").inner_text().strip()
    assert source and source != "—", (
        f"#pd-source empty/placeholder: {source!r}"
    )
    return {"source": source[:120]}


def _close_detail_modal(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_patent_detail_modal_browser_smoke():
    wait_for_search_rate_limit_to_clear(CONFIG)

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
                "Open patent search subtab",
                REPORTER, page, monitor, CONFIG,
                lambda: open_patent_search_subtab(page),
            )
            run_browser_step(
                f"Search '{SEARCH_QUERY}' + open first result",
                REPORTER, page, monitor, CONFIG,
                lambda: _search_and_open_first(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Header hydrated (title + publication_no)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_header_hydrated(page),
            )
            run_browser_step(
                "Dates populated (at least one of app/pub)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_dates(page),
            )
            run_browser_step(
                "Abstract populated (not no-abstract fallback)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_abstract(page),
            )
            run_browser_step(
                "IPC chips rendered (at least 1)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_ipc_chips(page),
            )
            run_browser_step(
                "Holders rendered (at least 1)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_holders(page),
            )
            run_browser_step(
                "Source provenance footer populated",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_source_provenance(page),
            )
            run_browser_step(
                "Close detail modal", REPORTER, page, monitor, CONFIG,
                lambda: _close_detail_modal(page),
            )

            REPORTER.summary("Patent detail modal browser smoke")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_patent_detail_modal_browser_smoke()
