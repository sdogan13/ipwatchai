"""
Scrape all GZ gazette download URLs from turkpatent.gov.tr/bultenler.

Two-phase approach:
  Phase A: Collect all card metadata + try to extract URLs from DOM (no clicks)
  Phase B: For cards without URLs, try one-at-a-time with isolated browser sessions

Saves results to bulletins/Marka/gz_download_urls.json
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_collection import (
    DOWNLOAD_LABEL_SELECTOR, maybe_dismiss_overlays, select_category,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

if sys.platform == "win32":
    try:
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)
    except Exception:
        pass

TARGET_URL = "https://www.turkpatent.gov.tr/bultenler"
BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"


async def collect_all_gz_info(headless: bool = True) -> list[dict]:
    """
    Load all cards, extract card metadata + download href from DOM.
    Uses JavaScript to examine each card's parent elements for download links.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, slow_mo=50)
        context = await browser.new_context(accept_downloads=True, viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
            await maybe_dismiss_overlays(page)
            await page.locator(DOWNLOAD_LABEL_SELECTOR).first.wait_for(state="visible", timeout=20000)
            await select_category(page, "Marka")
            await page.wait_for_timeout(1000)

            # Scroll to load all
            last_h = 0
            stall = 0
            while stall < 10:
                await page.mouse.wheel(0, 5000)
                await page.wait_for_timeout(2000)
                h = await page.evaluate("document.body.scrollHeight")
                if h == last_h:
                    stall += 1
                else:
                    stall = 0
                    last_h = h

            total = await page.locator(DOWNLOAD_LABEL_SELECTOR).count()
            logger.info(f"Loaded {total} cards")

            # Extract ALL card info via a single JS call — much faster than per-card evaluation
            results = await page.evaluate(r"""() => {
                const labelRe = /^\s*(İNDİR|İndir|INDIR|Indir)\s*$/;
                const cardIdRe = /^\s*\d{1,4}(?:_\d{1,2})?(?:\s*[-–—]\s*\d{1,4}(?:_\d{1,2})?)?\s*$/;
                const dateRe = /(\d{2})[./](\d{2})[./](\d{4})/;

                // Find all İNDİR labels
                const allElements = document.querySelectorAll('*');
                const cards = [];

                for (const el of allElements) {
                    const t = (el.textContent || '').trim();
                    if (!labelRe.test(t)) continue;

                    // Walk up to find card container
                    let n = el;
                    let foundId = null, foundDate = null, isGazette = false;
                    let downloadHrefs = [];

                    for (let step = 0; step < 15; step++) {
                        if (!n) break;
                        const text = (n.innerText || '');
                        const textLower = text.toLowerCase();

                        if (!foundId) {
                            const lines = text.split('\n').map(s => s.trim()).filter(Boolean);
                            const ids = lines.filter(l => cardIdRe.test(l));
                            if (ids.length >= 1) foundId = ids[0].replace(/\s+/g, '');
                        }
                        if (!foundDate) {
                            const m = text.match(dateRe);
                            if (m) foundDate = `${m[3]}-${m[2]}-${m[1]}`;
                        }
                        if (textLower.includes('gazete')) isGazette = true;

                        // Look for download links in this ancestor
                        const links = n.querySelectorAll('a[href*="file"], a[href*="download"], a[href*="webim"]');
                        for (const link of links) {
                            const href = link.getAttribute('href');
                            if (href && !href.startsWith('javascript') && href !== '#') {
                                const linkText = (link.textContent || '').toLowerCase();
                                downloadHrefs.push({href, text: linkText, isCD: linkText.includes('cd')});
                            }
                        }

                        // Also check buttons with onclick that contain URLs
                        const buttons = n.querySelectorAll('button[onclick*="file"], button[onclick*="download"]');
                        for (const btn of buttons) {
                            const onclick = btn.getAttribute('onclick') || '';
                            const urlMatch = onclick.match(/(https?:\/\/[^'"]+)/);
                            if (urlMatch) {
                                downloadHrefs.push({href: urlMatch[1], text: '', isCD: false});
                            }
                        }

                        if (foundId && foundDate && downloadHrefs.length > 0) break;
                        n = n.parentElement;
                    }

                    if (foundId && isGazette) {
                        // Deduplicate hrefs
                        const uniqueHrefs = [...new Map(downloadHrefs.map(h => [h.href, h])).values()];
                        cards.push({
                            id: foundId,
                            date: foundDate,
                            is_gazette: true,
                            hrefs: uniqueHrefs,
                        });
                    }
                }
                return cards;
            }""")

            logger.info(f"Found {len(results)} GZ cards via DOM scan")

            # Now for cards without hrefs, try opening download menus one at a time
            # First, identify which cards need menu interaction
            cards_with_urls = []
            cards_without_urls = []

            for card in results:
                non_cd_hrefs = [h for h in card.get("hrefs", []) if not h.get("isCD")]
                if non_cd_hrefs:
                    card["download_url"] = non_cd_hrefs[0]["href"]
                    cards_with_urls.append(card)
                elif card.get("hrefs"):
                    # Has hrefs but all are CD — take first non-CD or any
                    card["download_url"] = card["hrefs"][0]["href"]
                    cards_with_urls.append(card)
                else:
                    cards_without_urls.append(card)

            logger.info(f"  {len(cards_with_urls)} cards have download URLs from DOM")
            logger.info(f"  {len(cards_without_urls)} cards need menu interaction")

            # Phase B: Try menu interaction for cards without URLs
            if cards_without_urls:
                from data_collection import (
                    collect_download_clickables, extract_card_metadata,
                    open_download_menu, list_menu_items, force_close_menus, MENU_WAIT_MS,
                )

                clickables = await collect_download_clickables(page)
                count = await clickables.count()
                need_ids = {c["id"] for c in cards_without_urls}

                for i in range(count):
                    if not need_ids:
                        break

                    cl = clickables.nth(i)
                    try:
                        if not await cl.is_visible():
                            continue
                        meta = await extract_card_metadata(cl)
                    except Exception:
                        continue

                    cid = meta.get("id")
                    if not cid or cid not in need_ids or not meta.get("is_gazette"):
                        continue

                    logger.info(f"  Trying menu for GZ {cid}...")

                    try:
                        await cl.scroll_into_view_if_needed()
                        await page.wait_for_timeout(300)
                        menu = await open_download_menu(page, cl)
                        if menu:
                            items = await list_menu_items(page, timeout_ms=MENU_WAIT_MS)
                            pdf_items = [it for it in items if not it.get("is_cd") and it.get("href")]
                            if pdf_items:
                                url = pdf_items[0]["href"]
                            else:
                                href_items = [it for it in items if it.get("href")]
                                url = href_items[0]["href"] if href_items else None
                            await force_close_menus(page)

                            if url:
                                # Find the card and set URL
                                for c in cards_without_urls:
                                    if c["id"] == cid:
                                        c["download_url"] = url
                                        cards_with_urls.append(c)
                                        break
                                need_ids.discard(cid)
                                logger.info(f"    GZ {cid}: URL from menu -> {url[:60]}...")
                            else:
                                need_ids.discard(cid)
                                logger.warning(f"    GZ {cid}: menu opened but no download URL")
                        else:
                            # Try expect_download with 8s timeout
                            try:
                                async with page.expect_download(timeout=8000) as dl_info:
                                    await cl.click()
                                dl = await dl_info.value
                                url = dl.url
                                await dl.cancel()
                                for c in cards_without_urls:
                                    if c["id"] == cid:
                                        c["download_url"] = url
                                        cards_with_urls.append(c)
                                        break
                                need_ids.discard(cid)
                                logger.info(f"    GZ {cid}: URL from download -> {url[:60]}...")
                            except Exception:
                                need_ids.discard(cid)
                                logger.warning(f"    GZ {cid}: no URL (menu + download failed)")

                            # Check if page navigated away
                            try:
                                if page.url != TARGET_URL:
                                    logger.warning(f"    Page navigated away, stopping menu phase")
                                    break
                            except Exception:
                                break

                    except Exception as e:
                        need_ids.discard(cid)
                        logger.warning(f"    GZ {cid}: error: {e}")
                        try:
                            await force_close_menus(page)
                        except Exception:
                            pass
                        try:
                            if page.url != TARGET_URL:
                                break
                        except Exception:
                            break

            return cards_with_urls

        finally:
            await context.close()
            await browser.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()

    cards = await collect_all_gz_info(headless=not args.visible)

    # Save to cache
    cache = {}
    for c in cards:
        gz_id = c["id"]
        url = c.get("download_url", "")
        if url and not url.startswith("http"):
            url = f"https://www.turkpatent.gov.tr{url}" if url.startswith("/") else url
        if url:
            cache[gz_id] = {"url": url, "date": c.get("date")}

    cache_file = BULLETINS_ROOT / "gz_download_urls.json"
    with open(cache_file, "w") as f:
        json.dump(cache, f, indent=2)

    logger.info(f"\nSaved {len(cache)} GZ download URLs to {cache_file}")
    logger.info("Run 'python scripts/download_gz_targeted.py' to download the PDFs")


if __name__ == "__main__":
    asyncio.run(main())
