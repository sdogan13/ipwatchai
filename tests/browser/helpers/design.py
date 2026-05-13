"""Shared helpers for design (Tasarım) browser smokes.

Parallel to ``tests/browser/helpers/cografi.py`` and
``tests/browser/helpers/patent.py``. Each registry's helper file
is self-contained (doesn't import from the others) so a change
in one can't unintentionally break another; if a 3rd-registry
pattern emerges we extract the truly-shared bits then.

Design-specific deltas vs patent/cografi:
  - Watchlist list endpoint returns ``{items, total, page,
    page_size, total_pages}`` envelope; ``page_size`` caps at 100
    (vs patent's 200, cografi's 500)
  - No ``watch_type`` concept — designs are watched by
    product_name + locarno_classes
  - Alert endpoints use POST verbs (same as patent;
    /design-alerts/{id}/acknowledge etc) — different from
    cografi's PATCH-with-status-body
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


def design_config_for_persona(plan: str) -> BrowserConfig:
    """Return a ``BrowserConfig`` pointing at one of the managed test
    personas. Honors ``TEST_EMAIL`` env override if explicitly set.
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
# API login + token cache
# ---------------------------------------------------------------------------

_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_TTL_SECONDS = 1500


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
    """Delete every design watchlist item whose ``product_name`` starts
    with ``prefix`` (case-insensitive). Designs use product_name as
    the label-equivalent field (no separate ``label``).

    Run at slice start + in `finally` so leftover state doesn't break
    the unique-by-org constraint or eat into the cross-registry quota.
    """
    token = _api_login(config)
    if not token:
        return 0
    deleted = 0
    try:
        # page_size caps at 100. If a slice ever leaks >100 smoke items
        # something else is badly wrong; we'd see it as a quota failure
        # before this read returned partial results.
        list_req = urllib.request.Request(
            f"{config.base_url}/api/v1/design-watchlist?page_size=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(list_req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return 0
    items = payload.get("items", []) if isinstance(payload, dict) else (
        payload if isinstance(payload, list) else []
    )
    for item in items:
        product_name = (item.get("product_name") or "").strip()
        if not product_name.lower().startswith(prefix.lower()):
            continue
        try:
            del_req = urllib.request.Request(
                f"{config.base_url}/api/v1/design-watchlist/{item['id']}",
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
# API-side create (for setup state)
# ---------------------------------------------------------------------------

def create_smoke_watch_via_api(
    config: BrowserConfig,
    *,
    product_name: str,
    locarno_classes: list[str] | None = None,
) -> str | None:
    """Create a design watchlist item via REST. Returns the new item's
    id, or None on failure.

    ``product_name`` is the design's identifying label. The minimum
    server-side validation requires non-empty product_name +
    locarno_classes[] (defaults to one class to satisfy validation).
    """
    if locarno_classes is None:
        locarno_classes = ["06-01"]
    token = _api_login(config)
    if not token:
        return None
    body = {
        "product_name": product_name,
        "locarno_classes": locarno_classes,
    }
    try:
        req = urllib.request.Request(
            f"{config.base_url}/api/v1/design-watchlist",
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

def slice_label(slice_id: str, suffix: str = "watch") -> str:
    """Build a deterministic-but-timestamped product_name for a smoke
    item. Mirrors patent/cografi `slice_label` shape so cross-registry
    cleanup utilities recognize the SMOKE_PREFIX uniformly.

    Example: ``BROWSER SMOKE bulk upload-row 1778506857``
    """
    return f"{SMOKE_PREFIX} {slice_id} {suffix} {int(time.time())}"


# ---------------------------------------------------------------------------
# Tab activation helpers (locale-agnostic)
# ---------------------------------------------------------------------------

def open_design_search_subtab(page) -> None:
    """Activate the Search tab and the Design sub-view inside it.
    Locale-agnostic: drives Alpine ``searchView`` state directly."""
    page.evaluate("window.showDashboardTab && window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-search');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data) data.searchView = 'design';
            }
        }"""
    )
    page.wait_for_selector(
        "#design-search-input", state="visible", timeout=3000,
    )


def open_design_watchlist_subtab(page) -> None:
    """Activate the Watchlist tab and the Design sub-view inside it.
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
                if (data) data.watchlistView = 'design';
            }
            if (typeof window.initDesignWatchlistTab === 'function') {
                window.initDesignWatchlistTab();
            }
        }"""
    )
    page.wait_for_selector("#dwl-stats-bar", state="visible", timeout=5000)


# ---------------------------------------------------------------------------
# Native confirm() auto-accept
# ---------------------------------------------------------------------------

def accept_next_confirm_dialog(page) -> None:
    page.once("dialog", lambda d: d.accept())


# ---------------------------------------------------------------------------
# Shared request-failure budget
# ---------------------------------------------------------------------------

def transient_401_budget(base_url: str) -> tuple[str, ...]:
    """The design watchlist + alert endpoints briefly 401 during
    page boot before the SPA wires up the Authorization header."""
    return (
        f"401 GET {base_url}/api/v1/design-watchlist",
        f"401 GET {base_url}/api/v1/design-watchlist/stats",
        f"401 GET {base_url}/api/v1/design-alerts",
    )


# ---------------------------------------------------------------------------
# Rate-limit aware wait
# ---------------------------------------------------------------------------

def wait_for_search_rate_limit_to_clear(
    config: BrowserConfig,
    *,
    max_seconds: int = 90,
) -> bool:
    """Poll the design search endpoint until it stops 429-ing."""
    token = _api_login(config)
    if not token:
        return True
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{config.base_url}/api/v1/design-search",
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
