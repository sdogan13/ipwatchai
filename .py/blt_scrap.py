# Search by Marka Ilan Bulten No (Bulletin Number), load full rows, and extract data to JSON.
# This version uses the original robust scrolling/jiggle logic for data capture.
# Updated to save JSON files to a specific directory structure.
# Includes persistence logic to continue scrolling until Expected Total is reached.
# TIMEOUT DISABLED BY DEFAULT (runs until completion).
# PERFORMANCE TUNED: Uses JS-side execution for scraping and position checks (10x faster loop).

from __future__ import annotations

import argparse
import base64
import re
import sys
import time
import json
import os
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

# --- DIRECTORY CONFIGURATION ---
# Define the root directory. Subdirectories will be created dynamically based on Bulletin No.
ROOT_DIR = Path(os.getenv("DATA_ROOT", r"C:\Users\sdogan\turk_patent\bulletins\Marka"))

URL = "https://www.turkpatent.gov.tr/arastirma-yap?form=trademark"


def log(msg: str) -> None:
    """Helper function for consistent logging to stdout."""
    print(msg, flush=True)


def safe_mkdir(p: Path) -> None:
    """Ensures directory exists."""
    p.mkdir(parents=True, exist_ok=True)


def normalize_int(s: str) -> int:
    """Extracts digits from a string and converts to int."""
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else 0


def try_click(locator, timeout_ms: int = 1500) -> bool:
    """Attempts to click an element, failing gracefully on timeout."""
    try:
        locator.first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def close_cookie_banner(page) -> None:
    """Identifies and closes common cookie or GDPR banners."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"kabul|accept|tamam|ok", re.I)),
        page.get_by_role("button", name=re.compile(r"anlad[ıi]m", re.I)),
        page.locator("button:has-text('Kabul')"),
        page.locator("button:has-text('Accept')"),
    ]
    for c in candidates:
        if try_click(c, 1200):
            return


def ensure_marka_arastirma_tab(page) -> None:
    """Ensures the correct research tab is selected."""
    try:
        page.get_by_text(re.compile(r"Marka\s*Araştırma|Marka\s*Arastirma", re.I)).first.click(timeout=2000)
    except Exception:
        pass


def locate_bulten_no_input(page):
    """
    Locates the input field for 'Marka İlan Bülten No'.
    Includes logic to expand 'Detaylı Arama' if the field is not initially visible.
    """
    label_variants = ["Marka İlan Bülten No", "Bülten No"]

    def _find_input_element():
        for lab in label_variants:
            loc = page.locator(f"xpath=//mat-form-field[.//*[contains(normalize-space(.), {repr(lab)})]]//input")
            if loc.count() > 0:
                return loc.first
        
        # Fallback selectors
        loc = page.locator(
            "input:visible[placeholder*='Bülten No' i], input:visible[aria-label*='Bülten No' i]"
        )
        if loc.count() > 0:
            return loc.first
        return None

    # 1. Try to find it immediately
    target_input = _find_input_element()
    
    # 2. If found and visible, return it
    if target_input and target_input.is_visible():
        return target_input

    # 3. If not visible, try clicking "Detaylı Arama"
    log("[INFO] Expanding 'Detaylı Arama' to find Bulletin Number input...")
    try:
        # Try various ways to click the expansion panel
        detayli_btn = page.locator("text='Detaylı Arama'").first
        if detayli_btn.count() > 0:
            detayli_btn.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass

    # 4. Try to find it again after expansion
    target_input = _find_input_element()
    if target_input:
        return target_input

    return None


def click_sorgula(page) -> None:
    """Clicks the search button."""
    candidates = [
        page.get_by_role("button", name=re.compile(r"Sorgula", re.I)),
        page.locator("button:has-text('SORGULA')"),
        page.locator("button:has-text('Sorgula')"),
    ]
    for b in candidates:
        if try_click(b, 8000):
            return
    raise RuntimeError("Could not click SORGULA.")


def ensure_sonsuz_liste_on(page) -> bool:
    """Original Logic: Enables 'Sonsuz Liste' switch using role, checkbox, or container click."""
    log("[INFO] Checking 'Sonsuz Liste' switch...")
    try:
        sw = page.get_by_role("switch", name=re.compile(r"Sonsuz\s*Liste", re.I))
        if sw.count() > 0:
            aria = (sw.first.get_attribute("aria-checked") or "").lower()
            if aria != "true":
                log("[INFO] Switch found (role). Toggling ON.")
                sw.first.click(force=True)
                page.wait_for_timeout(800)
            else:
                log("[INFO] Switch found (role) and already ON.")
            return True
    except Exception: pass

    try:
        chk = page.locator("div:has-text('Sonsuz Liste') input[type=checkbox]").first
        if chk.count() > 0:
            try:
                if not chk.is_checked():
                    log("[INFO] Checkbox found. Toggling ON.")
                    try: chk.check()
                    except Exception: page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                    page.wait_for_timeout(800)
                else:
                    log("[INFO] Checkbox found and already ON.")
            except Exception:
                log("[WARN] Checkbox interaction failed. Clicking container.")
                page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                page.wait_for_timeout(800)
            return True
    except Exception: pass

    return False


def read_total_count(page) -> Optional[int]:
    """Reads the total record count from the results page."""
    try:
        loc = page.locator("xpath=//*[contains(translate(.,'ıİ','iI'),'kayit bulundu') or contains(., 'kayıt bulundu')]").first
        t = loc.inner_text(timeout=2500)
        m = re.search(r"([\d\.\,]+)\s*kay[ıi]t\s*bulundu", t, re.I)
        if m:
            return normalize_int(m.group(1))
    except Exception: pass

    try:
        body_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        patterns = [
            r"([\d\.\,]+)\s*kay[ıi]t\s*bulundu",
            r"Toplam\s*[:\-]?\s*([\d\.\,]+)\s*kay[ıi]t",
        ]
        for pat in patterns:
            m = re.search(pat, body_text, re.I)
            if m:
                n = normalize_int(m.group(1))
                if n > 0: return n
    except Exception: pass
    return None


def detect_grid(page) -> Tuple[str, str]:
    """Detects which type of data grid/table the site is currently using."""
    dx = page.locator(".dx-datagrid")
    if dx.count() > 0:
        return ("css=.dx-datagrid-rowsview .dx-data-row", "css=.dx-datagrid-rowsview")
    cdk = page.locator("cdk-virtual-scroll-viewport")
    if cdk.count() > 0:
        return ("css=cdk-virtual-scroll-viewport .cdk-virtual-scroll-content-wrapper > *", "css=cdk-virtual-scroll-viewport")
    return ("css=table tbody tr", "css=body")


def get_last_row_position(page, row_sel: str) -> int:
    """Finds the index of the last visible row using pure JS (10x Faster)."""
    # Optimized: Runs inside browser, avoids Playwright locator roundtrips
    return page.evaluate("""(sel) => {
        const css = sel.replace('css=', '');
        const rows = document.querySelectorAll(css);
        if (rows.length === 0) return 0;
        const last = rows[rows.length - 1];
        
        # Try all common index attributes
        const idx = last.getAttribute('aria-rowindex') || 
                    last.getAttribute('data-rowindex') || 
                    last.getAttribute('data-idx');
        
        if (idx) return parseInt(idx);
        return rows.length;
    }""", row_sel)


def _is_body_scroll(scroll_target_sel: str) -> bool:
    s = (scroll_target_sel or "").lower().strip()
    return s in ("css=body", "body", "css=html", "html", "css=document", "document")


def js_scroll_to_bottom(page, scroll_target_sel: str, offset: int = 0) -> None:
    if _is_body_scroll(scroll_target_sel):
        page.evaluate("""(off) => {
            const el = document.scrollingElement || document.documentElement;
            if (!el) return;
            const maxTop = Math.max(0, el.scrollHeight - el.clientHeight - off);
            el.scrollTop = maxTop;
            el.dispatchEvent(new Event('scroll', { bubbles: true }));
        }""", offset)
        return
    loc = page.locator(scroll_target_sel).first
    loc.evaluate("""(el, off) => {
        try {
          const tgt = el.querySelector('.dx-scrollable-container') || el;
          const maxTop = Math.max(0, tgt.scrollHeight - tgt.clientHeight - off);
          tgt.scrollTop = maxTop;
          tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
        } catch(e) {}
    }""", offset)


def js_scroll_by(page, scroll_target_sel: str, dy: int) -> None:
    if _is_body_scroll(scroll_target_sel):
        page.evaluate("""(dy) => {
            const el = document.scrollingElement || document.documentElement;
            if (!el) return;
            el.scrollTop = el.scrollTop + dy;
            el.dispatchEvent(new Event('scroll', { bubbles: true }));
        }""", dy)
        return
    loc = page.locator(scroll_target_sel).first
    loc.evaluate("""(el, dy) => {
        try {
          const tgt = el.querySelector('.dx-scrollable-container') || el;
          tgt.scrollTop = tgt.scrollTop + dy;
          tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
        } catch(e) {}
    }""", dy)


def wheel_inside_scroll_target(page, scroll_target_sel: str, delta_y: int) -> None:
    try:
        if _is_body_scroll(scroll_target_sel):
            vp = page.viewport_size
            if vp: page.mouse.move(vp["width"] / 2, vp["height"] / 2)
            page.mouse.wheel(0, delta_y)
            return
        base = page.locator(scroll_target_sel).first
        inner = base.locator(".dx-scrollable-container").first
        target = inner if inner.count() > 0 else base
        box = target.bounding_box()
        if box:
            x = box["x"] + box["width"] / 2
            y = box["y"] + min(box["height"] - 5, box["height"] / 2)
            page.mouse.move(x, y)
            try: page.mouse.click(x, y)
            except Exception: pass
        page.mouse.wheel(0, delta_y)
    except Exception:
        try: page.mouse.wheel(0, delta_y)
        except Exception: pass


def wait_until_position_changes(page, row_sel: str, prev_pos: int, timeout_s: float = 10.0) -> int:
    deadline = time.time() + timeout_s
    best = prev_pos
    while time.time() < deadline:
        # Check every 0.05s - now very cheap because get_last_row_position uses JS
        time.sleep(0.05)
        cur = get_last_row_position(page, row_sel)
        if cur > best: return cur
        best = max(best, cur)
    return best


def jiggle_recovery(page, scroll_target_sel: str, stagnation: int) -> None:
    log("    [DEBUG] Jiggle Strategy Activated")
    up_amount = -225
    down_amount = 2600
    wheel_inside_scroll_target(page, scroll_target_sel, delta_y=up_amount)
    time.sleep(1.0)
    js_scroll_to_bottom(page, scroll_target_sel, offset=0)
    time.sleep(0.2)
    try:
        if _is_body_scroll(scroll_target_sel): page.keyboard.press("End")
    except Exception: pass
    wheel_inside_scroll_target(page, scroll_target_sel, delta_y=down_amount)


def scrape_current_view(page, row_sel: str, data_store: Dict[str, List[str]]):
    """Scrapes visible data using pure JS (Bulk Scrape) for max speed."""
    try:
        # Executes one single JS function to get all text. 
        # Replaces 50+ network roundtrips with 1.
        new_data = page.evaluate("""(sel) => {
            const css = sel.replace('css=', '');
            const rows = Array.from(document.querySelectorAll(css));
            return rows.map(row => {
                const cells = Array.from(row.querySelectorAll("td, div[role='gridcell']"));
                return cells.map(c => c.innerText.trim());
            });
        }""", row_sel)
        
        for row_data in new_data:
            if any(row_data):
                # Dedup key logic remains same
                key = "|".join(row_data[1:min(5, len(row_data))])
                data_store[key] = row_data
    except Exception: pass


def scroll_and_capture(page, row_sel: str, scroll_target_sel: str, max_seconds: int, limit: int = 0) -> List[List[str]]:
    """Original Technique Logic: Drives UI scroll while scraping data."""
    captured_data: Dict[str, List[str]] = {}
    time.sleep(0.5) 
    
    # Attempt to read total immediately
    total = read_total_count(page)
    if total:
        log(f"[INFO] Expected total: {total}")
    else:
        log("[INFO] Expected total: UNKNOWN (will keep checking)")

    start_t = time.time()
    last_pos = get_last_row_position(page, row_sel)
    stagnation = 0

    try: wheel_inside_scroll_target(page, scroll_target_sel, delta_y=1)
    except Exception: pass

    while True:
        # Retry reading total if not found yet
        if not total:
            total = read_total_count(page)
            if total:
                log(f"[INFO] Expected total detected: {total}")

        scrape_current_view(page, row_sel, captured_data)
        current_len = len(captured_data)
        
        # Stop conditions
        if total and current_len >= total:
            log(f"[INFO] Reached target count: {current_len}/{total}")
            break
        
        if limit > 0 and current_len >= limit:
            log(f"[INFO] Reached user limit: {limit}")
            break
        
        if max_seconds > 0 and (time.time() - start_t > max_seconds):
            log(f"[WARN] Max execution time ({max_seconds}s) reached.")
            break

        prev = last_pos
        
        # SUPER FAST MICRO-BOUNCE
        js_scroll_to_bottom(page, scroll_target_sel, offset=80)
        time.sleep(0.02)
        js_scroll_by(page, scroll_target_sel, dy=-260)
        time.sleep(0.02)
        js_scroll_to_bottom(page, scroll_target_sel, offset=0)
        time.sleep(0.02)
        wheel_inside_scroll_target(page, scroll_target_sel, delta_y=2800)

        # CHANGED: Reduced timeout from 7.5s to 2.5s. 
        # If UI doesn't react in 2.5s, we should just try scrolling again.
        new_pos = wait_until_position_changes(page, row_sel, prev, timeout_s=2.5)

        if new_pos <= prev:
            stagnation += 1
            
            if total and current_len >= total: break
            
            log(f"[WARN] No growth (stagnation={stagnation}). last_pos={last_pos} count={current_len}/{total if total else '?'}")
            
            # Aggressive recovery mechanics
            js_scroll_by(page, scroll_target_sel, dy=-650 - (stagnation * 90))
            time.sleep(0.15) 
            js_scroll_to_bottom(page, scroll_target_sel, offset=0)
            time.sleep(0.05) 
            wheel_inside_scroll_target(page, scroll_target_sel, delta_y=3200)
            
            new_pos2 = wait_until_position_changes(page, row_sel, prev, timeout_s=3.0) # Reduced from 5.0
            new_pos = max(new_pos, new_pos2)
            if new_pos <= prev:
                jiggle_recovery(page, scroll_target_sel, stagnation)
                new_pos3 = wait_until_position_changes(page, row_sel, prev, timeout_s=4.0) # Reduced from 6.0
                new_pos = max(new_pos, new_pos3)
            
            threshold = 200 if (total and current_len < total) else 25
            
            if stagnation > threshold:
                log(f"[WARN] Stagnation limit ({threshold}) reached. Aborting scroll.")
                break
        else:
            stagnation = 0
            last_pos = new_pos
            log(f"[INFO] Captured {current_len} rows...")

    return list(captured_data.values())


def save_to_json(data: List[List[str]], filepath: Path):
    """Saves the captured data to a JSON file following the specified schema."""
    if not data:
        log("[WARN] No data to save.")
        return

    # Double check directory existence before final write
    safe_mkdir(filepath.parent)

    json_output = []
    
    for row in data:
        # Mapping indices (skipping counter at index 0):
        # [1] Application No, [2] Trademark Name, [3] Holder Name, 
        # [4] App Date, [5] Reg No, [6] Status, [7] NICE Classes
        
        app_no = row[1] if len(row) > 1 else ""
        name = row[2] if len(row) > 2 else ""
        holders_raw = row[3] if len(row) > 3 else ""
        app_date = row[4] if len(row) > 4 else ""
        reg_no = row[5] if len(row) > 5 else ""
        status = row[6] if len(row) > 6 else ""
        classes_raw = row[7] if len(row) > 7 else ""
        
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
                "TM_TYPE_CODE": "null",
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
        json_output.append(item)

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(json_output, f, ensure_ascii=False, indent=2)
        log(f"[SUCCESS] Data saved to JSON format: {filepath}")
    except Exception as e:
        log(f"[ERROR] Failed to save JSON: {e}")


def process_brand(context, page, bulten_no: str, out_dir: Path, max_scroll_seconds: int, limit: int):
    # Updated variable name for clarity, though logic treats it as generic search term
    log(f"\n[INFO] Starting search for Bulletin No: {bulten_no}")
    page.goto(URL, wait_until="domcontentloaded")
    close_cookie_banner(page)
    ensure_marka_arastirma_tab(page)

    # Use the new locator function for Bulletin Number
    inp = locate_bulten_no_input(page)
    if not inp: raise RuntimeError("Input field for Bulletin No not found (even after Detailed Search check).")
    
    inp.click()
    inp.fill(bulten_no)
    inp.press("Enter")
    page.wait_for_timeout(1000)
    click_sorgula(page)

    row_sel, scroll_target_sel = detect_grid(page)
    page.wait_for_selector(row_sel, timeout=20000)
    ensure_sonsuz_liste_on(page)

    # Scrape data live using the original robust scroll technique
    all_rows = scroll_and_capture(page, row_sel, scroll_target_sel, max_scroll_seconds, limit)
    
    # Ensure the target directory exists
    safe_mkdir(out_dir)

    # Save as metadata.json inside the folder (overwriting if exists)
    json_path = out_dir / "metadata.json"
    save_to_json(all_rows, json_path)


def main():
    ap = argparse.ArgumentParser()
    # Updated help text, but keeping argument name to match previous interface compatibility
    ap.add_argument("--names", type=str, nargs="+", default=["sedo"], help="Bulletin Numbers to search")
    ap.add_argument("--limit", type=int, default=0, help="Max rows")
    ap.add_argument("--headless", action="store_true", help="Run headless")
    # CHANGED: Default timeout changed from 1200 to 0 (infinite)
    ap.add_argument("--max-scroll-seconds", type=int, default=0, help="Max time (0 for infinite)")
    args = ap.parse_args()

    # NOTE: SCRAPED_DATA_DIR constant removed to allow dynamic directory creation per bulletin number

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        for val in args.names:
            # Create a safe directory name from the bulletin number
            safe_val = re.sub(r'[^\w\s-]', '', val).strip().replace(' ', '_')
            dynamic_out_dir = ROOT_DIR / f"BLT_{safe_val}"

            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()
            try: 
                # Pass the dynamic_out_dir to the processor
                process_brand(context, page, val, dynamic_out_dir, args.max_scroll_seconds, args.limit)
            except Exception as e: 
                log(f"[ERROR] Failed {val}: {e}")
            finally: 
                context.close()
        browser.close()


if __name__ == "__main__":
    main()