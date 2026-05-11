"""Shared helpers for patent (Patent / Faydalı Model) browser smokes.

Mirrors the structure of ``tests/browser/helpers/cografi.py`` with
patent-specific endpoint URLs + watch_type values. The two helpers
deliberately don't import from each other so each registry's
helper file is self-contained and easy to reason about; if a third
registry's helper emerges we'll extract the truly-shared bits then
(token cache, persona configs, slice_label).

Centralizes:
  * Persona switching via the managed-* accounts auto-provisioned
    by tests/live/helpers/test_accounts.py
  * API-side cleanup of leftover ``BROWSER SMOKE *`` patent
    watchlist items (faster than driving the UI)
  * Process-local API login token cache to avoid auth-rate-limit
    flakes when a slice fires multiple cleanup + setup helpers
  * Timestamped per-slice label generator
  * Patent search + watchlist + leads tab-activation helpers
    (locale-agnostic — drives Alpine state directly so they work
    in any locale)
  * Native confirm() dialog auto-accept setup
  * Shared transient-401 request-failure budget for the brief
    page-boot 401s on /api/v1/patent-watchlist + patent-alerts +
    patent-leads endpoints
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from tests.browser.helpers.config import BrowserConfig, load_browser_config


# ---------------------------------------------------------------------------
# Managed personas
# ---------------------------------------------------------------------------

MANAGED_PERSONAS: dict[str, tuple[str, str]] = {
    "free":          ("managed-free-smoke@example.com",         "Test1234!"),
    "starter":       ("managed-starter-smoke@example.com",      "Test1234!"),
    "professional":  ("managed-professional-smoke@example.com", "Test1234!"),
}


def patent_config_for_persona(plan: str) -> BrowserConfig:
    """Return a ``BrowserConfig`` pointing at one of the managed test
    personas. Honors ``TEST_EMAIL`` env override if explicitly set.

    Plans accepted: ``free``, ``starter``, ``professional``.
    """
    base = load_browser_config()
    if os.environ.get("TEST_EMAIL"):
        return base
    if plan not in MANAGED_PERSONAS:
        raise ValueError(
            f"Unknown plan: {plan!r}; choose one of {sorted(MANAGED_PERSONAS)}"
        )
    email, password = MANAGED_PERSONAS[plan]
    return BrowserConfig(
        base_url=base.base_url,
        timeout_ms=base.timeout_ms,
        email=email,
        password=password,
        browser_channel=base.browser_channel,
        headless=base.headless,
        artifacts_dir=base.artifacts_dir,
    )


# ---------------------------------------------------------------------------
# API login + token cache (per-process, keyed by base_url+email)
# ---------------------------------------------------------------------------

# Process-local cache so repeated helper calls within a single slice
# share one auth round-trip. /api/v1/auth/login is per-IP rate-limited;
# a slice that does cleanup + multiple setup + browser login can hit
# that limit otherwise.
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_TTL_SECONDS = 1500  # well under the JWT 30-min exp


def _api_login(config: BrowserConfig) -> str | None:
    cache_key = (config.base_url, config.email)
    cached = _TOKEN_CACHE.get(cache_key)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]
    try:
        req = urllib.request.Request(
            f"{config.base_url}/api/v1/auth/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({
                "email": config.email,
                "password": config.password,
            }).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            token = json.loads(resp.read().decode("utf-8")).get("access_token")
        if token:
            _TOKEN_CACHE[cache_key] = (token, now + _TOKEN_TTL_SECONDS)
        return token
    except Exception:
        return None


def clear_api_token_cache() -> None:
    """Drop cached tokens. Useful if a test changes the persona's plan."""
    _TOKEN_CACHE.clear()


# ---------------------------------------------------------------------------
# API-side cleanup of leftover smoke artifacts
# ---------------------------------------------------------------------------

SMOKE_PREFIX = "BROWSER SMOKE"


def cleanup_smoke_items(
    config: BrowserConfig,
    *,
    prefix: str = SMOKE_PREFIX,
) -> int:
    """Delete every patent watchlist item whose label starts with
    ``prefix`` (case-insensitive). Run at the start of each slice +
    in its `finally` block so leftover state from a prior failed run
    doesn't break the unique (organization_id, label) constraint.

    Non-fatal: any individual delete failure is logged and skipped.
    Returns count of items deleted.
    """
    token = _api_login(config)
    if not token:
        return 0
    deleted = 0
    try:
        # Patent list endpoint caps limit at 200 (vs cografi's 500).
        list_req = urllib.request.Request(
            f"{config.base_url}/api/v1/patent-watchlist?limit=200",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return 0
    # Patent watchlist list endpoint returns {items: [...]} envelope
    # (consistent with the alerts endpoint shape; cografi's was the
    # outlier returning a raw list).
    items = payload.get("items", []) if isinstance(payload, dict) else (
        payload if isinstance(payload, list) else []
    )
    for item in items:
        label = (item.get("label") or "").strip()
        if not label.lower().startswith(prefix.lower()):
            continue
        try:
            del_req = urllib.request.Request(
                f"{config.base_url}/api/v1/patent-watchlist/{item['id']}",
                method="DELETE",
                headers={"Authorization": f"Bearer {token}"},
            )
            urllib.request.urlopen(del_req, timeout=10).read()
            deleted += 1
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return deleted


# ---------------------------------------------------------------------------
# API-side create (for setup state like quota pre-fill)
# ---------------------------------------------------------------------------

def create_smoke_watch_via_api(
    config: BrowserConfig,
    *,
    label: str,
    watch_type: str = "holder",
    holder_name: str = "Test Holder",
    reference_query: str = "test reference text for patent watch",
) -> str | None:
    """Create a patent watchlist item via the REST API. Returns the new
    item's id, or None on failure. Use this for state setup (quota
    pre-fill, batch state prep) without driving the UI.

    Patent has only 2 watch types (holder + reference). Defaults to
    holder since it requires no embedding round-trip server-side.
    """
    token = _api_login(config)
    if not token:
        return None
    body = {"watch_type": watch_type, "label": label}
    if watch_type == "holder":
        body["holder_name"] = holder_name
    elif watch_type == "reference":
        body["reference_query"] = reference_query
    else:
        raise ValueError(
            f"unsupported watch_type: {watch_type!r} "
            "(patent supports holder + reference only)"
        )
    try:
        req = urllib.request.Request(
            f"{config.base_url}/api/v1/patent-watchlist",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(body).encode("utf-8"),
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return payload.get("id") if isinstance(payload, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Label generator
# ---------------------------------------------------------------------------

def slice_label(slice_id: str, watch_type: str = "watch") -> str:
    """Build a deterministic-but-timestamped label so concurrent runs
    + leftovers from prior failed runs don't collide on the unique
    (organization_id, label) constraint.

    Example: ``BROWSER SMOKE alerts reference 1778506857``
    """
    return f"{SMOKE_PREFIX} {slice_id} {watch_type} {int(time.time())}"


# ---------------------------------------------------------------------------
# Tab activation helpers (locale-agnostic — drive Alpine state directly)
# ---------------------------------------------------------------------------

def open_patent_search_subtab(page) -> None:
    """Activate the Search dashboard tab and the Patent sub-tab inside
    it. Idempotent. Drives Alpine ``searchView`` state directly via
    x-data lookup rather than clicking a button whose label changes
    per locale (Patent / Patent / براءة)."""
    page.evaluate("window.showDashboardTab && window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-search');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data) data.searchView = 'patent';
            }
        }"""
    )
    page.wait_for_selector(
        "#patent-search-input", state="visible", timeout=3000,
    )


def open_patent_watchlist_subtab(page) -> None:
    """Activate the Watchlist dashboard tab and the Patent sub-tab.
    Idempotent + locale-agnostic."""
    page.evaluate(
        "window.showDashboardTab && window.showDashboardTab('watchlist')"
    )
    page.wait_for_selector("#tab-content-watchlist:not(.hidden)", timeout=5000)
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-watchlist');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data) data.watchlistView = 'patent';
            }
            if (typeof window.initPatentWatchlistTab === 'function') {
                window.initPatentWatchlistTab();
            }
        }"""
    )
    page.wait_for_selector("#pwl-stats-bar", state="visible", timeout=5000)


def open_patent_leads_subtab(page) -> None:
    """Activate the Opposition Radar dashboard tab and the Patent
    leads sub-view.

    The leads / opposition-radar panel lives under
    ``#tab-content-opposition-radar`` (NOT ``tab-content-leads`` —
    the JS tab name is ``'opposition-radar'``). Inside it, an
    Alpine ``radarView`` switcher chooses between marka / tasarim
    / patent / cografi. We drive radarView=patent and call the
    JS-exposed ``loadPatentLeadsFeed()`` to fetch data without
    needing a button click.
    """
    page.evaluate(
        "window.showDashboardTab && window.showDashboardTab('opposition-radar')"
    )
    page.wait_for_selector(
        "#tab-content-opposition-radar:not(.hidden)", timeout=5000,
    )
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-opposition-radar');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data && 'radarView' in data) {
                    data.radarView = 'patent';
                }
            }
            if (typeof window.loadPatentLeadsFeed === 'function') {
                window.loadPatentLeadsFeed();
            }
        }"""
    )
    page.wait_for_timeout(1500)


# ---------------------------------------------------------------------------
# Native confirm() dialog auto-accept
# ---------------------------------------------------------------------------

def accept_next_confirm_dialog(page) -> None:
    """Register a one-shot handler that accepts the next native
    ``confirm()`` prompt. The JS in patent_watchlist.js calls
    ``confirm()`` before DELETE and before "scan all"; without an
    accept handler, the test would hang on the unhandled dialog."""
    page.once("dialog", lambda d: d.accept())


# ---------------------------------------------------------------------------
# Shared request-failure budget
# ---------------------------------------------------------------------------

def transient_401_budget(base_url: str) -> tuple[str, ...]:
    """The patent watchlist + alert + leads list endpoints briefly
    401 during page boot before the SPA wires up the Authorization
    header. These recover immediately; surfacing them as failures
    would be noise.
    """
    return (
        f"401 GET {base_url}/api/v1/patent-watchlist",
        f"401 GET {base_url}/api/v1/patent-alerts",
        f"401 GET {base_url}/api/v1/patent-leads",
    )


# ---------------------------------------------------------------------------
# Rate-limit aware waits
# ---------------------------------------------------------------------------

def wait_for_search_rate_limit_to_clear(
    config: BrowserConfig,
    *,
    max_seconds: int = 90,
) -> bool:
    """Poll the patent search endpoint until it stops 429-ing. Used
    by search-heavy slices that fire multiple queries in quick
    succession. Returns True if cleared within max_seconds.
    """
    token = _api_login(config)
    if not token:
        return True  # can't check — assume clear
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{config.base_url}/api/v1/patent-search/quick",
                method="POST",
                headers={"Authorization": f"Bearer {token}"},
                data=b"--x\r\nContent-Disposition: form-data; name=\"query\"\r\n\r\nping\r\n--x--\r\n",
            )
            req.add_header("Content-Type", "multipart/form-data; boundary=x")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 429:
                    return True
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return True
        except Exception:
            return True
        time.sleep(5)
    return False
