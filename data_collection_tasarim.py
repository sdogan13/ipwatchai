"""Tasarım (industrial design) bulletin collector.

Sister collector to ``data_collection.py``. Targets the TÜRKPATENT bulletin
page in single-category mode: Tasarım only, PDF only, single-track issues
(no GZ/BLT split, no HSQLDB CD bundle).

Output layout::

    bulletins/Tasarim/TS_{issue_no}_{YYYY-MM-DD}/bulletin.pdf

Completeness marker: ``bulletin.pdf`` exists and is non-empty.

CLI:

    python data_collection_tasarim.py                     # incremental, headless
    python data_collection_tasarim.py --full              # walk full archive
    python data_collection_tasarim.py --limit 1           # stop after 1 download
    python data_collection_tasarim.py --headless=false    # show browser
"""

import argparse
import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urljoin

import requests


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Tasarim"

TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CATEGORY_NAME = "Tasarım"
CATEGORY_LABEL_CANDIDATES = ("Tasarım", "Endüstriyel Tasarım", "Tasarim")
CATEGORY_FOLDER_NAME = "Tasarim"
BULLETIN_PDF_NAME = "bulletin.pdf"
ISSUE_FOLDER_PREFIX = "TS"

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_collector")


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_data_collection_tasarim.py)
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
    if "zip" in ct:
        return ".zip"
    if "rar" in ct:
        return ".rar"
    return ".bin"


def parse_issue_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_primary_issue_number(card_id: Optional[str]) -> Optional[int]:
    if not card_id:
        return None
    match = re.match(r"(\d+)", card_id.strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


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


def build_issue_folder_name(card_id: str, card_date: Optional[str]) -> str:
    """Return canonical folder name for an issue: ``TS_{id}_{date}`` or ``TS_{id}``."""
    card_id = (card_id or "").strip()
    if not card_id:
        raise ValueError("card_id required")
    if card_date:
        return f"{ISSUE_FOLDER_PREFIX}_{card_id}_{card_date}"
    return f"{ISSUE_FOLDER_PREFIX}_{card_id}"


def issue_folder_is_complete(issue_folder: Path) -> bool:
    """Issue is complete iff ``bulletin.pdf`` exists and is non-empty."""
    if not issue_folder.is_dir():
        return False
    pdf_path = issue_folder / BULLETIN_PDF_NAME
    try:
        return pdf_path.is_file() and pdf_path.stat().st_size > 0
    except OSError:
        return False


def check_local_existence(
    category_folder: str | Path,
    card_id: str,
    *,
    card_date: Optional[str] = None,
) -> bool:
    """True when the issue folder under ``category_folder`` already has its ``bulletin.pdf``."""
    root = Path(category_folder)
    if not root.is_dir():
        return False
    candidates = [build_issue_folder_name(card_id, card_date)]
    if card_date:
        candidates.append(build_issue_folder_name(card_id, None))
    for name in candidates:
        if issue_folder_is_complete(root / name):
            return True
    return False


@dataclass
class IncrementalScanTracker:
    """Single-track scan stopper.

    Tasarım has no BLT/GZ split, so the tracker stops once either:
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
    issue: Optional[str] = None


def parse_argv(argv: Optional[List[str]] = None) -> CLIArgs:
    """Parse CLI arguments for the Tasarım collector."""
    parser = argparse.ArgumentParser(prog="data_collection_tasarim", add_help=True)
    parser.add_argument("--full", action="store_true", help="walk the entire archive")
    parser.add_argument("--limit", type=int, default=None, help="stop after N downloads")
    parser.add_argument(
        "--issue",
        type=str,
        default=None,
        help="restrict to a single bulletin issue number (e.g. --issue 240). "
             "Implies --full so the incremental tracker doesn't stop early "
             "before reaching the targeted issue.",
    )
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
    ns = parser.parse_args(argv)
    # --issue implies --full: the incremental tracker would otherwise stop
    # walking the archive before reaching old bulletins.
    full = ns.full or ns.issue is not None
    return CLIArgs(
        full=full,
        limit=ns.limit,
        headless=ns.headless,
        bulletins_root=ns.bulletins_root,
        issue=ns.issue.strip() if ns.issue else None,
    )


def _parse_bool(value: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean, got {value!r}")


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


async def select_tasarim_category(page) -> str:
    """Open the category dropdown and pick the Tasarım option.

    TÜRKPATENT may render the option as ``Tasarım``, ``Endüstriyel Tasarım``,
    or ``Tasarim``. Try each label until one clicks. Returns the label that
    matched so callers can log it.
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
    """Extract { id, date } from a Tasarım card. No GZ/BLT classification."""
    return await clickable.evaluate(
        r"""(el) => {
            const cardIdRe = /^\s*\d{1,4}(?:_\d{1,2})?(?:\s*[-–—]\s*\d{1,4}(?:_\d{1,2})?)?\s*$/;
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
    """Resolve a usable direct-download href from the visible card action."""
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


def _stream_download_requests(url: str, out_base_path: str, cookies: dict, headers: dict) -> bool:
    tmp_path = out_base_path + ".part"
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

            cd = r.headers.get("content-disposition", "")
            ext = ext_from_headers(ct, cd)
            root, _ = os.path.splitext(out_base_path)
            final_path = root + ext
            os.makedirs(os.path.dirname(final_path), exist_ok=True)

            if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
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

            if os.path.exists(final_path):
                try:
                    os.remove(final_path)
                except Exception:
                    pass
            os.rename(tmp_path, final_path)
            return os.path.getsize(final_path) > 0


async def stream_download_with_browser_session(context, page, url: str, out_base_path: str) -> bool:
    ua = await page.evaluate("navigator.userAgent")
    headers = {"User-Agent": ua, "Referer": TARGET_URL}
    ck_list = await context.cookies(url)
    cookies = {c["name"]: c["value"] for c in ck_list}
    return await asyncio.to_thread(_stream_download_requests, url, out_base_path, cookies, headers)


async def playwright_download_click(page, item_locator, out_base_path: str) -> bool:
    try:
        async with page.expect_download(timeout=10 * 60 * 1000) as dl_info:
            await item_locator.click()
        dl = await dl_info.value

        sugg = safe_filename_keep_text(dl.suggested_filename or "download.bin")
        _, ext = os.path.splitext(sugg)
        ext = ext.lower() if ext else ".bin"

        root, _ = os.path.splitext(out_base_path)
        final_path = root + ext
        os.makedirs(os.path.dirname(final_path), exist_ok=True)

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            return True

        await dl.save_as(final_path)
        return True
    except Exception as e:
        logger.warning("Playwright download failed: %r", e)
        return False


async def direct_click_download(page, clickable, out_base_path: str) -> bool:
    try:
        async with page.expect_download(timeout=10 * 60 * 1000) as dl_info:
            await clickable.click()
        dl = await dl_info.value
        sugg = safe_filename_keep_text(dl.suggested_filename or "download.bin")
        _, ext = os.path.splitext(sugg)
        ext = ext.lower() if ext else ".bin"
        root, _ = os.path.splitext(out_base_path)
        final_path = root + ext
        os.makedirs(os.path.dirname(final_path), exist_ok=True)

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            return True
        await dl.save_as(final_path)
        return True
    except Exception as e:
        logger.warning("Direct click download failed: %r", e)
        return False


async def download_card_pdf(page, context, clickable, issue_folder: Path) -> bool:
    """Download a Tasarım bulletin PDF for a single card into ``issue_folder``.

    Strategy (ordered):
      1. If the card has a direct download href, stream it with cookies.
      2. Otherwise click the card and let Playwright catch the download event.
      3. If a menu opens instead of a direct download, click its first item.
    """
    issue_folder.mkdir(parents=True, exist_ok=True)
    out_base = str(issue_folder / "bulletin.bin")

    href = await get_clickable_download_href(clickable)
    if href:
        abs_url = urljoin(page.url, href)
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                if await stream_download_with_browser_session(context, page, abs_url, out_base):
                    return _normalize_to_bulletin_pdf(issue_folder)
            except Exception as e:
                logger.warning("requests attempt %d failed: %r", attempt, e)
                await page.wait_for_timeout(1000)

    if await direct_click_download(page, clickable, out_base):
        return _normalize_to_bulletin_pdf(issue_folder)

    menu = await get_open_menu_container(page, MENU_WAIT_MS)
    if menu:
        item = menu.locator("a, button, [role='menuitem'], li").first
        if await playwright_download_click(page, item, out_base):
            return _normalize_to_bulletin_pdf(issue_folder)

    return False


def _normalize_to_bulletin_pdf(issue_folder: Path) -> bool:
    """After download helpers run, rename the produced file to ``bulletin.pdf``.

    The Marka helpers save with the detected extension (``bulletin.pdf``,
    ``bulletin.zip``, …). For Tasarım we accept only PDFs; anything else is
    treated as a failure and removed.
    """
    target = issue_folder / BULLETIN_PDF_NAME
    if target.is_file() and target.stat().st_size > 0:
        return True

    pdf_candidates = sorted(p for p in issue_folder.glob("bulletin.*") if p.suffix.lower() == ".pdf" and p.is_file())
    if pdf_candidates:
        chosen = pdf_candidates[0]
        if chosen != target:
            chosen.replace(target)
        return target.stat().st_size > 0

    for stray in issue_folder.glob("bulletin.*"):
        try:
            stray.unlink()
        except Exception:
            pass
    return False


async def process_card(
    page,
    context,
    clickable,
    bulletins_root: Path,
    card_id: str,
    card_date: Optional[str],
) -> CollectionCounters:
    target_folder = bulletins_root / build_issue_folder_name(card_id, card_date)

    if check_local_existence(bulletins_root, card_id, card_date=card_date):
        logger.info("[=] %s already complete locally, skipping", target_folder.name)
        return CollectionCounters(skipped=1)

    logger.info("[*] Downloading %s ...", target_folder.name)
    ok = await download_card_pdf(page, context, clickable, target_folder)
    if ok:
        logger.info("[+] Saved %s/bulletin.pdf", target_folder.name)
        return CollectionCounters(downloaded=1)

    logger.warning("[!] Failed to download %s", target_folder.name)
    return CollectionCounters(failed=1)


async def run_collection(args: CLIArgs) -> CollectionCounters:
    """Drive the Tasarım collection loop."""
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
            await select_tasarim_category(page)

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
                    card_id = (meta.get("id") or "").strip() or None
                    card_date = (meta.get("date") or "").strip() or None
                    if not card_id or card_id in seen:
                        continue

                    # --issue NNN: silently skip every other card on the page
                    # so we focus the walk on just the targeted bulletin.
                    if args.issue is not None and card_id != args.issue:
                        continue

                    if tracker is not None:
                        if not tracker.observe(card_date=card_date):
                            seen.add(card_id)
                            logger.info(
                                "[-] %s [%s] older than recent window, skipping",
                                card_id, card_date or "?",
                            )
                            continue
                        if check_local_existence(args.bulletins_root, card_id, card_date=card_date):
                            seen.add(card_id)
                            counters.skipped += 1
                            logger.info("[=] %s [%s] already complete locally, skipping",
                                        card_id, card_date or "?")
                            continue

                    # --issue NNN: skip the download if this bulletin is
                    # already complete on disk (idempotent re-run friendly),
                    # then stop after we've handled the targeted card.
                    if args.issue is not None and check_local_existence(
                        args.bulletins_root, card_id, card_date=card_date,
                    ):
                        seen.add(card_id)
                        counters.skipped += 1
                        logger.info(
                            "[=] %s [%s] already complete locally, --issue stop",
                            card_id, card_date or "?",
                        )
                        return counters

                    issue_counters = await process_card(
                        page, context, clickable, args.bulletins_root, card_id, card_date,
                    )
                    counters.downloaded += issue_counters.downloaded
                    counters.failed += issue_counters.failed
                    counters.skipped += issue_counters.skipped
                    pass_downloads += issue_counters.downloaded
                    seen.add(card_id)

                    # --issue NNN early-stop: we've now processed the one
                    # bulletin we were targeting; no point walking further.
                    if args.issue is not None:
                        logger.info("[*] --issue %s handled, stopping walk", args.issue)
                        return counters

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
