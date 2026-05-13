"""Browser smoke: per-registry searches share the unified daily Agentic budget.

The post-Quick-removal architecture collapses the four registry endpoints into
a single shared daily counter (``api_usage.live_searches``). A search against
Design, Patent, or Coğrafi consumes one slot from the same budget that
trademark searches use.

This smoke proves the contract end-to-end:

  * ``POST /api/v1/design-search`` fires from the Design tab and the counter
    increments by 1.
  * ``POST /api/v1/patent-search`` fires from the Patent tab and the counter
    advances again.
  * ``POST /api/v1/cografi-search`` fires from the Cografi tab and the counter
    advances again.
  * Legacy ``/{registry}-search/quick`` URLs no longer appear in any of these
    request paths.

Run directly:
    python tests/browser/test_registry_search_smoke.py

Uses managed-professional persona (2000/day budget — plenty of headroom).
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
from tests.browser.helpers.config import load_browser_config, with_live_credentials
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.personas import (
    BUSINESS_PLANS,
    PersonaSession,
    canonical_plan_name,
    resolve_plan_persona_session,
)


CONFIG = load_browser_config()
REPORTER = LiveReporter()
REGISTRY_TIMEOUT_MS = max(CONFIG.timeout_ms, 120_000)


pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_registry_search_smoke.py"
)


# ===========================================================================
# Per-registry submit helpers
# ===========================================================================

def _fetch_today_used(page) -> int:
    return page.evaluate(
        """async () => {
            const token =
                localStorage.getItem('auth_token') ||
                sessionStorage.getItem('auth_token') || '';
            const headers = token ? { Authorization: `Bearer ${token}` } : {};
            const r = await fetch('/api/v1/usage/summary', { headers });
            const body = await r.json().catch(() => null);
            const block = (body && body.usage && body.usage.daily_live_searches) || {};
            return typeof block.used === 'number' ? block.used : -1;
        }"""
    )


def _submit_search_via_api(page, registry_path: str, payload: dict, timeout_ms: int):
    """Fire a POST to /api/v1/{registry}-search directly via fetch() inside the
    page. Skipping the per-tab UI is intentional — this smoke is about the
    quota counter and URL routing, not per-registry input wiring."""
    return page.evaluate(
        f"""async () => {{
            const token =
                localStorage.getItem('auth_token') ||
                sessionStorage.getItem('auth_token') || '';
            const fd = new FormData();
            const payload = {payload!r};
            for (const [k, v] of Object.entries(payload)) fd.append(k, v);
            const r = await fetch('{registry_path}', {{
                method: 'POST',
                headers: token ? {{ Authorization: `Bearer ${{token}}` }} : {{}},
                body: fd,
            }});
            return {{ status: r.status, url: r.url }};
        }}"""
    )


def _login_and_clear(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def _run_isolated(playwright, browser_config, name: str, action, **kwargs) -> None:
    browser, context, page, monitor = launch_browser_page(playwright, browser_config)
    try:
        run_browser_step(
            name, REPORTER, page, monitor, browser_config,
            lambda: action(page, monitor),
            **kwargs,
        )
    finally:
        context.close()
        browser.close()


# ===========================================================================
# Main
# ===========================================================================

def main() -> None:
    REPORTER.print_heading("REGISTRY SEARCH SHARED-QUOTA SMOKE", server=CONFIG.base_url)

    session, _ = resolve_plan_persona_session(
        REPORTER,
        label="registry search smoke business user",
        email_env="TEST_REGISTRY_SEARCH_EMAIL",
        password_env="TEST_REGISTRY_SEARCH_PASSWORD",
        required_plans=BUSINESS_PLANS,
        fallback_to_default=False,
        provision_plan="professional",
    )
    if session is None:
        REPORTER.warn("REGISTRY SEARCH SMOKE -> skipped (no business persona)")
        sys.exit(0)

    with sync_playwright() as playwright:
        browser_config = with_live_credentials(CONFIG, session.config)

        # ---------------------------------------------------------------
        # STEP 1: design-search advances the daily counter.
        # ---------------------------------------------------------------
        def step_design_share_quota(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            before = _fetch_today_used(page)
            resp = _submit_search_via_api(
                page, "/api/v1/design-search", {"query": "lamp", "limit": "5"},
                REGISTRY_TIMEOUT_MS,
            )
            if resp["status"] != 200:
                raise AssertionError(
                    f"design-search expected 200, got {resp['status']} (url={resp['url']})"
                )
            if "/api/v1/design-search/quick" in resp["url"]:
                raise AssertionError(f"design-search hit deleted /quick endpoint: {resp['url']}")
            after = _fetch_today_used(page)
            if after - before != 1:
                raise AssertionError(
                    f"design-search must consume 1 unit of shared quota, got delta={after - before}"
                )

        _run_isolated(playwright, browser_config,
                      "design-search consumes 1 unit of shared daily quota",
                      step_design_share_quota)

        # ---------------------------------------------------------------
        # STEP 2: patent-search advances the SAME counter.
        # ---------------------------------------------------------------
        def step_patent_share_quota(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            before = _fetch_today_used(page)
            resp = _submit_search_via_api(
                page, "/api/v1/patent-search", {"query": "battery", "limit": "5"},
                REGISTRY_TIMEOUT_MS,
            )
            if resp["status"] != 200:
                raise AssertionError(
                    f"patent-search expected 200, got {resp['status']} (url={resp['url']})"
                )
            if "/api/v1/patent-search/quick" in resp["url"]:
                raise AssertionError(f"patent-search hit deleted /quick endpoint: {resp['url']}")
            after = _fetch_today_used(page)
            if after - before != 1:
                raise AssertionError(
                    f"patent-search must consume 1 unit of shared quota, got delta={after - before}"
                )

        _run_isolated(playwright, browser_config,
                      "patent-search consumes 1 unit of shared daily quota",
                      step_patent_share_quota)

        # ---------------------------------------------------------------
        # STEP 3: cografi-search advances the SAME counter.
        # ---------------------------------------------------------------
        def step_cografi_share_quota(page, monitor) -> None:
            _login_and_clear(page, browser_config, monitor)
            before = _fetch_today_used(page)
            resp = _submit_search_via_api(
                page, "/api/v1/cografi-search", {"query": "antep", "limit": "5"},
                REGISTRY_TIMEOUT_MS,
            )
            if resp["status"] != 200:
                raise AssertionError(
                    f"cografi-search expected 200, got {resp['status']} (url={resp['url']})"
                )
            if "/api/v1/cografi-search/quick" in resp["url"]:
                raise AssertionError(f"cografi-search hit deleted /quick endpoint: {resp['url']}")
            after = _fetch_today_used(page)
            if after - before != 1:
                raise AssertionError(
                    f"cografi-search must consume 1 unit of shared quota, got delta={after - before}"
                )

        _run_isolated(playwright, browser_config,
                      "cografi-search consumes 1 unit of shared daily quota",
                      step_cografi_share_quota)

        failures = REPORTER.summary("REGISTRY SEARCH SHARED-QUOTA SMOKE SUMMARY")
        sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
