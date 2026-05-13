"""Browser smoke for the mobile-friendliness pass.

Covers the changes made when the dashboard / landing / watchlist / detail
modals were patched to fullscreen on phone-class viewports, plus the
responsive-padding tweaks for the dashboard tab toggles and the landing
risk-report grid.

What this suite proves
----------------------
1. **Class hygiene** — the 13 modal locations carry `modal-mobile-fullscreen`
   in their served HTML. A regression that drops the class on any of them
   surfaces here before users see broken mobile layout.

2. **Shared CSS rule** — the mobile fullscreen CSS has `margin: 0 !important`
   so the `mx-4` margin on inner cards is neutralised and the card pins to
   the viewport at `x=0` / `width=viewport`. Verified by reading the served
   `dashboard/page.html` and `marketing/landing.html`.

3. **Live mobile rendering** — at 390x844, after triggering a real
   `credits_exhausted` error from a Free-tier account, the combined buy-
   credits + upgrade modal opens with `bounding_rect.x == 0`, fills the
   viewport, and the inner two-column grid collapses to a single column.

4. **No horizontal overflow** — on mobile, neither the dashboard search
   tab toggle nor the marketing landing page lets `document.scrollWidth`
   exceed `clientWidth` (the regression we fixed by adding responsive
   padding and `min-w-0 sm:min-w-[620px]` on the risk-report grid).

Read-only
---------
Logs in as ``managed-free-smoke@example.com``; the persona auto-provisions
on first run. No watchlist items / leads / payments are created. The Free
persona naturally has 0 monthly AI credits so the buy-credits trigger uses
the real 402 path, not a stub.

Run directly:
    python tests/browser/test_mobile_layout_browser.py
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
from tests.browser.helpers.session import BrowserMonitor, open_url
from tests.live.helpers.assertions import LiveReporter
from tests.live.helpers.test_accounts import ensure_managed_persona_account


CONFIG = cografi_config_for_persona("free")
REPORTER = LiveReporter()
MOBILE_VIEWPORT = {"width": 390, "height": 844}

# The 13 modals patched in this task. Each tuple is
# (template_path_from_repo_root, unique_marker_inside_outer_div). The
# marker must be inside the modal's outermost <div> opening tag so we can
# find that tag and assert it carries `modal-mobile-fullscreen`.
FULLSCREEN_MODAL_FIXTURES = (
    # _search_panel.html lightbox + portfolio — no IDs, scope by x-show attr
    ("templates/dashboard/partials/_search_panel.html", 'x-show="lightboxImage"'),
    ("templates/dashboard/partials/_search_panel.html", 'x-show="showPortfolio"'),
    ("templates/dashboard/partials/_design_detail_modal.html", 'id="design-detail-modal"'),
    ("templates/dashboard/partials/_cografi_detail_modal.html", 'id="cografi-detail-modal"'),
    ("templates/dashboard/partials/_patent_detail_modal.html", 'id="patent-detail-modal"'),
    ("templates/dashboard/partials/_modals.html", 'id="design-watchlist-edit-modal"'),
    ("templates/dashboard/partials/_modals.html", 'id="design-watchlist-upload-modal"'),
    ("templates/dashboard/partials/_modals.html", 'id="events-timeline-modal"'),
    ("templates/dashboard/partials/_watchlist_panel.html", 'id="watchlist-edit-modal"'),
    ("templates/dashboard/partials/_watchlist_panel.html", 'id="watchlist-upload-modal"'),
    ("templates/dashboard/partials/_watchlist_patent_subview.html", 'id="pwl-add-modal"'),
    ("templates/dashboard/partials/_watchlist_cografi_subview.html", 'id="cwl-add-modal"'),
    ("templates/dashboard/partials/_ai_studio_panel.html", 'id="buy-credits-modal"'),
)

# Modals openable purely via JS (their content is hydrated lazily; we only
# verify the outer card pins to the viewport when made visible).
JS_TRIGGERABLE_MOBILE_MODALS = (
    "watchlist-upload-modal",
    "watchlist-edit-modal",
    "design-detail-modal",
    "patent-detail-modal",
    "cografi-detail-modal",
    "events-timeline-modal",
)


pytestmark = pytest.mark.skip(
    reason="Browser E2E script; run directly with python tests/browser/test_mobile_layout_browser.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def _assert_class_in_block(haystack: str, marker: str, klass: str, label: str) -> None:
    """Find the first occurrence of `marker` in `haystack`, then assert that
    the surrounding tag (looking back to the nearest '<div') contains `klass`.
    Cheap structural assertion that doesn't depend on running the app."""
    idx = haystack.find(marker)
    if idx < 0:
        raise AssertionError(f"{label} marker {marker!r} not found")
    tag_start = haystack.rfind("<div", 0, idx)
    if tag_start < 0:
        raise AssertionError(f"{label} could not locate <div opening before marker")
    tag_end = haystack.find(">", idx)
    tag_chunk = haystack[tag_start:tag_end + 1]
    if klass not in tag_chunk:
        raise AssertionError(
            f"{label} is missing class {klass!r}. Tag chunk: {tag_chunk[:200]!r}"
        )


def _login_simple(page) -> None:
    """Login flow that doesn't depend on the desktop nav being visible.

    The shared `login_via_modal` helper waits for `#tab-btn-overview` which
    is `display:none` on mobile; we replace it with a wait on the dashboard
    URL instead.
    """
    open_url(page, CONFIG, "/?login=1")
    page.locator('input[x-model="loginEmail"]').wait_for(state="visible")
    page.locator('input[x-model="loginEmail"]').fill(CONFIG.email)
    page.locator('input[x-model="loginPassword"]').fill(CONFIG.password)
    with page.expect_response(
        lambda r: "/api/v1/auth/login" in r.url, timeout=CONFIG.timeout_ms,
    ) as info:
        page.locator('[role="dialog"] button[type="submit"]').first.click()
    if info.value.status != 200:
        raise AssertionError(f"login expected 200, got {info.value.status}")
    page.wait_for_url("**/dashboard", timeout=CONFIG.timeout_ms)
    page.wait_for_load_state("domcontentloaded", timeout=CONFIG.timeout_ms)


def _assert_no_horizontal_overflow(page, label: str) -> None:
    metrics = page.evaluate(
        """() => ({
            clientWidth: document.documentElement.clientWidth,
            scrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body ? document.body.scrollWidth : 0
        })"""
    )
    widest = max(metrics["scrollWidth"], metrics["bodyScrollWidth"])
    if widest > metrics["clientWidth"] + 1:
        raise AssertionError(
            f"{label} overflows horizontally: "
            f"clientWidth={metrics['clientWidth']}, "
            f"scrollWidth={metrics['scrollWidth']}, "
            f"bodyScrollWidth={metrics['bodyScrollWidth']}"
        )


def _assert_modal_pins_to_viewport(page, modal_id: str) -> dict:
    """Assert the modal's inner card pins to the viewport (x=0, width=W)
    once visible. Returns the measured rect for use in step output."""
    rect = page.evaluate(
        """(id) => {
            const m = document.getElementById(id);
            if (!m) return { error: 'modal element not found' };
            // The inner "card" is the only positioned child div under the wrapper.
            const card = m.querySelector(':scope > div');
            if (!card) return { error: 'inner card not found' };
            const r = card.getBoundingClientRect();
            return {
                x: r.x, y: r.y, w: r.width, h: r.height,
                viewportW: window.innerWidth,
                viewportH: window.innerHeight,
                modalVisible: !m.classList.contains('hidden')
            };
        }""",
        modal_id,
    )
    if "error" in rect:
        raise AssertionError(f"#{modal_id}: {rect['error']}")
    if not rect["modalVisible"]:
        raise AssertionError(f"#{modal_id} did not become visible after open")
    # Tolerate up to 1px sub-pixel drift either side.
    if abs(rect["x"]) > 1:
        raise AssertionError(
            f"#{modal_id} card not pinned to left edge: x={rect['x']}"
        )
    if abs(rect["w"] - rect["viewportW"]) > 1:
        raise AssertionError(
            f"#{modal_id} card width {rect['w']} != viewport {rect['viewportW']} "
            f"(suggests mx-4 overflow regression)"
        )
    return rect


def _open_modal_via_js(page, modal_id: str) -> None:
    page.evaluate(
        """(id) => {
            const m = document.getElementById(id);
            if (m) m.classList.remove('hidden');
        }""",
        modal_id,
    )
    page.wait_for_timeout(150)


def _close_modal_via_js(page, modal_id: str) -> None:
    page.evaluate(
        """(id) => {
            const m = document.getElementById(id);
            if (m) m.classList.add('hidden');
        }""",
        modal_id,
    )
    page.wait_for_timeout(50)


# ---------------------------------------------------------------------------
# Static template assertions (run without launching a browser)
# ---------------------------------------------------------------------------

def _assert_all_fullscreen_classes_present() -> None:
    """Verify each of the 13 modal locations declares `modal-mobile-fullscreen`
    on its outer wrapper. Pure file read — fast and deterministic."""
    for rel_path, marker in FULLSCREEN_MODAL_FIXTURES:
        html = _read_text(rel_path)
        _assert_class_in_block(html, marker, "modal-mobile-fullscreen", f"{rel_path} :: {marker}")


def _assert_mobile_fullscreen_css_margin_zero() -> None:
    """The CSS rule that pins the inner card to the viewport must also
    override `mx-4` with `margin: 0`. Verified for both the dashboard and
    the marketing landing's scoped rule, since the two share the class name
    but each has its own CSS block."""
    dashboard = _read_text("templates/dashboard/page.html")
    if ".modal-mobile-fullscreen>div:last-child" not in dashboard:
        raise AssertionError("dashboard page.html: fullscreen rule selector missing")
    rule_idx = dashboard.find(".modal-mobile-fullscreen>div:last-child")
    rule_end = dashboard.find("}", rule_idx)
    rule_chunk = dashboard[rule_idx:rule_end + 1]
    if "margin: 0 !important" not in rule_chunk and "margin:0 !important" not in rule_chunk:
        raise AssertionError(
            "dashboard page.html: fullscreen rule missing `margin: 0 !important`"
        )

    landing = _read_text("templates/marketing/landing.html")
    rule_idx = landing.find(".modal-mobile-fullscreen > div ")
    if rule_idx < 0:
        rule_idx = landing.find(".modal-mobile-fullscreen > div{")
    if rule_idx < 0:
        rule_idx = landing.find(".modal-mobile-fullscreen > div")
    if rule_idx < 0:
        raise AssertionError("landing.html: fullscreen rule selector missing")
    rule_end = landing.find("}", rule_idx)
    rule_chunk = landing[rule_idx:rule_end + 1]
    if "margin: 0 !important" not in rule_chunk and "margin:0 !important" not in rule_chunk:
        raise AssertionError(
            "landing.html: fullscreen rule missing `margin: 0 !important`"
        )


def _assert_responsive_tab_padding_in_template(rel_path: str) -> None:
    """Both _search_panel.html and _leads_panel.html collapsed the registry-
    switcher button padding from `px-5` to `px-3 sm:px-5`. Pin that."""
    html = _read_text(rel_path)
    if "px-3 sm:px-5 py-1.5 rounded-lg text-sm font-medium" not in html:
        raise AssertionError(
            f"{rel_path}: responsive registry-switcher padding "
            "`px-3 sm:px-5 py-1.5` not present"
        )


def _assert_responsive_leads_chip_padding() -> None:
    html = _read_text("templates/dashboard/partials/_leads_panel.html")
    if "px-3 sm:px-4 py-1.5 sm:py-2 rounded-md text-xs sm:text-sm" not in html:
        raise AssertionError(
            "_leads_panel.html: responsive lead-mode chip padding not present"
        )


def _assert_landing_risk_report_responsive_min_width() -> None:
    html = _read_text("templates/marketing/landing.html")
    if 'class="min-w-0 sm:min-w-[620px] space-y-2"' not in html:
        raise AssertionError(
            "landing.html: risk-report grid must use `min-w-0 sm:min-w-[620px]` "
            "so it doesn't force horizontal scroll on mobile"
        )


# ---------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------

def _open_dashboard_search_tab(page) -> None:
    page.evaluate("window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=10000)
    page.wait_for_timeout(300)


def _trigger_buy_credits_modal_via_402(page) -> None:
    """Open AI Studio, fill the name-generation form, click generate, and
    wait for the 402 response that makes the combined buy-credits modal
    open. Free persona has 0 monthly AI credits + 0 purchased so this is
    the real path."""
    page.evaluate("window.showDashboardTab('ai-studio')")
    page.wait_for_selector("#tab-content-ai-studio:not(.hidden)", timeout=10000)
    page.wait_for_selector("#studio-name-panel", state="visible", timeout=10000)

    page.fill("#studio-name-query", "BrowserSmokeBrand")
    page.fill("#studio-name-industry", "Software")
    page.click("#studio-name-classes-toggle")
    page.wait_for_selector("#studio-name-classes:not(.hidden)", timeout=5000)
    page.locator("#studio-name-classes button:has-text('9')").first.click()
    page.click("#studio-name-classes-toggle")

    with page.expect_response(
        lambda r: "/api/v1/tools/suggest-names" in r.url,
        timeout=20000,
    ) as info:
        page.click("#studio-name-btn")
    resp = info.value
    if resp.status != 402:
        raise AssertionError(
            f"expected 402 credits_exhausted from /suggest-names, got {resp.status}"
        )
    page.wait_for_timeout(400)


def _assert_buy_credits_modal_grid_collapsed(page) -> None:
    """On mobile (<768px) the picker grid must collapse to a single column
    (`grid-cols-1 md:grid-cols-2`). Verify computed style."""
    cols = page.evaluate(
        """() => {
            const p = document.getElementById('buy-credits-picker');
            if (!p) return null;
            return getComputedStyle(p).getPropertyValue('grid-template-columns').trim();
        }"""
    )
    if cols is None:
        raise AssertionError("#buy-credits-picker not found")
    # On a 390px viewport with a single column the value should be a single
    # length (e.g. "348px"). Two columns would produce two space-separated
    # values like "174px 174px".
    if " " in cols:
        raise AssertionError(
            f"#buy-credits-picker should be single column on mobile, "
            f"grid-template-columns='{cols}'"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def test_mobile_layout_browser_smoke():  # noqa: PT019
    REPORTER.print_heading(
        "Mobile layout browser smoke", server=CONFIG.base_url
    )

    # Make sure the Free persona exists with the right plan + zero credits.
    info = ensure_managed_persona_account("free")
    REPORTER.ok(f"persona ready: {info['email']} (org={info['organization_id']})")

    # ---- Static template assertions (no browser needed) ----
    # These run before the browser launch so a class regression fails fast
    # without spinning up Playwright. We don't pipe them through
    # `run_browser_step` because that helper assumes a live page for the
    # failure-screenshot path.
    def _static_step(name: str, action) -> None:
        try:
            action()
            REPORTER.ok(name)
            REPORTER.record(name, True)
        except Exception as exc:
            REPORTER.fail(f"{name} -> {exc}")
            REPORTER.record(name, False, str(exc))

    _static_step(
        "static: 13 fixed modals carry modal-mobile-fullscreen",
        _assert_all_fullscreen_classes_present,
    )
    _static_step(
        "static: dashboard + landing fullscreen rules force margin:0",
        _assert_mobile_fullscreen_css_margin_zero,
    )
    _static_step(
        "static: _search_panel.html tabs have px-3 sm:px-5 padding",
        lambda: _assert_responsive_tab_padding_in_template(
            "templates/dashboard/partials/_search_panel.html"
        ),
    )
    _static_step(
        "static: _leads_panel.html tabs have px-3 sm:px-5 padding",
        lambda: _assert_responsive_tab_padding_in_template(
            "templates/dashboard/partials/_leads_panel.html"
        ),
    )
    _static_step(
        "static: _leads_panel.html chips have responsive padding",
        _assert_responsive_leads_chip_padding,
    )
    _static_step(
        "static: landing risk-report uses min-w-0 sm:min-w-[620px]",
        _assert_landing_risk_report_responsive_min_width,
    )

    # ---- Live browser steps (mobile viewport) ----
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel=CONFIG.browser_channel, headless=CONFIG.headless,
        )
        context = browser.new_context(
            ignore_https_errors=True,
            viewport=MOBILE_VIEWPORT,
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        page = context.new_page()
        page.set_default_timeout(CONFIG.timeout_ms)
        page.set_default_navigation_timeout(CONFIG.timeout_ms)
        monitor = BrowserMonitor()
        monitor.attach(page)

        try:
            # 1. Landing page (logged out) — no horizontal overflow
            run_browser_step(
                "landing: no horizontal overflow at 390x844",
                REPORTER, page, monitor, CONFIG,
                lambda: (
                    open_url(page, CONFIG, "/"),
                    page.wait_for_load_state("domcontentloaded"),
                    page.wait_for_timeout(1500),
                    _assert_no_horizontal_overflow(page, "landing"),
                ),
            )

            # 2. Login as Free persona
            run_browser_step(
                "login: managed-free persona on mobile",
                REPORTER, page, monitor, CONFIG,
                lambda: _login_simple(page),
            )

            # 3. Dashboard search tab — no horizontal overflow
            run_browser_step(
                "dashboard search tab: no horizontal overflow",
                REPORTER, page, monitor, CONFIG,
                lambda: (
                    _open_dashboard_search_tab(page),
                    _assert_no_horizontal_overflow(page, "dashboard search"),
                ),
            )

            # 4. JS-triggered modals — assert each pins to viewport on mobile
            for modal_id in JS_TRIGGERABLE_MOBILE_MODALS:
                run_browser_step(
                    f"modal pins to viewport on mobile: #{modal_id}",
                    REPORTER, page, monitor, CONFIG,
                    lambda mid=modal_id: (
                        _open_modal_via_js(page, mid),
                        _assert_modal_pins_to_viewport(page, mid),
                        _close_modal_via_js(page, mid),
                    ),
                )

            # 5. Real path: trigger 402, modal opens, assert mobile-fullscreen
            #    + single-column grid (md:grid-cols-2 collapses below md).
            run_browser_step(
                "buy-credits modal: real 402 path on mobile",
                REPORTER, page, monitor, CONFIG,
                lambda: (
                    _trigger_buy_credits_modal_via_402(page),
                    _assert_modal_pins_to_viewport(page, "buy-credits-modal"),
                    _assert_buy_credits_modal_grid_collapsed(page),
                ),
                # The 402 from /suggest-names is the expected trigger —
                # ignore it both at the network layer AND the console layer
                # (Chromium logs "Failed to load resource: ...status of 402"
                # for any non-2xx fetch).
                allow_console_errors=(
                    "status of 402",
                    "/api/v1/tools/suggest-names",
                ),
                allow_request_failures=(
                    "402 POST",
                    "/api/v1/tools/suggest-names",
                ),
            )

            failures = REPORTER.summary("Mobile layout browser smoke")
            if failures:
                raise AssertionError(
                    f"{failures} mobile-layout smoke step(s) failed"
                )
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    test_mobile_layout_browser_smoke()
