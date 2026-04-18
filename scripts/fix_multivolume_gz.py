"""
Fix multi-volume GZ gazettes: identify events volume and set as bulletin.pdf.

For multi-volume gazettes, the volume with the TOC (showing section headers)
typically contains registrations only. The events (transfers, seizures, renewals)
are in a separate volume that starts directly with event content.

This script:
1. Finds GZ folders with multiple PDF volumes and <100 events
2. Identifies which volume contains events (by scanning for section headers)
3. Renames it to bulletin.pdf
4. Removes stale events.json to force re-extraction
"""
import json
import sys
import logging
from pathlib import Path

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

BULLETINS_ROOT = Path(__file__).resolve().parent.parent / "bulletins" / "Marka"

EVENT_MARKERS = [
    "BİRLEŞME", "DEVREDİLEN", "DEVİR", "HACİZ KONULANLAR",
    "TEDBİR KONULANLAR", "İFLAS İLANI", "LİSANS KAYDI",
    "YENİLENEN MARKALAR", "DÜZELTMELER", "İŞLEMDEN ÇEKİLEN",
    "İPTAL EDİLENLER", "EŞYA SINIRLANDIRMA",
]


def count_event_markers(pdf_path: Path) -> int:
    """Count how many event section markers are found in a PDF."""
    import fitz
    doc = fitz.open(str(pdf_path))
    max_page = doc.page_count
    found = set()

    # Sample pages throughout the document
    sample = set()
    sample.update(range(min(30, max_page)))  # first 30
    sample.update(range(max(0, max_page - 100), max_page))  # last 100
    # Also sample every 500th page
    sample.update(range(0, max_page, 500))

    for p in sample:
        try:
            text = doc[p].get_text()
            for marker in EVENT_MARKERS:
                if marker in text:
                    found.add(marker)
        except Exception:
            continue

    doc.close()
    return len(found)


def has_toc(pdf_path: Path) -> bool:
    """Check if PDF has a TOC page with İÇİNDEKİLER or section listings."""
    import fitz
    doc = fitz.open(str(pdf_path))
    for i in range(min(5, doc.page_count)):
        text = doc[i].get_text()
        if "İÇİNDEKİLER" in text or "MARKA TESCİLLERİ" in text:
            doc.close()
            return True
    doc.close()
    return False


def main():
    stats = {"fixed": 0, "already_ok": 0, "no_events_vol": 0}

    for gz_dir in sorted(BULLETINS_ROOT.iterdir()):
        if not gz_dir.is_dir() or not gz_dir.name.startswith("GZ_"):
            continue

        # Check events count
        ef = gz_dir / "events.json"
        if ef.exists():
            try:
                data = json.loads(ef.read_text(encoding="utf-8"))
                events = data.get("events", [])
                if len(events) >= 100:
                    continue  # Already has enough events
            except Exception:
                pass

        # Find all PDFs > 10KB
        pdfs = [f for f in gz_dir.iterdir()
                if f.suffix.lower() == ".pdf" and f.stat().st_size > 10000]

        if len(pdfs) < 2:
            continue  # Not multi-volume

        logger.info(f"\n{gz_dir.name}: {len(pdfs)} PDFs, checking for events volume...")

        # Score each PDF by event marker count
        best_pdf = None
        best_score = 0
        for pdf in pdfs:
            if pdf.name == "bulletin.pdf":
                continue  # Check non-bulletin PDFs first
            score = count_event_markers(pdf)
            logger.info(f"  {pdf.name}: {score} event markers")
            if score > best_score:
                best_score = score
                best_pdf = pdf

        # Also check current bulletin.pdf
        bulletin_pdf = gz_dir / "bulletin.pdf"
        if bulletin_pdf.exists():
            bulletin_score = count_event_markers(bulletin_pdf)
            logger.info(f"  bulletin.pdf: {bulletin_score} event markers")
            if bulletin_score >= best_score:
                logger.info(f"  -> bulletin.pdf is already the best events volume")
                stats["already_ok"] += 1
                # Still remove events.json to force re-extraction with updated code
                if ef.exists():
                    ef.unlink()
                    logger.info(f"  Removed events.json for re-extraction")
                continue

        if best_pdf and best_score >= 2:
            logger.info(f"  -> Setting {best_pdf.name} as bulletin.pdf ({best_score} markers)")
            if bulletin_pdf.exists():
                # Rename current bulletin.pdf to bulletin_registrations.pdf
                backup = gz_dir / "bulletin_registrations.pdf"
                if not backup.exists():
                    bulletin_pdf.rename(backup)
                else:
                    bulletin_pdf.unlink()
            best_pdf.rename(bulletin_pdf)
            # Remove stale events.json
            if ef.exists():
                ef.unlink()
            stats["fixed"] += 1
        else:
            logger.info(f"  -> No clear events volume found (best score: {best_score})")
            stats["no_events_vol"] += 1

    logger.info(f"\n\nStats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
