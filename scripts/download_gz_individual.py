"""
Download individual GZ gazette PDFs that the main scraper missed.

The main scraper groups some GZ entries under bundled RAR ranges,
but many of these are also available as individual PDF downloads.

This script uses Playwright to navigate directly and find download URLs
for specific GZ numbers, then downloads the PDFs.

Usage:
    python scripts/download_gz_individual.py                    # all missing GZ 434-488
    python scripts/download_gz_individual.py --range 448 448    # single gazette
    python scripts/download_gz_individual.py --range 434 488    # specific range
    python scripts/download_gz_individual.py --visible          # show browser
"""
import asyncio
import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CHUNK_SIZE = 256 * 1024
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 600

# Force UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except Exception:
        pass


def find_gz_folder(gz_no: int) -> Path | None:
    """Find existing GZ folder for a gazette number."""
    for d in BULLETINS_ROOT.iterdir():
        if d.is_dir() and (d.name == f"GZ_{gz_no}" or d.name.startswith(f"GZ_{gz_no}_")):
            return d
    return None


def has_pdf(folder: Path) -> bool:
    """Check if folder has a PDF."""
    if not folder or not folder.exists():
        return False
    return any(f.suffix.lower() == ".pdf" and f.stat().st_size > 0 for f in folder.iterdir())


def download_file(url: str, target: Path) -> bool:
    """Download a URL to target path."""
    tmp = target.with_suffix(".pdf.part")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.Session() as s:
            s.headers.update(headers)
            with s.get(url, stream=True, allow_redirects=True,
                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
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

        # Handle multi-UUID URLs (pick PDF part)
        tmp.rename(target)
        logger.info(f"  Saved: {target.name} ({tmp.stat().st_size / 1e6:.1f} MB)" if target.exists()
                    else f"  Saved: {target.name}")
        return True
    except Exception as e:
        logger.error(f"  Download failed: {e}")
        tmp.unlink(missing_ok=True)
        return False


async def collect_gz_urls(gz_numbers: list[int], headless: bool = True) -> dict[int, str]:
    """Use Playwright to scrape download URLs for specific GZ numbers."""
    from playwright.async_api import async_playwright
    from data_collection import (
        extract_card_metadata, collect_download_clickables,
        maybe_dismiss_overlays, select_category,
        DOWNLOAD_LABEL_SELECTOR,
        open_download_menu, list_menu_items, force_close_menus,
        MENU_WAIT_MS,
    )

    needed = set(gz_numbers)
    found = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, slow_mo=50)
        context = await browser.new_context(accept_downloads=True,
                                            viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            logger.info(f"Opening {TARGET_URL}...")
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            await maybe_dismiss_overlays(page)
            await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(
                state="visible", timeout=20000)

            # Select Marka > Resmi Gazete (Gazette) category
            await select_category(page, "Marka")
            await page.wait_for_timeout(1000)

            last_height = 0
            stall_rounds = 0
            seen = set()

            while True:
                clickables = await collect_download_clickables(page)
                count = await clickables.count()

                for i in range(count):
                    clickable = clickables.nth(i)
                    try:
                        if not await clickable.is_visible():
                            continue
                    except Exception:
                        continue

                    meta = await extract_card_metadata(clickable)
                    card_id = meta.get("id")
                    is_gz = meta.get("is_gazette", False)

                    if not card_id or card_id in seen or not is_gz:
                        continue
                    seen.add(card_id)

                    # Skip bundled ranges
                    if "-" in card_id:
                        continue

                    try:
                        gz_num = int(card_id)
                    except ValueError:
                        continue

                    if gz_num not in needed:
                        continue

                    # Get download URL
                    url = None
                    try:
                        href = await clickable.get_attribute("href")
                        if href and ("download" in href.lower() or "file" in href.lower()):
                            url = href
                    except Exception:
                        pass

                    if not url:
                        try:
                            link = clickable.locator("a[href*='download'], a[href*='file']").first
                            if await link.count() > 0:
                                url = await link.get_attribute("href")
                        except Exception:
                            pass

                    if not url:
                        try:
                            menu = await open_download_menu(page, clickable)
                            if menu:
                                items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
                                pdf_items = [it for it in items
                                             if not it.get("is_cd") and it.get("href")]
                                if pdf_items:
                                    url = pdf_items[0]["href"]
                                elif items:
                                    href_items = [it for it in items if it.get("href")]
                                    if href_items:
                                        url = href_items[0]["href"]
                                await force_close_menus(page)
                                await page.wait_for_timeout(300)
                        except Exception:
                            try:
                                await force_close_menus(page)
                            except Exception:
                                pass

                    if url and not url.startswith("http"):
                        url = f"https://www.turkpatent.gov.tr{url}" if url.startswith("/") else url

                    if url:
                        found[gz_num] = url
                        logger.info(f"  Found GZ {gz_num}: {url[:80]}...")
                        # Stop if we have all
                        if needed <= set(found.keys()):
                            break

                # Stop if we have all
                if needed <= set(found.keys()):
                    break

                height = await page.evaluate("document.body.scrollHeight")
                if height == last_height:
                    stall_rounds += 1
                else:
                    stall_rounds = 0
                    last_height = height

                if stall_rounds >= 5:  # More patience than main scraper
                    break

                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(1500)

        finally:
            await context.close()
            await browser.close()

    return found


def main():
    parser = argparse.ArgumentParser(description="Download individual GZ gazette PDFs")
    parser.add_argument("--range", nargs=2, type=int, default=[434, 488],
                        help="GZ number range (start end)")
    parser.add_argument("--visible", action="store_true", help="Show browser")
    parser.add_argument("--urls-only", action="store_true", help="Just collect URLs, don't download")
    args = parser.parse_args()

    start_no, end_no = args.range

    # Find which GZ numbers are missing PDFs
    missing = []
    for gz_no in range(start_no, end_no + 1):
        folder = find_gz_folder(gz_no)
        if folder and has_pdf(folder):
            continue
        missing.append(gz_no)

    logger.info(f"GZ {start_no}-{end_no}: {len(missing)} missing PDFs")
    if not missing:
        logger.info("All GZ PDFs already downloaded!")
        return

    logger.info(f"Missing: {missing}")

    # Check if we already have URLs from previous scrape
    urls_file = BULLETINS_ROOT / "gz_individual_urls.json"
    cached_urls = {}
    if urls_file.exists():
        cached_urls = json.loads(urls_file.read_text())
        logger.info(f"Loaded {len(cached_urls)} cached URLs")

    # Find URLs for missing ones
    need_urls = [n for n in missing if str(n) not in cached_urls]
    if need_urls:
        logger.info(f"Need to scrape URLs for {len(need_urls)} gazettes")
        new_urls = asyncio.run(collect_gz_urls(need_urls, headless=not args.visible))
        for gz_no, url in new_urls.items():
            cached_urls[str(gz_no)] = url
        # Save cache
        with open(urls_file, "w") as f:
            json.dump(cached_urls, f, indent=2)
        logger.info(f"Saved {len(cached_urls)} URLs to cache")

    if args.urls_only:
        return

    # Download
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "no_url": 0}

    for gz_no in missing:
        url = cached_urls.get(str(gz_no))
        if not url:
            logger.warning(f"GZ {gz_no}: No URL found — may only be in bundled RAR")
            stats["no_url"] += 1
            continue

        folder = find_gz_folder(gz_no)
        if not folder:
            # Create folder
            folder = BULLETINS_ROOT / f"GZ_{gz_no}"
            folder.mkdir(parents=True, exist_ok=True)

        if has_pdf(folder):
            stats["skipped"] += 1
            continue

        target = folder / "bulletin.pdf"
        logger.info(f"GZ {gz_no}: Downloading to {folder.name}/bulletin.pdf...")

        # Handle multi-UUID URLs
        base_match = re.match(r"(https?://[^/]+/file/)([^?]+)\??(.*)", url)
        if base_match:
            base_url = base_match.group(1)
            file_ids = base_match.group(2)
            query = base_match.group(3)
            uuids = [u.strip() for u in file_ids.split(",") if u.strip()]

            if len(uuids) > 1:
                # Probe each to find PDF
                logger.info(f"  Multi-UUID ({len(uuids)} parts), probing...")
                best_url = None
                for uid in uuids:
                    part_url = f"{base_url}{uid}?{query}"
                    try:
                        r = requests.head(part_url, timeout=10, allow_redirects=True,
                                          headers={"User-Agent": "Mozilla/5.0"})
                        ct = r.headers.get("content-type", "").lower()
                        cl = int(r.headers.get("content-length", "0"))
                        if "pdf" in ct:
                            best_url = part_url
                            logger.info(f"  Found PDF part: {cl / 1e6:.0f} MB")
                            break
                    except Exception:
                        continue

                if best_url:
                    url = best_url
                else:
                    logger.warning(f"  No PDF part found in multi-UUID URL — all parts are RAR")
                    stats["no_url"] += 1
                    continue

        ok = download_file(url, target)
        if ok:
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1

    logger.info(f"Stats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
