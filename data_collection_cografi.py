"""Coğrafi İşaret ve Geleneksel Ürün Adı (geographical indication / traditional
product name) bulletin collector.

Sister collector to ``data_collection.py`` (Marka),
``data_collection_patent.py`` (Patent / Faydalı Model), and
``data_collection_tasarim.py`` (Tasarım). Targets the TÜRKPATENT bulletin
page in single-category mode: Coğrafi İşaret only, **PDF only**.

Every card observed on the live UI as of 2026-05-10 surfaces a single
direct-href ``<a>`` (``webim.turkpatent.gov.tr/file/{uuid}?name={ID}&download``)
pointing at the issue PDF. There is no CD .rar bundle and no İndir
dropdown menu, so the collector takes the direct-href fast path
exclusively. Cards that do not surface a direct href are reported as
failures rather than silently skipped, so a future UI change is loud.

Output layout (flat)::

    bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/{card_id}.pdf

where ``card_id`` is the issue number the site renders (e.g. ``220``).

Completeness marker: a card is "complete" when ``{card_id}.pdf`` exists
locally and is non-empty.

CLI:

    python data_collection_cografi.py                     # incremental, headless
    python data_collection_cografi.py --full              # walk full archive
    python data_collection_cografi.py --limit 1           # stop after 1 download
    python data_collection_cografi.py --headless=false    # show browser
    python data_collection_cografi.py --force             # ignore on-disk freshness
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
_LOCAL_DEFAULT_BULLETINS_DIR = (
    _LOCAL_PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)

TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CATEGORY_NAME = "Coğrafi İşaret ve Geleneksel Ürün Adı"
CATEGORY_LABEL_CANDIDATES = (
    "Coğrafi İşaret ve Geleneksel Ürün Adı",
    "Cografi Isaret ve Geleneksel Urun Adi",
    "Coğrafi İşaret",
    "Cografi Isaret",
)
CATEGORY_FOLDER_NAME = "Cografi_Isaret_ve_Geleneksel_Urun_Adi"

DEFAULT_LOOKBACK_DAYS = 60
DEFAULT_INCREMENTAL_THRESHOLD = 5
DEFAULT_HEADLESS = True
DEFAULT_DOWNLOAD_TIMEOUT = 600

SLOW_MO_MS = 150
VIEWPORT = {"width": 1400, "height": 900}
DOWNLOAD_LABEL_SELECTOR = r"text=/^\s*(İNDİR|İndir|INDIR|Indir)\s*$/"
CHUNK_BYTES = 1024 * 1024
CONNECT_TIMEOUT = 30
MAX_RETRIES = 2
SCROLL_STALL_LIMIT = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [CI] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.cografi_collector")


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_data_collection_cografi.py)
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

    Coğrafi İşaret cards render plain sequential issue numbers
    (``220``, ``219``, ``218``...). Strip everything that isn't a digit,
    underscore, or dash. Returns ``None`` if ``raw`` has no digits.
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


BULLETIN_FILENAME = "bulletin.pdf"
BUNDLE_SUFFIX = "_bundle.rar"
SUBFOLDER_PREFIX = "CI_"
SUBFOLDER_RE = re.compile(r"^CI_(\d+(?:-\d+)?)_(\d{4}-\d{2}-\d{2})$")


def bulletin_subfolder_name(card_id: str, card_date: str) -> str:
    """Return the subfolder name for a single-bulletin issue.

    Mirrors the tasarım layout (``TS_{N}_{date}``): each issue lives in
    its own ``CI_{card_id}_{ISO-date}`` directory containing
    ``bulletin.pdf`` plus (later) ``metadata.json``.
    """
    cid = (card_id or "").strip()
    if not cid:
        raise ValueError("card_id required")
    date = (card_date or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise ValueError(f"card_date must be ISO YYYY-MM-DD, got {date!r}")
    return f"{SUBFOLDER_PREFIX}{cid}_{date}"


def bulletin_path(category_folder: str | Path, card_id: str, card_date: str) -> Path:
    """Full path to a bulletin's PDF inside its subfolder."""
    return Path(category_folder) / bulletin_subfolder_name(card_id, card_date) / BULLETIN_FILENAME


def existing_bulletin(category_folder: str | Path, card_id: str) -> Optional[Path]:
    """Return a non-empty existing ``CI_{card_id}_*/bulletin.pdf``, or ``None``.

    Date-tolerant: matches any subfolder for this card_id regardless of
    its date suffix, so a card that's already on disk under one date is
    not re-downloaded under another.
    """
    root = Path(category_folder)
    if not root.is_dir():
        return None
    cid = (card_id or "").strip()
    if not cid:
        return None
    pattern = re.compile(rf"^{re.escape(SUBFOLDER_PREFIX)}{re.escape(cid)}_\d{{4}}-\d{{2}}-\d{{2}}$")
    try:
        for entry in root.iterdir():
            if not entry.is_dir() or not pattern.match(entry.name):
                continue
            candidate = entry / BULLETIN_FILENAME
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
            except OSError:
                continue
    except OSError:
        return None
    return None


def card_is_complete(category_folder: str | Path, card_id: str) -> bool:
    return existing_bulletin(category_folder, card_id) is not None


def is_rar_archive(path: str | Path) -> bool:
    """True if the first 4 bytes of ``path`` are the RAR magic ``Rar!``.

    Some legacy multi-bulletin downloads land with a ``.pdf`` filename
    (the site advertises them as PDF in Content-Disposition) while the
    body is a RAR v5 archive bundling 50 individual bulletin PDFs. We
    detect this post-download via magic bytes rather than extension.
    """
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"Rar!"
    except OSError:
        return False


def _looks_like_download_href(href: Optional[str]) -> bool:
    if not href:
        return False
    normalized = href.strip().lower()
    return normalized not in {"", "#"} and not normalized.startswith("javascript:")


@dataclass
class IncrementalScanTracker:
    """Single-track scan stopper.

    Stops once either:
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
    force: bool = False


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
    """Parse CLI arguments for the Coğrafi İşaret collector."""
    parser = argparse.ArgumentParser(prog="data_collection_cografi", add_help=True)
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="ignore on-disk completeness and re-download every card",
    )
    ns = parser.parse_args(argv)

    return CLIArgs(
        full=ns.full,
        limit=ns.limit,
        headless=ns.headless,
        bulletins_root=ns.bulletins_root,
        force=ns.force,
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


async def select_cografi_category(page) -> str:
    """Open the category dropdown and pick the Coğrafi İşaret option.

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
    """Extract { id, date } from a Coğrafi İşaret card.

    Cards render plain issue numbers (``220``) and dates that may use a
    single-digit day (``4.05.2026``) — the `\\d{1,2}` allowance is the
    cografi-specific divergence from the patent collector's regex, which
    silently mis-pairs a sibling card's date when the day is one digit.
    """
    return await clickable.evaluate(
        r"""(el) => {
            const cardIdRe = /^\s*\d{1,4}(?:[_/]\d{1,2})?(?:\s*[-–—]\s*\d{1,4}(?:[_/]\d{1,2})?)?\s*$/;
            const dateRe = /(\d{1,2})[./](\d{1,2})[./](\d{4})/;
            const pad = (n) => n.length === 1 ? "0" + n : n;

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
                        foundDate = `${m[3]}-${pad(m[2])}-${pad(m[1])}`;
                    }
                }
                if (foundId && foundDate) break;
                n = n.parentElement;
            }
            return { id: foundId, date: foundDate };
        }"""
    )


async def get_clickable_download_href(clickable) -> Optional[str]:
    """Resolve a usable direct-download href from the visible card action.

    The cografi UI as observed on 2026-05-10 renders each visible İndir as
    a plain anchor whose href looks like
    ``https://webim.turkpatent.gov.tr/file/{uuid}?name={ID}&download``.
    There is no dropdown menu on this UI — clicking the anchor downloads
    the PDF directly. Returns the href string when present, otherwise
    ``None`` (which signals the caller that this card has no usable
    download target — counted as a failure, not silently skipped, so a
    future UI change is loud).
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


def _bundle_path(bulletins_root: Path, card_id: str) -> Path:
    """Top-level path used when a downloaded file turns out to be a RAR
    archive (legacy multi-bulletin bundle). Stays flat at the bulletins
    root since one archive contains many bulletins."""
    return bulletins_root / f"{card_id}{BUNDLE_SUFFIX}"


async def process_card(
    page,
    context,
    clickable,
    bulletins_root: Path,
    card_id: str,
    card_date: Optional[str],
    *,
    force: bool = False,
) -> CollectionCounters:
    if not force and card_is_complete(bulletins_root, card_id):
        logger.info("[=] %s already complete, skipping", card_id)
        return CollectionCounters(skipped=1)

    # Skip cards we already saved as RAR bundles. Migration handles those.
    if _bundle_path(bulletins_root, card_id).is_file():
        logger.info("[=] %s already saved as bundle archive, skipping", card_id)
        return CollectionCounters(skipped=1)

    direct_href = await get_clickable_download_href(clickable)
    if not direct_href:
        logger.warning("[!] %s: no direct-href anchor on card; cografi UI shape may have changed",
                       card_id)
        return CollectionCounters(failed=1)

    if not card_date:
        # The collector relies on the site card date for the subfolder
        # name. If the site stops emitting one, treat as failure rather
        # than guessing — date is part of the natural key for the layout.
        logger.warning("[!] %s: no card_date from site; cannot place into subfolder layout",
                       card_id)
        return CollectionCounters(failed=1)

    bulletins_root.mkdir(parents=True, exist_ok=True)
    # Stream into a temp path so we can sniff the magic bytes before
    # committing to a layout (PDF -> subfolder, RAR -> bundle).
    tmp_path = str(bulletins_root / f".{card_id}.download.part")
    abs_url = urljoin(page.url, direct_href)
    logger.info("[*] %s: downloading", card_id)
    ok = False
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            if await stream_download_with_browser_session(context, page, abs_url, tmp_path):
                ok = True
                break
        except Exception as e:
            logger.warning("requests attempt %d failed for %s: %r", attempt, card_id, e)
            await page.wait_for_timeout(1000)

    if not ok:
        logger.warning("[!] %s: download failed after %d attempts", card_id, MAX_RETRIES + 1)
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return CollectionCounters(failed=1)

    # Magic-byte check: PDFs go into CI_{N}_{date}/bulletin.pdf, RAR
    # archives stay flat as {card_id}_bundle.rar (each one bundles many
    # bulletins; migration extracts and wraps them).
    if is_rar_archive(tmp_path):
        bundle = _bundle_path(bulletins_root, card_id)
        os.replace(tmp_path, bundle)
        logger.info("[+] %s: %s saved (RAR bundle, run migration to expand)",
                    card_id, bundle.name)
        return CollectionCounters(downloaded=1)

    target = bulletin_path(bulletins_root, card_id, card_date)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, target)
    logger.info("[+] %s: %s saved", card_id, target.relative_to(bulletins_root))
    return CollectionCounters(downloaded=1)


async def run_collection(args: CLIArgs) -> CollectionCounters:
    """Drive the Coğrafi İşaret collection loop."""
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
            await select_cografi_category(page)

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
                        if not args.force and card_is_complete(args.bulletins_root, card_id):
                            seen.add(card_id)
                            counters.skipped += 1
                            logger.info("[=] %s [%s] already complete locally, skipping",
                                        card_id, card_date or "?")
                            continue

                    issue_counters = await process_card(
                        page, context, clickable, args.bulletins_root,
                        card_id, card_date,
                        force=args.force,
                    )
                    counters.downloaded += issue_counters.downloaded
                    counters.failed += issue_counters.failed
                    counters.skipped += issue_counters.skipped
                    pass_downloads += issue_counters.downloaded
                    seen.add(card_id)
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


# ---------------------------------------------------------------------------
# Migration helper — one-shot conversion from the legacy flat layout
# (`{N}.pdf` plus mis-named `{N1}-{N2}.pdf` RAR bundles) into the new
# subfolder layout (`CI_{N}_{date}/bulletin.pdf`).
# ---------------------------------------------------------------------------

@dataclass
class MigrationReport:
    pdfs_wrapped: int = 0
    bundles_extracted: int = 0
    bulletins_from_bundles: int = 0
    skipped_already_present: int = 0
    failed: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "pdfs_wrapped": self.pdfs_wrapped,
            "bundles_extracted": self.bundles_extracted,
            "bulletins_from_bundles": self.bulletins_from_bundles,
            "skipped_already_present": self.skipped_already_present,
            "failed": self.failed,
        }


def _parse_pdf_cover_for_date(pdf_path: Path) -> Optional[str]:
    """Open ``pdf_path`` with PyMuPDF and parse the cover page for the
    bulletin issue date in ISO ``YYYY-MM-DD``. Returns ``None`` if the
    cover cannot be parsed (e.g. file is not a real PDF, or the cover
    is missing the ``Yayım/Yayın Tarihi`` marker).
    """
    try:
        import fitz  # local import — only needed during migration
    except ImportError:
        logger.error("PyMuPDF not available; cannot parse cover dates")
        return None
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        logger.warning("cannot open %s as PDF: %r", pdf_path, exc)
        return None
    try:
        text = doc.load_page(0).get_text()
    finally:
        doc.close()
    m = re.search(r"Yay[ıi][mn]\s+Tarihi\s*[:\s]+\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if not m:
        return None
    day, month, year = m.group(1), m.group(2), m.group(3)
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _extract_bundle_with_7z(rar_path: Path, target_dir: Path) -> bool:
    """Extract a RAR archive into ``target_dir`` using the 7-Zip binary."""
    seven_zip = os.environ.get("PIPELINE_SEVEN_ZIP_PATH") or r"C:\Program Files\7-Zip\7z.exe"
    if not Path(seven_zip).is_file():
        logger.error("7-Zip not found at %s; set PIPELINE_SEVEN_ZIP_PATH", seven_zip)
        return False
    target_dir.mkdir(parents=True, exist_ok=True)
    import subprocess
    cmd = [seven_zip, "x", "-y", str(rar_path), f"-o{target_dir}"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("7z extract failed for %s: %s", rar_path.name, proc.stderr.strip())
        return False
    return True


def _bundle_internal_card_id(name: str) -> Optional[str]:
    """Recover the internal bulletin number from a name like
    ``50 Say_l_ Resmi Co_rafi __aret ve Geleneksel Ürün Ad_ Bülteni.pdf``.
    Returns the leading integer as a string, or ``None``.
    """
    m = re.match(r"^(\d{1,4})\s+", name)
    return m.group(1) if m else None


def _wrap_pdf_into_subfolder(
    pdf_path: Path, bulletins_root: Path, card_id: str, report: MigrationReport,
) -> bool:
    """Move a single-bulletin PDF into ``CI_{card_id}_{date}/bulletin.pdf``.

    Reads the cover for the date. Returns True on success.
    """
    iso_date = _parse_pdf_cover_for_date(pdf_path)
    if not iso_date:
        logger.warning("[migrate] %s: could not parse cover date; leaving in place",
                       pdf_path.name)
        report.failed += 1
        return False
    target = bulletin_path(bulletins_root, card_id, iso_date)
    if target.is_file() and target.stat().st_size > 0:
        logger.info("[migrate=] %s already wrapped at %s",
                    pdf_path.name, target.relative_to(bulletins_root))
        report.skipped_already_present += 1
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(pdf_path, target)
    logger.info("[migrate+] %s -> %s",
                pdf_path.name, target.relative_to(bulletins_root))
    report.pdfs_wrapped += 1
    return True


def migrate_to_subfolder_layout(
    bulletins_root: Path,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    """One-shot conversion of an existing flat-layout bulletins folder
    into the subfolder layout.

    Operates idempotently. For each top-level entry:
      * ``{card_id}.pdf`` (real PDF) -> ``CI_{card_id}_{date}/bulletin.pdf``
        (date parsed from the PDF cover).
      * ``{card_id}.pdf`` whose magic bytes are RAR -> renamed to
        ``{card_id}{BUNDLE_SUFFIX}`` and treated as a bundle.
      * ``{card_id}{BUNDLE_SUFFIX}`` -> extracted with 7-Zip; each
        contained ``{N} Sayılı ... Bülteni.pdf`` is wrapped into
        ``CI_{N}_{date}/bulletin.pdf``.

    Already-migrated subfolders are left alone. Returns a counts report.
    """
    report = MigrationReport()
    if not bulletins_root.is_dir():
        return report
    if dry_run:
        logger.info("[migrate dry-run] would scan %s", bulletins_root)

    # Phase A: rename RAR-magic .pdf files to .rar so phase B can pick them up.
    for entry in sorted(bulletins_root.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".pdf":
            continue
        if not is_rar_archive(entry):
            continue
        new_name = entry.with_name(entry.stem + BUNDLE_SUFFIX)
        if new_name.exists():
            logger.info("[migrate=] %s already renamed to %s", entry.name, new_name.name)
            continue
        if dry_run:
            logger.info("[migrate dry-run] would rename %s -> %s", entry.name, new_name.name)
        else:
            os.rename(entry, new_name)
            logger.info("[migrate~] %s -> %s (was RAR mis-named .pdf)",
                        entry.name, new_name.name)

    # Phase B: extract every bundle and wrap each contained PDF.
    extract_root = bulletins_root / ".migration_extract"
    for entry in sorted(bulletins_root.iterdir()):
        if not entry.is_file() or not entry.name.endswith(BUNDLE_SUFFIX):
            continue
        if dry_run:
            logger.info("[migrate dry-run] would extract %s", entry.name)
            continue
        target_dir = extract_root / entry.stem
        if target_dir.exists():
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)
        if not _extract_bundle_with_7z(entry, target_dir):
            report.failed += 1
            continue
        report.bundles_extracted += 1
        for inner in sorted(target_dir.rglob("*.pdf")):
            cid = _bundle_internal_card_id(inner.name)
            if not cid:
                logger.warning("[migrate] %s: cannot parse card_id from name", inner.name)
                report.failed += 1
                continue
            if _wrap_pdf_into_subfolder(inner, bulletins_root, cid, report):
                report.bulletins_from_bundles += 1
        # Tidy the per-bundle staging dir.
        try:
            import shutil
            shutil.rmtree(target_dir, ignore_errors=True)
        except OSError:
            pass

    # Tidy the staging root if it is empty.
    if extract_root.is_dir():
        try:
            import shutil
            shutil.rmtree(extract_root, ignore_errors=True)
        except OSError:
            pass

    # Phase C: wrap remaining single-bulletin PDFs.
    for entry in sorted(bulletins_root.iterdir()):
        if not entry.is_file() or entry.suffix.lower() != ".pdf":
            continue
        # Skip anything that's actually a sidecar (e.g. {N}_metadata.json
        # somehow with .pdf extension — defensive).
        cid = entry.stem
        if not re.fullmatch(r"\d+(?:-\d+)?", cid):
            continue
        if dry_run:
            logger.info("[migrate dry-run] would wrap %s", entry.name)
            continue
        # Idempotency: if a CI_{cid}_*/bulletin.pdf already exists, the
        # source is presumably a leftover.
        if existing_bulletin(bulletins_root, cid) is not None:
            logger.info("[migrate=] %s already wrapped, removing leftover flat copy",
                        entry.name)
            try:
                os.remove(entry)
            except OSError:
                pass
            report.skipped_already_present += 1
            continue
        _wrap_pdf_into_subfolder(entry, bulletins_root, cid, report)

    logger.info("Migration complete: %s", report.as_dict())
    return report


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
