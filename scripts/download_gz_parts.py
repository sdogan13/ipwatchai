"""
Download missing Part 2 (or Part 3) volumes for multi-UUID GZ gazettes.

These are gazettes where the download URL contains multiple comma-separated UUIDs,
each pointing to a separate PDF volume. The initial download only grabbed one UUID.
This script downloads the remaining UUIDs that weren't already downloaded.

It identifies the "events volume" (containing renewals, transfers, etc.) by checking
the TOC page for section headers, and renames it to bulletin.pdf for the extractor.
"""
import json
import re
import sys
import time
import logging
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BULLETINS_ROOT = PROJECT_ROOT / "bulletins" / "Marka"

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

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CHUNK_SIZE = 256 * 1024


def find_gz_dir(gz_num: int) -> Path | None:
    for d in BULLETINS_ROOT.iterdir():
        if d.is_dir() and (d.name == f"GZ_{gz_num}" or d.name.startswith(f"GZ_{gz_num}_")):
            return d
    return None


def get_existing_pdf_sizes(gz_dir: Path) -> dict[int, str]:
    """Return dict of file_size -> filename for existing PDFs in the folder."""
    sizes = {}
    for f in gz_dir.iterdir():
        if f.suffix.lower() == ".pdf" and f.stat().st_size > 10000:
            sizes[f.stat().st_size] = f.name
    return sizes


def download_file(url: str, target: Path) -> bool:
    tmp = target.with_suffix(".pdf.part")
    try:
        with requests.Session() as s:
            s.headers.update(HEADERS)
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
                            logger.info(f"    ... {total / 1024 / 1024:.1f} MB")
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


def check_is_events_volume(pdf_path: Path) -> bool:
    """Check if a PDF contains events (transfers, seizures, renewals, etc.).

    Checks both TOC markers and actual event sub-section headers.
    For multi-volume gazettes, the events volume may not have a TOC at all —
    it starts directly with event sections like BİRLEŞME, DEVİR, etc.
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        max_page = doc.page_count

        # Check first 10 pages for TOC markers
        for i in range(min(10, max_page)):
            text = doc[i].get_text()
            if "İLİŞKİN İLANLAR" in text or "YENİLENEN MARKALAR" in text:
                doc.close()
                return True

        # Check pages around the middle and end for event sub-section headers
        # Events volumes typically have BİRLEŞME, DEVİR, HACİZ near the start
        event_markers = ["BİRLEŞME", "DEVREDİLEN", "HACİZ KONULANLAR",
                        "TEDBİR KONULANLAR", "YENİLENEN MARKALAR", "DÜZELTMELER"]

        # Sample pages: first 20, last 50
        sample_pages = list(range(min(20, max_page))) + list(range(max(0, max_page - 50), max_page))
        for i in set(sample_pages):
            try:
                text = doc[i].get_text()
                if any(marker in text for marker in event_markers):
                    doc.close()
                    return True
            except Exception:
                continue

        doc.close()
        return False
    except Exception:
        return False


def main():
    with open(BULLETINS_ROOT / "gz_download_urls.json") as f:
        url_cache = json.load(f)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "events_found": 0}

    for gz_str, info in sorted(url_cache.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if not gz_str.isdigit():
            continue
        gz_num = int(gz_str)
        if gz_num < 434:
            continue

        url = info["url"] if isinstance(info, dict) else info
        base = url.split("?")[0]
        query = url.split("?", 1)[1] if "?" in url else ""

        if "," not in base:
            continue

        # Extract UUIDs
        file_part = base.split("file/")[-1]
        uuids = [u.strip() for u in file_part.split(",") if u.strip()]
        if len(uuids) < 2:
            continue

        gz_dir = find_gz_dir(gz_num)
        if not gz_dir:
            continue

        # Check current events count
        ef = gz_dir / "events.json"
        if ef.exists():
            data = json.loads(ef.read_text(encoding="utf-8"))
            events = data.get("events", [])
            if len(events) >= 100:
                continue  # Already has enough events

        existing_sizes = get_existing_pdf_sizes(gz_dir)
        logger.info(f"\nGZ {gz_num} ({gz_dir.name}): {len(uuids)} UUIDs, {len(existing_sizes)} existing PDFs")

        # Probe and download each UUID
        for idx, uuid in enumerate(uuids):
            part_url = f"https://webim.turkpatent.gov.tr/file/{uuid}{'?' + query if query else ''}"

            # Check size first
            try:
                r = requests.head(part_url, timeout=15, allow_redirects=True, headers=HEADERS)
                ct = r.headers.get("content-type", "").lower()
                cl = int(r.headers.get("content-length", 0))

                if "pdf" not in ct:
                    logger.info(f"  UUID {idx+1}: not a PDF (ct={ct}), skipping")
                    continue

                # Check if we already have a file of this size
                if cl in existing_sizes:
                    logger.info(f"  UUID {idx+1}: already downloaded ({existing_sizes[cl]}, {cl//1048576}MB)")
                    continue

            except Exception as e:
                logger.warning(f"  UUID {idx+1}: probe failed: {e}")
                continue

            # Download
            target = gz_dir / f"bulletin_vol{idx+1}.pdf"
            logger.info(f"  UUID {idx+1}: downloading {cl//1048576}MB -> {target.name}")
            ok = download_file(part_url, target)
            if ok:
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1

        # Now find the events volume and set it as bulletin.pdf
        bulletin_pdf = gz_dir / "bulletin.pdf"
        events_volume = None

        for pdf in sorted(gz_dir.glob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True):
            if pdf.name == "bulletin.pdf":
                continue
            if check_is_events_volume(pdf):
                events_volume = pdf
                break

        if events_volume:
            logger.info(f"  Events volume: {events_volume.name}")
            # If bulletin.pdf exists but is not the events volume, rename it
            if bulletin_pdf.exists():
                if not check_is_events_volume(bulletin_pdf):
                    # Current bulletin.pdf is not the events volume — swap
                    bulletin_pdf.rename(gz_dir / "bulletin_registrations.pdf")
                    events_volume.rename(bulletin_pdf)
                    logger.info(f"  Swapped bulletin.pdf with {events_volume.name}")
                    stats["events_found"] += 1
                # else bulletin.pdf is already the events volume
            else:
                events_volume.rename(bulletin_pdf)
                logger.info(f"  Set {events_volume.name} as bulletin.pdf")
                stats["events_found"] += 1

            # Remove events.json to force re-extraction
            if ef.exists():
                ef.unlink()
                logger.info(f"  Removed old events.json for re-extraction")
        else:
            logger.info(f"  No events volume identified among downloaded PDFs")

    logger.info(f"\n\nFinal stats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
