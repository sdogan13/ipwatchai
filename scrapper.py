import time
import logging
import json
import os
import re
import random
import requests
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_local_scraper_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


# Handle API changes in playwright-stealth v2.0+
# v2.0+ uses Stealth().use_sync(page); older versions had stealth_sync(page)
try:
    from playwright_stealth import Stealth as _StealthClass
    stealth_sync = _StealthClass().use_sync
except ImportError:
    try:
        from playwright_stealth import stealth_sync
        if not callable(stealth_sync):
            stealth_sync = None
    except ImportError:
        # playwright_stealth not installed — scraping features disabled
        stealth_sync = None

load_dotenv()

# ===================== CONFIG =====================
# Directory Structure Configuration (Windows-compatible)
ROOT_DIR = _resolve_local_scraper_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)

# Ensure root data directory exists
ROOT_DIR.mkdir(parents=True, exist_ok=True)

# ===================== SCRAPING LIMITS =====================
# Maximum records to scrape per search (hard cap to prevent runaway scraping)
MAX_SCRAPE_LIMIT = 1000

# ===================== TURKISH CHARACTER FALLBACK =====================
# Maps Latin characters to their Turkish equivalents for fallback search
TURKISH_CHAR_MAP = {
    'c': 'ç', 'C': 'Ç',
    'g': 'ğ', 'G': 'Ğ',
    'i': 'ı', 'I': 'İ',
    's': 'ş', 'S': 'Ş',
    # o→ö and u→ü intentionally excluded: too aggressive, creates invalid words
    # e.g. 'dogan' → 'doğan' (correct) vs 'döğan' (wrong)
}

# Rotation pool for fallback UA to avoid IP fingerprinting
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/120.0",
]


def _apply_turkish_chars(text: str) -> str:
    """Replace Latin equivalents with Turkish characters per TURKISH_CHAR_MAP."""
    return ''.join(TURKISH_CHAR_MAP.get(ch, ch) for ch in text)


def _has_latin_equivalents(text: str) -> bool:
    """Return True if text contains any Latin char that has a Turkish equivalent."""
    return any(ch in TURKISH_CHAR_MAP for ch in text)


# ===================== SKIP TERMS =====================
# Placeholder/generic terms that are not real trademark names
# These should be skipped during agentic search to avoid wasting time
SKIP_TERMS = [
    # Turkish "image/figure" indicators
    'şekil', 'sekil', 'şekıl', 'sekıl',
    'şek', 'sek',
    'şekil+kelime', 'sekil+kelime',
    'şekil + kelime', 'sekil + kelime',
    '+şekil', 'şekil+', '+ şekil', 'şekil +',
    '+sekil', 'sekil+', '+ sekil', 'sekil +',
    # Generic terms
    'logo', 'marka', 'resim', 'görsel', 'gorsel',
    'figure', 'shape', 'image', 'picture',
    'figür', 'figur', 'amblem', 'emblem',
    'işaret', 'isaret', 'sembol', 'symbol',
    'grafik', 'graphic', 'ikon', 'icon',
    # Other placeholders
    'test', 'deneme', 'örnek', 'ornek',
    'n/a', 'na', '-', '--', '---', '...',
]

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - [SCRAPER] - %(levelname)s - %(message)s'
)


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _resolve_project_path(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


def _epoch_to_iso(value: float | int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _utc_day_key(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def _next_utc_midnight(now: float) -> float:
    current = datetime.fromtimestamp(now, tz=timezone.utc)
    midnight = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
    return (midnight + timedelta(days=1)).timestamp()


def _looks_like_blocking_content(text: str | None) -> bool:
    if not text:
        return False
    normalized = html.unescape(text).lower()
    replacements = {
        "\u00e7": "c",
        "\u011f": "g",
        "\u0131": "i",
        "\u00f6": "o",
        "\u015f": "s",
        "\u00fc": "u",
    }
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    block_tokens = (
        "captcha",
        "guvenlik",
        "dogrulama",
        "cok fazla",
        "too many",
        "rate limit",
        "access denied",
        "erisim engellendi",
        "izin verilmiyor",
        "robot",
        "otomatik",
        "blocked",
        "yasak",
    )
    return any(token in normalized for token in block_tokens)


class ScraperSafetyStop(Exception):
    """Raised internally when the scraper should soft-stop before more live traffic."""

    def __init__(self, event: dict):
        self.event = event
        super().__init__(event.get("message") or event.get("reason") or "scraper safety stop")


@dataclass
class ScraperSafetyPolicy:
    enabled: bool = True
    state_path: Path = _LOCAL_PROJECT_ROOT / "artifacts" / "scraper_safety" / "turkpatent_state.json"
    min_interval_seconds: float = 60.0
    jitter_min_seconds: float = 10.0
    jitter_max_seconds: float = 30.0
    hourly_budget: int = 100
    daily_budget: int = 1000
    block_cooldown_seconds: float = 24 * 60 * 60
    max_wait_seconds: float = 120.0
    stale_lock_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> "ScraperSafetyPolicy":
        jitter_min = _env_float("SCRAPER_SAFETY_JITTER_MIN_SECONDS", 10.0)
        jitter_max = _env_float("SCRAPER_SAFETY_JITTER_MAX_SECONDS", 30.0)
        if jitter_max < jitter_min:
            jitter_max = jitter_min
        return cls(
            enabled=_env_bool("SCRAPER_SAFETY_ENABLED", True),
            state_path=_resolve_project_path(
                os.environ.get("SCRAPER_SAFETY_STATE_PATH"),
                _LOCAL_PROJECT_ROOT / "artifacts" / "scraper_safety" / "turkpatent_state.json",
            ),
            min_interval_seconds=_env_float("SCRAPER_SAFETY_MIN_INTERVAL_SECONDS", 60.0),
            jitter_min_seconds=jitter_min,
            jitter_max_seconds=jitter_max,
            hourly_budget=_env_int("SCRAPER_SAFETY_HOURLY_BUDGET", 100),
            daily_budget=_env_int("SCRAPER_SAFETY_DAILY_BUDGET", 1000),
            block_cooldown_seconds=_env_float("SCRAPER_SAFETY_BLOCK_COOLDOWN_SECONDS", 24 * 60 * 60),
            max_wait_seconds=_env_float("SCRAPER_SAFETY_MAX_WAIT_SECONDS", 120.0),
            stale_lock_seconds=_env_float("SCRAPER_SAFETY_STALE_LOCK_SECONDS", 120.0),
        )


class ScraperSafetyGuard:
    def __init__(self, policy: ScraperSafetyPolicy | None = None, *, now_fn=None, sleep_fn=None):
        self.policy = policy or ScraperSafetyPolicy.from_env()
        self._now = now_fn or time.time
        self._sleep = sleep_fn or time.sleep

    def request_permission(self, *, operation: str, query: str | None = None) -> dict:
        if not self.policy.enabled:
            return self._event(
                safety_stop=False,
                reason=None,
                operation=operation,
                query=query,
                message="Scraper safety disabled.",
            )

        while True:
            event = self._evaluate_permission(operation=operation, query=query)
            if event.get("safety_stop") or not event.get("wait_seconds"):
                return event
            wait_seconds = float(event["wait_seconds"])
            logging.info("   [SAFETY] Waiting %.1fs before TurkPatent request.", wait_seconds)
            self._sleep(wait_seconds)

    def record_block(self, *, reason: str, operation: str | None = None, query: str | None = None) -> dict:
        if not self.policy.enabled:
            return self._event(
                safety_stop=False,
                reason=None,
                operation=operation,
                query=query,
                message="Scraper safety disabled.",
            )

        now = self._now()
        blocked_until = now + max(float(self.policy.block_cooldown_seconds), 0.0)

        def mutate(state: dict) -> dict:
            state["blocked_until"] = blocked_until
            state["block_reason"] = reason
            state["updated_at"] = now
            return state

        self._mutate_state(mutate)
        return self._event(
            safety_stop=True,
            reason="safety_blocked",
            operation=operation,
            query=query,
            next_allowed_at=blocked_until,
            message=f"TurkPatent block signal detected ({reason}); cooldown active.",
        )

    def _evaluate_permission(self, *, operation: str, query: str | None = None) -> dict:
        now = self._now()

        def mutate(state: dict) -> tuple[dict, dict]:
            state = self._normalize_windows(state, now)
            blocked_until = float(state.get("blocked_until") or 0)
            if blocked_until > now:
                return state, self._event(
                    safety_stop=True,
                    reason="safety_blocked",
                    operation=operation,
                    query=query,
                    next_allowed_at=blocked_until,
                    message="TurkPatent scraper is cooling down after a block signal.",
                )

            hour_count = int(state.get("hour_count") or 0)
            day_count = int(state.get("day_count") or 0)
            if self.policy.hourly_budget >= 0 and hour_count >= self.policy.hourly_budget:
                next_allowed = float(state.get("hour_window_start") or now) + 3600
                return state, self._event(
                    safety_stop=True,
                    reason="safety_rate_limited",
                    operation=operation,
                    query=query,
                    next_allowed_at=next_allowed,
                    message="TurkPatent hourly scraper budget exhausted.",
                )
            if self.policy.daily_budget >= 0 and day_count >= self.policy.daily_budget:
                next_allowed = _next_utc_midnight(now)
                return state, self._event(
                    safety_stop=True,
                    reason="safety_rate_limited",
                    operation=operation,
                    query=query,
                    next_allowed_at=next_allowed,
                    message="TurkPatent daily scraper budget exhausted.",
                )

            next_allowed_at = float(state.get("next_allowed_at") or 0)
            if next_allowed_at > now:
                wait_seconds = next_allowed_at - now
                if wait_seconds > self.policy.max_wait_seconds:
                    return state, self._event(
                        safety_stop=True,
                        reason="safety_rate_limited",
                        operation=operation,
                        query=query,
                        wait_seconds=wait_seconds,
                        next_allowed_at=next_allowed_at,
                        message="TurkPatent scraper wait exceeds configured maximum.",
                    )
                return state, self._event(
                    safety_stop=False,
                    reason=None,
                    operation=operation,
                    query=query,
                    wait_seconds=wait_seconds,
                    next_allowed_at=next_allowed_at,
                    message="TurkPatent scraper request is waiting for pacing.",
                )

            jitter = random.uniform(
                max(self.policy.jitter_min_seconds, 0.0),
                max(self.policy.jitter_max_seconds, self.policy.jitter_min_seconds, 0.0),
            )
            state["hour_count"] = hour_count + 1
            state["day_count"] = day_count + 1
            state["last_request_at"] = now
            state["next_allowed_at"] = now + max(self.policy.min_interval_seconds, 0.0) + jitter
            state["updated_at"] = now
            return state, self._event(
                safety_stop=False,
                reason=None,
                operation=operation,
                query=query,
                next_allowed_at=state["next_allowed_at"],
                message="TurkPatent scraper request allowed.",
            )

        return self._mutate_state(mutate, returns_event=True)

    def _normalize_windows(self, state: dict, now: float) -> dict:
        hour_start = float(state.get("hour_window_start") or now)
        if now - hour_start >= 3600:
            state["hour_window_start"] = now
            state["hour_count"] = 0
        else:
            state["hour_window_start"] = hour_start
            state["hour_count"] = int(state.get("hour_count") or 0)

        day_key = _utc_day_key(now)
        if state.get("day_key") != day_key:
            state["day_key"] = day_key
            state["day_count"] = 0
        else:
            state["day_count"] = int(state.get("day_count") or 0)
        return state

    def _event(
        self,
        *,
        safety_stop: bool,
        reason: str | None,
        operation: str | None,
        query: str | None,
        wait_seconds: float | None = None,
        next_allowed_at: float | None = None,
        message: str | None = None,
    ) -> dict:
        return {
            "safety_stop": safety_stop,
            "reason": reason,
            "operation": operation,
            "query": query,
            "wait_seconds": wait_seconds,
            "next_allowed_at": _epoch_to_iso(next_allowed_at),
            "message": message,
        }

    def _mutate_state(self, mutator, *, returns_event: bool = False):
        self.policy.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.policy.state_path.with_suffix(self.policy.state_path.suffix + ".lock")
        fd = self._acquire_lock(lock_path)
        try:
            state = self._load_state()
            result = mutator(state)
            if returns_event:
                state, event = result
            else:
                state = result
                event = None
            self._save_state(state)
            return event if returns_event else None
        finally:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass

    def _acquire_lock(self, lock_path: Path) -> int:
        deadline = time.monotonic() + max(self.policy.max_wait_seconds, 1.0)
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {self._now()}".encode("ascii", errors="ignore"))
                return fd
            except FileExistsError:
                try:
                    if self._now() - lock_path.stat().st_mtime > self.policy.stale_lock_seconds:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise ScraperSafetyStop(
                        self._event(
                            safety_stop=True,
                            reason="safety_rate_limited",
                            operation="state_lock",
                            query=None,
                            message="Could not acquire scraper safety state lock.",
                        )
                    )
                self._sleep(0.25)

    def _load_state(self) -> dict:
        try:
            with self.policy.state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        tmp_path = self.policy.state_path.with_suffix(self.policy.state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_path.replace(self.policy.state_path)


class TurkPatentScraper:
    def __init__(self, headless: bool = True, safety_policy: ScraperSafetyPolicy | None = None):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.headless = headless
        self.url = "https://www.turkpatent.gov.tr/arastirma-yap?form=trademark"
        self.safety_policy = safety_policy or ScraperSafetyPolicy.from_env()
        self.safety = ScraperSafetyGuard(self.safety_policy)
        self.last_safety_event: dict | None = None
        self.last_save_info: dict | None = None
        
        # Dynamic storage management
        self.active_data_dir: Path = None
        self.active_metadata_file: Path = None
        self._resolve_storage_path()

    def _resolve_storage_path(self):
        """
        Scans for APP_N folders, checks the latest one's size.
        If > 5,000 records, creates APP_{N+1}.
        Sets self.active_data_dir and self.active_metadata_file.
        """
        # 1. Find existing APP_N folders
        app_folders = []
        for p in ROOT_DIR.glob("APP_*"):
            if p.is_dir() and re.match(r"APP_\d+$", p.name):
                try:
                    n = int(p.name.split("_")[1])
                    app_folders.append((n, p))
                except ValueError:
                    continue
        
        app_folders.sort(key=lambda x: x[0])

        if not app_folders:
            current_n = 1
            current_dir = ROOT_DIR / f"APP_{current_n}"
            current_dir.mkdir(parents=True, exist_ok=True)
        else:
            current_n, current_dir = app_folders[-1]

        # 2. Check record count in the latest file
        meta_file = current_dir / "metadata.json"
        count = 0
        if meta_file.exists():
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        count = len(data)
            except Exception:
                pass # Corrupt or empty file

        # 3. Rotate if limit reached
        if count >= 10000:
            current_n += 1
            current_dir = ROOT_DIR / f"APP_{current_n}"
            current_dir.mkdir(parents=True, exist_ok=True)
            meta_file = current_dir / "metadata.json"
            logging.info(f"🔄 Storage limit reached. Rotating to new folder: {current_dir.name}")
        
        self.active_data_dir = current_dir
        self.active_metadata_file = meta_file

    def start_browser(self):
        logging.info(f"🚀 Launching Playwright Chromium (Headless={self.headless})...")
        self.pw = sync_playwright().start()
        
        self.browser = self.pw.chromium.launch(
            headless=self.headless, 
            slow_mo=50 if not self.headless else 0
        )
        
        self.context = self.browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080}
        )
        self.page = self.context.new_page()
        if stealth_sync is not None:
            stealth_sync(self.page)
        self.page.set_default_timeout(60000)

    def close(self):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def _rotate_user_agent(self):
        """Recreate browser context with a rotated user agent for fallback search."""
        new_ua = random.choice(FALLBACK_USER_AGENTS)
        logging.info(f"   [UA] Rotating user agent for Turkish character fallback")
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        self.context = self.browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080},
            user_agent=new_ua,
        )
        self.page = self.context.new_page()
        if stealth_sync is not None:
            stealth_sync(self.page)
        self.page.set_default_timeout(60000)

    def _ensure_request_allowed(self, *, operation: str, query: str | None = None) -> dict:
        event = self.safety.request_permission(operation=operation, query=query)
        if event.get("safety_stop"):
            self.last_safety_event = event
            logging.warning("   [SAFETY] Soft-stopping TurkPatent request: %s", event.get("message"))
            raise ScraperSafetyStop(event)
        return event

    def _record_block_and_stop(self, *, reason: str, operation: str | None = None, query: str | None = None):
        event = self.safety.record_block(reason=reason, operation=operation, query=query)
        self.last_safety_event = event
        logging.warning("   [SAFETY] TurkPatent block signal: %s", reason)
        raise ScraperSafetyStop(event)

    def _current_page_text(self) -> str:
        try:
            return self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            try:
                return self.page.content() or ""
            except Exception:
                return ""

    def _safety_stop_evidence(self, application_no: str, trademark_name: str, event: dict) -> dict:
        reason = event.get("reason") or "safety_rate_limited"
        return {
            "application_no": application_no,
            "query": trademark_name,
            "matched": False,
            "status_text": "",
            "registration_no": "",
            "nice_classes": [],
            "artifact_dir": None,
            "artifact_error": reason,
            "safety_stop": True,
            "safety_reason": reason,
            "next_allowed_at": event.get("next_allowed_at"),
            "safety_message": event.get("message"),
        }

    # --- COMPONENT LOGIC ---

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(ScraperSafetyStop),
    )
    def _safe_goto(
        self,
        url: str,
        *,
        safety_checked: bool = False,
        operation: str = "goto",
        query: str | None = None,
    ):
        """Navigates to a URL with exponential backoff retries."""
        if not safety_checked:
            self._ensure_request_allowed(operation=operation, query=query or url)
        logging.info(f"   [NETWORK] Attempting to load {url}...")
        response = self.page.goto(url, wait_until="domcontentloaded")
        status = getattr(response, "status", None)
        if status in {403, 429}:
            self._record_block_and_stop(reason=f"http_{status}", operation=operation, query=query or url)
        return response

    def _close_popups(self):
        """Identifies and closes common cookie banners and announcement (Duyuru) modals."""
        # 1. Hit Escape, which commonly closes active dialogs
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        except Exception:
            pass

        # 2. Try common consent and close buttons
        candidates = [
            self.page.get_by_role("button", name=re.compile(r"kabul|accept|tamam|ok|anlad[ıi]m", re.I)),
            self.page.locator("button:has-text('Kabul')"),
            self.page.locator("button:has-text('Accept')"),
            self.page.locator("button[aria-label*='Close']"),
            self.page.locator("button[aria-label*='Kapat']"),
            self.page.locator("div[role='dialog'] button").first,
        ]
        
        for c in candidates:
            try:
                if c.count() > 0:
                    c.first.click(timeout=800, force=True)
                    self.page.wait_for_timeout(300)
            except Exception:
                pass
                
        # 3. Click completely outside to dismiss backdrop-dismissible modals
        try:
            self.page.mouse.click(2, 2)
            self.page.wait_for_timeout(300)
        except Exception:
            pass

        # 4. Nuclear option: hide any remaining full-screen overlays via JS
        try:
            self.page.evaluate("""() => {
                document.querySelectorAll('section[class*="jss"], div[role="dialog"], .MuiDialog-root, .MuiBackdrop-root').forEach(el => {
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' && parseInt(style.zIndex || 0) > 50) {
                        el.style.display = 'none';
                    }
                });
            }""")
        except Exception:
            pass

    def _ensure_marka_arastirma_tab(self):
        try:
            self.page.get_by_text(re.compile(r"Marka\s*Araştırma|Marka\s*Arastirma", re.I)).first.click(timeout=2000)
            self.page.wait_for_timeout(500)
        except Exception:
            pass

    def _locate_marka_adi_input(self):
        label_variants = ["Marka Adı", "Marka Adi", "Marka"]
        for lab in label_variants:
            loc = self.page.locator(f"xpath=//mat-form-field[.//*[contains(normalize-space(.), {repr(lab)})]]//input")
            if loc.count() > 0: 
                return loc.first

        loc = self.page.locator(
            "input:visible[placeholder*='Marka Adı' i], input:visible[placeholder*='Marka Adi' i], "
            "input:visible[aria-label*='Marka Adı' i], input:visible[aria-label*='Marka Adi' i]"
        )
        if loc.count() > 0: 
            return loc.first

        loc = self.page.locator("input:visible[id*='markaAdi' i], input:visible[name*='markaAdi' i]")
        if loc.count() > 0: 
            return loc.first
        return None

    def _ensure_sonsuz_liste_on(self):
        """Original Logic: Enables 'Sonsuz Liste' switch using role, checkbox, or container click."""
        logging.info("   [INFO] Checking 'Sonsuz Liste' switch...")
        try:
            sw = self.page.get_by_role("switch", name=re.compile(r"Sonsuz\s*Liste", re.I))
            if sw.count() > 0:
                aria = (sw.first.get_attribute("aria-checked") or "").lower()
                if aria != "true":
                    logging.info("   [INFO] Switch found (role). Toggling ON.")
                    sw.first.click(force=True)
                    self.page.wait_for_timeout(800)
                else:
                    logging.info("   [INFO] Switch found (role) and already ON.")
                return True
        except Exception:
            pass

        try:
            chk = self.page.locator("div:has-text('Sonsuz Liste') input[type=checkbox]").first
            if chk.count() > 0:
                try:
                    if not chk.is_checked():
                        logging.info("   [INFO] Checkbox found. Toggling ON.")
                        try:
                            chk.check()
                        except Exception:
                            self.page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                        self.page.wait_for_timeout(800)
                    else:
                        logging.info("   [INFO] Checkbox found and already ON.")
                except Exception:
                    logging.warning("   [WARN] Checkbox interaction failed. Clicking container.")
                    self.page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                    self.page.wait_for_timeout(800)
                return True
        except Exception:
            pass
        return False

    def _get_total_records_count(self) -> int:
        """Reads the total record count from the results page."""
        try:
            loc = self.page.locator("xpath=//*[contains(translate(.,'ıİ','iI'),'kayit bulundu') or contains(., 'kayıt bulundu')]").first
            t = loc.inner_text(timeout=2500)
            m = re.search(r"([\d\.\,]+)\s*kay[ıi]t\s*bulundu", t, re.I)
            if m:
                digits = re.sub(r"[^\d]", "", m.group(1))
                return int(digits)
        except Exception:
            pass

        try:
            body_text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            patterns = [
                r"([\d\.\,]+)\s*kay[ıi]t\s*bulundu",
                r"Toplam\s*[:\-]?\s*([\d\.\,]+)\s*kay[ıi]t",
            ]
            for pat in patterns:
                m = re.search(pat, body_text, re.I)
                if m:
                    n = re.sub(r"[^\d]", "", m.group(1))
                    if int(n) > 0: 
                        return int(n)
        except Exception:
            pass
        return 0

    def _get_last_row_position(self, row_sel: str = ".dx-data-row") -> int:
        """Finds the index of the last visible row using pure JS (10x Faster)."""
        return self.page.evaluate("""(sel) => {
            const css = sel.replace('css=', '');
            const rows = document.querySelectorAll(css);
            if (rows.length === 0) return 0;
            const last = rows[rows.length - 1];
            
            // Try all common index attributes
            const idx = last.getAttribute('aria-rowindex') || 
                        last.getAttribute('data-rowindex') || 
                        last.getAttribute('data-idx');
            
            if (idx) return parseInt(idx);
            return rows.length;
        }""", row_sel)

    # --- GRID DETECTION ---

    def _detect_grid(self) -> tuple:
        """Detects grid type and returns (row_selector, scroll_target_selector)."""
        dx = self.page.locator(".dx-datagrid")
        if dx.count() > 0:
            return ("css=.dx-datagrid-rowsview .dx-data-row", "css=.dx-datagrid-rowsview")
        cdk = self.page.locator("cdk-virtual-scroll-viewport")
        if cdk.count() > 0:
            return ("css=cdk-virtual-scroll-viewport .cdk-virtual-scroll-content-wrapper > *", "css=cdk-virtual-scroll-viewport")
        return ("css=table tbody tr", "css=body")

    def _is_body_scroll(self, scroll_target_sel: str) -> bool:
        s = (scroll_target_sel or "").lower().strip()
        return s in ("css=body", "body", "css=html", "html", "css=document", "document")

    # --- SCROLLING (EXACT LOGIC FROM tescil_test.py) ---

    def _js_scroll_to_bottom(self, scroll_target_sel: str, offset: int = 0):
        """Scrolls to bottom of container using JavaScript."""
        if self._is_body_scroll(scroll_target_sel):
            self.page.evaluate("""(off) => {
                const el = document.scrollingElement || document.documentElement;
                if (!el) return;
                const maxTop = Math.max(0, el.scrollHeight - el.clientHeight - off);
                el.scrollTop = maxTop;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
            }""", offset)
            return
        loc = self.page.locator(scroll_target_sel).first
        loc.evaluate("""(el, off) => {
            try {
              const tgt = el.querySelector('.dx-scrollable-container') || el;
              const maxTop = Math.max(0, tgt.scrollHeight - tgt.clientHeight - off);
              tgt.scrollTop = maxTop;
              tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
            } catch(e) {}
        }""", offset)

    def _js_scroll_by(self, scroll_target_sel: str, dy: int):
        """Scrolls by delta using JavaScript."""
        if self._is_body_scroll(scroll_target_sel):
            self.page.evaluate("""(dy) => {
                const el = document.scrollingElement || document.documentElement;
                if (!el) return;
                el.scrollTop = el.scrollTop + dy;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
            }""", dy)
            return
        loc = self.page.locator(scroll_target_sel).first
        loc.evaluate("""(el, dy) => {
            try {
              const tgt = el.querySelector('.dx-scrollable-container') || el;
              tgt.scrollTop = tgt.scrollTop + dy;
              tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
            } catch(e) {}
        }""", dy)

    def _js_scroll_to_top(self, scroll_target_sel: str):
        """Scrolls to top of the current result container."""
        if self._is_body_scroll(scroll_target_sel):
            self.page.evaluate("""() => {
                const el = document.scrollingElement || document.documentElement;
                if (!el) return;
                el.scrollTop = 0;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
            }""")
            return
        loc = self.page.locator(scroll_target_sel).first
        loc.evaluate("""(el) => {
            try {
              const tgt = el.querySelector('.dx-scrollable-container') || el;
              tgt.scrollTop = 0;
              tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
            } catch(e) {}
        }""")

    def _wheel_inside_scroll_target(self, scroll_target_sel: str, delta_y: int):
        """
        EXACT wheel scroll logic from tescil_test.py - includes click before wheel.
        Mouse wheel scroll with click for more reliable loading.
        """
        try:
            if self._is_body_scroll(scroll_target_sel):
                vp = self.page.viewport_size
                if vp:
                    self.page.mouse.move(vp["width"] / 2, vp["height"] / 2)
                self.page.mouse.wheel(0, delta_y)
                return
            base = self.page.locator(scroll_target_sel).first
            inner = base.locator(".dx-scrollable-container").first
            target = inner if inner.count() > 0 else base
            box = target.bounding_box()
            if box:
                x = box["x"] + box["width"] / 2
                y = box["y"] + min(box["height"] - 5, box["height"] / 2)
                self.page.mouse.move(x, y)
                # KEY DIFFERENCE: Click before wheel scroll (from tescil_test.py)
                try:
                    self.page.mouse.click(x, y)
                except Exception:
                    pass
            self.page.mouse.wheel(0, delta_y)
        except Exception:
            try:
                self.page.mouse.wheel(0, delta_y)
            except Exception:
                pass

    def _wait_for_position_change(self, row_sel: str, prev_pos: int, timeout_s: float = 10.0) -> int:
        """Wait until row position increases or timeout."""
        deadline = time.time() + timeout_s
        best = prev_pos
        while time.time() < deadline:
            # PERFORMANCE TUNED: Check every 0.05s
            time.sleep(0.05)
            cur = self._get_last_row_position(row_sel)
            if cur > best:
                return cur
            best = max(best, cur)
        return best

    def _jiggle_recovery(self, scroll_target_sel: str, stagnation: int):
        """EXACT jiggle recovery logic from tescil_test.py."""
        logging.info("    [DEBUG] Jiggle Strategy Activated")
        up_amount = -225
        down_amount = 2600
        self._wheel_inside_scroll_target(scroll_target_sel, delta_y=up_amount)
        time.sleep(1.0)
        self._js_scroll_to_bottom(scroll_target_sel, offset=0)
        time.sleep(0.2)
        try:
            if self._is_body_scroll(scroll_target_sel):
                self.page.keyboard.press("End")
        except Exception:
            pass
        self._wheel_inside_scroll_target(scroll_target_sel, delta_y=down_amount)

    # --- EXTRACTION ---

    def _scrape_current_view(self, row_sel: str, data_store: Dict[str, List[str]]):
        """Scrapes visible data using pure JS (Bulk Scrape) for max speed."""
        try:
            # Executes one single JS function to get all text. 
            # Replaces 50+ network roundtrips with 1.
            new_data = self.page.evaluate("""(sel) => {
                const css = sel.replace('css=', '');
                const rows = Array.from(document.querySelectorAll(css));
                return rows.map(row => {
                    const cells = Array.from(row.querySelectorAll("td, div[role='gridcell']"));
                    return cells.map(c => c.innerText.trim());
                });
            }""", row_sel)
            
            for row_data in new_data:
                if any(row_data):
                    # Dedup key: combine first few columns (standard is starting from index 1)
                    key = "|".join(row_data[1:min(5, len(row_data))])
                    data_store[key] = row_data
        except Exception:
            pass

    @staticmethod
    def _row_application_no(row: List[str]) -> str:
        return row[1].strip() if len(row) > 1 and row[1] else ""

    @staticmethod
    def _row_status(row: List[str]) -> str:
        return row[6].strip() if len(row) > 6 and row[6] else ""

    @staticmethod
    def _row_registration_no(row: List[str]) -> str:
        return row[5].strip() if len(row) > 5 and row[5] else ""

    @staticmethod
    def parse_detail_nice_classes(detail_text: str) -> List[int]:
        """Extract Nice classes from DETAY -> Marka Bilgileri -> Nice Sınıfları."""
        if not detail_text:
            return []

        text = html.unescape(str(detail_text))
        text = re.sub(r"\r\n?", "\n", text)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        def normalize_label(value: str) -> str:
            return (
                value.lower()
                .replace("Ä±", "i")
                .replace("Ä°", "i")
                .replace("ÅŸ", "s")
                .replace("Åž", "s")
                .replace("ı", "i")
                .replace("İ", "i")
                .replace("ş", "s")
                .replace("Ş", "s")
                .replace("ü", "u")
                .replace("Ü", "u")
            )

        normalized_lines = [normalize_label(line) for line in lines]
        start_index = 0
        end_index = len(lines)
        for index, normalized in enumerate(normalized_lines):
            if "marka bilgileri" in normalized:
                start_index = index
                break
        for index in range(start_index, len(lines)):
            if "mal ve hizmet bilgileri" in normalized_lines[index]:
                end_index = index
                break

        label_re = re.compile(r"nice\s+s(?:ı|i|Ä±)n(?:ı|i|Ä±)flar(?:ı|i|Ä±)", flags=re.IGNORECASE)
        for index in range(start_index, end_index):
            normalized = normalized_lines[index]
            if "nice siniflari" not in normalized:
                continue
            if "islem" in normalized and "sekil" in normalized:
                continue

            same_line = lines[index]
            inline = label_re.split(same_line, maxsplit=1)
            candidates = []
            if len(inline) > 1:
                candidates.append(re.split(r"\bT(?:ü|u|Ã¼)r(?:ü|u|Ã¼)\b", inline[-1], maxsplit=1, flags=re.IGNORECASE)[0])
            candidates.extend(lines[index + 1:min(index + 4, end_index)])

            for candidate in candidates:
                if "/" not in candidate and len(re.findall(r"\d{1,3}", candidate)) == 1:
                    continue
                values = sorted(
                    {
                        int(value)
                        for value in re.findall(r"\d{1,3}", candidate)
                        if value.isdigit() and 1 <= int(value) <= 45
                    }
                )
                if values:
                    return values

        match = re.search(
            r"nice\s+s(?:ı|i|Ä±)n(?:ı|i|Ä±)flar(?:ı|i|Ä±)\s*[:\-]?\s*([0-9\s/.,;]+)",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return []
        return sorted(
            {
                int(value)
                for value in re.findall(r"\d{1,3}", match.group(1))
                if value.isdigit() and 1 <= int(value) <= 45
            }
        )

    def _click_detail_for_application_no(self, application_no: str, max_scrolls: int = 80) -> bool:
        row_sel, scroll_target_sel = self._detect_grid()
        self._js_scroll_to_top(scroll_target_sel)
        self.page.wait_for_timeout(300)

        for _ in range(max_scrolls):
            rows = self.page.locator(row_sel)
            try:
                count = rows.count()
            except Exception:
                count = 0

            for index in range(count):
                row = rows.nth(index)
                try:
                    row_text = row.inner_text(timeout=1000)
                except Exception:
                    continue
                if application_no not in row_text:
                    continue

                detail = row.locator("button:has-text('DETAY'), a:has-text('DETAY')").first
                try:
                    if detail.count() > 0:
                        detail.click(timeout=5000, force=True)
                        self.page.wait_for_timeout(1200)
                        return True
                except Exception:
                    pass

                try:
                    row.get_by_text(re.compile(r"DETAY", re.I)).first.click(timeout=5000, force=True)
                    self.page.wait_for_timeout(1200)
                    return True
                except Exception:
                    return False

            self._wheel_inside_scroll_target(scroll_target_sel, delta_y=2200)
            self.page.wait_for_timeout(250)

        return False

    def _scroll_detail_content_to_bottom(self) -> None:
        try:
            self.page.evaluate(
                """async () => {
                    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                    const normalize = (value) => (value || '')
                        .toLowerCase()
                        .replaceAll('ı', 'i')
                        .replaceAll('İ', 'i')
                        .replaceAll('ş', 's')
                        .replaceAll('Ş', 's');
                    const elements = Array.from(document.querySelectorAll('body, body *'));
                    const detailMatches = elements
                        .filter((el) => {
                            const text = normalize(el.innerText);
                            return text.includes('marka bilgileri') && text.includes('mal ve hizmet bilgileri');
                        })
                        .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length);
                    const detailRoot = detailMatches[0] || document.body;
                    const scrollables = elements.filter((el) => {
                        const style = window.getComputedStyle(el);
                        const canScroll = el.scrollHeight > el.clientHeight + 24;
                        const scrollStyle = `${style.overflow} ${style.overflowY}`;
                        return canScroll && /(auto|scroll|overlay)/i.test(scrollStyle);
                    });
                    const scopedScrollables = scrollables.filter(
                        (el) => detailRoot.contains(el) || el.contains(detailRoot) || el === detailRoot
                    );
                    const targets = scopedScrollables.length ? scopedScrollables : scrollables;
                    for (const el of targets) {
                        let previous = -1;
                        for (let attempt = 0; attempt < 30; attempt += 1) {
                            el.scrollTop = el.scrollHeight;
                            await sleep(80);
                            if (el.scrollTop === previous) {
                                break;
                            }
                            previous = el.scrollTop;
                        }
                    }
                    window.scrollTo(0, document.body.scrollHeight);
                    await sleep(120);
                }"""
            )
        except Exception:
            pass

    def _extract_detail_panel_text(self, application_no: str | None = None) -> str:
        try:
            return self.page.evaluate(
                """(applicationNo) => {
                    const normalize = (value) => (value || '')
                        .toLowerCase()
                        .replaceAll('ı', 'i')
                        .replaceAll('İ', 'i')
                        .replaceAll('ş', 's')
                        .replaceAll('Ş', 's');
                    const isVisible = (el) => {
                        if (!el || !el.tagName) {
                            return false;
                        }
                        const tag = el.tagName.toLowerCase();
                        if (['script', 'style', 'noscript', 'template', 'svg', 'path'].includes(tag)) {
                            return false;
                        }
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
                            return false;
                        }
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const elements = Array.from(document.querySelectorAll('body, body *'));
                    const detailMatches = elements
                        .filter((el) => {
                            if (!isVisible(el)) {
                                return false;
                            }
                            const text = normalize(el.innerText);
                            if (!text.includes('marka bilgileri') || !text.includes('mal ve hizmet bilgileri')) {
                                return false;
                            }
                            return !applicationNo || (el.innerText || '').includes(applicationNo);
                        })
                        .map((el) => {
                            const text = el.innerText || '';
                            const normalized = normalize(text);
                            return {
                                el,
                                text,
                                detailIndex: normalized.indexOf('marka bilgileri'),
                                length: text.length,
                                hasHistory: normalized.includes('basvuru islem bilgileri'),
                                hasSearchHeader: normalized.includes('marka arastirma')
                            };
                        })
                        .filter((item) => item.el !== document.body && item.el !== document.documentElement)
                        .sort((a, b) => {
                            if (a.hasSearchHeader !== b.hasSearchHeader) {
                                return a.hasSearchHeader ? 1 : -1;
                            }
                            if (a.hasHistory !== b.hasHistory) {
                                return a.hasHistory ? -1 : 1;
                            }
                            if (a.detailIndex !== b.detailIndex) {
                                return a.detailIndex - b.detailIndex;
                            }
                            return b.length - a.length;
                        });
                    return detailMatches[0]?.text || '';
                }""",
                application_no,
            )
        except Exception:
            return ""

    def _save_detail_artifacts(self, application_no: str, artifact_root: Path, detail_text: str | None = None) -> dict:
        safe_app_no = re.sub(r"[^0-9A-Za-z._-]+", "_", application_no)
        out_dir = artifact_root / safe_app_no
        out_dir.mkdir(parents=True, exist_ok=True)

        result = {"artifact_dir": str(out_dir), "html_saved": False, "pdf_saved": False, "error": None}
        try:
            (out_dir / "detail.html").write_text(self.page.content(), encoding="utf-8")
            artifact_text = detail_text or self._extract_detail_panel_text(application_no)
            if not artifact_text:
                artifact_text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            (out_dir / "detail.txt").write_text(artifact_text, encoding="utf-8")
            result["html_saved"] = True
        except Exception as exc:
            result["error"] = str(exc)

        return result

    def fetch_live_detail_evidence(
        self,
        application_no: str,
        trademark_name: str,
        *,
        artifact_root: str | Path,
        limit: int = 200,
        max_scroll_seconds: int = 90,
    ) -> dict:
        """
        Search TURKPATENT, exact-match application_no, open DETAY, and return live evidence.

        This method does not persist APP metadata. It is intended for repair/audit flows.
        """
        self.last_safety_event = None
        if not self.page:
            self.start_browser()

        try:
            rows = self._do_search(trademark_name, min(max(limit, 1), MAX_SCRAPE_LIMIT), max_scroll_seconds)
        except ScraperSafetyStop as exc:
            return self._safety_stop_evidence(application_no, trademark_name, exc.event)

        matched_row = next((row for row in rows if self._row_application_no(row) == application_no), None)
        if matched_row is None:
            return {
                "application_no": application_no,
                "query": trademark_name,
                "matched": False,
                "status_text": "",
                "registration_no": "",
                "nice_classes": [],
                "artifact_dir": None,
                "artifact_error": None,
            }

        detail_opened = self._click_detail_for_application_no(application_no)
        if not detail_opened:
            return {
                "application_no": application_no,
                "query": trademark_name,
                "matched": True,
                "detail_opened": False,
                "status_text": self._row_status(matched_row),
                "registration_no": self._row_registration_no(matched_row),
                "nice_classes": [],
                "artifact_dir": None,
                "artifact_error": "detail_button_not_found",
            }

        detail_text = ""
        for _ in range(20):
            detail_text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            detail_ready_text = (
                detail_text.lower()
                .replace("ı", "i")
                .replace("İ", "i")
                .replace("ş", "s")
                .replace("Ş", "s")
            )
            if (
                application_no in detail_text
                and "marka bilgileri" in detail_ready_text
                and "mal ve hizmet bilgileri" in detail_ready_text
            ):
                break
            self.page.wait_for_timeout(250)

        self._scroll_detail_content_to_bottom()
        detail_text = self._extract_detail_panel_text(application_no)
        if not detail_text:
            detail_text = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        artifact = self._save_detail_artifacts(application_no, Path(artifact_root), detail_text=detail_text)
        return {
            "application_no": application_no,
            "query": trademark_name,
            "matched": True,
            "detail_opened": True,
            "status_text": self._row_status(matched_row),
            "registration_no": self._row_registration_no(matched_row),
            "nice_classes": self.parse_detail_nice_classes(detail_text),
            "artifact_dir": artifact.get("artifact_dir"),
            "artifact_error": artifact.get("error"),
            "artifact_html_saved": artifact.get("html_saved", False),
            "artifact_pdf_saved": artifact.get("pdf_saved", False),
        }

    def fetch_live_grid_evidence(
        self,
        application_no: str,
        trademark_name: str,
        *,
        limit: int = 200,
        max_scroll_seconds: int = 90,
    ) -> dict:
        """
        Search TURKPATENT, exact-match application_no, and return grid-only evidence.

        Status repair uses this path because Durumu and Tescil No are available in
        the search grid; full Nice-class repair still opens DETAY.
        """
        self.last_safety_event = None
        if not self.page:
            self.start_browser()

        try:
            rows = self._do_search(trademark_name, min(max(limit, 1), MAX_SCRAPE_LIMIT), max_scroll_seconds)
        except ScraperSafetyStop as exc:
            return self._safety_stop_evidence(application_no, trademark_name, exc.event)

        matched_row = next((row for row in rows if self._row_application_no(row) == application_no), None)
        if matched_row is None:
            return {
                "application_no": application_no,
                "query": trademark_name,
                "matched": False,
                "detail_opened": False,
                "status_text": "",
                "registration_no": "",
                "nice_classes": [],
                "artifact_dir": None,
                "artifact_error": None,
            }

        return {
            "application_no": application_no,
            "query": trademark_name,
            "matched": True,
            "detail_opened": False,
            "status_text": self._row_status(matched_row),
            "registration_no": self._row_registration_no(matched_row),
            "nice_classes": [],
            "artifact_dir": None,
            "artifact_error": None,
        }

    def _save_to_json_legacy_unused(self, data: List[List[str]]):
        """Saves the captured data to a JSON file following the specified schema."""
        if not data:
            logging.warning("No data to save.")
            return

        # Ensure we are pointing to the correct storage file (handling rotation)
        self._resolve_storage_path()
        target_file = self.active_metadata_file

        # Ensure directory exists
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing items for deduplication
        existing_items = []
        if target_file.exists():
            try:
                with open(target_file, 'r', encoding='utf-8') as f:
                    existing_items = json.load(f)
            except Exception:
                pass

        seen_app_nos = {item.get("APPLICATIONNO") for item in existing_items}
        new_count = 0

        for row in data:
            # Mapping indices (skipping counter at index 0):
            # [1] Application No, [2] Trademark Name, [3] Holder Name,
            # [4] App Date, [5] Reg No, [6] Status, [7] NICE Classes

            app_no = row[1].strip() if len(row) > 1 else ""
            name = row[2].strip() if len(row) > 2 else ""
            holders_raw = row[3].strip() if len(row) > 3 else ""
            app_date = row[4].strip() if len(row) > 4 else ""
            reg_no = row[5].strip() if len(row) > 5 else ""
            status = row[6].strip() if len(row) > 6 else ""
            classes_raw = row[7].strip() if len(row) > 7 else ""

            # Skip if no valid app number or already exists
            if not app_no or app_no in seen_app_nos:
                continue

            # Parse NICE classes
            nice_classes_list = []
            if classes_raw:
                nice_classes_list = re.findall(r'\d+', classes_raw)
                classes_raw = ", ".join(nice_classes_list)

            item = {
                "APPLICATIONNO": app_no,
                "STATUS": status,
                "IMAGE": app_no.replace('/', '_'),
                "TRADEMARK": {
                    "APPLICATIONDATE": app_date,
                    "REGISTERNO": reg_no,
                    "REGISTERDATE": "",
                    "INTREGNO": "",
                    "NAME": name,
                    "NICECLASSES_RAW": classes_raw,
                    "NICECLASSES_LIST": nice_classes_list,
                    "TM_TYPE_CODE": "",
                    "VIENNACLASSES_RAW": "",
                    "VIENNACLASSES_LIST": [],
                    "BULLETIN_NO": "",
                    "BULLETIN_DATE": "",
                    "EXTRA_COL_11": "",
                    "EXTRA_COL_12": ""
                },
                "HOLDERS": [
                    {
                        "TPECLIENTID": "",
                        "TITLE": holders_raw,
                        "ADDRESS": "",
                        "TOWN_DISTRICT": "",
                        "POSTALCODE": "",
                        "CITY_PROVINCE": "",
                        "COUNTRY": "TÜRKİYE"
                    }
                ],
                "ATTORNEYS": [],
                "GOODS": [],
                "EXTRACTEDGOODS": []
            }

            existing_items.append(item)
            seen_app_nos.add(app_no)
            new_count += 1

        # Save to file
        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(existing_items, f, indent=2, ensure_ascii=False)

        logging.info(f"   💾 Saved to {target_file.parent.name}/{target_file.name}: Added {new_count} new records (Total: {len(existing_items)})")

    @staticmethod
    def _metadata_item_from_row(row):
        if isinstance(row, dict):
            return json.loads(json.dumps(row, ensure_ascii=False))

        app_no = row[1].strip() if len(row) > 1 and row[1] else ""
        name = row[2].strip() if len(row) > 2 and row[2] else ""
        holders_raw = row[3].strip() if len(row) > 3 and row[3] else ""
        app_date = row[4].strip() if len(row) > 4 and row[4] else ""
        reg_no = row[5].strip() if len(row) > 5 and row[5] else ""
        status = row[6].strip() if len(row) > 6 and row[6] else ""
        classes_raw = row[7].strip() if len(row) > 7 and row[7] else ""

        nice_classes_list = []
        if classes_raw:
            nice_classes_list = re.findall(r'\d+', classes_raw)
            classes_raw = ", ".join(nice_classes_list)

        return {
            "APPLICATIONNO": app_no,
            "STATUS": status,
            "IMAGE": app_no.replace('/', '_'),
            "TRADEMARK": {
                "APPLICATIONDATE": app_date,
                "REGISTERNO": reg_no,
                "REGISTERDATE": "",
                "INTREGNO": "",
                "NAME": name,
                "NICECLASSES_RAW": classes_raw,
                "NICECLASSES_LIST": nice_classes_list,
                "TM_TYPE_CODE": "",
                "VIENNACLASSES_RAW": "",
                "VIENNACLASSES_LIST": [],
                "BULLETIN_NO": "",
                "BULLETIN_DATE": "",
                "EXTRA_COL_11": "",
                "EXTRA_COL_12": ""
            },
            "HOLDERS": [
                {
                    "TPECLIENTID": "",
                    "TITLE": holders_raw,
                    "ADDRESS": "",
                    "TOWN_DISTRICT": "",
                    "POSTALCODE": "",
                    "CITY_PROVINCE": "",
                    "COUNTRY": "TÜRKİYE"
                }
            ],
            "ATTORNEYS": [],
            "GOODS": [],
            "EXTRACTEDGOODS": []
        }

    @staticmethod
    def _has_save_value(value):
        return value is not None and value != "" and value != [] and value != {}

    @classmethod
    def _merge_metadata_item(cls, existing, incoming):
        merged = dict(existing or {})
        for key, value in incoming.items():
            if key == "TRADEMARK" and isinstance(value, dict):
                trademark = dict(merged.get("TRADEMARK") or {})
                for subkey, subvalue in value.items():
                    if cls._has_save_value(subvalue):
                        trademark[subkey] = subvalue
                merged["TRADEMARK"] = trademark
            elif key == "STATUS":
                merged[key] = "" if value is None else value
            elif cls._has_save_value(value):
                merged[key] = value
        return merged

    def save_to_json(self, data: List[List[str]], target_file: Path | str | None = None):
        """Upsert captured data into the canonical APP metadata file."""
        self.last_save_info = None
        if not data:
            logging.warning("No data to save.")
            return {
                "metadata_path": None,
                "folder_name": None,
                "saved_application_numbers": [],
                "saved_records": [],
                "added_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
            }

        if target_file is None:
            self._resolve_storage_path()
            target_file = self.active_metadata_file
        else:
            target_file = Path(target_file)
            self.active_data_dir = target_file.parent
            self.active_metadata_file = target_file

        target_file.parent.mkdir(parents=True, exist_ok=True)

        existing_items = []
        if target_file.exists():
            try:
                with open(target_file, 'r', encoding='utf-8') as f:
                    existing_items = json.load(f)
            except Exception:
                pass
        if not isinstance(existing_items, list):
            existing_items = []

        existing_by_app_no = {
            item.get("APPLICATIONNO"): index
            for index, item in enumerate(existing_items)
            if isinstance(item, dict) and item.get("APPLICATIONNO")
        }
        added_count = 0
        updated_count = 0
        skipped_count = 0
        saved_records = []
        saved_app_nos = []

        for row in data:
            item = self._metadata_item_from_row(row)
            app_no = item.get("APPLICATIONNO")
            if not app_no:
                skipped_count += 1
                continue

            if app_no in existing_by_app_no:
                index = existing_by_app_no[app_no]
                merged = self._merge_metadata_item(existing_items[index], item)
                if merged == existing_items[index]:
                    skipped_count += 1
                else:
                    existing_items[index] = merged
                    updated_count += 1
                    saved_records.append(merged)
                    saved_app_nos.append(app_no)
            else:
                existing_by_app_no[app_no] = len(existing_items)
                existing_items.append(item)
                added_count += 1
                saved_records.append(item)
                saved_app_nos.append(app_no)

        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(existing_items, f, indent=2, ensure_ascii=False)

        self.last_save_info = {
            "metadata_path": str(target_file),
            "folder_name": target_file.parent.name,
            "saved_application_numbers": saved_app_nos,
            "saved_records": saved_records,
            "added_count": added_count,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
        }
        logging.info(
            "   Saved to %s/%s: Added %s, Updated %s, Skipped %s (Total: %s)",
            target_file.parent.name,
            target_file.name,
            added_count,
            updated_count,
            skipped_count,
            len(existing_items),
        )
        return self.last_save_info

    # --- MAIN WORKFLOW (EXACT scroll_and_capture LOGIC FROM tescil_test.py) ---

    def _click_sorgula(self):
        """Clicks the search button."""
        candidates = [
            self.page.get_by_role("button", name=re.compile(r"Sorgula", re.I)),
            self.page.locator("button:has-text('SORGULA')"),
            self.page.locator("button:has-text('Sorgula')"),
        ]
        for btn in candidates:
            try:
                if btn.first.count() > 0:
                    btn.first.click(timeout=8000)
                    return
            except Exception:
                continue
        # Fallback: press Enter
        self.page.keyboard.press("Enter")

    def _do_search(self, trademark_name: str, effective_limit: int, max_scroll_seconds: int) -> List[List]:
        """
        Core search execution: navigate → fill → submit → scroll → return raw rows.
        Returns empty list if no records found. Raises on hard failures.
        """
        logging.info(f"🔎 Searching TurkPatent for: '{trademark_name}' (max {effective_limit} records)")
        self._ensure_request_allowed(operation="search", query=trademark_name)
        self._safe_goto(self.url, safety_checked=True, operation="search", query=trademark_name)
        self._close_popups()
        self._ensure_marka_arastirma_tab()

        inp = self._locate_marka_adi_input()
        if not inp:
            raise Exception("Marka Adı input field not found.")

        try:
            inp.click(timeout=4000)
            inp.fill(trademark_name)
        except Exception:
            logging.warning("   [WARN] Input action intercepted by overlay. Forcing click and fill...")
            inp.click(force=True, timeout=2000)
            inp.fill(trademark_name, force=True)

        inp.press("Enter")
        self.page.wait_for_timeout(1000)

        self._click_sorgula()

        row_sel, scroll_target_sel = self._detect_grid()
        try:
            self.page.wait_for_selector(row_sel, timeout=20000)
        except Exception:
            page_text = self._current_page_text()
            if _looks_like_blocking_content(page_text):
                self._record_block_and_stop(
                    reason="blocking_page_content",
                    operation="search",
                    query=trademark_name,
                )
            if "bulunamadı" in self.page.content().lower() or "bulunamadi" in self.page.content().lower():
                logging.info("   ℹ️ No records found.")
                return []
            raise Exception("Grid did not load within timeout.")

        self._ensure_sonsuz_liste_on()

        captured_data: Dict[str, List[str]] = {}
        time.sleep(0.5)

        total = self._get_total_records_count()
        if total:
            logging.info(f"   📊 Expected total: {total} (will scrape up to {min(effective_limit, total)})")
        else:
            logging.info(f"   📊 Expected total: UNKNOWN (max limit: {effective_limit})")

        start_t = time.time()
        last_pos = self._get_last_row_position(row_sel)
        stagnation = 0

        try:
            self._wheel_inside_scroll_target(scroll_target_sel, delta_y=1)
        except Exception:
            pass

        while True:
            if not total:
                total = self._get_total_records_count()
                if total:
                    logging.info(f"   📊 Expected total detected: {total}")

            self._scrape_current_view(row_sel, captured_data)
            current_len = len(captured_data)

            if total and current_len >= total:
                logging.info(f"   ✅ Reached target count: {current_len}/{total}")
                break
            if current_len >= effective_limit:
                logging.info(f"   ✅ Reached effective limit: {current_len}/{effective_limit}")
                break
            if current_len >= MAX_SCRAPE_LIMIT:
                logging.info(f"   ✅ Reached max scrape limit: {MAX_SCRAPE_LIMIT}")
                break
            if max_scroll_seconds > 0 and (time.time() - start_t > max_scroll_seconds):
                logging.warning(f"   ⚠️ Max execution time ({max_scroll_seconds}s) reached.")
                break

            prev = last_pos
            self._js_scroll_to_bottom(scroll_target_sel, offset=80)
            time.sleep(0.02)
            self._js_scroll_by(scroll_target_sel, dy=-260)
            time.sleep(0.02)
            self._js_scroll_to_bottom(scroll_target_sel, offset=0)
            time.sleep(0.02)
            self._wheel_inside_scroll_target(scroll_target_sel, delta_y=2800)

            new_pos = self._wait_for_position_change(row_sel, prev, timeout_s=2.5)

            if new_pos <= prev:
                stagnation += 1
                if total and current_len >= total:
                    break

                logging.info(f"   [WARN] No growth (stagnation={stagnation}). last_pos={last_pos} count={current_len}/{total if total else '?'}")

                self._js_scroll_by(scroll_target_sel, dy=-650 - (stagnation * 90))
                time.sleep(0.15)
                self._js_scroll_to_bottom(scroll_target_sel, offset=0)
                time.sleep(0.05)
                self._wheel_inside_scroll_target(scroll_target_sel, delta_y=3200)

                new_pos2 = self._wait_for_position_change(row_sel, prev, timeout_s=3.0)
                new_pos = max(new_pos, new_pos2)
                if new_pos <= prev:
                    self._jiggle_recovery(scroll_target_sel, stagnation)
                    new_pos3 = self._wait_for_position_change(row_sel, prev, timeout_s=4.0)
                    new_pos = max(new_pos, new_pos3)

                threshold = 200 if (total and current_len < total) else 25
                if stagnation > threshold:
                    logging.warning(f"   [WARN] Stagnation limit ({threshold}) reached. Aborting scroll.")
                    break
            else:
                stagnation = 0
                last_pos = new_pos
                logging.info(f"   📉 Captured {current_len} rows...")

        return list(captured_data.values())

    def search_and_ingest(self, trademark_name: str, limit: int = 0,
                          max_scroll_seconds: int = 1200,
                          progress_callback=None) -> List[List]:
        """
        Orchestrates the full search pipeline with Turkish character surgical fallback.

        1. Primary search with the original query.
        2. If 0 results AND query contains Latin chars with Turkish equivalents:
           - Wait 2.0s (IP-safe human re-type simulation)
           - Rotate user agent
           - Retry with Turkish character substitutions applied
        """
        # ===================== SKIP CHECK =====================
        self.last_safety_event = None
        self.last_save_info = None
        term_lower = trademark_name.lower().strip()
        term_normalized = term_lower.replace(' ', '')

        for skip_term in SKIP_TERMS:
            skip_lower = skip_term.lower()
            if term_lower == skip_lower or term_normalized == skip_lower.replace(' ', ''):
                logging.info(f"   ⏭️ Skipping scrape for placeholder term: '{trademark_name}'")
                return []

        if len(term_lower) <= 2:
            logging.info(f"   ⏭️ Skipping scrape: '{trademark_name}' is too short (≤2 chars)")
            return []

        if term_lower.isdigit():
            logging.info(f"   ⏭️ Skipping scrape: '{trademark_name}' is just numbers")
            return []

        effective_limit = MAX_SCRAPE_LIMIT
        if limit > 0:
            effective_limit = min(limit, MAX_SCRAPE_LIMIT)

        if not self.page:
            self.start_browser()

        # ===================== PRIMARY SEARCH =====================
        try:
            primary_rows = self._do_search(trademark_name, effective_limit, max_scroll_seconds)
        except ScraperSafetyStop as exc:
            self.last_safety_event = exc.event
            logging.warning("   [SAFETY] Search soft-stopped before APP metadata write.")
            return []

        if primary_rows:
            logging.info(f"   ✅ Primary search returned {len(primary_rows)} records.")
            self.save_to_json(primary_rows)
            return primary_rows

        # ===================== SURGICAL FALLBACK =====================
        if not _has_latin_equivalents(trademark_name):
            logging.info("   ℹ️ Primary returned 0 results and no Latin equivalents found. Done.")
            return []

        fallback_query = _apply_turkish_chars(trademark_name)
        logging.info(f"   🔄 Primary returned 0. Surgical fallback: '{trademark_name}' → '{fallback_query}'")

        # 2-second IP-safe pause to simulate human re-type
        time.sleep(2.0)

        if progress_callback:
            progress_callback(
                'character_fallback', 44,
                f"'{trademark_name}' → '{fallback_query}'"
            )

        # Rotate user agent before the second request
        self._rotate_user_agent()

        try:
            fallback_rows = self._do_search(fallback_query, effective_limit, max_scroll_seconds)
        except ScraperSafetyStop as exc:
            self.last_safety_event = exc.event
            logging.warning("   [SAFETY] Fallback search soft-stopped before APP metadata write.")
            return []
        logging.info(f"   {'✅' if fallback_rows else 'ℹ️'} Fallback returned {len(fallback_rows)} records.")

        all_rows = fallback_rows  # fallback result is canonical when primary was 0
        self.save_to_json(all_rows)
        return all_rows

if __name__ == "__main__":
    bot = TurkPatentScraper(headless=False)
    try:
        bot.search_and_ingest("Nike")
    finally:
        bot.close()
