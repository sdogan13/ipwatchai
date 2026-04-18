"""
Targeted GZ gazette PDF downloader — Phase-based approach.

Phase 1: Scroll + collect all GZ card metadata (no clicks)
Phase 2: For each needed GZ, open menu/click to get download URL
         If click corrupts page, restart browser and skip to next
Phase 3: Download all collected URLs via requests

Usage:
    python scripts/download_gz_targeted.py                    # all missing GZ
    python scripts/download_gz_targeted.py --visible          # show browser
    python scripts/download_gz_targeted.py --range 434 488    # specific range
    python scripts/download_gz_targeted.py --urls-only        # just collect URLs
"""
import asyncio
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_collection import (
    DOWNLOAD_LABEL_SELECTOR, maybe_dismiss_overlays, select_category,
    collect_download_clickables, extract_card_metadata,
    open_download_menu, list_menu_items, force_close_menus, MENU_WAIT_MS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CHUNK_SIZE = 256 * 1024

if sys.platform == "win32":
    try:
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except Exception:
        pass


def find_gz_folder(gz_no: int) -> Path | None:
    for d in BULLETINS_ROOT.iterdir():
        if d.is_dir() and (d.name == f"GZ_{gz_no}" or d.name.startswith(f"GZ_{gz_no}_")):
            return d
    return None


def has_pdf(folder: Path) -> bool:
    if not folder or not folder.exists():
        return False
    return any(f.suffix.lower() == ".pdf" and f.stat().st_size > 10000 for f in folder.iterdir())


def download_file(url: str, target: Path) -> bool:
    tmp = target.with_suffix(".pdf.part")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        with requests.Session() as s:
            s.headers.update(headers)
            with s.get(url, stream=True, allow_redirects=True, timeout=(30, 600)) as r:
                if r.status_code >= 400:
                    logger.warning(f"  HTTP {r.status_code}")
                    return False
                ct = r.headers.get("content-type", "").lower()
                if "text/html" in ct:
                    logger.warning(f"  Got HTML instead of PDF")
                    return False
                total = 0
                last_log = time.time()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        total += len(chunk)
                        if time.time() - last_log >= 30:
                            logger.info(f"    ... {total / 1024 / 1024:.1f} MB downloaded")
                            last_log = time.time()
        if tmp.stat().st_size < 10000:
            logger.warning(f"  File too small ({tmp.stat().st_size} bytes)")
            tmp.unlink(missing_ok=True)
            return False
        tmp.rename(target)
        size_mb = target.stat().st_size / 1e6
        logger.info(f"  Saved: {target.name} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        logger.error(f"  Download failed: {e}")
        tmp.unlink(missing_ok=True)
        return False


async def init_page(p, headless=True):
    """Create browser, page, navigate to bulletins, select Marka, scroll to load all."""
    browser = await p.chromium.launch(headless=headless, slow_mo=50)
    context = await browser.new_context(accept_downloads=True, viewport={"width": 1400, "height": 900})
    page = await context.new_page()

    await page.goto(TARGET_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await maybe_dismiss_overlays(page)
    await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)
    await select_category(page, "Marka")
    await page.wait_for_timeout(1000)

    # Scroll to load all cards
    last_height = 0
    stall = 0
    scroll_n = 0
    while stall < 10:
        scroll_n += 1
        await page.mouse.wheel(0, 5000)
        await page.wait_for_timeout(2000)
        h = await page.evaluate("document.body.scrollHeight")
        if h == last_height:
            stall += 1
        else:
            stall = 0
            last_height = h

    total = await page.locator(DOWNLOAD_LABEL_SELECTOR).count()
    logger.info(f"Page loaded: {total} cards in {scroll_n} scrolls")
    return browser, context, page


async def get_gz_card_index(page, gz_needed: set[int]) -> list[dict]:
    """Scan all cards and return metadata for GZ cards we need."""
    clickables = await collect_download_clickables(page)
    count = await clickables.count()
    results = []
    seen = set()

    for i in range(count):
        cl = clickables.nth(i)
        try:
            if not await cl.is_visible():
                continue
        except Exception:
            continue

        meta = await extract_card_metadata(cl)
        card_id = meta.get("id")
        is_gz = meta.get("is_gazette", False)
        card_date = meta.get("date")

        if not card_id or not is_gz or "-" in card_id:
            continue
        try:
            gz_num = int(card_id)
        except ValueError:
            continue
        if gz_num in seen or gz_num not in gz_needed:
            continue
        seen.add(gz_num)
        results.append({"gz_num": gz_num, "date": card_date, "index": i})

    logger.info(f"Found {len(results)} GZ cards matching our needs")
    return results


async def extract_url_for_card(page, clickable, gz_num: int) -> str | None:
    """Try to extract download URL for a single card. Returns URL or None."""
    try:
        await clickable.scroll_into_view_if_needed()
        await page.wait_for_timeout(300)
    except Exception:
        pass

    # Strategy 1: Open download menu
    try:
        menu = await open_download_menu(page, clickable)
        if menu:
            items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
            pdf_items = [it for it in items if not it.get("is_cd") and it.get("href")]
            if pdf_items:
                url = pdf_items[0]["href"]
                await force_close_menus(page)
                return url
            href_items = [it for it in items if it.get("href")]
            if href_items:
                url = href_items[0]["href"]
                await force_close_menus(page)
                return url
            await force_close_menus(page)
    except Exception:
        try:
            await force_close_menus(page)
        except Exception:
            pass

    # Strategy 2: expect_download with short timeout
    try:
        async with page.expect_download(timeout=10000) as dl_info:
            await clickable.click()
        dl = await dl_info.value
        url = dl.url
        await dl.cancel()
        logger.info(f"  GZ {gz_num}: got URL from download event")
        return url
    except Exception:
        pass

    # Check if page navigated to a file URL
    try:
        current = page.url
        if current != TARGET_URL and ("file" in current or "webim" in current):
            return current
    except Exception:
        pass

    return None


async def scrape_urls(gz_needed: set[int], headless: bool = True) -> dict[int, dict]:
    """Main scraping loop — extracts URLs one card at a time, restarting browser on failure."""
    from playwright.async_api import async_playwright

    found = {}  # gz_num -> {"url": ..., "date": ...}
    remaining = set(gz_needed)
    max_retries = 3

    async with async_playwright() as p:
        for attempt in range(max_retries):
            if not remaining:
                break

            logger.info(f"\n--- Scrape attempt {attempt+1}: {len(remaining)} GZ cards remaining ---")
            browser = context = page = None
            try:
                browser, context, page = await init_page(p, headless)
                card_index = await get_gz_card_index(page, remaining)

                if not card_index:
                    logger.warning("No matching GZ cards found on page")
                    break

                for card_info in card_index:
                    gz_num = card_info["gz_num"]
                    if gz_num not in remaining:
                        continue

                    logger.info(f"  GZ {gz_num} ({card_info['date']}): extracting URL...")

                    # Re-collect clickables (page state may have changed)
                    clickables = await collect_download_clickables(page)
                    count = await clickables.count()

                    if card_info["index"] >= count:
                        logger.warning(f"  GZ {gz_num}: card index out of range, skipping")
                        continue

                    clickable = clickables.nth(card_info["index"])

                    # Verify this is still the right card
                    try:
                        meta = await extract_card_metadata(clickable)
                        if meta.get("id") != str(gz_num):
                            logger.warning(f"  GZ {gz_num}: card shifted (now {meta.get('id')}), skipping for retry")
                            continue
                    except Exception:
                        continue

                    url = await extract_url_for_card(page, clickable, gz_num)

                    # Check if page was corrupted
                    try:
                        current_url = page.url
                    except Exception:
                        current_url = ""

                    if current_url != TARGET_URL:
                        logger.warning(f"  GZ {gz_num}: page navigated away, will restart browser")
                        if url and ("file" in url or "webim" in url):
                            found[gz_num] = {"url": url, "date": card_info["date"]}
                            remaining.discard(gz_num)
                            logger.info(f"  GZ {gz_num}: URL found (from navigation) -> {url[:80]}...")
                        break  # Restart browser

                    if url:
                        if not url.startswith("http"):
                            url = f"https://www.turkpatent.gov.tr{url}" if url.startswith("/") else url
                        found[gz_num] = {"url": url, "date": card_info["date"]}
                        remaining.discard(gz_num)
                        logger.info(f"  GZ {gz_num}: URL found -> {url[:80]}...")
                    else:
                        logger.warning(f"  GZ {gz_num}: no URL found")
                        remaining.discard(gz_num)  # Don't retry — card genuinely has no PDF

            except Exception as e:
                logger.error(f"  Scrape attempt {attempt+1} failed: {e}")
            finally:
                try:
                    if context:
                        await context.close()
                    if browser:
                        await browser.close()
                except Exception:
                    pass

    return found


async def main_async(args):
    start_no, end_no = args.range

    # Find which GZ numbers need PDFs
    needed = set()
    for gz_no in range(start_no, end_no + 1):
        folder = find_gz_folder(gz_no)
        if folder and has_pdf(folder):
            continue
        needed.add(gz_no)

    logger.info(f"GZ {start_no}-{end_no}: {len(needed)} missing PDFs")
    if not needed:
        logger.info("All GZ PDFs already downloaded!")
        return

    logger.info(f"Missing: {sorted(needed)}")

    # Check URL cache
    cache_file = BULLETINS_ROOT / "gz_download_urls.json"
    cached = {}
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        logger.info(f"Loaded {len(cached)} cached URLs")

    # Filter out already-cached URLs
    need_scrape = {n for n in needed if str(n) not in cached}
    if need_scrape:
        logger.info(f"Need to scrape URLs for {len(need_scrape)} gazettes")
        new_urls = await scrape_urls(need_scrape, headless=not args.visible)
        for gz_num, info in new_urls.items():
            cached[str(gz_num)] = info
        with open(cache_file, "w") as f:
            json.dump(cached, f, indent=2)
        logger.info(f"Saved {len(cached)} URLs to cache")
    else:
        logger.info("All URLs already cached")

    if args.urls_only:
        logger.info("URL collection complete (--urls-only)")
        return

    # Phase 3: Download
    stats = {"downloaded": 0, "failed": 0, "no_url": 0, "skipped": 0}

    for gz_no in sorted(needed):
        info = cached.get(str(gz_no))
        if not info:
            logger.warning(f"GZ {gz_no}: no URL available")
            stats["no_url"] += 1
            continue

        url = info["url"] if isinstance(info, dict) else info
        card_date = info.get("date") if isinstance(info, dict) else None

        folder = find_gz_folder(gz_no)
        if not folder:
            folder_name = f"GZ_{gz_no}_{card_date}" if card_date else f"GZ_{gz_no}"
            folder = BULLETINS_ROOT / folder_name
            folder.mkdir(parents=True, exist_ok=True)

        if has_pdf(folder):
            stats["skipped"] += 1
            continue

        target = folder / "bulletin.pdf"
        logger.info(f"GZ {gz_no}: downloading to {folder.name}/bulletin.pdf...")

        # Handle multi-UUID URLs
        base_match = re.match(r"(https?://[^/]+/file/)([^?]+)\??(.*)", url)
        if base_match:
            file_ids = base_match.group(2)
            uuids = [u.strip() for u in file_ids.split(",") if u.strip()]
            if len(uuids) > 1:
                base_url = base_match.group(1)
                query = base_match.group(3)
                logger.info(f"  Multi-UUID ({len(uuids)} parts), probing...")
                best_url = None
                for uid in uuids:
                    part_url = f"{base_url}{uid}{'?' + query if query else ''}"
                    try:
                        r = requests.head(part_url, timeout=10, allow_redirects=True,
                                          headers={"User-Agent": "Mozilla/5.0"})
                        ct = r.headers.get("content-type", "").lower()
                        if "pdf" in ct:
                            best_url = part_url
                            break
                    except Exception:
                        continue
                if best_url:
                    url = best_url
                else:
                    logger.warning(f"  GZ {gz_no}: no PDF in multi-UUID")
                    stats["no_url"] += 1
                    continue

        ok = download_file(url, target)
        if ok:
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1

    logger.info(f"\nFinal stats: {json.dumps(stats)}")


def main():
    parser = argparse.ArgumentParser(description="Download GZ gazette PDFs")
    parser.add_argument("--range", nargs=2, type=int, default=[434, 499])
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--urls-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
