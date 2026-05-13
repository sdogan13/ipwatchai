"""
Live browser smoke for professional email visibility across the UI.

Verifies that the 4 customer-facing addresses appear in the right places
with their localized labels rendered by Alpine, and that the mailto
hrefs are wired correctly. Run directly:

    python tests/browser/test_email_visibility_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, login_via_modal, open_url

pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_email_visibility_smoke.py"
)


def _assert_mailto_in_dom(page, mailto: str, where: str) -> None:
    count = page.locator(f'a[href="{mailto}"]').count()
    if count == 0:
        raise AssertionError(f"{where}: missing link to {mailto}")


def run_smoke() -> None:
    config = load_browser_config()
    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, config)
        try:
            # 1. Landing footer
            open_url(page, config, "/")
            # Wait for Alpine to render localized text
            page.locator('a[href="mailto:info@ipwatchai.com"]').first.wait_for(
                state="visible", timeout=config.timeout_ms
            )
            for addr in ("info", "support", "sales", "billing"):
                _assert_mailto_in_dom(
                    page, f"mailto:{addr}@ipwatchai.com", f"Landing footer"
                )

            # 2. Pricing page Enterprise tier
            open_url(page, config, "/pricing")
            page.locator(
                'a[href^="mailto:sales@ipwatchai.com"]'
            ).first.wait_for(state="visible", timeout=config.timeout_ms)
            sales_count = page.locator('a[href^="mailto:sales@ipwatchai.com"]').count()
            if sales_count < 2:
                raise AssertionError(
                    f"Pricing page: expected >=2 sales@ links (Talk-to-Sales CTA + contact strip), got {sales_count}"
                )

            # 3. Checkout page billing strip
            open_url(page, config, "/checkout?plan=starter")
            page.locator(
                'a[href="mailto:billing@ipwatchai.com"]'
            ).first.wait_for(state="visible", timeout=config.timeout_ms)
            _assert_mailto_in_dom(
                page, "mailto:billing@ipwatchai.com", "Checkout page"
            )

            # 4. Dashboard user menu Help link
            login_via_modal(page, config, monitor)
            # Open the user menu (alpine x-data scope)
            page.locator(
                'button:has(img[alt="Avatar"]), button:has(div.bg-indigo-600)'
            ).first.click()
            help_link = page.locator(
                'a[href="mailto:support@ipwatchai.com"]'
            ).first
            help_link.wait_for(state="visible", timeout=config.timeout_ms)
            help_text = (help_link.text_content() or "").strip().lower()
            # Should contain Help/Support translation (EN default or TR if user is TR)
            if not any(
                kw in help_text for kw in ("help", "support", "yardım", "destek", "المساعدة", "الدعم")
            ):
                raise AssertionError(
                    f"Dashboard user menu Help link text unexpected: {help_text!r}"
                )

            if monitor.page_errors:
                raise AssertionError(
                    f"page errors during smoke: {monitor.page_errors}"
                )

            print("OK: 4 mailtos visible across landing/pricing/checkout/dashboard; menu Help link renders localized text")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    run_smoke()
