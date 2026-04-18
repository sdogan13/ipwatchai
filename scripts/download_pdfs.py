"""
Download missing bulletin PDFs from turkpatent.gov.tr and place inside BLT folders.

Two-phase approach:
  Phase 1: Scroll the website, collect download URLs for all bulletins
  Phase 2: Download missing PDFs via HTTP and place in BLT_xxx/bulletin.pdf

Usage:
    python scripts/download_pdfs.py                 # full run
    python scripts/download_pdfs.py --distribute-only  # just move existing root PDFs into folders
    python scripts/download_pdfs.py --visible       # show browser
"""
import os
import re
import sys
import json
import shutil
import asyncio
import argparse
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"
TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
CHUNK_SIZE = 256 * 1024  # 256KB
CONNECT_TIMEOUT = 30
READ_TIMEOUT = 600


def find_folder(bulletin_no: str, is_gazette: bool = False,
                 root: Path = BULLETINS_ROOT) -> Optional[Path]:
    """Find BLT or GZ folder matching a bulletin number."""
    prefix = "GZ_" if is_gazette else "BLT_"
    for d in root.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith(f"{prefix}{bulletin_no}_") or d.name == f"{prefix}{bulletin_no}":
            return d
    return None


def has_pdf_in_folder(folder: Path) -> bool:
    """Check if a BLT folder already contains a PDF."""
    if not folder.exists():
        return False
    for f in folder.iterdir():
        if f.suffix.lower() == ".pdf" and f.stat().st_size > 0:
            return True
    return False


def has_root_pdf(bulletin_no: str, root: Path = BULLETINS_ROOT) -> Optional[Path]:
    """Check if there's a root-level PDF for this bulletin."""
    for f in root.glob(f"{bulletin_no}*.pdf"):
        if f.stat().st_size > 0:
            return f
    return None


def distribute_pdfs(root: Path = BULLETINS_ROOT) -> dict:
    """Move root-level PDFs into their matching BLT folders as bulletin.pdf."""
    stats = {"moved": 0, "already_exists": 0, "no_folder": 0}
    for pdf_file in sorted(root.glob("*.pdf")):
        match = re.match(r"^(\d+)", pdf_file.name)
        if not match:
            continue
        bulletin_no = match.group(1)
        folder = find_folder(bulletin_no, root=root)
        if not folder:
            stats["no_folder"] += 1
            continue
        target = folder / "bulletin.pdf"
        if target.exists() and target.stat().st_size > 0:
            stats["already_exists"] += 1
            continue
        shutil.copy2(pdf_file, target)
        logger.info(f"  {pdf_file.name} -> {folder.name}/bulletin.pdf")
        stats["moved"] += 1
    return stats


async def collect_download_urls(headless: bool = True) -> List[Dict]:
    """Scroll through turkpatent.gov.tr and collect all bulletin download URLs.

    Returns list of {"id": "488", "date": "2026-03-12", "is_gazette": bool, "url": str}
    """
    from playwright.async_api import async_playwright
    from data_collection import (
        extract_card_metadata, collect_download_clickables,
        maybe_dismiss_overlays, select_category,
        DOWNLOAD_LABEL_SELECTOR, CD_TEXT_RE,
        open_download_menu, list_menu_items, force_close_menus,
        MENU_WAIT_MS,
    )

    cards = []
    seen = set()

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

            # Select Marka category
            await select_category(page, "Marka")
            await page.wait_for_timeout(1000)

            last_height = 0
            stall_rounds = 0

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
                    card_date = meta.get("date")
                    is_gz = meta.get("is_gazette", False)

                    if not card_id or card_id in seen:
                        continue
                    seen.add(card_id)

                    # Try to get download URL
                    url = None

                    # Method 1: Check if the button itself has an href
                    try:
                        href = await clickable.get_attribute("href")
                        if href and "download" in href.lower():
                            url = href
                    except Exception:
                        pass

                    # Method 2: Try to get href from inner <a> tag
                    if not url:
                        try:
                            link = clickable.locator("a[href*='download'], a[href*='file']").first
                            if await link.count() > 0:
                                url = await link.get_attribute("href")
                        except Exception:
                            pass

                    # Method 3: Open menu and find non-CD PDF item
                    if not url:
                        try:
                            menu = await open_download_menu(page, clickable)
                            if menu:
                                items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
                                # Find non-CD items (these are the PDFs)
                                pdf_items = [it for it in items
                                             if not it.get("is_cd") and it.get("href")]
                                if pdf_items:
                                    url = pdf_items[0]["href"]
                                elif items:
                                    # Any item with href
                                    href_items = [it for it in items if it.get("href")]
                                    if href_items:
                                        url = href_items[0]["href"]
                                try:
                                    await force_close_menus(page)
                                except Exception:
                                    pass
                                await page.wait_for_timeout(300)
                        except Exception:
                            try:
                                await force_close_menus(page)
                            except Exception:
                                pass

                    # Make URL absolute
                    if url and not url.startswith("http"):
                        url = f"https://www.turkpatent.gov.tr{url}" if url.startswith("/") else url

                    type_label = "GZ" if is_gz else "BLT"
                    if url:
                        logger.info(f"  [{type_label}] {card_id} [{card_date}] -> {url[:80]}...")
                    else:
                        logger.info(f"  [{type_label}] {card_id} [{card_date}] -> NO URL FOUND")

                    cards.append({
                        "id": card_id,
                        "date": card_date,
                        "is_gazette": is_gz,
                        "url": url,
                    })

                # Scroll down
                height = await page.evaluate("document.body.scrollHeight")
                if height == last_height:
                    stall_rounds += 1
                else:
                    stall_rounds = 0
                    last_height = height

                if stall_rounds >= 8:
                    logger.info(f"Reached end of bulletin list. Total cards found: {len(cards)}")
                    break

                # Aggressive scroll: try larger jumps when stalled
                scroll_amount = 3000 if stall_rounds >= 3 else 1800
                await page.mouse.wheel(0, scroll_amount)
                await page.wait_for_timeout(2000 if stall_rounds >= 3 else 1500)

                # Also try clicking "load more" or similar buttons
                if stall_rounds >= 2:
                    try:
                        load_more = page.locator("button:has-text('Daha Fazla'), button:has-text('Load More'), button:has-text('devam'), a:has-text('Daha Fazla')").first
                        if await load_more.count() > 0 and await load_more.is_visible():
                            logger.info("  Clicking 'load more' button...")
                            await load_more.click()
                            await page.wait_for_timeout(3000)
                            stall_rounds = 0
                    except Exception:
                        pass

        finally:
            await context.close()
            await browser.close()

    return cards


def _download_single_url(url: str, target_path: Path, cookies: dict = None) -> bool:
    """Download a single URL to target path."""
    tmp_path = target_path.with_suffix(".pdf.part")
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.Session() as s:
            s.headers.update(headers)
            if cookies:
                s.cookies.update(cookies)
            with s.get(url, stream=True, allow_redirects=True,
                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
                if r.status_code >= 400:
                    logger.warning(f"    HTTP {r.status_code} for {url}")
                    return False

                ct = r.headers.get("content-type", "").lower()
                if "text/html" in ct:
                    logger.warning(f"    Got HTML instead of PDF, skipping")
                    return False

                total = 0
                last_log = time.time()
                with open(tmp_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        total += len(chunk)
                        now = time.time()
                        if now - last_log >= 30:
                            logger.info(f"    ... {total / 1024 / 1024:.1f} MB downloaded")
                            last_log = now

        if tmp_path.stat().st_size < 10000:
            logger.warning(f"    File too small ({tmp_path.stat().st_size} bytes), likely error page")
            tmp_path.unlink(missing_ok=True)
            return False

        # Rename to final path
        tmp_path.rename(target_path)
        logger.info(f"    Saved: {target_path.name} ({total / 1024 / 1024:.1f} MB)")
        return True

    except Exception as e:
        logger.warning(f"    Download failed: {e}")
        tmp_path.unlink(missing_ok=True)
        return False


def download_pdf(url: str, target_path: Path, cookies: dict = None) -> bool:
    """Download a PDF from URL to target path.

    Handles multi-UUID URLs (comma-separated) by splitting and downloading
    each part separately. Prefers PDF files over RAR/ZIP archives.
    """
    # Parse base URL and check for comma-separated file IDs
    base_match = re.match(r"(https?://[^/]+/file/)([^?]+)\??(.*)", url)
    if not base_match:
        return _download_single_url(url, target_path, cookies)

    base_url = base_match.group(1)
    file_ids = base_match.group(2)
    query = base_match.group(3)

    uuids = [uid.strip() for uid in file_ids.split(",") if uid.strip()]

    if len(uuids) <= 1:
        # Single UUID — download directly
        return _download_single_url(url, target_path, cookies)

    # Multi-UUID — probe each to find the PDF
    logger.info(f"    Multi-file URL detected ({len(uuids)} parts), probing each...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    best_url = None
    for i, uid in enumerate(uuids):
        part_url = f"{base_url}{uid}?{query}" if query else f"{base_url}{uid}"
        try:
            r = requests.head(part_url, headers=headers, timeout=(10, 15),
                              allow_redirects=True)
            ct = r.headers.get("content-type", "").lower()
            cl = r.headers.get("content-length", "0")
            logger.info(f"    Part {i+1}/{len(uuids)}: {ct} ({int(cl)/1024/1024:.0f} MB)")
            if "pdf" in ct or "octet-stream" in ct:
                best_url = part_url
                break  # Prefer PDF
        except Exception as e:
            logger.warning(f"    Part {i+1} probe failed: {e}")

    # Fallback: try the largest file (often part 2 is the PDF)
    if not best_url:
        # Just try the last UUID — usually the PDF
        best_url = f"{base_url}{uuids[-1]}?{query}" if query else f"{base_url}{uuids[-1]}"
        logger.info(f"    No PDF detected, trying last part...")

    return _download_single_url(best_url, target_path, cookies)


async def run_full(headless: bool = True, skip_collect: bool = False):
    """Full run: collect URLs, download missing PDFs, distribute into folders."""

    # Phase 1: Collect URLs
    logger.info("=" * 60)
    logger.info("Phase 1: Collecting download URLs from turkpatent.gov.tr")
    logger.info("=" * 60)

    # Check if we have cached URLs from a previous run
    url_file = BULLETINS_ROOT / "download_urls.json"
    if skip_collect and url_file.exists():
        logger.info("Using cached download URLs from previous run")
        with open(url_file, "r", encoding="utf-8") as f:
            cards = json.load(f)
    else:
        cards = await collect_download_urls(headless=headless)

    blt_cards = [c for c in cards if not c.get("is_gazette")]
    gz_cards = [c for c in cards if c.get("is_gazette")]
    logger.info(f"Found {len(blt_cards)} bulletin cards, {len(gz_cards)} gazette cards")

    # Save URLs for reference
    with open(url_file, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved URLs to {url_file}")

    # Phase 2: Download missing PDFs (both BLT and GZ)
    logger.info("=" * 60)
    logger.info("Phase 2: Downloading missing PDFs (bulletins + gazettes)")
    logger.info("=" * 60)

    stats = {"downloaded": 0, "skipped": 0, "no_url": 0, "failed": 0, "no_folder": 0}
    failed_cards = []

    for card in cards:
        card_id = card["id"]
        card_date = card.get("date", "")
        url = card.get("url")
        is_gz = card.get("is_gazette", False)
        type_label = "GZ" if is_gz else "BLT"

        # Find matching folder
        folder = find_folder(card_id, is_gazette=is_gz)

        # Check if we already have a PDF (in folder or root)
        if folder and has_pdf_in_folder(folder):
            stats["skipped"] += 1
            continue
        root_pdf = has_root_pdf(card_id)
        if root_pdf:
            if folder:
                target = folder / "bulletin.pdf"
                shutil.copy2(root_pdf, target)
                logger.info(f"  [{type_label} {card_id}] Copied root PDF to {folder.name}/bulletin.pdf")
                stats["skipped"] += 1
            continue

        if not url:
            logger.warning(f"  [{type_label} {card_id}] No download URL found")
            stats["no_url"] += 1
            continue

        if not folder:
            # Skip bundled ranges like "427-433" — can't map to a single folder
            if re.search(r"\d+-\d+", card_id):
                logger.info(f"  [{type_label} {card_id}] Bundled range, skipping")
                stats["no_folder"] += 1
                continue
            # Auto-create folder for missing GZ/BLT entries
            prefix = "GZ_" if is_gz else "BLT_"
            folder_name = f"{prefix}{card_id}_{card_date}" if card_date else f"{prefix}{card_id}"
            folder = BULLETINS_ROOT / folder_name
            folder.mkdir(parents=True, exist_ok=True)
            logger.info(f"  [{type_label} {card_id}] Created folder: {folder_name}")

        target = folder / "bulletin.pdf"
        logger.info(f"  [{type_label} {card_id}] Downloading to {folder.name}/bulletin.pdf...")

        ok = download_pdf(url, target)
        if ok:
            stats["downloaded"] += 1
        else:
            stats["failed"] += 1
            failed_cards.append(card)

    logger.info(f"Download stats: {stats}")

    # Phase 2b: Retry failed downloads (once, after a short pause)
    if failed_cards:
        logger.info(f"Retrying {len(failed_cards)} failed downloads after 30s pause...")
        time.sleep(30)
        retry_stats = {"retried": 0, "succeeded": 0, "failed": 0}
        for card in failed_cards:
            card_id = card["id"]
            is_gz = card.get("is_gazette", False)
            url = card.get("url")
            type_label = "GZ" if is_gz else "BLT"
            folder = find_folder(card_id, is_gazette=is_gz)
            if folder and has_pdf_in_folder(folder):
                continue
            if not folder:
                card_date = card.get("date", "")
                target = BULLETINS_ROOT / f"{card_id}_{card_date}.pdf" if card_date else BULLETINS_ROOT / f"{card_id}.pdf"
            else:
                target = folder / "bulletin.pdf"
            retry_stats["retried"] += 1
            logger.info(f"  [RETRY {type_label} {card_id}] Downloading...")
            ok = download_pdf(url, target)
            if ok:
                retry_stats["succeeded"] += 1
                stats["downloaded"] += 1
                stats["failed"] -= 1
            else:
                retry_stats["failed"] += 1
        logger.info(f"Retry stats: {retry_stats}")

    # Phase 3: Distribute any remaining root PDFs
    logger.info("=" * 60)
    logger.info("Phase 3: Distributing root-level PDFs into BLT folders")
    logger.info("=" * 60)
    dist_stats = distribute_pdfs()
    logger.info(f"Distribution stats: {dist_stats}")

    return {"download": stats, "distribute": dist_stats}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download missing bulletin PDFs")
    parser.add_argument("--visible", action="store_true", help="Show browser")
    parser.add_argument("--distribute-only", action="store_true",
                        help="Just move existing root PDFs into BLT/GZ folders")
    parser.add_argument("--skip-collect", action="store_true",
                        help="Skip URL collection, use cached download_urls.json")
    args = parser.parse_args()

    if args.distribute_only:
        stats = distribute_pdfs()
        logger.info(f"Done: {stats}")
    else:
        asyncio.run(run_full(headless=not args.visible,
                             skip_collect=args.skip_collect))
