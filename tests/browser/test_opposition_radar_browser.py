"""Browser smoke for the Opposition Radar dashboard tab.

Covers the registry-first switcher introduced when Opposition Radar
moved from a flat 6-button mode row to a Marka / Tasarım / Patent /
Coğrafi structure mirroring the Search and Watchlist tabs:

  * registry switcher toggles the right Alpine x-show container,
  * Marka sub-mode chips still drive switchRadarMode(),
  * Patent sub-category chips drive switchPatentLeadsCategory(),
  * Tasarım + Coğrafi coming-soon placeholders render,
  * localStorage('radarView') is written by the $watch.

Read-only — no DB writes, no cleanup needed.

Run directly:
    python tests/browser/test_opposition_radar_browser.py

Uses the managed-professional persona
(``managed-professional-smoke@example.com``). Professional plan has
``daily_lead_views = 10`` so the backend lead endpoints don't 403
during the smoke — keeps the test focused on UI wiring rather than
paywall handling. The persona auto-provisions on first run via
``tests/live/helpers/test_accounts.py``. Override with
``TEST_EMAIL`` / ``TEST_PASSWORD`` env vars to swap in a different
account.
"""
from __future__ import annotations

import re
import sys
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
    reason="Browser E2E script; run directly with python tests/browser/test_opposition_radar_browser.py"
)


# The lead endpoints can briefly 401 during page boot before the auth
# header is wired up. Same pattern as the cografi smoke.
_TRANSIENT_401S = (
    f"401 GET {CONFIG.base_url}/api/v1/leads",
    f"401 GET {CONFIG.base_url}/api/v1/patent-leads",
)


# Stable Turkish substrings for the coming-soon panels. The smoke runs
# under the app's default locale (TR), matching the cografi smoke's
# `:has-text('Coğrafi')` convention.
_TASARIM_COMING_SOON_SUBSTR = "Tasarım fırsat radarı"
_COGRAFI_COMING_SOON_SUBSTR = "Coğrafi İşaret fırsat radarı"


def _registry_switcher_button(page, label: str):
    """Locate one of the 4 top-level registry buttons. They live in the
    first ``inline-flex`` row inside the Opposition Radar tab and have
    no stable IDs (Alpine ``@click`` handlers), so we scope by visible
    text. ``label`` is the localized button text (TR by default)."""
    return page.locator(
        f"#tab-content-opposition-radar > div.inline-flex button:has-text('{label}')"
    ).first


def _open_opposition_radar_tab(page) -> dict:
    page.evaluate("window.showDashboardTab('opposition-radar')")
    page.wait_for_selector(
        "#tab-content-opposition-radar:not(.hidden)", timeout=5000
    )
    return {"ok": True}


def _assert_default_marka_visible(page) -> dict:
    """After the tab opens, the Marka registry is the default view: the
    sub-mode chip strip should be visible and the conflicts section
    should be the active sub-mode."""
    page.wait_for_selector("#radar-mode-conflicts", state="visible", timeout=5000)
    page.wait_for_selector(
        "#radar-conflicts-section:not(.hidden)", timeout=5000
    )
    # localStorage may not be set on a fresh session — that's fine, the
    # in-memory Alpine default is 'marka'.
    return {"conflicts_section_visible": True}


def _switch_to_patent_registry(page) -> dict:
    """Click the Patent registry button and assert the patent section
    becomes visible while the Marka chip strip is hidden by Alpine
    ``x-show``."""
    _registry_switcher_button(page, "Patent").click()
    # Patent section visible
    page.wait_for_selector("#radar-patent-section", state="visible", timeout=5000)
    # Marka chip strip hidden via the parent x-show on the marka registry
    # wrapper. Use is_visible() rather than wait_for_selector(state=hidden)
    # because the chip element still exists in the DOM, just inside a
    # display:none ancestor.
    conflicts_chip_visible = page.locator("#radar-mode-conflicts").is_visible()
    assert not conflicts_chip_visible, (
        "Marka sub-mode chip still visible after switching to Patent registry"
    )
    return {"patent_section_visible": True}


def _rgb_channels(value: str) -> tuple[int, int, int] | None:
    """Parse the first three integer channels from a CSS color value.
    Accepts both ``rgb(r, g, b)`` and ``rgba(r, g, b, a)`` — browser
    computed-style serialization varies between the two for the same
    color, so we normalize to channels for the equality check."""
    match = re.search(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", value or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _click_each_patent_subcategory_chip(page) -> dict:
    """Click each of the 4 patent sub-category chips and assert the
    clicked chip gets the active background style applied. Verifies
    switchPatentLeadsCategory() in patent_leads.js wires up properly."""
    chips = ("patent-cat-lapse", "patent-cat-transfer", "patent-cat-license", "patent-cat-rejected")
    primary_raw = page.evaluate(
        "() => {"
        " const probe = document.createElement('div');"
        " probe.style.background = 'var(--color-primary)';"
        " document.body.appendChild(probe);"
        " const rgb = getComputedStyle(probe).backgroundColor;"
        " probe.remove();"
        " return rgb;"
        "}"
    )
    primary = _rgb_channels(primary_raw)
    assert primary is not None, (
        f"could not resolve --color-primary computed value (got {primary_raw!r})"
    )
    activated: list[str] = []
    for chip_id in chips:
        btn = page.locator(f"#{chip_id}")
        assert btn.count() == 1, f"chip {chip_id} not in DOM"
        btn.click()
        page.wait_for_timeout(150)  # let the inline style update
        bg_raw = page.evaluate(
            f"() => getComputedStyle(document.getElementById('{chip_id}')).backgroundColor"
        )
        bg = _rgb_channels(bg_raw)
        assert bg == primary, (
            f"chip {chip_id} after click: expected background channels {primary!r} "
            f"(from {primary_raw!r}), got {bg!r} (from {bg_raw!r})"
        )
        activated.append(chip_id)
    # Leave the patent registry on the default (lapse) chip so subsequent
    # steps see a clean state.
    page.locator("#patent-cat-lapse").click()
    page.wait_for_timeout(100)
    return {"chips_activated": activated}


def _switch_to_tasarim_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Tasarım").click()
    # The coming-soon panel renders the localized hint text.
    locator = page.locator(
        f"#tab-content-opposition-radar p:has-text({_TASARIM_COMING_SOON_SUBSTR!r})"
    )
    locator.wait_for(state="visible", timeout=5000)
    # Patent + Marka containers should be hidden.
    assert not page.locator("#radar-patent-section").is_visible(), (
        "Patent section still visible after switching to Tasarım"
    )
    assert not page.locator("#radar-conflicts-section").is_visible(), (
        "Marka conflicts section still visible after switching to Tasarım"
    )
    return {"coming_soon_visible": True}


def _switch_to_cografi_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Coğrafi").click()
    locator = page.locator(
        f"#tab-content-opposition-radar p:has-text({_COGRAFI_COMING_SOON_SUBSTR!r})"
    )
    locator.wait_for(state="visible", timeout=5000)
    return {"coming_soon_visible": True}


def _switch_back_to_marka(page) -> dict:
    _registry_switcher_button(page, "Marka").click()
    page.wait_for_selector("#radar-mode-conflicts", state="visible", timeout=5000)
    page.wait_for_selector(
        "#radar-conflicts-section:not(.hidden)", timeout=5000
    )
    return {"marka_visible_again": True}


def _click_renewals_sub_mode(page) -> dict:
    """Click the renewals chip — proves switchRadarMode('renewals') still
    drives Marka sub-mode switching after the registry-first refactor."""
    page.locator("#radar-mode-renewals").click()
    page.wait_for_selector(
        "#radar-renewals-section:not(.hidden)", timeout=5000
    )
    # Conflicts section hides when renewals is active.
    assert not page.locator("#radar-conflicts-section").is_visible(), (
        "conflicts section still visible after clicking renewals chip"
    )
    # Reset to conflicts to leave a clean state.
    page.locator("#radar-mode-conflicts").click()
    page.wait_for_selector(
        "#radar-conflicts-section:not(.hidden)", timeout=5000
    )
    return {"renewals_section_visible": True}


def _assert_localstorage_radarview_marka(page) -> dict:
    """After the Tasarım/Coğrafi/Patent/Marka round-trip the Alpine
    ``$watch`` should have written 'marka' into localStorage."""
    value = page.evaluate("() => localStorage.getItem('radarView')")
    assert value == "marka", (
        f"localStorage.radarView expected 'marka', got {value!r}"
    )
    return {"radarView": value}


def test_opposition_radar_browser_smoke():
    REPORTER.print_heading(
        "Opposition Radar browser smoke", server=CONFIG.base_url
    )
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(
            playwright, CONFIG,
        )
        try:
            run_browser_step(
                "Login as managed-professional persona",
                REPORTER, page, monitor, CONFIG,
                lambda: login_via_modal(page, CONFIG, monitor),
            )

            run_browser_step(
                "Open Opposition Radar tab",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_opposition_radar_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Default registry is Marka (conflicts section visible)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_default_marka_visible(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to Patent registry (x-show toggles)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_patent_registry(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Patent sub-category chips toggle active styling",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_each_patent_subcategory_chip(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to Tasarım registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_tasarim_coming_soon(page),
            )
            run_browser_step(
                "Switch to Coğrafi registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_cografi_coming_soon(page),
            )
            run_browser_step(
                "Switch back to Marka registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_back_to_marka(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Marka sub-mode chips: click renewals + back to conflicts",
                REPORTER, page, monitor, CONFIG,
                lambda: _click_renewals_sub_mode(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "localStorage.radarView persists last registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_localstorage_radarview_marka(page),
            )

            failures = REPORTER.summary("Opposition Radar browser smoke")
            if failures:
                raise AssertionError(
                    f"{failures} Opposition Radar smoke step(s) failed"
                )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        test_opposition_radar_browser_smoke()
    except AssertionError as exc:
        print(exc)
        sys.exit(1)
    sys.exit(0)
