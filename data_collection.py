import os
import re
import asyncio
import logging
import time
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, List, Set, Dict, Any
from urllib.parse import unquote, urljoin

import requests
from playwright.async_api import async_playwright

from ui_scrape_collection import (
    SCRAPED_METADATA_NAME,
    UIScrapeSession,
    collect_blt_issue,
    collect_gz_issue,
)

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins"


def _resolve_local_download_root(value: Optional[str], default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    path = path.resolve()

    return path if path.name.lower() == "bulletins" else path.parent


# ----------------------------
# Config (from settings with fallback defaults)
# ----------------------------
try:
    from config.settings import settings as _app_settings
    _pipe = _app_settings.pipeline
    TARGET_URL = _pipe.turkpatent_url
    BASE_DOWNLOAD_DIR = str(Path(_pipe.bulletins_root).parent)
    HEADLESS = _pipe.headless_browser
    CATEGORIES: List[str] = list(_pipe.categories)
    READ_TIMEOUT = _pipe.download_timeout
    INCREMENTAL_LOOKBACK = _pipe.incremental_lookback
    RECENT_WINDOW_DAYS = _pipe.recent_window_days
    MIN_GAZETTE_ISSUE_NUMBER = _pipe.min_gazette_issue_number
    UI_SCRAPE_ENABLED = _pipe.enable_ui_scrape
    SCRAPE_MAX_SCROLL_SECONDS = _pipe.scrape_max_scroll_seconds
    SCRAPE_LIMIT = _pipe.scrape_limit
except Exception:
    TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
    BASE_DOWNLOAD_DIR = str(
        _resolve_local_download_root(
            os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
            _LOCAL_DEFAULT_BULLETINS_DIR,
        )
    )
    HEADLESS = True
    CATEGORIES = ["Marka"]
    READ_TIMEOUT = 600
    INCREMENTAL_LOOKBACK = int(os.environ.get("PIPELINE_INCREMENTAL_LOOKBACK", "5"))
    RECENT_WINDOW_DAYS = int(os.environ.get("PIPELINE_RECENT_WINDOW_DAYS", "60"))
    MIN_GAZETTE_ISSUE_NUMBER = int(os.environ.get("PIPELINE_MIN_GAZETTE_ISSUE_NUMBER", "300"))
    UI_SCRAPE_ENABLED = os.environ.get("PIPELINE_ENABLE_UI_SCRAPE", "true").lower() not in {"0", "false", "no"}
    SCRAPE_MAX_SCROLL_SECONDS = int(os.environ.get("PIPELINE_SCRAPE_MAX_SCROLL_SECONDS", "0"))
    SCRAPE_LIMIT = int(os.environ.get("PIPELINE_SCRAPE_LIMIT", "0"))

SLOW_MO_MS = 150
VIEWPORT = {"width": 1400, "height": 900}

DOWNLOAD_LABEL_SELECTOR = r"text=/^\s*(İNDİR|İndir|INDIR|Indir)\s*$/"

# CD menu option text pattern
CD_TEXT_RE = re.compile(
    r"CD[_\s]?Icerigi|CD[_\s]?icerigi|CD\s*İçeriği|CD\s*İçerigi",
    re.IGNORECASE,
)

# Menu key parsing
MENU_RANGE_RE = re.compile(r"\b(\d{1,4}\s*[-\u2013\u2014]\s*\d{1,4})\b")
MENU_SINGLE_RE = re.compile(r"\b(\d{2,4}(?:_\d{1,2})?)\b")

MENU_WAIT_MS = 4000
CHUNK_BYTES = 1024 * 1024
CONNECT_TIMEOUT = 30
MAX_RETRIES = 2
REQUIRED_ISSUE_MARKERS = ("metadata.json", "events.json")
RAW_DOWNLOAD_EXTENSIONS = {".pdf", ".zip", ".rar", ".7z", ".xml", ".bin"}
RAW_SOURCE_SUFFIXES = {".pdf", ".script", ".data", ".properties", ".log", ".bak", ".txt", ".sql", ".xml", ".zip"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [COLLECTOR] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.collector")


# ----------------------------
# Utils
# ----------------------------
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
        root, ext = os.path.splitext(name)
        if ext:
            root = root[: max_len - len(ext)]
            name = root + ext
        else:
            name = name[:max_len]
        name = name.rstrip(" .")

    return name or "file"

def stem_from_menu_text(text: str) -> str:
    return safe_filename_keep_text(text)


def build_issue_download_stem(card_id: str, card_date: Optional[str], is_gazette: bool) -> str:
    prefix = "GZ" if is_gazette else "BLT"
    parts = [prefix, card_id]
    if card_date:
        parts.append(card_date)
    return "_".join(parts)


def filename_from_content_disposition(cd: Optional[str]) -> Optional[str]:
    if not cd:
        return None
    m = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        return unquote(m.group(1)).strip().strip('"')
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"filename\s*=\s*([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None

def ext_from_headers(content_type: str, content_disp: str) -> str:
    cd_name = filename_from_content_disposition(content_disp)
    if cd_name:
        _, ext = os.path.splitext(cd_name)
        if ext:
            return ext.lower()

    ct = (content_type or "").lower()
    if "zip" in ct:
        return ".zip"
    if "pdf" in ct:
        return ".pdf"
    if "xml" in ct:
        return ".xml"
    return ".bin"

def parse_bulletin_key(text: str) -> Optional[str]:
    if not text:
        return None
    m = MENU_RANGE_RE.search(text)
    if m:
        raw = m.group(1).replace("–", "-").replace("—", "-")
        raw = re.sub(r"\s*", "", raw)
        return raw
    m = MENU_SINGLE_RE.search(text)
    if m:
        return m.group(1)
    return None

def build_key_regex(key: str) -> re.Pattern:
    if "-" in key:
        a, b = key.split("-", 1)
        return re.compile(rf"\b{re.escape(a)}\s*[-\u2013\u2014]\s*{re.escape(b)}\b")
    return re.compile(rf"\b{re.escape(key)}\b")


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


def normalize_card_metadata(
    meta: Dict[str, Optional[str]],
    *,
    min_gazette_issue_number: Optional[int] = None,
) -> Dict[str, Optional[str]]:
    """Normalize scraped card metadata and reject impossible Gazette ids."""
    card_id = (meta.get("id") or "").strip() or None
    card_date = (meta.get("date") or "").strip() or None
    is_gazette = bool(meta.get("is_gazette"))
    issue_number = extract_primary_issue_number(card_id)
    threshold = MIN_GAZETTE_ISSUE_NUMBER if min_gazette_issue_number is None else min_gazette_issue_number

    if (
        is_gazette
        and threshold > 0
        and issue_number is not None
        and issue_number < threshold
    ):
        logger.warning(
            "Treating card %s dated %s as BLT because Gazette numbers start at %s",
            card_id,
            card_date or "unknown",
            threshold,
        )
        is_gazette = False

    return {"id": card_id, "date": card_date, "is_gazette": is_gazette}


def is_recent_issue(
    card_date: Optional[str],
    *,
    today: Optional[date] = None,
    lookback_days: Optional[int] = None,
) -> bool:
    issue_date = parse_issue_date(card_date)
    if issue_date is None:
        return True

    reference_day = today or date.today()
    window_days = RECENT_WINDOW_DAYS if lookback_days is None else lookback_days
    cutoff_day = reference_day - timedelta(days=max(0, window_days))
    return issue_date >= cutoff_day


def build_issue_folder_candidates(card_id: str, card_date: Optional[str], is_gazette: bool) -> List[str]:
    prefix = "GZ" if is_gazette else "BLT"
    candidates: List[str] = []
    if card_date:
        candidates.append(f"{prefix}_{card_id}_{card_date}")
    candidates.append(f"{prefix}_{card_id}")
    return candidates


def issue_folder_has_required_markers(issue_folder: Path) -> bool:
    return issue_folder.is_dir() and all(
        (issue_folder / marker).is_file() for marker in REQUIRED_ISSUE_MARKERS
    )


def has_matching_pdf_artifact(folder: str | Path, card_id: str, is_gazette: bool) -> bool:
    root = Path(folder)
    if not root.exists():
        return False

    target_id = card_id.strip()
    id_pattern = re.compile(rf"(?:^|\D){re.escape(target_id)}(?:\D|$)")

    for child in root.iterdir():
        fn_lower = child.name.lower()

        file_is_gazette = "gazete" in fn_lower or fn_lower.startswith("gz_")
        if is_gazette and not file_is_gazette:
            continue
        if not is_gazette and file_is_gazette:
            continue
        if not id_pattern.search(child.name):
            continue

        try:
            if child.is_file() and child.suffix.lower() == ".pdf" and child.stat().st_size > 0:
                return True
            if child.is_dir():
                for sub in child.iterdir():
                    if sub.is_file() and sub.suffix.lower() == ".pdf" and sub.stat().st_size > 0:
                        return True
        except Exception:
            pass

    return False


@dataclass
class IncrementalScanTracker:
    threshold: int
    lookback_days: int
    today: date = field(default_factory=date.today)
    recent_counts: Dict[str, int] = field(
        default_factory=lambda: {"BLT": 0, "GZ": 0}
    )
    cutoff_reached: Dict[str, bool] = field(
        default_factory=lambda: {"BLT": False, "GZ": False}
    )

    def observe(self, *, card_date: Optional[str], is_gazette: bool) -> bool:
        issue_type = "GZ" if is_gazette else "BLT"
        recent = is_recent_issue(
            card_date,
            today=self.today,
            lookback_days=self.lookback_days,
        )
        if recent:
            self.recent_counts[issue_type] += 1
        else:
            self.cutoff_reached[issue_type] = True
        return recent

    def should_stop(self) -> bool:
        return all(
            self.cutoff_reached[issue_type] or self.recent_counts[issue_type] >= self.threshold
            for issue_type in ("BLT", "GZ")
        )


@dataclass
class CollectionCounters:
    downloaded_raw: int = 0
    download_failed: int = 0
    scraped: int = 0
    scrape_failed: int = 0
    partial_issues: int = 0
    retry_needed: int = 0
    skipped: int = 0

    def add(self, other: "CollectionCounters") -> None:
        self.downloaded_raw += other.downloaded_raw
        self.download_failed += other.download_failed
        self.scraped += other.scraped
        self.scrape_failed += other.scrape_failed
        self.partial_issues += other.partial_issues
        self.retry_needed += other.retry_needed
        self.skipped += other.skipped

    def to_summary(self, *, duration_seconds: float) -> Dict[str, Any]:
        return {
            "downloaded": self.downloaded_raw + self.scraped,
            "skipped": self.skipped,
            "failed": self.download_failed + self.scrape_failed,
            "downloaded_raw": self.downloaded_raw,
            "download_failed": self.download_failed,
            "scraped": self.scraped,
            "scrape_failed": self.scrape_failed,
            "partial_issues": self.partial_issues,
            "retry_needed": self.retry_needed,
            "duration_seconds": round(duration_seconds, 1),
        }

def _legacy_check_local_existence(folder: str, card_id: str, is_gazette: bool,
                                  pdf_only: bool = False) -> bool:
    """
    Robustly checks if the file or extracted directory exists,
    handling legacy names, ranges, new formats, and GZ_/BLT_ directory prefixes.

    When pdf_only=True, only returns True if a PDF file exists for this bulletin
    (either at the top level or inside its subfolder).
    """
    if not os.path.exists(folder):
        return False

    target_id = card_id.strip()

    # Handle Range IDs (e.g., "103-106")
    if "-" in target_id:
        id_pattern = re.compile(rf"(?:^|\D){re.escape(target_id)}(?:\D|$)")
    else:
        id_pattern = re.compile(rf"(?:^|\D){re.escape(target_id)}(?:\D|$)")

    for fn in os.listdir(folder):
        fn_lower = fn.lower()

        # 1. Type Alignment Check
        # Detect gazette by "gazete" in name OR "gz_" directory prefix
        file_is_gazette = "gazete" in fn_lower or fn_lower.startswith("gz_")
        # Detect bulletin by "blt_" or "bülten" prefix (not gazette)
        file_is_bulletin = fn_lower.startswith("blt_") or "bulten" in fn_lower or "bülten" in fn_lower

        # Strict type checking to prevent GZ_485 matching BLT_485:
        if is_gazette and not file_is_gazette:
            continue
        if not is_gazette and file_is_gazette:
            continue

        # 2. ID Match Check
        if id_pattern.search(fn):
            p = os.path.join(folder, fn)
            # 3. Existence Check
            try:
                if pdf_only:
                    # Only consider "existing" if a PDF is present
                    if fn_lower.endswith(".pdf") and os.path.getsize(p) > 0:
                        return True
                    if os.path.isdir(p):
                        # Check inside the subfolder for PDFs
                        for sub in os.listdir(p):
                            if sub.lower().endswith(".pdf"):
                                sub_p = os.path.join(p, sub)
                                if os.path.isfile(sub_p) and os.path.getsize(sub_p) > 0:
                                    return True
                    # Directory or non-PDF file exists but no PDF — not "existing" in pdf_only mode
                    continue
                else:
                    if os.path.isdir(p):
                        return True
                    if os.path.getsize(p) > 0:
                        return True
            except Exception:
                pass
    return False


def check_local_existence(
    folder: str,
    card_id: str,
    is_gazette: bool,
    *,
    card_date: Optional[str] = None,
    pdf_only: bool = False,
) -> bool:
    """
    Check whether this issue is already available locally.

    When pdf_only=True, only returns True if a PDF file exists for this bulletin
    (either at the top level or inside its subfolder).

    In normal pipeline mode, an issue counts as complete only when its canonical
    BLT_/GZ_ folder contains both metadata.json and events.json.
    """
    root = Path(folder)
    if not root.exists():
        return False

    if pdf_only:
        return has_matching_pdf_artifact(root, card_id, is_gazette)

    for candidate in build_issue_folder_candidates(card_id, card_date, is_gazette):
        if issue_folder_has_required_markers(root / candidate):
            return True
    return False


def build_issue_folder_path(
    category_folder: str | Path,
    card_id: str,
    card_date: Optional[str],
    is_gazette: bool,
) -> Path:
    return Path(category_folder) / build_issue_download_stem(card_id, card_date, is_gazette)


def issue_folder_has_scraped_metadata(issue_folder: Path) -> bool:
    return issue_folder.is_dir() and (issue_folder / SCRAPED_METADATA_NAME).is_file()


def _is_raw_source_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in REQUIRED_ISSUE_MARKERS or path.name == SCRAPED_METADATA_NAME:
        return False
    if path.name.lower() == "bulletin.pdf":
        return True
    if path.suffix.lower() in RAW_SOURCE_SUFFIXES:
        return True
    return path.name.lower().startswith("tmbulletin")


def issue_folder_has_raw_sources(issue_folder: Path) -> bool:
    if not issue_folder.is_dir():
        return False
    try:
        for child in issue_folder.rglob("*"):
            if _is_raw_source_file(child):
                return True
    except Exception:
        return False
    return False


def has_matching_download_artifact(
    folder: str | Path,
    card_id: str,
    is_gazette: bool,
    *,
    card_date: Optional[str] = None,
) -> bool:
    root = Path(folder)
    if not root.exists():
        return False

    target_id = card_id.strip()
    id_pattern = re.compile(rf"(?:^|\D){re.escape(target_id)}(?:\D|$)")

    for child in root.iterdir():
        child_lower = child.name.lower()
        child_is_gazette = "gazete" in child_lower or child_lower.startswith("gz_")
        if is_gazette and not child_is_gazette:
            continue
        if not is_gazette and child_is_gazette:
            continue
        if not id_pattern.search(child.name):
            continue

        try:
            if child.is_file() and child.suffix.lower() in RAW_DOWNLOAD_EXTENSIONS and child.stat().st_size > 0:
                return True
            if child.is_dir() and (
                child.name in build_issue_folder_candidates(card_id, card_date, is_gazette)
                or child.name == build_issue_download_stem(card_id, card_date, is_gazette)
            ):
                if issue_folder_has_raw_sources(child):
                    return True
        except Exception:
            pass
    return False


def ensure_issue_folder(
    category_folder: str | Path,
    card_id: str,
    card_date: Optional[str],
    is_gazette: bool,
) -> Path:
    issue_folder = build_issue_folder_path(category_folder, card_id, card_date, is_gazette)
    issue_folder.mkdir(parents=True, exist_ok=True)
    return issue_folder


async def maybe_scrape_issue(
    issue_no: str,
    issue_date: Optional[str],
    issue_folder: Path,
    *,
    is_gazette: bool,
    scrape_session: Optional[UIScrapeSession],
    scrape_enabled: bool,
    scrape_max_scroll_seconds: int,
    scrape_limit: int,
) -> Dict[str, Any]:
    existing_sidecar = issue_folder / SCRAPED_METADATA_NAME
    if existing_sidecar.exists() and existing_sidecar.stat().st_size > 0:
        return {"available": True, "created": 0, "attempted": False, "result": None}
    if not scrape_enabled or scrape_session is None:
        return {"available": False, "created": 0, "attempted": False, "result": None}

    issue_folder.mkdir(parents=True, exist_ok=True)
    if is_gazette:
        result = await collect_gz_issue(
            issue_no,
            issue_date,
            issue_folder,
            session=scrape_session,
            max_scroll_seconds=scrape_max_scroll_seconds,
            limit=scrape_limit,
        )
    else:
        result = await collect_blt_issue(
            issue_no,
            issue_date,
            issue_folder,
            session=scrape_session,
            max_scroll_seconds=scrape_max_scroll_seconds,
            limit=scrape_limit,
        )

    available = existing_sidecar.exists() and existing_sidecar.stat().st_size > 0
    return {
        "available": available,
        "created": 1 if result.get("status") == "success" and available else 0,
        "attempted": True,
        "result": result,
    }


def summarize_issue_sources(
    *,
    raw_available: bool,
    scrape_available: bool,
    raw_created: int,
    scrape_created: int,
    raw_attempted: bool,
    scrape_attempted: bool,
) -> CollectionCounters:
    counters = CollectionCounters(
        downloaded_raw=raw_created,
        scraped=scrape_created,
    )
    raw_considered = raw_attempted or raw_available
    scrape_considered = scrape_attempted or scrape_available
    if raw_attempted and not raw_available:
        counters.download_failed = 1
    if scrape_attempted and not scrape_available:
        counters.scrape_failed = 1
    if raw_considered and scrape_considered and raw_available != scrape_available:
        counters.partial_issues = 1
    if (raw_attempted or scrape_attempted) and not raw_available and not scrape_available:
        counters.retry_needed = 1
    return counters

# ----------------------------
# Site interactions
# ----------------------------
async def maybe_dismiss_overlays(page) -> None:
    """Dismiss any full-screen overlays: Duyuru announcements, fraud warnings,
    cookie consent banners — mirrors scrapper.py _close_popups() logic."""

    # 1. Hit Escape to close any active dialog
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except Exception:
        pass

    # 2. Try common consent, close, and announcement buttons
    candidates = [
        page.get_by_role("button", name=re.compile(
            r"kabul|accept|tamam|ok|anlad[ıi]m", re.I)),
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
                logger.info("Dismissed overlay via button click")
        except Exception:
            pass

    # 3. Click outside to dismiss backdrop-dismissible modals
    try:
        await page.mouse.click(2, 2)
        await page.wait_for_timeout(300)
    except Exception:
        pass

    # 4. Nuclear option: hide remaining full-screen overlays via JS
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

async def select_category(page, category_name: str) -> None:
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)

    await open_kategorisi_dropdown(page)
    await page.get_by_role("option", name=category_name).click()

    await page.wait_for_timeout(2000)
    await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)

async def collect_download_clickables(page):
    labels = page.locator(DOWNLOAD_LABEL_SELECTOR)
    return labels.locator("xpath=ancestor-or-self::*[self::a or self::button or @role='button'][1]")

async def extract_card_metadata(clickable) -> Dict[str, Optional[str]]:
    """
    Extracts ID, Date, and Type (Gazette vs Bulletin) from the card.
    Returns: { "id": "484", "date": "2024-01-15", "is_gazette": True/False }
    """
    return await clickable.evaluate(
        r"""(el) => {
            const cardIdRe = /^\s*\d{1,4}(?:_\d{1,2})?(?:\s*[-–—]\s*\d{1,4}(?:_\d{1,2})?)?\s*$/;
            const dateRe = /(\d{2})[./](\d{2})[./](\d{4})/;
            
            let n = el;
            let foundId = null;
            let foundDate = null;
            let isGazette = false;

            for (let step = 0; step < 10; step++) {
                if (!n) break;
                const t = (n.innerText || "");
                const tLower = t.toLowerCase();
                
                // Look for ID in lines
                if (!foundId) {
                    const lines = t.split("\n").map(s => s.trim()).filter(Boolean);
                    const ids = lines.filter(l => cardIdRe.test(l));
                    if (ids.length >= 1) foundId = ids[0].replace(/\s+/g, "");
                }

                // Look for Date
                if (!foundDate) {
                    const m = t.match(dateRe);
                    if (m) {
                        foundDate = `${m[3]}-${m[2]}-${m[1]}`; // YYYY-MM-DD
                    }
                }

                // Look for Type (Gazete/Gazetesi)
                if (tLower.includes("gazete")) {
                    isGazette = true;
                }

                if (foundId && foundDate) break;
                n = n.parentElement;
            }
            return { id: foundId, date: foundDate, is_gazette: isGazette };
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

    items = menu.locator("a, button, [role='menuitem'], li")
    out: List[Dict[str, Any]] = []

    n = await items.count()
    for i in range(n):
        it = items.nth(i)
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

        is_cd = bool(CD_TEXT_RE.search(text))
        key = parse_bulletin_key(text)

        out.append({
            "text": text,
            "href": href,
            "is_cd": is_cd,
            "key": key,
            "stem": stem_from_menu_text(text),
        })

    dedup: Dict[str, Dict[str, Any]] = {}
    for d in out:
        dedup.setdefault(d["text"], d)
    return list(dedup.values())


def build_download_plan(items: List[Dict[str, Any]], *, pdf_only: bool = False) -> List[Dict[str, Any]]:
    """Choose which menu items to fetch for a single non-grouped issue."""
    cd_items = [it for it in items if it.get("is_cd")]
    non_cd_items = [it for it in items if not it.get("is_cd")]
    numbered_items = [it for it in non_cd_items if it.get("key")]

    plan: List[Dict[str, Any]] = []
    if not pdf_only and cd_items:
        plan.append({"item": cd_items[0], "is_cd_file": True})

    pick = numbered_items[0] if numbered_items else (non_cd_items[0] if non_cd_items else None)
    if pick:
        plan.append({"item": pick, "is_cd_file": False})
    return plan


# ----------------------------
# Download helpers
# ----------------------------
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

        with s.get(url, stream=True, allow_redirects=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}")
            
            ct = r.headers.get("content-type", "").lower()
            if "text/html" in ct:
                logger.warning(f"    [!] URL returned text/html. Rejecting.")
                return False

            cd = r.headers.get("content-disposition", "")
            ext = ext_from_headers(ct, cd)
            root, _ = os.path.splitext(out_base_path)
            final_path = root + ext
            os.makedirs(os.path.dirname(final_path), exist_ok=True)

            if os.path.exists(final_path):
                try:
                    if os.path.getsize(final_path) > 0:
                        return True
                except Exception:
                    pass

            total = 0
            last_log = time.time()
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=CHUNK_BYTES):
                    if not chunk: continue
                    f.write(chunk)
                    total += len(chunk)
                    now = time.time()
                    if now - last_log >= 60:
                        mb = total / (1024 * 1024)
                        logger.info(f"    ... downloaded {mb:.1f} MB so far")
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

        if os.path.exists(final_path):
            try:
                if os.path.getsize(final_path) > 0:
                    return True
            except Exception:
                pass

        await dl.save_as(final_path)
        return True
    except Exception as e:
        logger.warning(f"[!] Playwright download failed: {e!r}")
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

        if os.path.exists(final_path):
            try:
                if os.path.getsize(final_path) > 0:
                    return True
            except Exception:
                pass
        await dl.save_as(final_path)
        return True
    except Exception as e:
        logger.warning(f"[!] Direct click download failed: {e!r}")
        return False


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


# ----------------------------
# Core per-card logic
# ----------------------------
async def download_one_item(page, context, clickable, menu_item: Dict[str, Any], category_folder: str, out_prefix: str) -> bool:
    href = menu_item.get("href")
    out_base = os.path.join(category_folder, f"{out_prefix}.bin")

    if href:
        abs_url = urljoin(page.url, href)
        last_err = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                ok = await stream_download_with_browser_session(context, page, abs_url, out_base)
                if ok: return True
            except Exception as e:
                last_err = e
                logger.warning(f"[!] Requests attempt {attempt} failed: {e!r}")
                await page.wait_for_timeout(1000)
        logger.info(f"[i] Falling back to click download for: {menu_item['text']}")

    menu = await get_open_menu_container(page, 500)
    if not menu:
        menu = await open_download_menu(page, clickable)
        if not menu: return False

    if menu_item.get("is_cd"):
        item_locator = menu.locator("a, button, [role='menuitem'], li").filter(has_text=CD_TEXT_RE).first
    else:
        key = menu_item.get("key")
        if key:
            pat = build_key_regex(key)
            item_locator = menu.locator("a, button, [role='menuitem'], li").filter(has_text=pat).first
        else:
            item_locator = menu.locator("a, button, [role='menuitem'], li").filter(has_text=re.compile(re.escape(menu_item["text"]))).first

    last_err = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            ok = await playwright_download_click(page, item_locator, out_base)
            if ok: return True
        except Exception as e:
            last_err = e
            logger.warning(f"[!] Click attempt {attempt} failed: {e!r}")
            await page.wait_for_timeout(1000)

    if last_err:
        logger.warning(f"[!] Giving up on item: {menu_item['text']}")
    return False

async def process_card(
    page,
    context,
    clickable,
    category_folder: str,
    card_id: str,
    card_date: Optional[str],
    is_gazette: bool,
    *,
    pdf_only: bool = False,
    scrape_session: Optional[UIScrapeSession] = None,
    scrape_enabled: bool = True,
    scrape_max_scroll_seconds: int = 0,
    scrape_limit: int = 0,
) -> CollectionCounters:
    target_name = build_issue_download_stem(card_id, card_date, is_gazette)

    if check_local_existence(
        category_folder,
        card_id,
        is_gazette,
        card_date=card_date,
        pdf_only=pdf_only,
    ):
        return CollectionCounters(skipped=1)

    async def _collect_single_issue(
        issue_no: str,
        *,
        raw_download_coro=None,
        target_prefix: Optional[str] = None,
    ) -> CollectionCounters:
        raw_available = has_matching_download_artifact(
            category_folder,
            issue_no,
            is_gazette,
            card_date=card_date,
        )
        raw_created = 0
        raw_attempted = False
        if not raw_available and raw_download_coro is not None:
            raw_attempted = True
            ok = False
            try:
                ok = await raw_download_coro()
            except Exception as exc:
                logger.warning("[!] Raw download failed for %s: %r", issue_no, exc)
            raw_created = 1 if ok else 0
            raw_available = has_matching_download_artifact(
                category_folder,
                issue_no,
                is_gazette,
                card_date=card_date,
            )

        issue_folder = build_issue_folder_path(category_folder, issue_no, card_date, is_gazette)
        scrape_state = await maybe_scrape_issue(
            issue_no,
            card_date,
            issue_folder,
            is_gazette=is_gazette,
            scrape_session=scrape_session,
            scrape_enabled=scrape_enabled,
            scrape_max_scroll_seconds=scrape_max_scroll_seconds,
            scrape_limit=scrape_limit,
        )
        counters = summarize_issue_sources(
            raw_available=raw_available,
            scrape_available=bool(scrape_state["available"]),
            raw_created=raw_created,
            scrape_created=int(scrape_state["created"]),
            raw_attempted=raw_attempted,
            scrape_attempted=bool(scrape_state["attempted"]),
        )

        if counters.partial_issues:
            logger.info(
                "[*] %s marked partial (raw_available=%s, scrape_available=%s)",
                target_prefix or issue_no,
                raw_available,
                bool(scrape_state["available"]),
            )
        if counters.retry_needed:
            logger.warning("[!] %s needs retry; both source collections are unavailable", target_prefix or issue_no)
        return counters

    direct_href = await get_clickable_download_href(clickable)
    if direct_href:
        abs_url = urljoin(page.url, direct_href)
        out_base = os.path.join(category_folder, f"{target_name}.bin")
        logger.info(f"[*] Card {card_id}: direct download href detected")

        async def _download_direct_href() -> bool:
            last_err = None
            for attempt in range(1, MAX_RETRIES + 2):
                try:
                    ok = await stream_download_with_browser_session(context, page, abs_url, out_base)
                    if ok:
                        return True
                except Exception as exc:
                    last_err = exc
                    logger.warning(f"[!] Direct href attempt {attempt} failed: {exc!r}")
                    await page.wait_for_timeout(1000)
            if last_err:
                logger.info(f"[i] Direct href stream failed for card {card_id}; falling back to menu/click flow")
            return False

        direct_counters = await _collect_single_issue(
            card_id,
            raw_download_coro=_download_direct_href,
            target_prefix=target_name,
        )
        if has_matching_download_artifact(
            category_folder,
            card_id,
            is_gazette,
            card_date=card_date,
        ):
            return direct_counters

    menu = await open_download_menu(page, clickable)
    if not menu:
        if check_local_existence(
            category_folder,
            card_id,
            is_gazette,
            card_date=card_date,
            pdf_only=pdf_only,
        ):
            return CollectionCounters(skipped=1)
        logger.info(f"[*] Card {card_id}: menu did not open -> direct click download")

        async def _download_direct_click() -> bool:
            return await direct_click_download(page, clickable, os.path.join(category_folder, f"{target_name}.bin"))

        return await _collect_single_issue(
            card_id,
            raw_download_coro=_download_direct_click,
            target_prefix=target_name,
        )

    items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
    numbered_items = [it for it in items if (not it.get("is_cd") and it.get("key"))]
    is_grouped = len(numbered_items) > 1

    if not is_grouped:
        plan = build_download_plan(items, pdf_only=pdf_only)
        if not plan:
            try:
                await force_close_menus(page)
            except Exception:
                pass
            return await _collect_single_issue(card_id, target_prefix=target_name)

        async def _download_plan_items() -> bool:
            ok_count = 0
            for planned in plan:
                item = planned["item"]
                is_cd_file = planned["is_cd_file"]
                action_label = "CD" if is_cd_file else "PDF/Other"
                logger.info(f"[*] {card_id}: downloading {action_label}")
                ok = await download_one_item(page, context, clickable, item, category_folder, target_name)
                ok_count += 1 if ok else 0
            return ok_count > 0

        try:
            return await _collect_single_issue(
                card_id,
                raw_download_coro=_download_plan_items,
                target_prefix=target_name,
            )
        finally:
            try:
                await force_close_menus(page)
            except Exception:
                pass

    if check_local_existence(
        category_folder,
        card_id,
        is_gazette,
        card_date=card_date,
        pdf_only=pdf_only,
    ):
        logger.info(f"[*] Group card {card_id}: already exists locally, skipping all sub-items")
        try:
            await force_close_menus(page)
        except Exception:
            pass
        return CollectionCounters(skipped=1)

    logger.info(f"[*] Group card {card_id}: downloading {len(numbered_items)} items")
    counters = CollectionCounters()
    href_first = [it for it in numbered_items if it.get("href")]
    click_later = [it for it in numbered_items if not it.get("href")]

    try:
        for item in href_first + click_later:
            sub_id = item.get("key") or item["stem"]
            if check_local_existence(
                category_folder,
                sub_id,
                is_gazette,
                card_date=card_date,
                pdf_only=pdf_only,
            ):
                counters.skipped += 1
                continue

            item_prefix = build_issue_download_stem(sub_id, card_date, is_gazette)
            logger.info(f"    -> {item['text']}")

            async def _download_group_item(it=item, prefix=item_prefix) -> bool:
                return await download_one_item(page, context, clickable, it, category_folder, prefix)

            counters.add(
                await _collect_single_issue(
                    sub_id,
                    raw_download_coro=_download_group_item,
                    target_prefix=item_prefix,
                )
            )
    finally:
        try:
            await force_close_menus(page)
        except Exception:
            pass
    return counters


# ----------------------------
# Category loop
# ----------------------------
async def download_all_for_category(
    page,
    context,
    category_name: str,
    *,
    full_scan: bool = False,
    pdf_only: bool = False,
    scrape_session: Optional[UIScrapeSession] = None,
    scrape_enabled: bool = True,
    scrape_max_scroll_seconds: int = 0,
    scrape_limit: int = 0,
) -> CollectionCounters:
    category_folder = os.path.join(BASE_DOWNLOAD_DIR, slugify(category_name))
    os.makedirs(category_folder, exist_ok=True)

    done_cards: Set[str] = set()
    last_height = 0
    stall_rounds = 0

    tracker = None if full_scan else IncrementalScanTracker(
        threshold=INCREMENTAL_LOOKBACK,
        lookback_days=RECENT_WINDOW_DAYS,
    )
    counters = CollectionCounters()

    while True:
        clickables = await collect_download_clickables(page)
        count = await clickables.count()
        logger.info(f"Visible cards (İNDİR): {count}")

        new_sources_in_pass = 0

        for i in range(count):
            clickable = clickables.nth(i)
            try:
                if not await clickable.is_visible(): continue
            except Exception: continue

            # Extract Metadata
            meta = normalize_card_metadata(await extract_card_metadata(clickable))
            card_id = meta.get("id")
            card_date = meta.get("date")
            is_gz = meta.get("is_gazette")

            # Dedup by (id, type) — BLT 484 and GZ 484 are different cards
            dedup_key = f"{card_id}_{'GZ' if is_gz else 'BLT'}"
            if not card_id or dedup_key in done_cards: continue

            suffix_log = " (Gazete)" if is_gz else " (Bülten)"
            date_log = f" [{card_date}]" if card_date else ""

            if tracker is not None:
                within_recent_window = tracker.observe(card_date=card_date, is_gazette=is_gz)
                if not within_recent_window:
                    done_cards.add(dedup_key)
                    logger.info(
                        f"[-] {card_id}{suffix_log}{date_log} is older than the "
                        f"{RECENT_WINDOW_DAYS}-day recent window, skipping"
                    )
                    continue

                already_exists = check_local_existence(
                    category_folder,
                    card_id,
                    is_gz,
                    card_date=card_date,
                    pdf_only=pdf_only,
                )
                if already_exists:
                    done_cards.add(dedup_key)
                    logger.info(
                        f"[=] {card_id}{suffix_log}{date_log} is complete locally, skipping"
                    )
                    continue

            # Incremental mode: check if we already have this card locally
            if False and not full_scan:
                already_exists = check_local_existence(category_folder, card_id, is_gz,
                                                       pdf_only=pdf_only)
                if already_exists:
                    if is_gz:
                        consecutive_existing_gz += 1
                    else:
                        consecutive_existing_blt += 1
                    done_cards.add(dedup_key)
                    logger.info(f"[=] {card_id}{suffix_log}{date_log} exists locally, skipping")
                    continue
                else:
                    # Reset counter — new card found between existing ones
                    if is_gz:
                        consecutive_existing_gz = 0
                    else:
                        consecutive_existing_blt = 0

            logger.info(f"[*] Checking {card_id}{suffix_log}{date_log}...")

            issue_counters = await process_card(
                page,
                context,
                clickable,
                category_folder,
                card_id,
                card_date,
                is_gz,
                pdf_only=pdf_only,
                scrape_session=scrape_session,
                scrape_enabled=scrape_enabled,
                scrape_max_scroll_seconds=scrape_max_scroll_seconds,
                scrape_limit=scrape_limit,
            )
            counters.add(issue_counters)
            new_sources_in_pass += issue_counters.downloaded_raw + issue_counters.scraped
            done_cards.add(dedup_key)

            await page.wait_for_timeout(200)

        if tracker is not None and tracker.should_stop():
            logger.info(
                "Incremental stop: recent window covered "
                f"(days={RECENT_WINDOW_DAYS}, threshold={INCREMENTAL_LOOKBACK}, "
                f"recent_blt={tracker.recent_counts['BLT']}, "
                f"recent_gz={tracker.recent_counts['GZ']}, "
                f"cutoff_blt={tracker.cutoff_reached['BLT']}, "
                f"cutoff_gz={tracker.cutoff_reached['GZ']}, "
                f"downloaded_raw={counters.downloaded_raw}, "
                f"scraped={counters.scraped})."
            )
            break

        # Incremental mode: stop if we've seen enough consecutive existing cards
        if False and not full_scan:
            if (consecutive_existing_blt >= INCREMENTAL_LOOKBACK and
                    consecutive_existing_gz >= INCREMENTAL_LOOKBACK):
                logger.info(
                    f"Incremental stop: {consecutive_existing_blt} consecutive existing BLT "
                    f"and {consecutive_existing_gz} consecutive existing GZ cards. "
                    f"Downloaded {counters.downloaded_raw} raw files."
                )
                break

        height = await page.evaluate("document.body.scrollHeight")
        # Logic to detect end of scroll (no new files and height didn't change)
        if height == last_height and new_sources_in_pass == 0:
            stall_rounds += 1
        else:
            stall_rounds = 0
            last_height = height

        if stall_rounds >= 8:
            logger.info(f"Reached end of category '{category_name}'")
            break

        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(2000)

    return counters


# ----------------------------
# Main
# ----------------------------
async def run_collection(
    settings=None,
    full_scan: bool = False,
    pdf_only: bool = False,
    enable_ui_scrape: Optional[bool] = None,
    scrape_max_scroll_seconds: Optional[int] = None,
    scrape_limit: Optional[int] = None,
) -> dict:
    """
    Run bulletin collection. Returns summary dict.

    Args:
        settings: Optional PipelineSettings override. If None, uses module-level config.
        full_scan: If True, scroll through ALL bulletins (original behavior).
                   If False (default), inspect recent issues within
                   RECENT_WINDOW_DAYS and stop once the recent window is covered
                   or the per-type threshold is reached.
        pdf_only:  If True, only download PDF files. Skips CD/ZIP items and only
                   considers a bulletin as "existing" if a PDF is already present.
                   Use with full_scan=True to backfill PDFs for all bulletins.

    Returns:
        { "downloaded": int, "skipped": int, "failed": int, "duration_seconds": float }
    """
    global TARGET_URL, BASE_DOWNLOAD_DIR, HEADLESS, CATEGORIES, READ_TIMEOUT
    global INCREMENTAL_LOOKBACK, RECENT_WINDOW_DAYS, MIN_GAZETTE_ISSUE_NUMBER
    global UI_SCRAPE_ENABLED, SCRAPE_MAX_SCROLL_SECONDS, SCRAPE_LIMIT

    if settings is not None:
        TARGET_URL = settings.turkpatent_url
        BASE_DOWNLOAD_DIR = str(Path(settings.bulletins_root).parent)
        HEADLESS = settings.headless_browser
        CATEGORIES = list(settings.categories)
        READ_TIMEOUT = settings.download_timeout
        INCREMENTAL_LOOKBACK = settings.incremental_lookback
        RECENT_WINDOW_DAYS = settings.recent_window_days
        MIN_GAZETTE_ISSUE_NUMBER = settings.min_gazette_issue_number
        UI_SCRAPE_ENABLED = settings.enable_ui_scrape
        SCRAPE_MAX_SCROLL_SECONDS = settings.scrape_max_scroll_seconds
        SCRAPE_LIMIT = settings.scrape_limit

    if enable_ui_scrape is not None:
        UI_SCRAPE_ENABLED = enable_ui_scrape
    if scrape_max_scroll_seconds is not None:
        SCRAPE_MAX_SCROLL_SECONDS = scrape_max_scroll_seconds
    if scrape_limit is not None:
        SCRAPE_LIMIT = scrape_limit

    mode = "PDF-ONLY FULL" if pdf_only else ("FULL" if full_scan else "INCREMENTAL")
    effective_ui_scrape = UI_SCRAPE_ENABLED and not pdf_only
    # pdf_only implies full_scan — must scroll through everything
    if pdf_only:
        full_scan = True
    t0 = time.time()
    os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)
    logger.info(f"Files will be saved under: {os.path.abspath(BASE_DOWNLOAD_DIR)}")
    logger.info(f"Target URL: {TARGET_URL}")
    logger.info(f"Categories: {CATEGORIES}")
    logger.info(f"Headless: {HEADLESS}")
    logger.info(
        f"Mode: {mode} (threshold={INCREMENTAL_LOOKBACK}, "
        f"recent_window_days={RECENT_WINDOW_DAYS}, "
        f"min_gazette_issue_number={MIN_GAZETTE_ISSUE_NUMBER})"
    )
    logger.info(
        "UI scrape: enabled=%s max_scroll_seconds=%s limit=%s",
        effective_ui_scrape,
        SCRAPE_MAX_SCROLL_SECONDS,
        SCRAPE_LIMIT,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        context = await browser.new_context(accept_downloads=True, viewport=VIEWPORT)
        page = await context.new_page()
        scrape_page = await context.new_page() if effective_ui_scrape else None
        try:
            logger.info(f"Opening {TARGET_URL} ...")
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            await maybe_dismiss_overlays(page)
            await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)

            total_counters = CollectionCounters()
            scrape_session = UIScrapeSession(scrape_page) if scrape_page is not None else None
            for category in CATEGORIES:
                logger.info(f"--- Category: {category} ---")
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(600)
                await select_category(page, category)
                total_counters.add(
                    await download_all_for_category(
                        page,
                        context,
                        category,
                        full_scan=full_scan,
                        pdf_only=pdf_only,
                        scrape_session=scrape_session,
                        scrape_enabled=effective_ui_scrape,
                        scrape_max_scroll_seconds=SCRAPE_MAX_SCROLL_SECONDS,
                        scrape_limit=SCRAPE_LIMIT,
                    )
                )
            logger.info("Mass download complete.")
        finally:
            if scrape_page is not None:
                await scrape_page.close()
            await context.close()
            await browser.close()

    duration = time.time() - t0
    logger.info(f"Collection finished in {duration:.1f}s ({mode} mode)")
    return total_counters.to_summary(duration_seconds=duration)


async def run():
    """Legacy entry point — calls run_collection() with module-level config."""
    await run_collection()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trademark bulletin collector")
    parser.add_argument(
        "--full", action="store_true",
        help="Full scan: scroll through ALL bulletins (slow, ~12h). "
             "Default is incremental: only check for new bulletins (~2-5 min)."
    )
    parser.add_argument(
        "--pdf-only", action="store_true",
        help="Download only PDF files, skipping CD/ZIP items. Implies --full. "
             "Use to backfill PDFs for existing bulletins that only have ZIP extracts."
    )
    parser.add_argument(
        "--no-ui-scrape",
        action="store_true",
        help="Disable the secondary trademark search UI scraper for this run.",
    )
    parser.add_argument(
        "--scrape-max-scroll-seconds",
        type=int,
        default=None,
        help="Override max scroll time for the UI scraper (0 means unlimited).",
    )
    parser.add_argument(
        "--scrape-limit",
        type=int,
        default=None,
        help="Override max row limit for the UI scraper (0 means unlimited).",
    )
    args = parser.parse_args()
    asyncio.run(
        run_collection(
            full_scan=args.full,
            pdf_only=args.pdf_only,
            enable_ui_scrape=False if args.no_ui_scrape else None,
            scrape_max_scroll_seconds=args.scrape_max_scroll_seconds,
            scrape_limit=args.scrape_limit,
        )
    )
