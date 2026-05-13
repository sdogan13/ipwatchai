"""
Browser smoke suite for authenticated member journeys.

Run directly:
    python tests/browser/test_member_browser_smoke.py
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
from tests.browser.helpers.config import load_browser_config
from tests.browser.helpers.session import launch_browser_page, login_via_modal
from tests.live.helpers.assertions import LiveReporter


CONFIG = load_browser_config()
REPORTER = LiveReporter()
pytestmark = pytest.mark.skip(reason="Browser E2E script; run directly with python tests/browser/test_member_browser_smoke.py")


def _get_body_state(page):
    return page.evaluate(
        """() => {
            const state = document.body._x_dataStack && document.body._x_dataStack[0];
            return {
                searchResults: state ? (state.searchResults || []).length : -1,
                searchError: state ? (state.searchError || '') : 'missing alpine state'
            };
        }"""
    )


def _get_dashboard_overview_contract(page):
    return page.evaluate(
        """async () => {
            function text(id) {
                const el = document.getElementById(id);
                return el ? el.textContent.trim() : '';
            }

            function fmtLimit(value) {
                return value >= 999999 ? '∞' : String(value || 0);
            }

            function fmtUsage(bucket) {
                const data = bucket || {};
                return `${data.used || 0} / ${fmtLimit(data.limit || 0)}`;
            }

            function parseUsage(textValue) {
                const parts = String(textValue || '').split('/').map((part) => part.trim());
                if (parts.length !== 2) {
                    return null;
                }

                const used = Number(parts[0]);
                const limitRaw = parts[1];
                const limit = limitRaw === 'âˆž' ? Number.POSITIVE_INFINITY : Number(limitRaw);
                if (Number.isNaN(used) || Number.isNaN(limit)) {
                    return null;
                }

                return { used, limit };
            }

            function authHeaders() {
                const token =
                    localStorage.getItem('auth_token') ||
                    localStorage.getItem('access_token') ||
                    sessionStorage.getItem('auth_token') ||
                    sessionStorage.getItem('access_token') ||
                    '';
                return token ? { Authorization: `Bearer ${token}` } : {};
            }

            async function fetchJson(path) {
                const response = await fetch(path, { headers: authHeaders() });
                let payload = null;
                try {
                    payload = await response.json();
                } catch (_error) {
                    payload = null;
                }
                return { ok: response.ok, status: response.status, payload };
            }

            const [statsRes, usageRes, statusRes, scanRes, creditsRes, meRes] = await Promise.all([
                fetchJson('/api/v1/dashboard/stats'),
                fetchJson('/api/v1/usage/summary'),
                fetchJson('/api/v1/status'),
                fetchJson('/api/v1/watchlist/scan-status'),
                fetchJson('/api/v1/search/credits'),
                fetchJson('/api/v1/auth/me'),
            ]);

            const failed = [
                ['stats', statsRes],
                ['usage', usageRes],
                ['status', statusRes],
                ['scan', scanRes],
                ['credits', creditsRes],
                ['me', meRes],
            ]
                .filter(([, result]) => !result.ok)
                .map(([name, result]) => `${name}:${result.status}`);
            if (failed.length) {
                return { error: `dashboard contract fetch failed -> ${failed.join(', ')}` };
            }

            const stats = statsRes.payload || {};
            const usage = (usageRes.payload || {}).usage || {};
            const systemStats = (statusRes.payload || {}).statistics || {};
            const scan = scanRes.payload || {};
            const credits = creditsRes.payload || {};
            const me = meRes.payload || {};
            const organization = me.organization || {};

            const expected = {
                totalWatched: String(stats.active_watchlist || 0),
                highRisk: String(stats.critical_alerts || 0),
                pendingDeadlines: String(stats.active_deadline_count || 0),
                recentActivity: String(stats.alerts_this_week || 0),
                quickUsage: fmtUsage(usage.daily_live_searches),
                liveUsage: fmtUsage(usage.daily_live_searches),
                watchlistUsage: fmtUsage(usage.watchlist_items),
                totalTrademarks: Number(systemStats.total_trademarks || 0).toLocaleString(),
                creditResetDate: credits.resets_on
                    ? `${window.t('usage.resets')} ${new Date(credits.resets_on).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
                    : '',
                planDisplayName: credits.display_name || '',
                lastBulletin: systemStats.last_bulletin_date
                    ? new Date(systemStats.last_bulletin_date).toLocaleDateString('tr-TR', {
                        day: '2-digit',
                        month: '2-digit',
                        year: 'numeric',
                    })
                    : '',
                nextScan: scan.next_scan_at
                    ? `${new Date(scan.next_scan_at).toLocaleDateString('tr-TR', {
                        day: '2-digit',
                        month: '2-digit',
                    })} ${new Date(scan.next_scan_at).toLocaleTimeString('tr-TR', {
                        hour: '2-digit',
                        minute: '2-digit',
                    })}`
                    : '',
                autoScanEnabled: scan.auto_scan_enabled,
                limitsExpected: Boolean(
                    organization.max_monthly_searches ||
                    organization.max_watchlist_items ||
                    organization.max_users
                ),
                aiVisible: Boolean((usage.monthly_ai_credits || {}).limit > 0),
                aiUsage: (() => {
                    const ai = usage.monthly_ai_credits || {};
                    if (!(ai.limit > 0)) {
                        return '';
                    }
                    if (ai.limit >= 999999) {
                        return '0 / ∞';
                    }
                    const used = Math.max(0, (ai.limit || 0) - (ai.remaining || 0));
                    return `${used} / ${ai.limit}`;
                })(),
            };

            const dom = {
                totalWatched: text('kpi-total-watched'),
                highRisk: text('kpi-high-risk'),
                pendingDeadlines: text('kpi-pending-deadlines'),
                recentActivity: text('kpi-recent-activity'),
                quickUsage: text('usage-quick-text'),
                liveUsage: text('usage-live-text'),
                watchlistUsage: text('usage-watchlist-text'),
                aiUsage: text('usage-ai-text'),
                aiCardVisible: !document.getElementById('usage-ai-card')?.classList.contains('hidden'),
                totalTrademarks: text('sys-total-trademarks'),
                creditResetDate: text('credit-reset-date'),
                planDisplayBadge: text('plan-display-badge'),
                lastBulletin: text('sys-last-bulletin'),
                nextScan: text('sys-next-scan'),
                autoScanBadge: text('auto-scan-badge'),
                planLimitsInfo: text('plan-limits-info'),
            };

            const mismatches = [];
            [
                'totalWatched',
                'highRisk',
                'pendingDeadlines',
                'recentActivity',
                'watchlistUsage',
                'totalTrademarks',
            ].forEach((key) => {
                if (dom[key] !== expected[key]) {
                    mismatches.push(`${key}: expected "${expected[key]}", got "${dom[key]}"`);
                }
            });

            const quickDom = parseUsage(dom.quickUsage);
            const quickExpected = parseUsage(expected.quickUsage);
            if (!quickDom || !quickExpected) {
                mismatches.push(`quickUsage: unable to parse "${dom.quickUsage}" vs "${expected.quickUsage}"`);
            } else if (
                quickDom.limit !== quickExpected.limit ||
                Math.abs(quickDom.used - quickExpected.used) > 1
            ) {
                mismatches.push(`quickUsage: expected "${expected.quickUsage}", got "${dom.quickUsage}"`);
            }

            const liveDom = parseUsage(dom.liveUsage);
            const liveExpected = parseUsage(expected.liveUsage);
            if (!liveDom || !liveExpected) {
                mismatches.push(`liveUsage: unable to parse "${dom.liveUsage}" vs "${expected.liveUsage}"`);
            } else if (liveDom.limit !== liveExpected.limit || liveDom.used !== liveExpected.used) {
                mismatches.push(`liveUsage: expected "${expected.liveUsage}", got "${dom.liveUsage}"`);
            }

            if (expected.creditResetDate && dom.creditResetDate !== expected.creditResetDate) {
                mismatches.push(`creditResetDate: expected "${expected.creditResetDate}", got "${dom.creditResetDate}"`);
            }
            if (expected.planDisplayName && !dom.planDisplayBadge.includes(expected.planDisplayName)) {
                mismatches.push(`planDisplayBadge missing "${expected.planDisplayName}" -> "${dom.planDisplayBadge}"`);
            }
            if (expected.lastBulletin && dom.lastBulletin !== expected.lastBulletin) {
                mismatches.push(`lastBulletin: expected "${expected.lastBulletin}", got "${dom.lastBulletin}"`);
            }
            if (expected.nextScan && dom.nextScan !== expected.nextScan) {
                mismatches.push(`nextScan: expected "${expected.nextScan}", got "${dom.nextScan}"`);
            }
            if (expected.autoScanEnabled !== null && expected.autoScanEnabled !== undefined && !dom.autoScanBadge) {
                mismatches.push('autoScanBadge expected populated text');
            }
            if (expected.limitsExpected && !dom.planLimitsInfo) {
                mismatches.push('planLimitsInfo expected populated text');
            }
            if (expected.aiVisible !== dom.aiCardVisible) {
                mismatches.push(`aiCard visibility mismatch: expected ${expected.aiVisible}, got ${dom.aiCardVisible}`);
            }
            if (expected.aiVisible && dom.aiUsage !== expected.aiUsage) {
                mismatches.push(`aiUsage: expected "${expected.aiUsage}", got "${dom.aiUsage}"`);
            }

            return { mismatches, dom, expected };
        }"""
    )


def _login_and_clear_monitor(page, browser_config, monitor) -> None:
    login_via_modal(page, browser_config, monitor)
    monitor.clear()


def main() -> None:
    REPORTER.print_heading("MEMBER BROWSER SMOKE", server=CONFIG.base_url, user=CONFIG.email)

    with sync_playwright() as playwright:
        browser, context, page, monitor = launch_browser_page(playwright, CONFIG)
        try:
            run_browser_step(
                "login modal journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                lambda: (
                    _login_and_clear_monitor(page, CONFIG, monitor),
                    "/dashboard" in page.url or (_ for _ in ()).throw(AssertionError(f"expected dashboard URL, got {page.url}")),
                ),
            )

            def dashboard_overview_contract() -> None:
                page.click("#tab-btn-overview")
                page.locator("#tab-content-overview").wait_for(state="visible")
                page.wait_for_function(
                    """() => {
                        const requiredIds = [
                            'kpi-total-watched',
                            'kpi-high-risk',
                            'kpi-pending-deadlines',
                            'kpi-recent-activity',
                            'usage-quick-text',
                            'usage-live-text',
                            'usage-watchlist-text',
                            'plan-display-badge',
                            'sys-total-trademarks',
                        ];
                        return requiredIds.every((id) => {
                            const el = document.getElementById(id);
                            const value = el ? el.textContent.trim() : '';
                            return value && value !== '-';
                        });
                    }""",
                    timeout=CONFIG.timeout_ms,
                )
                contract = _get_dashboard_overview_contract(page)
                if contract.get("error"):
                    raise AssertionError(contract["error"])
                mismatches = contract.get("mismatches", [])
                if mismatches:
                    raise AssertionError("; ".join(mismatches[:3]))

            run_browser_step(
                "dashboard overview stats and usage contract",
                REPORTER,
                page,
                monitor,
                CONFIG,
                dashboard_overview_contract,
            )

            def agentic_search() -> None:
                page.click("#tab-btn-search")
                page.locator("#tab-content-search").wait_for(state="visible")
                with page.expect_response(lambda response: "/api/v1/search" in response.url, timeout=CONFIG.timeout_ms) as response_info:
                    page.fill('#search-input', "wosen")
                    page.press('#search-input', "Enter")
                response = response_info.value
                if response.status == 429:
                    monitor.request_failures = [
                        failure
                        for failure in monitor.request_failures
                        if "/api/v1/search" not in failure
                    ]
                    return
                if response.status != 200:
                    raise AssertionError(f"unexpected quick search status: {response.status}")
                page.wait_for_function(
                    "() => document.body._x_dataStack && document.body._x_dataStack[0] && !document.body._x_dataStack[0].searchLoading",
                    timeout=CONFIG.timeout_ms,
                )
                state = _get_body_state(page)
                if state["searchError"]:
                    raise AssertionError(f"unexpected quick search error: {state['searchError']}")
                if state["searchResults"] <= 0:
                    raise AssertionError(f"expected quick search results > 0, got {state['searchResults']}")

            run_browser_step(
                "dashboard quick search journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                agentic_search,
            )

            def watchlist_tab() -> None:
                page.click("#tab-btn-watchlist")
                page.locator("#tab-content-watchlist").wait_for(state="visible")

            run_browser_step(
                "watchlist tab navigation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                watchlist_tab,
                allow_console_errors=("status of 429",),
            )

            def reports_tab() -> None:
                page.click("#tab-btn-reports")
                page.locator("#tab-content-reports").wait_for(state="visible")
                page.locator("#reports-list").wait_for(state="attached")

            run_browser_step(
                "reports tab navigation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                reports_tab,
            )

            def applications_tab() -> None:
                page.click("#tab-btn-applications")
                page.locator("#tab-content-applications").wait_for(state="visible")
                page.locator("#applications-list-view").wait_for(state="visible")

            run_browser_step(
                "applications tab navigation",
                REPORTER,
                page,
                monitor,
                CONFIG,
                applications_tab,
            )

            def logout() -> None:
                user_menu = page.locator('div[x-data*="userMenuOpen"]').first
                user_menu.locator("> button").click()
                with page.expect_navigation(url="**/", timeout=CONFIG.timeout_ms):
                    user_menu.locator("button").last.click()
                page.wait_for_load_state("networkidle", timeout=CONFIG.timeout_ms)
                page.locator("#search-input").wait_for(state="visible")
                if page.url.rstrip("/") != CONFIG.base_url.rstrip("/"):
                    raise AssertionError(f"expected post-logout landing page, got {page.url}")

            run_browser_step(
                "logout journey",
                REPORTER,
                page,
                monitor,
                CONFIG,
                logout,
            )
        finally:
            context.close()
            browser.close()

    sys.exit(0 if REPORTER.summary("MEMBER BROWSER SUMMARY") == 0 else 1)


if __name__ == "__main__":
    main()
