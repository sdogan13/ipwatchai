"""Shared helpers for trademark (Marka) browser smokes.

Parallel to ``cografi.py`` / ``patent.py`` / ``design.py``. Each
registry's helper file is self-contained (doesn't import from
the others) so a change in one can't unintentionally break
another.

Trademark-specific deltas vs the other 3 registries:
  - Trademark is the DEFAULT registry — the search panel boots
    with ``searchView === 'trademark'`` and the watchlist panel
    with ``watchlistView === 'trademark'``. No subview activation
    needed in most cases, but we still expose tab-activation
    helpers that drive the Alpine state so locale-agnostic tests
    can target trademark explicitly.
  - Identifier field is ``brand_name`` (vs design's
    ``product_name``, patent's ``label``).
  - Nice class numbers (1..45) are an integer list — UNIQUE to
    trademark; the other 3 registries use IPC / Locarno /
    coordinates.
  - Trademark uniquely supports ``monitor_phonetic`` in addition
    to text + visual.
  - Watchlist endpoint paths are ``/api/v1/watchlist`` (no
    registry prefix) since trademark predates the other 3.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from tests.browser.helpers.config import BrowserConfig, load_browser_config


# ---------------------------------------------------------------------------
# Managed personas — shared shape across cografi/patent/design/trademark
# ---------------------------------------------------------------------------

MANAGED_PERSONAS: dict[str, tuple[str, str]] = {
    "free":          ("managed-free-smoke@example.com",         "Test1234!"),
    "starter":       ("managed-starter-smoke@example.com",      "Test1234!"),
    "professional":  ("managed-professional-smoke@example.com", "Test1234!"),
}


def trademark_config_for_persona(plan: str) -> BrowserConfig:
    """Return a ``BrowserConfig`` pointing at one of the managed test
    personas. Honors ``TEST_EMAIL`` env override if explicitly set."""
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
# API login + per-process token cache (same shape as design/patent/cografi)
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
# Cleanup — delete every trademark watchlist item whose brand_name
# starts with the smoke prefix.
# ---------------------------------------------------------------------------

SMOKE_PREFIX = "BROWSER SMOKE"


def cleanup_smoke_items(
    config: BrowserConfig,
    *,
    prefix: str = SMOKE_PREFIX,
) -> int:
    """Delete every trademark watchlist item whose ``brand_name`` starts
    with ``prefix`` (case-insensitive). Trademark uses ``brand_name``
    as the label-equivalent field (cf. design's ``product_name``)."""
    token = _api_login(config)
    if not token:
        return 0
    deleted = 0
    try:
        list_req = urllib.request.Request(
            f"{config.base_url}/api/v1/watchlist?page_size=200",
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
        brand_name = (item.get("brand_name") or "").strip()
        if not brand_name.lower().startswith(prefix.lower()):
            continue
        try:
            del_req = urllib.request.Request(
                f"{config.base_url}/api/v1/watchlist/{item['id']}",
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
    brand_name: str,
    nice_class_numbers: list[int] | None = None,
    monitor_phonetic: bool = True,
    monitor_visual: bool = True,
    monitor_text: bool = True,
) -> str | None:
    """Create a trademark watchlist item via REST. Returns the new
    item's id, or None on failure.

    ``brand_name`` is the trademark's identifying label. Minimum
    server-side validation requires non-empty brand_name +
    nice_class_numbers[] (defaults to [9] which is the
    Electronics/Software class — a safe broad default).
    """
    if nice_class_numbers is None:
        nice_class_numbers = [9]
    token = _api_login(config)
    if not token:
        return None
    body = {
        "brand_name": brand_name,
        "nice_class_numbers": nice_class_numbers,
        "monitor_text": monitor_text,
        "monitor_visual": monitor_visual,
        "monitor_phonetic": monitor_phonetic,
    }
    try:
        req = urllib.request.Request(
            f"{config.base_url}/api/v1/watchlist",
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
    """Build a deterministic-but-timestamped brand_name for a smoke
    item. Mirrors the other registries' ``slice_label`` shape so
    cross-registry cleanup utilities recognize ``SMOKE_PREFIX``
    uniformly.

    Example: ``BROWSER SMOKE filter alpha 1778506857``
    """
    return f"{SMOKE_PREFIX} {slice_id} {suffix} {int(time.time())}"


# ---------------------------------------------------------------------------
# Tab activation helpers (locale-agnostic — drive Alpine state directly)
# ---------------------------------------------------------------------------

def open_trademark_search_subtab(page) -> None:
    """Activate the Search tab and the Trademark sub-view inside it.
    Trademark is the DEFAULT searchView so the subview may already be
    active; we still write the Alpine state explicitly for idempotency."""
    page.evaluate("window.showDashboardTab && window.showDashboardTab('search')")
    page.wait_for_selector("#tab-content-search:not(.hidden)", timeout=5000)
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-search');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data) data.searchView = 'trademark';
            }
        }"""
    )
    # The trademark subview uses #search-input (a generic ID — no
    # registry suffix). Wait for it to be visible.
    page.wait_for_selector("#search-input", state="visible", timeout=3000)


def open_trademark_watchlist_subtab(page) -> None:
    """Activate the Watchlist tab and the Trademark sub-view inside it.
    Idempotent + locale-agnostic. Trademark is the DEFAULT view so the
    subview may already be active on first dashboard load."""
    page.evaluate(
        "window.showDashboardTab && window.showDashboardTab('watchlist')"
    )
    page.wait_for_selector("#tab-content-watchlist:not(.hidden)", timeout=5000)
    page.evaluate(
        """() => {
            const root = document.getElementById('tab-content-watchlist');
            if (root && window.Alpine && window.Alpine.$data) {
                const data = window.Alpine.$data(root);
                if (data) data.watchlistView = 'trademark';
            }
            // Trademark watchlist mounts via loadWatchlistOverview() on
            // first activation. If the global init function exists,
            // call it so the stats bar and list render immediately.
            if (typeof window.loadWatchlistOverview === 'function') {
                window.loadWatchlistOverview();
            }
        }"""
    )
    # 6-cell stats bar is the trademark watchlist's signature surface
    # (4-cell on design, 6-cell on cografi). Wait for it.
    page.wait_for_selector("#wl-stats-bar", state="visible", timeout=5000)


# ---------------------------------------------------------------------------
# Native confirm() auto-accept
# ---------------------------------------------------------------------------

def accept_next_confirm_dialog(page) -> None:
    page.once("dialog", lambda d: d.accept())


# ---------------------------------------------------------------------------
# Shared request-failure budget — the trademark watchlist + alert
# endpoints briefly 401 during page boot before the SPA wires up the
# Authorization header (same pattern as design/patent/cografi).
# ---------------------------------------------------------------------------

def transient_401_budget(base_url: str) -> tuple[str, ...]:
    return (
        f"401 GET {base_url}/api/v1/watchlist",
        f"401 GET {base_url}/api/v1/watchlist/stats",
        f"401 GET {base_url}/api/v1/alerts",
    )


# ---------------------------------------------------------------------------
# Rate-limit aware wait — trademark search lives at /api/v1/search
# (no registry prefix). Polls until 429s clear.
# ---------------------------------------------------------------------------

def wait_for_search_rate_limit_to_clear(
    config: BrowserConfig,
    *,
    max_seconds: int = 90,
) -> bool:
    """Poll the trademark search endpoint until it stops 429-ing."""
    token = _api_login(config)
    if not token:
        return True
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{config.base_url}/api/v1/search?query=ping&limit=1",
                headers={"Authorization": f"Bearer {token}"},
            )
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
