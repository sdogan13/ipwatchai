import os
import re
import asyncio
import logging
import time
import argparse
from pathlib import Path
from typing import Optional, List, Set, Dict, Any
from urllib.parse import unquote, urljoin

import requests
from playwright.async_api import async_playwright

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

SLOW_MO_MS = 150
VIEWPORT = {"width": 1400, "height": 900}

# Incremental mode: stop scrolling after this many consecutive existing cards
INCREMENTAL_LOOKBACK = 5

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

def check_local_existence(folder: str, card_id: str, is_gazette: bool,
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

async def process_card(page, context, clickable, category_folder: str, card_id: str,
                       card_date: Optional[str], is_gazette: bool,
                       pdf_only: bool = False) -> int:

    # --- HELPER: Construct target filename ---
    # Bulletin: {ID}_CD_{Date}
    # Gazette:  {ID}_Gazete_CD_{Date}
    # Note: Using .bin suffix for download path setup, but actual file takes extension
    def make_target_name(is_cd_file: bool) -> str:
        parts = [card_id]
        if is_gazette: parts.append("Gazete")
        if is_cd_file: parts.append("CD")
        if card_date: parts.append(card_date)
        return "_".join(parts)

    # Quick pre-check: skip cards that already exist locally (avoids slow menu open)
    if check_local_existence(category_folder, card_id, is_gazette, pdf_only=pdf_only):
        return 0

    menu = await open_download_menu(page, clickable)
    if not menu:
        # Fallback for Direct Click (Usually non-CD)
        fname = make_target_name(is_cd_file=False)
        if check_local_existence(category_folder, card_id, is_gazette, pdf_only=pdf_only):
            return 0
        logger.info(f"[*] Card {card_id}: menu did not open -> direct click download")
        ok = await direct_click_download(page, clickable, os.path.join(category_folder, f"{fname}.bin"))
        return 1 if ok else 0

    items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
    cd_items = [it for it in items if it.get("is_cd")]
    numbered_items = [it for it in items if (not it.get("is_cd") and it.get("key"))]
    is_grouped = len(numbered_items) > 1

    if not is_grouped:
        # In pdf_only mode, skip CD items entirely — we only want the PDF
        if cd_items and not pdf_only:
            fname = make_target_name(is_cd_file=True)
            if check_local_existence(category_folder, card_id, is_gazette, pdf_only=pdf_only):
                try: await force_close_menus(page)
                except Exception: pass
                return 0
            logger.info(f"[*] {card_id}: CD found -> downloading")
            ok = await download_one_item(page, context, clickable, cd_items[0], category_folder, fname)
            try: await force_close_menus(page)
            except Exception: pass
            return 1 if ok else 0

        # No CD available (or pdf_only mode), try normal item (usually the PDF)
        fname = make_target_name(is_cd_file=False)
        if check_local_existence(category_folder, card_id, is_gazette, pdf_only=pdf_only):
            try: await force_close_menus(page)
            except Exception: pass
            return 0
        pick = numbered_items[0] if numbered_items else (items[0] if items else None)
        if not pick:
            try: await force_close_menus(page)
            except Exception: pass
            return 0

        logger.info(f"[*] {card_id}: {'pdf_only mode -> ' if pdf_only else 'no CD -> '}downloading PDF/Other")
        ok = await download_one_item(page, context, clickable, pick, category_folder, fname)
        try: await force_close_menus(page)
        except Exception: pass
        return 1 if ok else 0

    # GROUPED (e.g. 103-106)
    # First check if the parent card_id already has data locally
    if check_local_existence(category_folder, card_id, is_gazette, pdf_only=pdf_only):
        logger.info(f"[*] Group card {card_id}: already exists locally, skipping all sub-items")
        try: await force_close_menus(page)
        except Exception: pass
        return 0

    logger.info(f"[*] Group card {card_id}: downloading {len(numbered_items)} items")
    ok_count = 0
    href_first = [it for it in numbered_items if it.get("href")]
    click_later = [it for it in numbered_items if not it.get("href")]

    for it in href_first + click_later:
        # Grouped items usually just map to the item's key/text
        sub_id = it.get("key") or it["stem"]

        # Check existence for THIS specific sub-item
        if check_local_existence(category_folder, sub_id, is_gazette, pdf_only=pdf_only):
            continue
            
        # Naming: {SubID}_...
        is_sub_cd = it.get("is_cd", False)
        sub_parts = [sub_id]
        if is_gazette: sub_parts.append("Gazete")
        if is_sub_cd: sub_parts.append("CD")
        if card_date: sub_parts.append(card_date)
        item_prefix = "_".join(sub_parts)
        
        logger.info(f"    -> {it['text']}")
        ok = await download_one_item(page, context, clickable, it, category_folder, item_prefix)
        ok_count += 1 if ok else 0

    try: await force_close_menus(page)
    except Exception: pass
    return ok_count


# ----------------------------
# Category loop
# ----------------------------
async def download_all_for_category(page, context, category_name: str,
                                     full_scan: bool = False,
                                     pdf_only: bool = False) -> int:
    category_folder = os.path.join(BASE_DOWNLOAD_DIR, slugify(category_name))
    os.makedirs(category_folder, exist_ok=True)

    done_cards: Set[str] = set()
    last_height = 0
    stall_rounds = 0

    # Incremental mode tracking: consecutive existing cards per type
    consecutive_existing_blt = 0
    consecutive_existing_gz = 0
    downloaded_count = 0

    while True:
        clickables = await collect_download_clickables(page)
        count = await clickables.count()
        logger.info(f"Visible cards (İNDİR): {count}")

        new_files_in_pass = 0

        for i in range(count):
            clickable = clickables.nth(i)
            try:
                if not await clickable.is_visible(): continue
            except Exception: continue

            # Extract Metadata
            meta = await extract_card_metadata(clickable)
            card_id = meta.get("id")
            card_date = meta.get("date")
            is_gz = meta.get("is_gazette")

            # Dedup by (id, type) — BLT 484 and GZ 484 are different cards
            dedup_key = f"{card_id}_{'GZ' if is_gz else 'BLT'}"
            if not card_id or dedup_key in done_cards: continue

            suffix_log = " (Gazete)" if is_gz else " (Bülten)"
            date_log = f" [{card_date}]" if card_date else ""

            # Incremental mode: check if we already have this card locally
            if not full_scan:
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

            got = await process_card(page, context, clickable, category_folder, card_id, card_date, is_gz,
                                    pdf_only=pdf_only)
            new_files_in_pass += got
            downloaded_count += got
            done_cards.add(dedup_key)

            await page.wait_for_timeout(200)

        # Incremental mode: stop if we've seen enough consecutive existing cards
        if not full_scan:
            if (consecutive_existing_blt >= INCREMENTAL_LOOKBACK and
                    consecutive_existing_gz >= INCREMENTAL_LOOKBACK):
                logger.info(
                    f"Incremental stop: {consecutive_existing_blt} consecutive existing BLT "
                    f"and {consecutive_existing_gz} consecutive existing GZ cards. "
                    f"Downloaded {downloaded_count} new files."
                )
                break

        height = await page.evaluate("document.body.scrollHeight")
        # Logic to detect end of scroll (no new files and height didn't change)
        if height == last_height and new_files_in_pass == 0:
            stall_rounds += 1
        else:
            stall_rounds = 0
            last_height = height

        if stall_rounds >= 8:
            logger.info(f"Reached end of category '{category_name}'")
            break

        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(2000)

    return downloaded_count


# ----------------------------
# Main
# ----------------------------
async def run_collection(settings=None, full_scan: bool = False,
                         pdf_only: bool = False) -> dict:
    """
    Run bulletin collection. Returns summary dict.

    Args:
        settings: Optional PipelineSettings override. If None, uses module-level config.
        full_scan: If True, scroll through ALL bulletins (original behavior).
                   If False (default), stop after INCREMENTAL_LOOKBACK consecutive
                   existing cards per type — only downloads new bulletins.
        pdf_only:  If True, only download PDF files. Skips CD/ZIP items and only
                   considers a bulletin as "existing" if a PDF is already present.
                   Use with full_scan=True to backfill PDFs for all bulletins.

    Returns:
        { "downloaded": int, "skipped": int, "failed": int, "duration_seconds": float }
    """
    global TARGET_URL, BASE_DOWNLOAD_DIR, HEADLESS, CATEGORIES, READ_TIMEOUT

    if settings is not None:
        TARGET_URL = settings.turkpatent_url
        BASE_DOWNLOAD_DIR = str(Path(settings.bulletins_root).parent)
        HEADLESS = settings.headless_browser
        CATEGORIES = list(settings.categories)
        READ_TIMEOUT = settings.download_timeout

    mode = "PDF-ONLY FULL" if pdf_only else ("FULL" if full_scan else "INCREMENTAL")
    # pdf_only implies full_scan — must scroll through everything
    if pdf_only:
        full_scan = True
    t0 = time.time()
    os.makedirs(BASE_DOWNLOAD_DIR, exist_ok=True)
    logger.info(f"Files will be saved under: {os.path.abspath(BASE_DOWNLOAD_DIR)}")
    logger.info(f"Target URL: {TARGET_URL}")
    logger.info(f"Categories: {CATEGORIES}")
    logger.info(f"Headless: {HEADLESS}")
    logger.info(f"Mode: {mode} (lookback={INCREMENTAL_LOOKBACK})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO_MS)
        context = await browser.new_context(accept_downloads=True, viewport=VIEWPORT)
        page = await context.new_page()
        try:
            logger.info(f"Opening {TARGET_URL} ...")
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            await maybe_dismiss_overlays(page)
            await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)

            total_downloaded = 0
            for category in CATEGORIES:
                logger.info(f"--- Category: {category} ---")
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(600)
                await select_category(page, category)
                total_downloaded += await download_all_for_category(page, context, category,
                                                                    full_scan=full_scan,
                                                                    pdf_only=pdf_only)
            logger.info("Mass download complete.")
        finally:
            await context.close()
            await browser.close()

    duration = time.time() - t0
    logger.info(f"Collection finished in {duration:.1f}s ({mode} mode)")
    return {
        "downloaded": total_downloaded,
        "skipped": 0,
        "failed": 0,
        "duration_seconds": round(duration, 1),
    }


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
    args = parser.parse_args()
    asyncio.run(run_collection(full_scan=args.full, pdf_only=args.pdf_only))
