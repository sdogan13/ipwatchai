"""Browser smoke for the AI Studio dashboard tab.

Covers the registry-first switcher: Marka / Tasarım / Patent. The
existing Name Lab + Logo Studio content lives under the Marka
registry; the other two are "coming soon" placeholders pending their
own studio implementations. (Coğrafi is intentionally absent from
AI Studio — no name/logo generation use case there.)

  * registry switcher toggles the right Alpine x-show container,
  * Marka registry renders the existing studio-shell + mode toggle,
  * Tasarım + Patent coming-soon placeholders render,
  * localStorage('studioView') is written by the $watch.

Read-only — no AI Studio runs are kicked off, no DB writes, no
credits consumed.

Run directly:
    python tests/browser/test_ai_studio_browser.py

Uses the managed-professional persona
(``managed-professional-smoke@example.com``). Professional plan has
``monthly_ai_credits = 50`` so AI Studio is unlocked. The persona
auto-provisions on first run via ``tests/live/helpers/test_accounts.py``.
Override with ``TEST_EMAIL`` / ``TEST_PASSWORD`` env vars to swap in
a different account.
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
from tests.browser.helpers.cografi import cografi_config_for_persona
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = cografi_config_for_persona("professional")
REPORTER = LiveReporter()

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_ai_studio_browser.py"
)


# AI Studio surfaces /api/v1/tools/status + /api/v1/usage/credits on
# tab init; both can briefly 401 during page boot before the auth
# header is wired up. Same allowlist pattern as the cografi + radar
# smokes.
_TRANSIENT_401S = (
    f"401 GET {CONFIG.base_url}/api/v1/tools/status",
    f"401 GET {CONFIG.base_url}/api/v1/usage/credits",
    f"401 GET {CONFIG.base_url}/api/v1/tools/creative-history",
)


# Stable Turkish substrings for the coming-soon panels (the smoke runs
# under the app's default TR locale, matching other smokes' convention).
_TASARIM_COMING_SOON_SUBSTR = "Tasarım Stüdyosu"
_PATENT_COMING_SOON_SUBSTR = "Patent Stüdyosu"


def _registry_switcher_button(page, label: str):
    """Locate one of the 4 top-level registry buttons. They live in the
    first ``inline-flex`` row inside the AI Studio tab and have no
    stable IDs (Alpine ``@click`` handlers), so we scope by visible
    text. ``label`` is the localized button text (TR by default)."""
    return page.locator(
        f"#tab-content-ai-studio > div.inline-flex button:has-text('{label}')"
    ).first


def _open_ai_studio_tab(page) -> dict:
    page.evaluate("window.showDashboardTab('ai-studio')")
    page.wait_for_selector(
        "#tab-content-ai-studio:not(.hidden)", timeout=5000
    )
    return {"ok": True}


def _assert_default_marka_visible(page) -> dict:
    """After the tab opens, the Marka registry is the default view:
    the studio-shell (Name Lab + Logo Studio) should be visible."""
    page.wait_for_selector(
        "#tab-content-ai-studio section.studio-shell", state="visible", timeout=5000
    )
    # The Name Lab mode toggle exists in the studio shell.
    page.wait_for_selector("#studio-mode-name", state="visible", timeout=5000)
    return {"studio_shell_visible": True}


def _switch_to_tasarim_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Tasarım").click()
    locator = page.locator(
        f"#tab-content-ai-studio p:has-text({_TASARIM_COMING_SOON_SUBSTR!r})"
    )
    locator.wait_for(state="visible", timeout=5000)
    # The studio-shell (Marka content) must be hidden behind its x-show.
    assert not page.locator("#tab-content-ai-studio section.studio-shell").is_visible(), (
        "Marka studio shell still visible after switching to Tasarım"
    )
    return {"coming_soon_visible": True}


def _switch_to_patent_coming_soon(page) -> dict:
    _registry_switcher_button(page, "Patent").click()
    locator = page.locator(
        f"#tab-content-ai-studio p:has-text({_PATENT_COMING_SOON_SUBSTR!r})"
    )
    locator.wait_for(state="visible", timeout=5000)
    return {"coming_soon_visible": True}


def _switch_back_to_marka(page) -> dict:
    _registry_switcher_button(page, "Marka").click()
    page.wait_for_selector(
        "#tab-content-ai-studio section.studio-shell", state="visible", timeout=5000
    )
    # Coming-soon panels must be hidden now.
    assert not page.locator(
        f"#tab-content-ai-studio p:has-text({_TASARIM_COMING_SOON_SUBSTR!r})"
    ).is_visible(), "Tasarım coming-soon still visible after switching to Marka"
    return {"marka_visible_again": True}


def _assert_localstorage_studioview_marka(page) -> dict:
    """After the round-trip the Alpine ``$watch`` should have written
    'trademark' into localStorage. (The state name is 'trademark'
    internally even though the visible label is 'Marka'.)"""
    value = page.evaluate("() => localStorage.getItem('studioView')")
    assert value == "trademark", (
        f"localStorage.studioView expected 'trademark', got {value!r}"
    )
    return {"studioView": value}


def test_ai_studio_browser_smoke():
    REPORTER.print_heading(
        "AI Studio browser smoke", server=CONFIG.base_url
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
                "Open AI Studio tab",
                REPORTER, page, monitor, CONFIG,
                lambda: _open_ai_studio_tab(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Default registry is Marka (studio shell visible)",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_default_marka_visible(page),
                allow_request_failures=_TRANSIENT_401S,
            )
            run_browser_step(
                "Switch to Tasarım registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_tasarim_coming_soon(page),
            )
            run_browser_step(
                "Switch to Patent registry (coming-soon panel)",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_to_patent_coming_soon(page),
            )
            run_browser_step(
                "Switch back to Marka registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _switch_back_to_marka(page),
            )
            run_browser_step(
                "localStorage.studioView persists last registry",
                REPORTER, page, monitor, CONFIG,
                lambda: _assert_localstorage_studioview_marka(page),
            )

            failures = REPORTER.summary("AI Studio browser smoke")
            if failures:
                raise AssertionError(
                    f"{failures} AI Studio smoke step(s) failed"
                )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        test_ai_studio_browser_smoke()
    except AssertionError as exc:
        print(exc)
        sys.exit(1)
    sys.exit(0)
