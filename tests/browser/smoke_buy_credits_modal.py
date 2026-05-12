"""One-off browser smoke for the combined buy-credits / upgrade-plan modal.

Logs in as the managed-free-smoke persona, opens AI Studio, fires off a name
generation request (Free has 0 monthly AI credits + 0 purchased credits, so
the backend returns 402 and the frontend should open the combined modal).
We capture:
  * which modals are visible after the trigger
  * size / position of each modal
  * a screenshot saved under tests/browser/artifacts/
  * any console errors

Intentionally NOT a pytest test — run directly:
    python tests/browser/smoke_buy_credits_modal.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from tests.browser.helpers.cografi import cografi_config_for_persona
from tests.browser.helpers.session import launch_browser_page, login_via_modal, open_url
from tests.live.helpers.test_accounts import ensure_managed_persona_account


ARTIFACTS = ROOT / "tests" / "browser" / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def main() -> int:
    print("=== buy-credits modal smoke ===")
    # Make sure the Free persona exists with the right plan + zero credits.
    info = ensure_managed_persona_account("free")
    print(f"persona ready: {info['email']} org={info['organization_id']}")

    config = cografi_config_for_persona("free")
    # Force headed mode so a human can also watch if they choose; the script
    # is fully automated either way.
    import os
    os.environ["TEST_BROWSER_HEADLESS"] = "1"
    config = cografi_config_for_persona("free")

    with sync_playwright() as pw:
        browser, context, page, monitor = launch_browser_page(pw, config)
        try:
            login_via_modal(page, config, monitor)
            print("logged in OK")

            # Switch to AI Studio tab
            page.evaluate("window.showDashboardTab('ai-studio')")
            page.wait_for_selector("#tab-content-ai-studio:not(.hidden)", timeout=10000)
            page.wait_for_selector("#studio-name-panel", state="visible", timeout=10000)

            # Fill required fields for name generation
            page.fill("#studio-name-query", "TestBrand")
            page.fill("#studio-name-industry", "Software")
            # Pick Nice class 9 via the class picker:
            page.click("#studio-name-classes-toggle")
            # Class 9 chip — search for a button inside the picker with label
            page.wait_for_selector("#studio-name-classes:not(.hidden)", timeout=5000)
            # Each class is a button. Click the one for class 9 by exact text.
            page.locator(
                "#studio-name-classes button:has-text('9')"
            ).first.click()
            # Close picker
            page.click("#studio-name-classes-toggle")

            # Fire generation — expect a 402 from the server
            generation_response = None
            with page.expect_response(
                lambda r: "/api/v1/tools/suggest-names" in r.url,
                timeout=20000,
            ) as resp_info:
                page.click("#studio-name-btn")
            generation_response = resp_info.value
            print(f"suggest-names -> {generation_response.status}")
            try:
                body = generation_response.json()
                print(f"response body: {json.dumps(body)[:300]}")
            except Exception:
                pass

            # Give the modal a beat to open
            page.wait_for_timeout(500)

            # Snapshot which modals are visible
            modals_state = page.evaluate("""
                () => {
                    const bc = document.getElementById('buy-credits-modal');
                    const um = document.getElementById('upgrade-modal');
                    function box(el) {
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        const cs = window.getComputedStyle(el);
                        return {
                            id: el.id,
                            hidden: el.classList.contains('hidden'),
                            display: cs.display,
                            visibility: cs.visibility,
                            zIndex: cs.zIndex,
                            x: r.x, y: r.y, w: r.width, h: r.height
                        };
                    }
                    return {
                        buyCredits: box(bc),
                        upgrade: box(um),
                        viewport: { w: window.innerWidth, h: window.innerHeight }
                    };
                }
            """)
            print("modal state:")
            print(json.dumps(modals_state, indent=2))

            # Also describe the buy-credits modal's internal columns
            columns_state = page.evaluate("""
                () => {
                    const picker = document.getElementById('buy-credits-picker');
                    const plans = document.getElementById('buy-credits-plans');
                    const packs = document.getElementById('buy-credits-packs');
                    function box(el) {
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {
                            id: el.id,
                            tag: el.tagName,
                            classes: el.className,
                            childCount: el.children.length,
                            x: r.x, y: r.y, w: r.width, h: r.height,
                            firstChildText: el.firstElementChild
                                ? el.firstElementChild.innerText.slice(0, 100)
                                : null
                        };
                    }
                    return {
                        picker: box(picker),
                        plans: box(plans),
                        packs: box(packs)
                    };
                }
            """)
            print("columns state:")
            print(json.dumps(columns_state, indent=2))

            # Screenshot whole viewport
            shot_path = ARTIFACTS / "buy_credits_modal_smoke.png"
            page.screenshot(path=str(shot_path), full_page=False)
            print(f"screenshot: {shot_path}")

            # Console errors / 4xx network failures
            if monitor.console_errors:
                print("console errors:")
                for e in monitor.console_errors:
                    print(f"  - {e}")
            interesting_failures = [
                f for f in monitor.request_failures
                if "402" not in f  # 402 from suggest-names is expected
                and "401" not in f  # transient page-boot 401s
            ]
            if interesting_failures:
                print("unexpected request failures:")
                for f in interesting_failures:
                    print(f"  - {f}")

            return 0
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
