"""Shared helpers for cografi (Coğrafi İşaret) browser smokes.

Centralizes:
  * Persona switching via the managed-* accounts auto-provisioned by
    tests/live/helpers/test_accounts.py
  * Defensive cleanup of leftover ``BROWSER SMOKE *`` watchlist items
    via the REST API (faster than driving the UI)
  * Timestamped per-slice label generator so concurrent / re-run
    tests don't collide on the (organization_id, label) unique
  * Tab-activation helpers (Coğrafi search / watchlist sub-tabs)
  * Native confirm() dialog auto-accept setup
  * Shared request-failure budget that filters the transient page-
    boot 401s on /api/v1/cografi-watchlist and /api/v1/cografi-alerts

Every cografi browser test file should import from this module
rather than reinventing the same setup.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterable

from tests.browser.helpers.config import BrowserConfig, load_browser_config


# ---------------------------------------------------------------------------
# Managed personas
# ---------------------------------------------------------------------------

MANAGED_PERSONAS: dict[str, tuple[str, str]] = {
    "free":          ("managed-free-smoke@example.com",         "Test1234!"),
    "starter":       ("managed-starter-smoke@example.com",      "Test1234!"),
    "professional":  ("managed-professional-smoke@example.com", "Test1234!"),
}


def cografi_config_for_persona(plan: str) -> BrowserConfig:
    """Return a ``BrowserConfig`` pointing at one of the managed cografi
    test personas. Honors ``TEST_EMAIL`` env override if explicitly set
    (so a developer can swap in their own paid account on demand).

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
# API-side cleanup of leftover smoke artifacts
# ---------------------------------------------------------------------------

SMOKE_PREFIX = "BROWSER SMOKE"


# Process-local token cache keyed by (base_url, email). Helper calls
# within a single test slice should share a token rather than each
# helper re-logging — auth/login is rate-limited (per IP), and a
# slice that fires cleanup + pre-fill + browser-login back-to-back
# can easily hit the limit otherwise.
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_TTL_SECONDS = 1500  # well under the JWT 30-min exp


def _api_login(config: BrowserConfig) -> str | None:
    """Best-effort: get a bearer token via the login API. Caches the
    token in-process for ``_TOKEN_TTL_SECONDS`` per (base_url, email)
    so repeated helper calls share a single auth round-trip. Returns
    None on any failure (cleanup is non-essential — if login fails
    we just continue).
    """
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
    """Drop the cached tokens. Useful if a test needs to force a fresh
    login (e.g. after changing the persona's plan)."""
    _TOKEN_CACHE.clear()


def cleanup_smoke_items(
    config: BrowserConfig,
    *,
    prefix: str = SMOKE_PREFIX,
) -> int:
    """Delete every cografi watchlist item whose label starts with
    ``prefix`` (case-insensitive). Run at the start of each slice so
    leftover state from a prior failed run doesn't break the unique
    (organization_id, label) constraint when this slice creates its
    own timestamped items. Returns count of items deleted.

    Non-fatal: any individual delete failure is logged and skipped.
    """
    token = _api_login(config)
    if not token:
        return 0
    deleted = 0
    try:
        list_req = urllib.request.Request(
            f"{config.base_url}/api/v1/cografi-watchlist?limit=500",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return 0
    if not isinstance(items, list):
        items = items.get("items", []) if isinstance(items, dict) else []
    for item in items:
        label = (item.get("label") or "").strip()
        if not label.lower().startswith(prefix.lower()):
            continue
        try:
            del_req = urllib.request.Request(
                f"{config.base_url}/api/v1/cografi-watchlist/{item['id']}",
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
    watch_type: str = "region",
    region_query: str = "TestRegion",
    holder_name: str = "Test Holder",
    reference_query: str = "test reference text",
    lifecycle_registration_no: int = 999999,
) -> str | None:
    """Create a cografi watchlist item via the REST API. Returns the
    new item's id, or None on failure. Use this to set up state
    (e.g. fill the quota before testing the gate) without spending
    a slow round-trip through the UI.

    Defaults to ``watch_type=region`` since region requires no
    embedding round-trip and is the cheapest to validate server-side.
    """
    token = _api_login(config)
    if not token:
        return None
    body = {"watch_type": watch_type, "label": label}
    if watch_type == "region":
        body["region_query"] = region_query
    elif watch_type == "holder":
        body["holder_name"] = holder_name
    elif watch_type == "reference":
        body["reference_query"] = reference_query
    elif watch_type == "lifecycle":
        body["lifecycle_registration_no"] = lifecycle_registration_no
    else:
        raise ValueError(f"unsupported watch_type: {watch_type!r}")
    try:
        req = urllib.request.Request(
            f"{config.base_url}/api/v1/cografi-watchlist",
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
    """Build a deterministic-but-timestamped label so concurrent runs +
    leftovers from prior failed runs don't collide on the unique
    (organization_id, label) constraint.

    Example: ``BROWSER SMOKE watchtypes holder 1778506857``
    """
    return f"{SMOKE_PREFIX} {slice_id} {watch_type} {int(time.time())}"


# ---------------------------------------------------------------------------
# Tab activation helpers
# ---------------------------------------------------------------------------

def open_cografi_search_subtab(page) -> None:
    """Activate the Search dashboard tab and the Coğrafi sub-tab inside
    it. Idempotent — calling twice is safe."""
    page.evaluate("window.showDashboardTab && window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)
    page.locator(
        "#tab-content-search button:has-text('Coğrafi')"
    ).first.click()
    page.wait_for_selector(
        "#cografi-search-input", state="visible", timeout=3000,
    )


def open_cografi_watchlist_subtab(page) -> None:
    """Activate the Watchlist dashboard tab and the Coğrafi sub-tab
    inside it. Idempotent."""
    page.evaluate(
        "window.showDashboardTab && window.showDashboardTab('watchlist')"
    )
    page.wait_for_selector("#tab-content-watchlist:not(.hidden)", timeout=5000)
    page.locator(
        "#tab-content-watchlist button:has-text('Coğrafi')"
    ).first.click()
    page.wait_for_selector(
        "#cwl-stats-bar", state="visible", timeout=5000,
    )


# ---------------------------------------------------------------------------
# Native confirm() dialog auto-accept
# ---------------------------------------------------------------------------

def accept_next_confirm_dialog(page) -> None:
    """Register a one-shot handler that accepts the next native
    ``confirm()`` prompt. The JS in cografi_watchlist.js calls
    ``confirm()`` before DELETE and before "scan all"; without an
    accept handler, the test would hang on the unhandled dialog."""
    page.once("dialog", lambda d: d.accept())


# ---------------------------------------------------------------------------
# Shared request-failure budget
# ---------------------------------------------------------------------------

def transient_401_budget(base_url: str) -> tuple[str, ...]:
    """The cografi watchlist + alert list endpoints briefly 401 during
    page boot before the SPA wires up the Authorization header. These
    recover immediately under normal user flow; surfacing them as
    failures would be noise. Pass the returned tuple as
    ``allow_request_failures=`` to ``run_browser_step``.
    """
    return (
        f"401 GET {base_url}/api/v1/cografi-watchlist",
        f"401 GET {base_url}/api/v1/cografi-alerts",
    )


# ---------------------------------------------------------------------------
# Rate-limit aware waits
# ---------------------------------------------------------------------------

def wait_for_search_rate_limit_to_clear(
    config: BrowserConfig,
    *,
    max_seconds: int = 90,
) -> bool:
    """Poll the public search endpoint until it stops 429-ing. Used by
    search-heavy slices that fire multiple queries in quick succession.

    Returns True if the limit cleared within ``max_seconds``, False
    otherwise (caller can then either skip the slice or proceed with
    the risk of hitting 429 mid-run)."""
    token = _api_login(config)
    if not token:
        return True  # can't check — assume clear
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{config.base_url}/api/v1/cografi-search/quick",
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
