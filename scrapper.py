import time
import logging
import json
import os
import re
import requests
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

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

class TurkPatentScraper:
    def __init__(self, headless: bool = True):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.headless = headless
        self.url = "https://www.turkpatent.gov.tr/arastirma-yap?form=trademark"
        
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
        import random
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

    # --- COMPONENT LOGIC ---

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _safe_goto(self, url: str):
        """Navigates to a URL with exponential backoff retries."""
        logging.info(f"   [NETWORK] Attempting to load {url}...")
        self.page.goto(url, wait_until="domcontentloaded")

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

    def save_to_json(self, data: List[List[str]]):
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
        self._safe_goto(self.url)
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
        primary_rows = self._do_search(trademark_name, effective_limit, max_scroll_seconds)

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

        fallback_rows = self._do_search(fallback_query, effective_limit, max_scroll_seconds)
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
