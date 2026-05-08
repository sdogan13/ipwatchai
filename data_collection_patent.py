"""Patent / Faydalı Model bulletin collector.

Sister collector to ``data_collection.py`` (Marka) and
``data_collection_tasarim.py`` (industrial design). Targets the TÜRKPATENT
bulletin page in single-category mode: Patent / Faydalı Model only,
**dual-track** issues — every modern month ships both a CD bundle (.rar)
and a sidecar PDF.

Output layout (flat, matches the 184 pre-existing files in this folder)::

    bulletins/Patent__Faydali_Model/{card_id}_CD.rar
    bulletins/Patent__Faydali_Model/{card_id}.pdf

where ``card_id`` is the year_month string the site renders (e.g. ``2025_12``).

Completeness marker: a card is "complete" when every requested track
(CD and/or PDF, per CLI flags) is present locally and non-empty.

CLI:

    python data_collection_patent.py                     # both tracks, incremental, headless
    python data_collection_patent.py --full              # walk full archive
    python data_collection_patent.py --pdf-only          # PDF only
    python data_collection_patent.py --cd-only           # CD only
    python data_collection_patent.py --limit 1           # stop after 1 download
    python data_collection_patent.py --headless=false    # show browser
"""

import argparse
import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urljoin

import requests


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CATEGORY_NAME = "Patent / Faydalı Model"
CATEGORY_LABEL_CANDIDATES = (
    "Patent / Faydalı Model",
    "Patent ve Faydalı Model",
    "Patent / Faydali Model",
    "Patent",
)
CATEGORY_FOLDER_NAME = "Patent__Faydali_Model"

CD_TEXT_RE = re.compile(
    r"CD[_\s]?Icerigi|CD[_\s]?icerigi|CD\s*İçeriği|CD\s*İçerigi",
    re.IGNORECASE,
)

DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_INCREMENTAL_THRESHOLD = 5
DEFAULT_HEADLESS = True
DEFAULT_DOWNLOAD_TIMEOUT = 600

SLOW_MO_MS = 150
VIEWPORT = {"width": 1400, "height": 900}
DOWNLOAD_LABEL_SELECTOR = r"text=/^\s*(İNDİR|İndir|INDIR|Indir)\s*$/"
MENU_WAIT_MS = 4000
CHUNK_BYTES = 1024 * 1024
CONNECT_TIMEOUT = 30
MAX_RETRIES = 2
SCROLL_STALL_LIMIT = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [PATENT] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.patent_collector")


class Track(str, Enum):
    CD = "cd"
    PDF = "pdf"


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_data_collection_patent.py)
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    rep = {
        "ı": "i", "ğ": "g", "ü": "u", "ş": "s", "ö": "o", "ç": "c",
        "İ": "I", "Ğ": "G", "Ü": "U", "Ş": "S", "Ö": "O", "Ç": "C",
    }
    for k, v in rep.items():
        text = text.replace(k, v)
    return re.sub(r"[^\w\s-]", "", text).strip().replace(" ", "_")


def safe_filename_keep_text(name: str, max_len: int = 180) -> str:
    name = (name or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(" .")
    if not name:
        name = "file"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .") or "file"
    return name


def filename_from_content_disposition(cd: Optional[str]) -> Optional[str]:
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def ext_from_headers(content_type: str, content_disp: str) -> str:
    name = filename_from_content_disposition(content_disp) or ""
    _, ext = os.path.splitext(name)
    if ext:
        return ext.lower()
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "rar" in ct:
        return ".rar"
    if "zip" in ct:
        return ".zip"
    return ".bin"


def parse_issue_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_card_id(raw: Optional[str]) -> Optional[str]:
    """Sanitize a card id rendered by the site so it is safe for filenames.

    The site renders patent issue ids like ``2025/12`` or ``2025_12``; we
    fold both into ``2025_12``. Returns ``None`` if ``raw`` has no digits.
    """
    if not raw:
        return None
    cleaned = raw.strip().replace("/", "_")
    cleaned = re.sub(r"[^0-9_\-]", "", cleaned)
    if not re.search(r"\d", cleaned):
        return None
    return cleaned


def is_recent_issue(
    card_date: Optional[str],
    *,
    today: Optional[date] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> bool:
    """True when the card has no parseable date or falls within the lookback window."""
    issue_date = parse_issue_date(card_date)
    if issue_date is None:
        return True
    reference_day = today or date.today()
    cutoff_day = reference_day - timedelta(days=max(0, lookback_days))
    return issue_date >= cutoff_day


def build_cd_filename(card_id: str) -> str:
    """e.g. ``2025_12`` -> ``2025_12_CD.rar``."""
    cid = (card_id or "").strip()
    if not cid:
        raise ValueError("card_id required")
    return f"{cid}_CD.rar"


def build_pdf_filename(card_id: str) -> str:
    """e.g. ``2025_12`` -> ``2025_12.pdf``."""
    cid = (card_id or "").strip()
    if not cid:
        raise ValueError("card_id required")
    return f"{cid}.pdf"


def track_filename(card_id: str, track: Track) -> str:
    if track is Track.CD:
        return build_cd_filename(card_id)
    if track is Track.PDF:
        return build_pdf_filename(card_id)
    raise ValueError(f"unknown track: {track!r}")


def existing_track_file(category_folder: str | Path, card_id: str, track: Track) -> Optional[Path]:
    """Return a non-empty existing path for this track, or ``None``.

    The exact filename match comes first. We then fall back to scanning the
    folder for any file whose name starts with the card id and ends with the
    track suffix — this catches legacy filenames already on disk that may
    differ in casing or spacing.
    """
    root = Path(category_folder)
    if not root.is_dir():
        return None

    exact = root / track_filename(card_id, track)
    try:
        if exact.is_file() and exact.stat().st_size > 0:
            return exact
    except OSError:
        pass

    cid = (card_id or "").strip()
    if not cid:
        return None
    cid_lower = cid.lower()
    if track is Track.CD:
        suffix_re = re.compile(rf"^{re.escape(cid_lower)}_cd\.(?:rar|zip)$")
    else:
        suffix_re = re.compile(rf"^{re.escape(cid_lower)}\.pdf$")

    try:
        for entry in root.iterdir():
            if not entry.is_file():
                continue
            if suffix_re.match(entry.name.lower()):
                try:
                    if entry.stat().st_size > 0:
                        return entry
                except OSError:
                    continue
    except OSError:
        return None
    return None


def tracks_missing(
    category_folder: str | Path,
    card_id: str,
    wanted: Set[Track],
) -> Set[Track]:
    """Tracks in ``wanted`` that don't yet have a non-empty file on disk."""
    return {t for t in wanted if existing_track_file(category_folder, card_id, t) is None}


def card_is_complete(
    category_folder: str | Path,
    card_id: str,
    wanted: Set[Track],
) -> bool:
    return not tracks_missing(category_folder, card_id, wanted)


def classify_menu_item_text(text: str) -> Track:
    """Classify a download-menu entry as CD or PDF based on its label.

    Anything matching the ``CD İçeriği`` family is CD; everything else
    (the bare numbered "monthly bulletin" entry, "PDF", etc.) is PDF.
    """
    if CD_TEXT_RE.search(text or ""):
        return Track.CD
    return Track.PDF


@dataclass
class IncrementalScanTracker:
    """Single-track scan stopper.

    Patent has no BLT/GZ split, so the tracker stops once either:
      - we've observed ``threshold`` recent issues, or
      - we've crossed the recency cutoff (an out-of-window issue was seen).
    """

    threshold: int = DEFAULT_INCREMENTAL_THRESHOLD
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    today: date = field(default_factory=date.today)
    recent_count: int = 0
    cutoff_reached: bool = False

    def observe(self, *, card_date: Optional[str]) -> bool:
        recent = is_recent_issue(
            card_date, today=self.today, lookback_days=self.lookback_days
        )
        if recent:
            self.recent_count += 1
        else:
            self.cutoff_reached = True
        return recent

    def should_stop(self) -> bool:
        return self.cutoff_reached or self.recent_count >= self.threshold


@dataclass
class CollectionCounters:
    downloaded: int = 0
    failed: int = 0
    skipped: int = 0

    def to_summary(self, *, duration_seconds: float) -> Dict[str, Any]:
        return {
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "duration_seconds": round(duration_seconds, 1),
        }


@dataclass
class CLIArgs:
    full: bool
    limit: Optional[int]
    headless: bool
    bulletins_root: Path
    tracks: Set[Track]


def _parse_bool(value: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean, got {value!r}")


def parse_argv(argv: Optional[List[str]] = None) -> CLIArgs:
    """Parse CLI arguments for the Patent collector."""
    parser = argparse.ArgumentParser(prog="data_collection_patent", add_help=True)
    parser.add_argument("--full", action="store_true", help="walk the entire archive")
    parser.add_argument("--limit", type=int, default=None, help="stop after N downloads")
    parser.add_argument(
        "--headless",
        type=_parse_bool,
        default=DEFAULT_HEADLESS,
        help="run browser headless (default: true)",
    )
    parser.add_argument(
        "--bulletins-root",
        type=Path,
        default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"output root (default: {_LOCAL_DEFAULT_BULLETINS_DIR})",
    )
    track_group = parser.add_mutually_exclusive_group()
    track_group.add_argument(
        "--pdf-only",
        action="store_true",
        help="only download the sidecar PDF, skip CD .rar",
    )
    track_group.add_argument(
        "--cd-only",
        action="store_true",
        help="only download the CD .rar, skip the sidecar PDF",
    )
    ns = parser.parse_args(argv)

    if ns.pdf_only:
        tracks = {Track.PDF}
    elif ns.cd_only:
        tracks = {Track.CD}
    else:
        tracks = {Track.CD, Track.PDF}

    return CLIArgs(
        full=ns.full,
        limit=ns.limit,
        headless=ns.headless,
        bulletins_root=ns.bulletins_root,
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# Browser orchestration (Playwright)
# ---------------------------------------------------------------------------

async def maybe_dismiss_overlays(page) -> None:
    """Dismiss announcements / cookie banners / fraud warnings."""
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except Exception:
        pass

    candidates = [
        page.get_by_role("button", name=re.compile(r"kabul|accept|tamam|ok|anlad[ıi]m", re.I)),
        page.locator("button:has-text('Kabul')"),
        page.locator("button:has-text('Accept')"),
        page.locator("button[aria-label*='Close']"),
        page.locator("button[aria-label*='Kapat']"),
        page.locator("div[role='dialog'] button").first,
    ]
    for c in candidates:
        try:
            if await c.count() > 0:
                await c.first.click(timeout=800, force=True)
                await page.wait_for_timeout(300)
        except Exception:
            pass

    try:
        await page.mouse.click(2, 2)
        await page.wait_for_timeout(300)
    except Exception:
        pass

    try:
        await page.evaluate("""() => {
            document.querySelectorAll(
                'section[class*="jss"], div[role="dialog"], '
                + '.MuiDialog-root, .MuiBackdrop-root'
            ).forEach(el => {
                const style = window.getComputedStyle(el);
                if (style.position === 'fixed' && parseInt(style.zIndex || 0) > 50) {
                    el.style.display = 'none';
                }
            });
        }""")
    except Exception:
        pass


async def open_kategorisi_dropdown(page) -> None:
    await page.get_by_text("Kategorisi", exact=True).locator("xpath=..").click()
    await page.get_by_role("option").first.wait_for(state="visible", timeout=5000)


async def select_patent_category(page) -> str:
    """Open the category dropdown and pick the Patent / Faydalı Model option.

    The site label may render with slightly different spacing or punctuation;
    try each candidate until one clicks. Returns the label that matched.
    """
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)
    await open_kategorisi_dropdown(page)

    last_err: Optional[Exception] = None
    for label in CATEGORY_LABEL_CANDIDATES:
        try:
            option = page.get_by_role("option", name=label)
            if await option.count() == 0:
                continue
            await option.first.click()
            await page.wait_for_timeout(2000)
            await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)
            logger.info("Selected category label: %r", label)
            return label
        except Exception as e:
            last_err = e
            logger.warning("Category label %r did not click cleanly: %r", label, e)
            try:
                await open_kategorisi_dropdown(page)
            except Exception:
                pass
    raise RuntimeError(f"None of {CATEGORY_LABEL_CANDIDATES!r} matched the dropdown") from last_err


async def collect_download_clickables(page):
    labels = page.locator(DOWNLOAD_LABEL_SELECTOR)
    return labels.locator("xpath=ancestor-or-self::*[self::a or self::button or @role='button'][1]")


async def extract_card_metadata(clickable) -> Dict[str, Optional[str]]:
    """Extract { id, date } from a Patent card. Patent has no GZ/BLT split."""
    return await clickable.evaluate(
        r"""(el) => {
            const cardIdRe = /^\s*\d{1,4}(?:[_/]\d{1,2})?(?:\s*[-–—]\s*\d{1,4}(?:[_/]\d{1,2})?)?\s*$/;
            const dateRe = /(\d{2})[./](\d{2})[./](\d{4})/;

            let n = el;
            let foundId = null;
            let foundDate = null;
            for (let step = 0; step < 10; step++) {
                if (!n) break;
                const t = (n.innerText || "");
                if (!foundId) {
                    const lines = t.split("\n").map(s => s.trim()).filter(Boolean);
                    const ids = lines.filter(l => cardIdRe.test(l));
                    if (ids.length >= 1) foundId = ids[0].replace(/\s+/g, "");
                }
                if (!foundDate) {
                    const m = t.match(dateRe);
                    if (m) {
                        foundDate = `${m[3]}-${m[2]}-${m[1]}`;
                    }
                }
                if (foundId && foundDate) break;
                n = n.parentElement;
            }
            return { id: foundId, date: foundDate };
        }"""
    )


async def click_dropdown_area(page, clickable) -> None:
    box = await clickable.bounding_box()
    if not box:
        await clickable.click()
        return
    x = box["x"] + box["width"] * 0.92
    y = box["y"] + box["height"] * 0.55
    await page.mouse.click(x, y)


async def get_open_menu_container(page, timeout_ms: int):
    candidates = [
        "div[role='menu']:visible", "ul[role='menu']:visible", "[role='listbox']:visible",
        ".p-menu:visible", ".p-tieredmenu:visible", ".p-menu-overlay:visible",
        ".dropdown-menu:visible", ".menu:visible",
    ]
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        for sel in candidates:
            loc = page.locator(sel)
            try:
                c = await loc.count()
            except Exception:
                c = 0
            if c:
                return loc.nth(c - 1)
        await page.wait_for_timeout(100)
    return None


async def force_close_menus(page) -> None:
    await page.keyboard.press("Escape")
    candidates = [
        "div[role='menu']", "ul[role='menu']", "[role='listbox']", ".p-menu",
        ".p-tieredmenu", ".p-menu-overlay", ".dropdown-menu", ".menu",
    ]
    for _ in range(3):
        visible_count = 0
        for sel in candidates:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    visible_count += 1
            except Exception:
                pass
        if visible_count == 0:
            return
        await page.mouse.click(0, 0)
        await page.wait_for_timeout(300)


def _looks_like_download_href(href: Optional[str]) -> bool:
    if not href:
        return False
    normalized = href.strip().lower()
    return normalized not in {"", "#"} and not normalized.startswith("javascript:")


async def get_clickable_download_href(clickable) -> Optional[str]:
    """Resolve a usable direct-download href from the visible card action.

    The Patent / Faydalı Model UI as observed on 2026-05-08 renders each
    visible İndir as a plain anchor whose href looks like
    ``https://webim.turkpatent.gov.tr/file/{uuid}?name={YYYY_M}&download``.
    There is no dropdown menu on this UI — clicking the anchor downloads
    the PDF directly. This helper extracts that href so we can stream-
    download with cookies instead of waiting for a non-existent menu.

    Returns the href string when present and useful, otherwise ``None``
    (which signals the caller to fall through to the legacy menu path).
    """
    try:
        href = await clickable.get_attribute("href")
        if _looks_like_download_href(href):
            return href
    except Exception:
        pass

    try:
        href = await clickable.evaluate(
            """(el) => {
                const anchor = el.tagName === "A"
                    ? el
                    : (el.closest("a[href]") || el.querySelector("a[href]"));
                return anchor ? anchor.getAttribute("href") : null;
            }"""
        )
        if _looks_like_download_href(href):
            return href
    except Exception:
        pass

    return None


async def open_download_menu(page, clickable):
    await force_close_menus(page)
    await click_dropdown_area(page, clickable)
    menu = await get_open_menu_container(page, MENU_WAIT_MS)
    if menu:
        return menu
    try:
        await clickable.click()
    except Exception:
        pass
    return await get_open_menu_container(page, 1200)


async def list_menu_items(page, timeout_ms: int) -> List[Dict[str, Any]]:
    menu = await get_open_menu_container(page, timeout_ms)
    if not menu:
        return []

    items_loc = menu.locator("a, button, [role='menuitem'], li")
    out: List[Dict[str, Any]] = []
    n = await items_loc.count()
    for i in range(n):
        it = items_loc.nth(i)
        try:
            if not await it.is_visible():
                continue
            text = (await it.inner_text()).strip()
        except Exception:
            continue
        if not text:
            continue

        href = None
        try:
            raw_href = await it.get_attribute("href")
            if raw_href:
                raw_href = raw_href.strip()
                if len(raw_href) > 1 and not raw_href.lower().startswith("javascript") and raw_href != "#":
                    href = raw_href
        except Exception:
            href = None

        out.append({
            "text": text,
            "href": href,
            "track": classify_menu_item_text(text),
        })

    # Dedup by visible text — repeated rendering is common in PrimeNG menus.
    dedup: Dict[str, Dict[str, Any]] = {}
    for d in out:
        dedup.setdefault(d["text"], d)
    return list(dedup.values())


def _stream_download_requests(url: str, out_path: str, cookies: dict, headers: dict) -> bool:
    tmp_path = out_path + ".part"
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    with requests.Session() as s:
        s.headers.update(headers)
        s.cookies.update(cookies)

        with s.get(url, stream=True, allow_redirects=True, timeout=(CONNECT_TIMEOUT, DEFAULT_DOWNLOAD_TIMEOUT)) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            ct = r.headers.get("content-type", "").lower()
            if "text/html" in ct:
                logger.warning("URL returned text/html, rejecting")
                return False

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True

            total = 0
            last_log = time.time()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK_BYTES):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
                    now = time.time()
                    if now - last_log >= 60:
                        mb = total / (1024 * 1024)
                        logger.info("    ... downloaded %.1f MB so far", mb)
                        last_log = now

            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception:
                    pass
            os.rename(tmp_path, out_path)
            return os.path.getsize(out_path) > 0


async def stream_download_with_browser_session(context, page, url: str, out_path: str) -> bool:
    ua = await page.evaluate("navigator.userAgent")
    headers = {"User-Agent": ua, "Referer": TARGET_URL}
    ck_list = await context.cookies(url)
    cookies = {c["name"]: c["value"] for c in ck_list}
    return await asyncio.to_thread(_stream_download_requests, url, out_path, cookies, headers)


async def playwright_download_click(page, item_locator, out_path: str) -> bool:
    try:
        async with page.expect_download(timeout=10 * 60 * 1000) as dl_info:
            await item_locator.click()
        dl = await dl_info.value

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
        await dl.save_as(out_path)
        return True
    except Exception as e:
        logger.warning("Playwright download failed: %r", e)
        return False


async def download_one_track(
    page,
    context,
    clickable,
    menu_item: Dict[str, Any],
    out_path: str,
) -> bool:
    """Download a single menu item to ``out_path``.

    Tries direct streaming via the item's href first (faster, gives us
    cookies + content-type), then falls back to a Playwright click that
    catches the browser's download event.
    """
    href = menu_item.get("href")
    if href:
        abs_url = urljoin(page.url, href)
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                if await stream_download_with_browser_session(context, page, abs_url, out_path):
                    return True
            except Exception as e:
                logger.warning("requests attempt %d failed for %r: %r", attempt, menu_item.get("text"), e)
                await page.wait_for_timeout(1000)

    # Re-open the menu (the prior failed attempt may have closed it) and
    # locate the item by its visible text so we can let Playwright catch the
    # download event.
    menu = await get_open_menu_container(page, 500)
    if not menu:
        menu = await open_download_menu(page, clickable)
        if not menu:
            return False

    text_pattern = re.compile(re.escape(menu_item["text"]))
    item_locator = menu.locator("a, button, [role='menuitem'], li").filter(has_text=text_pattern).first
    return await playwright_download_click(page, item_locator, out_path)


async def process_card(
    page,
    context,
    clickable,
    bulletins_root: Path,
    card_id: str,
    card_date: Optional[str],
    wanted: Set[Track],
) -> CollectionCounters:
    missing = tracks_missing(bulletins_root, card_id, wanted)
    if not missing:
        logger.info("[=] %s already complete (%s), skipping",
                    card_id, sorted(t.value for t in wanted))
        return CollectionCounters(skipped=1)

    by_track: Dict[Track, Dict[str, Any]] = {}
    is_direct_href_card = False

    # Patent UI fast path: each visible İndir is a direct-href <a> to the
    # PDF. There is no dropdown menu to open. When the clickable carries a
    # download href, treat the card as a single-PDF anchor and skip the
    # menu logic entirely.
    direct_href = await get_clickable_download_href(clickable)
    if direct_href:
        is_direct_href_card = True
        by_track[Track.PDF] = {
            "text": f"direct:{card_id}",
            "href": direct_href,
            "track": Track.PDF,
        }
    else:
        # Fallback: legacy menu path (kept in case TÜRKPATENT introduces a
        # dropdown UI for some bulletins). Marka and Tasarım both rely on
        # this path.
        menu = await open_download_menu(page, clickable)
        if not menu:
            logger.warning("[!] %s: no direct href and download menu did not open", card_id)
            return CollectionCounters(failed=1)

        items = await list_menu_items(page, MENU_WAIT_MS)
        for it in items:
            by_track.setdefault(it["track"], it)

    counters = CollectionCounters()
    bulletins_root.mkdir(parents=True, exist_ok=True)

    for track in (Track.CD, Track.PDF):
        if track not in missing:
            continue
        item = by_track.get(track)
        if not item:
            if track is Track.CD and is_direct_href_card:
                # The current Patent UI exposes only PDFs via direct-href
                # anchors. CD .rar bundles are not reachable from here as of
                # 2026-05-08; existing _CD.rar files in the folder predate
                # this UI shape. Don't count this as a failure — note it
                # and move on so the run summary stays meaningful.
                logger.info("[i] %s: CD track not exposed by this UI, skipping CD",
                            card_id)
                counters.skipped += 1
            else:
                logger.warning("[!] %s: no menu item for track %s", card_id, track.value)
                counters.failed += 1
            continue

        out_path = str(bulletins_root / track_filename(card_id, track))
        logger.info("[*] %s: downloading %s -> %s",
                    card_id, track.value, os.path.basename(out_path))
        ok = await download_one_track(page, context, clickable, item, out_path)
        if ok:
            counters.downloaded += 1
            logger.info("[+] %s: %s saved", card_id, os.path.basename(out_path))
        else:
            counters.failed += 1
            logger.warning("[!] %s: %s failed", card_id, track.value)

    if not is_direct_href_card:
        await force_close_menus(page)
    return counters


async def run_collection(args: CLIArgs) -> CollectionCounters:
    """Drive the Patent collection loop."""
    from playwright.async_api import async_playwright

    args.bulletins_root.mkdir(parents=True, exist_ok=True)
    counters = CollectionCounters()
    seen: Set[str] = set()
    tracker = None if args.full else IncrementalScanTracker(
        threshold=DEFAULT_INCREMENTAL_THRESHOLD,
        lookback_days=DEFAULT_LOOKBACK_DAYS,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless, slow_mo=SLOW_MO_MS)
        context = await browser.new_context(viewport=VIEWPORT, accept_downloads=True)
        page = await context.new_page()
        try:
            logger.info("Opening %s", TARGET_URL)
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await maybe_dismiss_overlays(page)
            await select_patent_category(page)

            last_height = 0
            stall_rounds = 0

            while True:
                clickables = await collect_download_clickables(page)
                count = await clickables.count()
                logger.info("Visible cards: %d", count)
                pass_downloads = 0

                for i in range(count):
                    if args.limit is not None and counters.downloaded >= args.limit:
                        logger.info("Reached --limit=%d, stopping", args.limit)
                        return counters

                    clickable = clickables.nth(i)
                    try:
                        if not await clickable.is_visible():
                            continue
                    except Exception:
                        continue

                    meta = await extract_card_metadata(clickable)
                    raw_id = (meta.get("id") or "").strip() or None
                    card_id = normalize_card_id(raw_id)
                    card_date = (meta.get("date") or "").strip() or None
                    if not card_id or card_id in seen:
                        continue

                    if tracker is not None:
                        if not tracker.observe(card_date=card_date):
                            seen.add(card_id)
                            logger.info(
                                "[-] %s [%s] older than recent window, skipping",
                                card_id, card_date or "?",
                            )
                            continue
                        if card_is_complete(args.bulletins_root, card_id, args.tracks):
                            seen.add(card_id)
                            counters.skipped += 1
                            logger.info("[=] %s [%s] already complete locally, skipping",
                                        card_id, card_date or "?")
                            continue

                    issue_counters = await process_card(
                        page, context, clickable, args.bulletins_root,
                        card_id, card_date, args.tracks,
                    )
                    counters.downloaded += issue_counters.downloaded
                    counters.failed += issue_counters.failed
                    counters.skipped += issue_counters.skipped
                    pass_downloads += issue_counters.downloaded
                    seen.add(card_id)
                    await force_close_menus(page)
                    await page.wait_for_timeout(200)

                if args.limit is not None and counters.downloaded >= args.limit:
                    logger.info("Reached --limit=%d, stopping", args.limit)
                    break

                if tracker is not None and tracker.should_stop():
                    logger.info(
                        "Incremental stop: recent=%d cutoff=%s downloaded=%d",
                        tracker.recent_count, tracker.cutoff_reached, counters.downloaded,
                    )
                    break

                height = await page.evaluate("document.body.scrollHeight")
                if height == last_height and pass_downloads == 0:
                    stall_rounds += 1
                else:
                    stall_rounds = 0
                    last_height = height

                if stall_rounds >= SCROLL_STALL_LIMIT:
                    logger.info("Reached end of category (scroll stalled %d rounds)", stall_rounds)
                    break

                await page.mouse.wheel(0, 5000)
                await page.wait_for_timeout(2000)

        finally:
            await context.close()
            await browser.close()

    return counters


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_argv(argv)
    args.bulletins_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    counters = asyncio.run(run_collection(args))
    summary = counters.to_summary(duration_seconds=time.time() - started)
    logger.info("Collection complete: %s", summary)
    return 0 if counters.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
