"""Tasarım bulletin events extractor.

Sister module to ``pdf_extract_tasarim.py``. Reads each
``bulletins/Tasarim/TS_*/bulletin.pdf`` and emits ``events.json`` per issue
holding events on EXISTING design registrations (transfers, seizures,
renewals, cancellations).

Twelve event types are recognized, drawn from the announcement section of
the bulletin:

  * transfer                      — DEVİR
  * seizure                       — HACİZ KONULAN TESCİLLER
  * provisional_seizure           — İHTİYATİ HACİZ KOYULAN TESCİLLER
  * provisional_injunction_lifted — İHTİYATİ TEDBİRİ KALDIRILAN TESCİLLER
  * renewal                       — YENİLENEN TESCİLLER
  * partial_renewal               — KISMI YENİLEME
  * partial_cancellation_owner    — SAHİBİNİN TALEBİ İLE KISMI İPTAL
  * partial_provisional_injunction — KISMİ İHTİYATİ TEDBİR KONAN TASARIMLAR
  * full_cancellation_board       — YİDK KARARI İLE İPTAL EDİLEN TESCİLLER
  * partial_cancellation_board    — YİDK KARARI İLE KISMI İPTAL EDİLEN TESCİLLER
  * full_cancellation_applicant   — BAŞVURU SAHİBİNİN TALEBİ İLE İPTAL
  * partial_cancellation_applicant — BAŞVURU SAHİBİNİN TALEBİ İLE KISMI İPTAL

Most events use INID-coded records; YİDK board-decision events use a free-text
narrative shape that's parsed via regex. Every event preserves its raw
Turkish ``free_text`` for downstream display / translation / recovery.
"""

import argparse
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Tasarim"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM-EVENTS] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_events")


def _get_fitz():
    import fitz
    return fitz


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

# Each section header is matched case-sensitive against a stripped page line.
# Order matters: longer / more-specific phrases first so substring overlaps
# resolve correctly.
SECTION_MARKERS: List[Tuple[str, str]] = [
    ("BAŞVURU SAHİBİNİN TALEBİ İLE KISMİ İPTAL", "partial_cancellation_applicant"),
    ("BAŞVURU SAHİBİNİN TALEBİ İLE KISMI İPTAL", "partial_cancellation_applicant"),
    ("BAŞVURU SAHİBİNİN TALEBİ İLE İPTAL", "full_cancellation_applicant"),
    ("YİDK KARARI İLE KISMİ İPTAL EDİLEN TESCİLLER", "partial_cancellation_board"),
    ("YİDK KARARI İLE KISMI İPTAL EDİLEN TESCİLLER", "partial_cancellation_board"),
    ("YİDK KARARI İLE İPTAL EDİLEN TESCİLLER", "full_cancellation_board"),
    ("KISMİ İHTİYATİ TEDBİR KONAN TASARIMLAR", "partial_provisional_injunction"),
    ("KISMI İHTİYATİ TEDBİR KONAN TASARIMLAR", "partial_provisional_injunction"),
    ("İHTİYATİ TEDBİRİ KALDIRILAN TESCİLLER", "provisional_injunction_lifted"),
    ("İHTİYATİ HACİZ KOYULAN TESCİLLER", "provisional_seizure"),
    ("HACİZ KONULAN TESCİLLER", "seizure"),
    ("SAHİBİNİN TALEBİ İLE KISMİ İPTAL", "partial_cancellation_owner"),
    ("SAHİBİNİN TALEBİ İLE KISMI İPTAL", "partial_cancellation_owner"),
    ("YENİLENEN TESCİLLER", "renewal"),
    ("YENILENEN TESCILLER", "renewal"),
    ("KISMİ YENİLEME", "partial_renewal"),
    ("KISMI YENİLEME", "partial_renewal"),
    ("KISMI YENILEME", "partial_renewal"),
    ("DEVİR", "transfer"),
]

# Event types that use the INID record form ((11), (15), (73), (58), (78), (100), (203), (204), (205)).
INID_EVENT_TYPES = {
    "transfer",
    "seizure",
    "provisional_seizure",
    "provisional_injunction_lifted",
    "partial_provisional_injunction",
    "renewal",
    "partial_renewal",
    "partial_cancellation_owner",
    "full_cancellation_applicant",
    "partial_cancellation_applicant",
}

# Event types that use the YİDK free-text narrative form.
BOARD_EVENT_TYPES = {
    "full_cancellation_board",
    "partial_cancellation_board",
}

ALL_EVENT_TYPES = INID_EVENT_TYPES | BOARD_EVENT_TYPES

# Record boundary in INID-event sections: each event starts with `(11) <regno>`.
EVENT_REGNO_RE = re.compile(r"\(11\)\s*(\d{4}\s+\d{3,6}|DM\s*\d+)")

# Within an INID event block, find the various coded fields.
INID_TOKEN_RE = re.compile(r"\((\d{2,3})\)")
TR_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
DESIGN_INDEX_LIST_RE = re.compile(r"(\d+(?:\s*,\s*\d+)*)")

# Footer regex for bulletin metadata (mirror metadata extractor)
FOOTER_BULLETIN_RE = re.compile(r"(\d{4})\s*/\s*(\d{3,4})\s+Tasar[ıi]mlar\s+B[üu]lteni")
FOOTER_DATE_RE = re.compile(r"Yay[ıi]n\s+Tarihi\s*:?\s*(\d{2})\.(\d{2})\.(\d{4})")

# YİDK board-decision narrative components
YIDK_PUB_REF_RE = re.compile(
    r"(\d{2}\.\d{2}\.\d{4})\s+tarih\s+ve\s+(\d{1,4})\s+sayılı\s+Resmi\s+Tasarımlar\s+Bülteninde\s+yayınlanan,\s*"
    r"(\d{2}\.\d{2}\.\d{4})\s+tarih\s+ve\s+(\d{4}\s+\d{3,6})\s+sayı\s+ile\s+\"([^\"]+)\""
)
YIDK_DECISION_RE = re.compile(
    r"(\d{2}\.\d{2}\.\d{4})\s+tarih\s+ve\s+(\d{4}/T-\d+)\s+sayılı\s+kararı"
)


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class HolderRef:
    name: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None


@dataclass
class CourtRef:
    name: Optional[str] = None
    case_no: Optional[str] = None


@dataclass
class TasarimEvent:
    event_type: str
    event_index: int
    page: int
    registration_no: Optional[str] = None
    registration_date: Optional[str] = None
    event_date: Optional[str] = None
    holder: Optional[HolderRef] = None
    previous_holder: Optional[HolderRef] = None
    new_holder: Optional[HolderRef] = None
    court: Optional[CourtRef] = None
    design_indices: List[int] = field(default_factory=list)
    decision_date: Optional[str] = None
    decision_no: Optional[str] = None
    referenced_bulletin_no: Optional[int] = None
    referenced_bulletin_date: Optional[str] = None
    free_text: Optional[str] = None
    fingerprint: Optional[str] = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\x00", "")).strip()


def normalize_tr_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = TR_DATE_RE.search(raw)
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def detect_section_for_page(page_text: str, current: Optional[str]) -> Optional[str]:
    """Return the event section a page belongs to (sticky unless overridden).

    ``None`` means we are NOT in any event section yet (or we have left it).
    """
    upper = page_text.upper()
    for marker, name in SECTION_MARKERS:
        if marker.upper() in upper:
            return name
    # Lahey (Hague) section starts new design publications, not events;
    # exiting all event types is the right move.
    if "LAHEY ANLAŞMASI ÇERÇEVESİNDE TÜRKİYE" in page_text or "LAHEY" in upper:
        return None
    return current


def parse_inid_fields(text: str) -> Dict[str, List[str]]:
    """Tokenize INID-coded text. Same behavior as the metadata parser's helper."""
    out: Dict[str, List[str]] = {}
    matches = list(INID_TOKEN_RE.finditer(text))
    for idx, m in enumerate(matches):
        code = m.group(1)
        value_start = m.end()
        if idx + 1 < len(matches):
            value_end = matches[idx + 1].start()
        else:
            value_end = len(text)
        value = text[value_start:value_end].strip()
        out.setdefault(code, []).append(value)
    return out


def parse_holder_with_address(raw: str) -> Optional[HolderRef]:
    """Parse a (73)/(78) holder string: ``NAME (CLIENT_ID) ADDRESS COUNTRY`` or
    plain text. Also handles transfer-style ``"ADDRESS" adresinde mukim "NAME"``.
    """
    raw = clean_text(raw)
    if not raw:
        return None

    # Transfer-style: "ADDRESS" adresinde mukim "NAME"
    m = re.search(r'"([^"]+)"\s+adresinde\s+mukim\s+"([^"]+)"', raw)
    if m:
        return HolderRef(name=m.group(2).strip(), address=m.group(1).strip())

    # Standard: NAME (CLIENT_ID) ADDRESS [COUNTRY]
    client_match = re.search(r"\((\d{4,9})\)", raw)
    if client_match:
        name = raw[: client_match.start()].strip().rstrip(",")
        tail = raw[client_match.end():].strip()
        # First parenthetical wraps client_id; trailing parens may wrap address+country
        country = None
        country_match = re.search(r"\b([A-ZÇĞİÖŞÜ]{4,})\s*$", tail)
        if country_match:
            country = country_match.group(1)
            tail = tail[: country_match.start()].strip().rstrip(",")
        return HolderRef(name=name, address=tail or None, country=country)

    # Format: NAME (ADDRESS) — parens around address
    paren_addr = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", raw)
    if paren_addr:
        return HolderRef(name=paren_addr.group(1).strip(), address=paren_addr.group(2).strip())

    return HolderRef(name=raw)


def parse_design_indices(raw: str) -> List[int]:
    """Parse a (100) value like ``4,12,13,15`` into ``[4, 12, 13, 15]``."""
    if not raw:
        return []
    m = DESIGN_INDEX_LIST_RE.search(raw)
    if not m:
        return []
    out: List[int] = []
    for token in m.group(1).split(","):
        token = token.strip()
        if token.isdigit():
            out.append(int(token))
    return out


def fingerprint_event(event: TasarimEvent, bulletin_no: Optional[int]) -> str:
    """Stable hash of (bulletin_no, event_type, registration_no, event_date,
    free_text) so reruns can dedup."""
    parts = [
        str(bulletin_no or ""),
        event.event_type,
        event.registration_no or "",
        event.event_date or "",
        (event.free_text or "")[:200],
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Per-event-type parsers
# ---------------------------------------------------------------------------

def parse_inid_event(
    block: str,
    *,
    event_type: str,
    event_index: int,
    page: int,
) -> TasarimEvent:
    """Parse an INID-coded event block (transfer/seizure/renewal/cancellation/...)."""
    fields = parse_inid_fields(block)
    event = TasarimEvent(event_type=event_type, event_index=event_index, page=page)
    event.free_text = clean_text(block)[:500]

    if "11" in fields and fields["11"]:
        event.registration_no = clean_text(fields["11"][0])
    if "15" in fields and fields["15"]:
        event.registration_date = normalize_tr_date(fields["15"][0])
    if "58" in fields and fields["58"]:
        event.event_date = normalize_tr_date(fields["58"][0])
    if "100" in fields and fields["100"]:
        event.design_indices = parse_design_indices(fields["100"][0])

    holders_raw = fields.get("73", [])

    if event_type == "transfer":
        # Transfer text: "(73) <prev> ne devretmiştir" — sometimes also (78) <new>.
        # In TS_483 the visible pattern is: (73) "<prev_addr>" adresinde mukim "<prev_name>" (78) "<new_addr>" adresinde mukim "<new_name>" ne devretmiştir.
        prev = parse_holder_with_address(holders_raw[0]) if holders_raw else None
        new_raw = fields.get("78", [])
        new = parse_holder_with_address(new_raw[0]) if new_raw else None
        event.previous_holder = prev
        event.new_holder = new
        event.holder = prev  # primary holder for indexing/lookup
    else:
        if holders_raw:
            event.holder = parse_holder_with_address(holders_raw[0])

    if event_type in {"seizure", "provisional_seizure", "provisional_injunction_lifted",
                      "partial_provisional_injunction"}:
        court = CourtRef()
        if "203" in fields and fields["203"]:
            court.name = clean_text(fields["203"][0])
        if "204" in fields and fields["204"]:
            court.case_no = clean_text(fields["204"][0])
        if "205" in fields and fields["205"]:
            event.event_date = normalize_tr_date(fields["205"][0]) or event.event_date
        if court.name or court.case_no:
            event.court = court

    return event


def parse_board_event(
    block: str,
    *,
    event_type: str,
    event_index: int,
    page: int,
) -> Optional[TasarimEvent]:
    """Parse a YİDK board-decision narrative event."""
    block_clean = clean_text(block)
    pub = YIDK_PUB_REF_RE.search(block_clean)
    dec = YIDK_DECISION_RE.search(block_clean)
    if not pub:
        # Not a parseable narrative; skip (the section may have a stray header
        # or a transitional sentence).
        return None

    event = TasarimEvent(event_type=event_type, event_index=event_index, page=page)
    event.referenced_bulletin_date = normalize_tr_date(pub.group(1))
    event.referenced_bulletin_no = int(pub.group(2)) if pub.group(2).isdigit() else None
    event.registration_date = normalize_tr_date(pub.group(3))
    event.registration_no = pub.group(4)
    event.holder = HolderRef(name=pub.group(5).split('"')[0].strip())

    if dec:
        event.decision_date = normalize_tr_date(dec.group(1))
        event.decision_no = dec.group(2)
        event.event_date = event.decision_date

    event.free_text = block_clean[:600]
    return event


# ---------------------------------------------------------------------------
# Streaming PDF traversal
# ---------------------------------------------------------------------------

def _scan_page_texts(doc) -> List[str]:
    return [doc[i].get_text("text") for i in range(doc.page_count)]


def _section_for_each_page(page_texts: List[str]) -> List[Optional[str]]:
    """Per-page section assignment (legacy; kept for tests).

    For real parsing use ``_section_transitions_in_text`` which is position-based.
    """
    sections: List[Optional[str]] = []
    current: Optional[str] = None
    for text in page_texts:
        current = detect_section_for_page(text, current)
        sections.append(current)
    return sections


def _section_transitions_in_text(full_text: str) -> List[Tuple[int, Optional[str]]]:
    """Find every section-header occurrence in ``full_text`` and return
    ``(char_pos, event_type)`` transitions sorted by position.

    Bare TOC mentions of section names are still emitted as transitions, but
    they're harmless: TR records (which carry their own ``(21)`` marker) are
    filtered out at the record level by ``_is_tr_record_marker``, so events
    get the right section even if the same name appears earlier in the TOC.

    LAHEY resets the section to ``None`` (events end, Hague designs follow).
    """
    upper = full_text.upper()
    transitions: List[Tuple[int, Optional[str]]] = []
    for marker, name in SECTION_MARKERS:
        marker_up = marker.upper()
        start = 0
        while True:
            idx = upper.find(marker_up, start)
            if idx == -1:
                break
            transitions.append((idx, name))
            start = idx + len(marker_up)
    for needle in ("LAHEY ANLAŞMASI ÇERÇEVESİNDE TÜRKİYE", "LAHEY"):
        idx = full_text.find(needle)
        if idx != -1:
            transitions.append((idx, None))
            break
    transitions.sort(key=lambda x: x[0])
    cleaned: List[Tuple[int, Optional[str]]] = []
    last_name: Optional[str] = "<sentinel>"
    for pos, name in transitions:
        if name != last_name:
            cleaned.append((pos, name))
            last_name = name
    return cleaned


# A (11) match is an event candidate only if it's NOT immediately followed by
# a (21) field — TR design records always carry both.
_TR_RECORD_HINT_RE = re.compile(r"\(21\)\s*\d{4}/")


def _is_tr_record_marker(full_text: str, pos: int) -> bool:
    """True when the (11) occurrence at ``pos`` belongs to a TR design record
    (i.e. a ``(21)`` marker follows within ~80 chars). False means it's an
    event-record candidate.
    """
    window = full_text[pos:pos + 80]
    return bool(_TR_RECORD_HINT_RE.search(window))


def _section_at_pos(transitions: List[Tuple[int, Optional[str]]], pos: int) -> Optional[str]:
    """Return the section type active at character position ``pos``.

    Uses binary search over the (sorted) transition list.
    """
    if not transitions:
        return None
    lo, hi = 0, len(transitions) - 1
    if pos < transitions[0][0]:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if transitions[mid][0] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return transitions[lo][1]


def extract_bulletin_metadata(full_text: str) -> Tuple[Optional[int], Optional[str]]:
    bulletin_no: Optional[int] = None
    bulletin_date: Optional[str] = None
    m = FOOTER_BULLETIN_RE.search(full_text)
    if m:
        try:
            bulletin_no = int(m.group(2))
        except ValueError:
            pass
    d = FOOTER_DATE_RE.search(full_text)
    if d:
        dd, mm, yyyy = d.groups()
        bulletin_date = f"{yyyy}-{mm}-{dd}"
    return bulletin_no, bulletin_date


def _find_inid_event_blocks(
    full_text: str,
    transitions: List[Tuple[int, Optional[str]]],
) -> List[Tuple[int, int, str]]:
    """Return list of ``(start_pos, end_pos, event_type)`` for INID events.

    Each block starts at its ``(11)`` boundary and runs to just before the
    next ``(11)`` OR a section transition (whichever comes first).
    """
    matches = list(EVENT_REGNO_RE.finditer(full_text))
    out: List[Tuple[int, int, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        # Filter out TR design records (they have (11) AND (21))
        if _is_tr_record_marker(full_text, start):
            continue
        section = _section_at_pos(transitions, start)
        if section not in INID_EVENT_TYPES:
            continue
        # End at the next event-candidate (11) match (skip TR (11)s when
        # computing the end-bound, otherwise the block ends prematurely on
        # the next TR record's (11)).
        end = len(full_text)
        for j in range(i + 1, len(matches)):
            cand = matches[j]
            if _is_tr_record_marker(full_text, cand.start()):
                continue
            end = cand.start()
            break
        # Or earlier — at the next section transition that lands inside the block
        for tpos, _ in transitions:
            if start < tpos < end:
                end = tpos
                break
        out.append((start, end, section))
    return out


def _char_pos_to_page(pos: int, page_starts: List[int]) -> int:
    lo, hi = 0, len(page_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if page_starts[mid] <= pos:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _slice_board_blocks(
    full_text: str,
    transitions: List[Tuple[int, Optional[str]]],
) -> List[Tuple[int, str, str]]:
    """Return ``(start_pos, event_type, block_text)`` for YİDK board-decision
    events. Each ``YIDK_PUB_REF_RE`` occurrence becomes a candidate block."""
    out: List[Tuple[int, str, str]] = []
    matches = list(YIDK_PUB_REF_RE.finditer(full_text))
    for i, m in enumerate(matches):
        section = _section_at_pos(transitions, m.start())
        if section not in BOARD_EVENT_TYPES:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        # Cap at next transition
        for tpos, _ in transitions:
            if m.start() < tpos < end:
                end = tpos
                break
        out.append((m.start(), section, full_text[m.start():end]))
    return out


# ---------------------------------------------------------------------------
# Top-level extraction
# ---------------------------------------------------------------------------

def parse_pdf_events(pdf_path: Path) -> Dict[str, Any]:
    fitz = _get_fitz()
    doc = fitz.open(str(pdf_path))
    page_texts = _scan_page_texts(doc)

    # Build the global text + page-start map once
    parts: List[str] = []
    page_starts: List[int] = []
    cursor = 0
    for i, text in enumerate(page_texts):
        page_starts.append(cursor)
        parts.append(text)
        cursor += len(text)
        if i + 1 < len(page_texts):
            parts.append("\n")
            cursor += 1
    full_text = "".join(parts)

    transitions = _section_transitions_in_text(full_text)
    bulletin_no, bulletin_date = extract_bulletin_metadata(full_text)

    events: List[TasarimEvent] = []

    for start, end, event_type in _find_inid_event_blocks(full_text, transitions):
        block = full_text[start:end]
        page_idx = _char_pos_to_page(start, page_starts)
        event = parse_inid_event(
            block, event_type=event_type, event_index=len(events) + 1, page=page_idx + 1
        )
        events.append(event)

    for start, event_type, block in _slice_board_blocks(full_text, transitions):
        page_idx = _char_pos_to_page(start, page_starts)
        event = parse_board_event(
            block, event_type=event_type, event_index=len(events) + 1, page=page_idx + 1
        )
        if event is not None:
            events.append(event)

    for e in events:
        e.fingerprint = fingerprint_event(e, bulletin_no)

    doc.close()

    return {
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date,
        "source": pdf_path.name,
        "page_count": len(page_texts),
        "event_count": len(events),
        "events": [_event_to_dict(e) for e in events],
    }


def _event_to_dict(event: TasarimEvent) -> Dict[str, Any]:
    d = asdict(event)
    # Drop empty optionals for cleaner JSON
    for k in ("holder", "previous_holder", "new_holder", "court"):
        if d.get(k) is None:
            d.pop(k, None)
    if not d.get("design_indices"):
        d.pop("design_indices", None)
    return d


# ---------------------------------------------------------------------------
# Issue-folder orchestration
# ---------------------------------------------------------------------------

EVENTS_FILENAME = "events.json"


def find_issue_folders(bulletins_root: Path) -> List[Path]:
    if not bulletins_root.is_dir():
        return []
    return sorted(p for p in bulletins_root.iterdir() if p.is_dir() and p.name.startswith("TS_"))


def events_are_fresh(issue_folder: Path) -> bool:
    pdf = issue_folder / "bulletin.pdf"
    events = issue_folder / EVENTS_FILENAME
    if not (pdf.is_file() and events.is_file()):
        return False
    try:
        return events.stat().st_size > 0 and events.stat().st_mtime >= pdf.stat().st_mtime
    except OSError:
        return False


def extract_issue_events(issue_folder: Path, *, force: bool = False) -> Dict[str, Any]:
    pdf = issue_folder / "bulletin.pdf"
    out_path = issue_folder / EVENTS_FILENAME
    if not pdf.is_file():
        raise FileNotFoundError(f"missing bulletin.pdf in {issue_folder}")
    if not force and events_are_fresh(issue_folder):
        logger.info("[=] %s events already up to date", issue_folder.name)
        return {"status": "skipped", "issue": issue_folder.name}

    logger.info("[*] parsing events for %s", issue_folder.name)
    started = time.time()
    payload = parse_pdf_events(pdf)
    payload["extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["extract_duration_seconds"] = round(time.time() - started, 1)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    type_counts: Dict[str, int] = {}
    for e in payload["events"]:
        type_counts[e["event_type"]] = type_counts.get(e["event_type"], 0) + 1
    logger.info(
        "[+] %s: %d events in %.1fs (types: %s)",
        issue_folder.name,
        payload["event_count"],
        payload["extract_duration_seconds"],
        ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())),
    )
    return {"status": "ok", "issue": issue_folder.name, **payload}


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdf_extract_tasarim_events", add_help=True)
    parser.add_argument("--issue", type=str, default=None)
    parser.add_argument("--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    if args.issue:
        target = args.bulletins_root / args.issue
        result = extract_issue_events(target, force=args.force)
        return 0 if result.get("status") in {"ok", "skipped"} else 1

    folders = find_issue_folders(args.bulletins_root)
    if not folders:
        logger.warning("no TS_* folders under %s", args.bulletins_root)
        return 0
    logger.info("scanning %d issue folder(s)", len(folders))
    failed = 0
    for folder in folders:
        try:
            extract_issue_events(folder, force=args.force)
        except Exception as e:
            logger.exception("issue %s failed: %r", folder.name, e)
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
