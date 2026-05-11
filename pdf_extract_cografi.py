"""Coğrafi İşaret ve Geleneksel Ürün Adı bulletin PDF metadata extractor.

Sister to ``pdf_extract_patent.py`` (Patent / Faydalı Model) and
``pdf_extract_tasarim.py`` (Tasarım). Reads a single per-bulletin PDF and
emits ``metadata.json`` next to it.

Covers the **full archive**: cards 1-99 (KHK 555 legacy era, 2017-2021)
and cards 100-220 (modern SMK 6769 era, 2021 onwards). Section types are
classified by **title content**, not by bulletin section number, since
section numbering shifts between bulletins (e.g. some omit Article 40,
some legacy bulletins skip directly from Tescil Edilen to Article 42).

Per-record sections recognised (semantic keys):

  * ``examined`` — applications under examination (KHK 555 art 11 OR SMK 6769 art 40)
  * ``registered`` — newly registered applications (Tescil Edilen)
  * ``article_40_modified`` — modifications under SMK art 40 OR KHK art 12
  * ``article_42_change_requests`` — open change requests (SMK art 42)
  * ``article_42_finalized`` — finalized changes (Kesinleşen Değişiklikler / Değişikliğe Uğramış Tesciller)
  * ``article_43_modified`` — SMK art 43 modifications (legacy era only)
  * ``corrections`` — Düzeltmeler (typos, missing logos, etc.)
  * ``gazette_only_announcements`` — bulletin-1 era special section

Section 2 (Sıralı Liste) is the parsing oracle: it gives the application
number, name, and start page for every record in sections 3+. Multiple
sections can share the same semantic key (e.g. KHK examined + SMK
examined in the same transitional bulletin); the dispatcher routes each
record to the body extent that contains its index-claimed start page.

Legacy era introduces field-label aliases (Başvuru Sahibinin Adı vs
Başvuru Yapan, Ürünün Adı vs Ürün / Ürün Grubu) handled transparently
by the labels-with-alternation regexes.

CLI::

    python pdf_extract_cografi.py --pdf path/to/220.pdf
    python pdf_extract_cografi.py --issue 220 --bulletins-root ./bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi
    python pdf_extract_cografi.py --all --bulletins-root ./bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi
    python pdf_extract_cografi.py --all --force          # re-extract even if metadata.json exists

B1.5 results (full archive, cards 1-220): 220/220 bulletins extracted,
3,527 records produced, 26 records flagged by the verifier (≈99.26%
record-level success). The remaining flagged records cluster into:

* **Source-data omissions** — fields literally absent from the source
  PDF (e.g. registered records missing the ``Başvuru No`` line,
  examined records with no labelled ``Başvuru Tarihi``). Parser cannot
  recover what isn't there.
* **Free-form change-text records** — bulletin-38-era art42 records
  whose body describes changes in prose without ``"old" ifadesi
  "new" şeklinde değiştirilmiştir`` anchors; the registration reference
  is captured but ``changes`` stays empty. The verifier intentionally
  does not flag empty-changes for these.
* **Index-vs-body count mismatches in a few bulletins** — typically a
  single sub-section that ships its body in a layout the parser
  doesn't yet recognise.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = (
    _LOCAL_PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)

EXTRACTOR_VERSION = 2  # B1.5 — adds legacy KHK 555 + Article 43 + gazette-only support
MIN_SUPPORTED_BULLETIN_NO = 1  # legacy support added in B1.5; both eras parsed

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [CI-EXTRACT] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.cografi_extract")


def _get_fitz():
    """Lazy import of PyMuPDF so unit tests don't need the C library at collection time."""
    import fitz
    return fitz


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

# Cover: bulletins 144+ use minimal Turkish-only covers ("Sayı 220" /
# "Yayım Tarihi" on separate lines). Bulletins 100-143 use a bilingual
# cover with a colon ("Sayı: 120" / "Yayım Tarihi: 01.03.2022"). Accept
# both shapes.
COVER_BULLETIN_NO_RE = re.compile(r"Sayı\s*:?\s+(\d{1,4})")
# Pre-2021 bulletins use the older "Yayın Tarihi" spelling; modern ones
# use "Yayım Tarihi". Allow either nasal consonant.
COVER_DATE_RE = re.compile(r"Yay[ıi][mn]\s+Tarihi\s*[:\s]+\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")

# Section types are classified by title content, not by the bulletin's
# section number. Bulletin 145 (for example) has Article 42 change requests
# as Section 5 because it omits Article 40 entirely; bulletins from 2023+
# add Section 7 (Article 42 finalized) and Section 8 (corrections).
SECTION_KEY_EXAMINED = "examined"
SECTION_KEY_REGISTERED = "registered"
SECTION_KEY_ART40 = "article_40_modified"
SECTION_KEY_ART42_REQUESTS = "article_42_change_requests"
SECTION_KEY_ART42_FINALIZED = "article_42_finalized"
SECTION_KEY_ART43 = "article_43_modified"
SECTION_KEY_CORRECTIONS = "corrections"
SECTION_KEY_GAZETTE_ONLY = "gazette_only_announcements"

ALL_SECTION_KEYS: Tuple[str, ...] = (
    SECTION_KEY_EXAMINED,
    SECTION_KEY_REGISTERED,
    SECTION_KEY_ART40,
    SECTION_KEY_ART42_REQUESTS,
    SECTION_KEY_ART42_FINALIZED,
    SECTION_KEY_ART43,
    SECTION_KEY_CORRECTIONS,
    SECTION_KEY_GAZETTE_ONLY,
)

# More-specific patterns must come first so e.g. "Kesinleşen Değişikliklerin"
# is matched before the looser "Değişikliklerin" pattern would catch it.
# Length-bounded gap between heading anchors — a single sub-index header
# fits in ~100 chars even when wrapped, so a 150-char ceiling stops
# non-greedy matchers from spanning into the next sub-index and reading
# its keyword as if it belonged to the current one (e.g. art42_finalized
# falsely matching at the change_requests offset because "Kesinleşen"
# appears in the next sub-index).
_GAP = r"[\s\S]{1,150}?"

SECTION_TITLE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # KHK 555 examined (legacy era) — semantically the same as SMK
    # examined records. Both "Gereğince" (1, 25) and "Kapsamında" (60+)
    # show up; trailing "Yayımı" is optional in some bulletins.
    (re.compile(rf"555\s+Sayılı{_GAP}İncelenen{_GAP}Başvurular(?:ın\s+Yayımı)?", re.IGNORECASE), SECTION_KEY_EXAMINED),
    (re.compile(r"İncelenen\s+Başvuruların\s+Yayımı", re.IGNORECASE), SECTION_KEY_EXAMINED),
    (re.compile(r"Tescil\s+Edilen\s+Başvuruların\s+Yayımı", re.IGNORECASE), SECTION_KEY_REGISTERED),
    # Article 40 (SMK) and KHK 555 Article 12 are both "post-publication
    # modifications". Title can end with "Başvuruların Yayımı" (modern)
    # or just "Başvurular" (legacy, no Yayımı). Match either.
    (re.compile(rf"(?:40\s*[ıi]nc[ıi]|555\s+Sayılı{_GAP}12\s*nci)\s+Maddesi{_GAP}Değişikliğe\s+Uğramış{_GAP}Başvurular(?:ın\s+Yayımı)?", re.IGNORECASE), SECTION_KEY_ART40),
    # Article 42 finalized — modern uses "Kesinleşen Değişikliklerin
    # Yayımı"; bulletin 43 (legacy) uses "Değişikliğe Uğramış Tesciller".
    # Both refer to the SAME concept (changes that have been finalized
    # against an existing registration).
    (re.compile(rf"42\s*nci\s+Maddesi{_GAP}(?:Kesinleşen{_GAP}Değişikliklerin?\s+Yayımı|Değişikliğe\s+Uğramış{_GAP}Tesciller)", re.IGNORECASE), SECTION_KEY_ART42_FINALIZED),
    # Article 42 change requests — accept all wording variants. Must NOT
    # also match the finalized variants above; ordering protects us.
    (re.compile(rf"42\s*nci\s+Maddesi{_GAP}Değişiklik\s+Talepler(?:in?\s+Yayımı|i)?", re.IGNORECASE), SECTION_KEY_ART42_REQUESTS),
    # Article 43 (SMK) — legacy-era; appeared from bulletin ~60 onwards.
    (re.compile(rf"43\s*[üu]nc[üu]\s+Maddesi{_GAP}Değişiklikler(?:in\s+Yayımı)?", re.IGNORECASE), SECTION_KEY_ART43),
    # Düzeltmeler accepts both legacy short title and modern "...Yayımı".
    (re.compile(r"Düzeltmeler(?:in\s+Yayımı)?", re.IGNORECASE), SECTION_KEY_CORRECTIONS),
    # Legacy-only special section: "Resmi Gazetede İlan Edilmiş Ancak
    # Yerel ya da Ulusal Gazetede İlan Edilmemiş ..." (bulletin 1 era).
    (re.compile(rf"Resmi\s+Gazetede\s+İlan\s+Edilmiş{_GAP}Başvurular(?:ın\s+Yayımı)?", re.IGNORECASE), SECTION_KEY_GAZETTE_ONLY),
]

# Section 2 sub-index headers — same classification by title content.
# Listesi is the index-page word; Yayımı is the body-page word.
INDEX_HEADER_TO_KEY: List[Tuple[re.Pattern, str]] = [
    (re.compile(rf"555\s+Sayılı{_GAP}İncelenen{_GAP}Listesi", re.IGNORECASE), SECTION_KEY_EXAMINED),
    (re.compile(r"İncelenen\s+Başvuruların\s+Listesi", re.IGNORECASE), SECTION_KEY_EXAMINED),
    (re.compile(r"Tescil\s+Edilen\s+Başvuruların\s+Listesi", re.IGNORECASE), SECTION_KEY_REGISTERED),
    # Both SMK Art 40 and KHK Art 12 are modifications.
    (re.compile(rf"(?:40\s*[ıi]nc[ıi]|555\s+Sayılı{_GAP}12\s*nci)\s+Maddesi{_GAP}Listesi", re.IGNORECASE), SECTION_KEY_ART40),
    (re.compile(rf"42\s*nci\s+Maddesi{_GAP}(?:Kesinleşen|Değişikliğe\s+Uğramış){_GAP}Listesi", re.IGNORECASE), SECTION_KEY_ART42_FINALIZED),
    (re.compile(rf"42\s*nci\s+Maddesi{_GAP}Listesi", re.IGNORECASE), SECTION_KEY_ART42_REQUESTS),
    (re.compile(rf"43\s*[üu]nc[üu]\s+Maddesi{_GAP}Listesi", re.IGNORECASE), SECTION_KEY_ART43),
    (re.compile(r"Düzeltmeler(?:in\s+Listesi)?", re.IGNORECASE), SECTION_KEY_CORRECTIONS),
    (re.compile(rf"Resmi\s+Gazetede\s+İlan\s+Edilmiş{_GAP}Listesi", re.IGNORECASE), SECTION_KEY_GAZETTE_ONLY),
]

# Sub-indices that emit Tescil Numarası (existing registration ID) instead of
# Başvuru Numarası (new application ID).
SECTION_KEYS_USING_REGNO: frozenset = frozenset({
    SECTION_KEY_REGISTERED,
    SECTION_KEY_ART42_REQUESTS,
    SECTION_KEY_ART42_FINALIZED,
})

# Subsection within a Section 2 sub-index: "Coğrafi İşaretler" (GI) or "Geleneksel Ürün Adları" (TPN)
INDEX_SUBSECTION_GI = re.compile(r"^\s*Coğrafi İşaretler\s*$", re.MULTILINE)
INDEX_SUBSECTION_TPN = re.compile(r"^\s*Geleneksel Ürün Adları\s*$", re.MULTILINE)

# A single index row, normalised. Sections 3/5 have application_no like C2022/000469;
# section 4 has registration_no like 1838 (plain integer).
# Application number — sometimes the source PDFs render the slash with
# stray whitespace ("C2023 / 000109"), so match it tolerantly everywhere.
_APPNO_FRAG = r"C\d{4}\s*/\s*\d{3,6}"

INDEX_ROW_APPNO = re.compile(
    rf"^(\d+)\.\s*\n+\s*({_APPNO_FRAG})\s*\n+\s*(.+?)\s*\n+\s*(\d{{1,4}}(?:\s*-\s*\d{{1,4}})?)\s*$",
    re.MULTILINE,
)
INDEX_ROW_REGNO = re.compile(
    r"^(\d+)\.\s*\n+\s*(\d{1,5})\s*\n+\s*(.+?)\s*\n+\s*(\d{1,4}(?:\s*-\s*\d{1,4})?)\s*$",
    re.MULTILINE,
)
INDEX_EMPTY_MSG = re.compile(r"bulunmamaktadır", re.IGNORECASE)

# TOC: each section is "<N>.Bölüm" on one line, then the title (possibly
# multi-line) wrapping into a dotted leader and a final page number. Some
# legacy bulletin TOCs use a single dot as the separator (e.g. bulletin
# 43 sec 7: "...Değişiklik Talepleri. 24"), so accept 1+ dots — the
# anchor that matters is "<dots><whitespace><digits><newline>".
TOC_SECTION_RE = re.compile(
    r"(\d+)\.Bölüm\s*\n+([\s\S]+?)\.+\s*(\d+)\s*\n",
)

# Section header in body: "<N>. Bölüm  \n<title>"
SECTION_HEADER_RE = re.compile(r"(\d+)\.\s*Bölüm\s*\n+([^\n]+)")

# Record start at top of body page: "<N>. <Name>"
RECORD_START_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$", re.MULTILINE)

# Header field row in record: "<Label> \n: <value>" — the value can wrap
# across additional lines until the next field label or a section header.
# We extract via per-label search rather than one giant pattern.
APPLICATION_NO_RE = re.compile(rf"Başvuru\s+No\s*\n?\s*:\s*({_APPNO_FRAG})")
# Fallback: section 5 (Article 40) rejection records and section 4 records
# without a labelled "Başvuru No:" line surface the application number
# inside a sentence like "<C-app-no> numaralı <name> ibareli coğrafi işaret".
APPLICATION_NO_PROSE_RE = re.compile(rf"({_APPNO_FRAG})\s+numaralı")
APPLICATION_DATE_RE = re.compile(r"Başvuru\s+Tarihi\s*\n?\s*:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
REGISTRATION_NO_RE = re.compile(r"Tescil\s+No\s*\n?\s*:\s*(\d{1,5})")
REGISTRATION_DATE_RE = re.compile(r"Tescil\s+Tarihi\s*\n?\s*:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
GI_NAME_RE = re.compile(r"Coğrafi İşaretin\s+Adı\s*\n?\s*:\s*(.+?)\s*\n")
GI_TYPE_RE = re.compile(r"Coğrafi İşaretin\s+Türü\s*\n?\s*:\s*(.+?)\s*\n")
REGISTRANT_NAME_RE = re.compile(r"Tescil\s+Ettiren\s*\n?\s*:\s*(.+?)\s*\n")
AGENT_RE = re.compile(r"Vekil\s*\n?\s*:\s*(.+?)\s*\n")

# Field aliases — legacy KHK-555-era bulletins use different labels for
# the same conceptual fields:
#   * applicant name: "Başvuru Yapan" (modern) | "Başvuru Sahibinin Adı" (legacy)
#   * applicant address: "Başvuru Yapanın Adresi" | "Başvuru Sahibinin Adresi"
#   * product group/type: "Ürün / Ürün Grubu" (modern path-style) | "Ürünün Adı" (legacy single)
# Each alias is tried in order until one matches.
PRODUCT_GROUP_RE = re.compile(
    r"(?:Ürün\s*/\s*Ürün\s+Grubu|Ürünün\s+Adı)\s*\n?\s*:\s*(.+?)\s*\n"
)
APPLICANT_NAME_RE = re.compile(
    r"(?:Başvuru\s+Yapan|Başvuru\s+Sahibinin\s+Adı)\s*\n?\s*:\s*(.+?)\s*\n"
)

# Multi-line capture: address fields and Kullanım Biçimi can wrap across lines
# until the next labelled field or the body subsection header. We anchor on
# the label and stop at the next known label.
NEXT_LABEL_LOOKAHEAD = (
    r"(?=(?:Başvuru\s+No|Başvuru\s+Tarihi|Coğrafi İşaretin|Ürün\s*/|Ürünün\s+Adı|"
    r"Başvuru\s+Yapan(?:ın)?|Başvuru\s+Sahibinin|"
    r"Vekil|Coğrafi\s+Sınır|Kullanım\s+Biçimi|Tescil\s+No|Tescil\s+Tarihi|Tescil\s+Ettiren(?:in)?|"
    r"Ürünün\s+Tanımı))"
)
APPLICANT_ADDR_RE = re.compile(
    r"(?:Başvuru\s+Yapanın\s+Adresi|Başvuru\s+Sahibinin\s+Adresi)\s*\n?\s*:\s*([\s\S]+?)"
    + NEXT_LABEL_LOOKAHEAD,
)
REGISTRANT_ADDR_RE = re.compile(
    r"Tescil\s+Ettirenin\s+Adresi\s*\n?\s*:\s*([\s\S]+?)" + NEXT_LABEL_LOOKAHEAD,
)
GEOGRAPHICAL_BOUNDARY_RE = re.compile(
    r"Coğrafi\s+Sınır\s*\n?\s*:\s*([\s\S]+?)" + NEXT_LABEL_LOOKAHEAD,
)
USAGE_DESCRIPTION_RE = re.compile(
    r"Kullanım\s+Biçimi\s*\n?\s*:\s*([\s\S]+?)" + NEXT_LABEL_LOOKAHEAD,
)

# Lightweight change-request record header — both Section 6 (open requests)
# and Section 7 (finalized) use a near-identical preamble that differs only
# in the connector words. Either:
#   "<reg_no> tescil sayılı <name> ibareli coğrafi işaretin tescil
#    kayıtlarında yapılması uygun bulunan değişiklikler ..."
# or:
#   "<reg_no> tescil numaralı <name> ibareli coğrafi işarete ilişkin
#    kesinleşen değişiklikler ..."
CHANGE_REQUEST_REGREF_RE = re.compile(
    r"(\d{1,5})\s+tescil\s+(?:sayılı|numaralı)\s+(.+?)\s+ibareli\s+coğrafi\s+işaret",
    re.IGNORECASE,
)
# Legacy alternative: bulletin 38-era preamble uses
#   "<reg_no> sayı ile <DD.MM.YYYY> tarihinde tescil edilen <name> tescil metninde ..."
CHANGE_REQUEST_REGREF_LEGACY_RE = re.compile(
    r"(\d{1,5})\s+sayı\s+ile\s+\d{1,2}\.\d{1,2}\.\d{4}\s+tarihinde\s+tescil\s+edilen\s+(.+?)\s+tescil\s+metninde",
    re.IGNORECASE,
)
# Quote-character class fragments. Built from chr() codepoints to avoid the
# nightmare of nesting ASCII straight-quotes inside string-delimited regex
# literals — Python r-strings cannot escape the delimiter quote.
_OPEN_Q = '[' + chr(34) + chr(8220) + ']'   # " or LEFT DOUBLE QUOTATION MARK
_CLOSE_Q = '[' + chr(34) + chr(8221) + ']'  # " or RIGHT DOUBLE QUOTATION MARK
_NOT_OPEN_Q = '[^' + chr(34) + chr(8220) + ']'

# Field-by-field change tuple shared by sections 6 and 7:
#   <Field>: <opt preamble> <quote> old <quote>
#   ifadesi, <quote> new <quote> şeklinde değiştirilmiştir
# Some records insert a preamble between the field colon and the opening
# quote (e.g. Başlık altında yer alan;); allow up to ~400 chars of
# non-quote text in between, bounded so the gap can't span into the
# next change's payload.
CHANGE_TUPLE_RE = re.compile(
    r"\s+([A-ZÇĞİÖŞÜ][\wÇĞİÖŞÜçğıöşü\s]*?):\s*"
    + _NOT_OPEN_Q + r"{0,400}?"
    + _OPEN_Q + r"([\s\S]+?)" + _CLOSE_Q
    + r"\s*\n+ifadesi,?\s*\n+\s*"
    + _OPEN_Q + r"([\s\S]+?)" + _CLOSE_Q
    + r"\s*\n+şeklinde\s+değiştirilmiştir",
    re.IGNORECASE,
)
# Section 8 (Düzeltmelerin Yayımı) record: a single sentence per correction
#   <bulletin_no> Sayılı ve <date> tarihli Resmi ... Bülteninde yayımlanmış
#   olan <C-app-no or reg-no> numaralı ve <name> ibareli coğrafi işaret
#   başvurusunda geçen <quote>old<quote> ibareleri <quote>new<quote>
#   şeklinde düzeltilmiştir.
CORRECTION_RE = re.compile(
    r"(\d{1,4})\s+Sayılı\s+ve\s+(\d{1,2}\.\d{1,2}\.\d{4})\s+tarihli[\s\S]+?yayımlanmış\s+olan\s+(\S+)\s+numaralı\s+ve\s+(.+?)\s+ibareli[\s\S]+?"
    + _OPEN_Q + r"([\s\S]+?)" + _CLOSE_Q
    + r"\s+ibareleri\s+"
    + _OPEN_Q + r"([\s\S]+?)" + _CLOSE_Q
    + r"\s+şeklinde\s+düzeltilmiştir",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Schema dataclasses (asdict() for JSON emission)
# ---------------------------------------------------------------------------

@dataclass
class IndexEntry:
    record_type: str          # "GI" or "TPN"
    section_key: str          # one of ALL_SECTION_KEYS
    name: str
    start_page: int
    application_no: Optional[str] = None  # examined / article_40_modified
    registration_no: Optional[int] = None  # registered / art42_*


@dataclass
class RecordHeader:
    application_no: Optional[str]
    application_date: Optional[str]      # ISO YYYY-MM-DD
    name: str
    product_group: Optional[str]
    gi_type: Optional[str]
    applicant_name: Optional[str]
    applicant_address: Optional[str]
    agent: Optional[str]
    geographical_boundary: Optional[str]
    usage_description: Optional[str]
    registration_no: Optional[int] = None      # section 4 only
    registration_date: Optional[str] = None    # section 4 only


@dataclass
class ChangeRequest:
    name: str
    existing_registration_no: int
    changes: List[Dict[str, str]] = field(default_factory=list)  # {field, old, new}


@dataclass
class CorrectionRecord:
    name: str
    referenced_bulletin_no: int
    referenced_bulletin_date: str        # ISO YYYY-MM-DD
    referenced_record_id: str            # could be a C-style appno or a plain reg no
    correction_old: str
    correction_new: str


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_pdf_extract_cografi.py)
# ---------------------------------------------------------------------------

def _to_iso_date(day: str, month: str, year: str) -> str:
    return f"{year}-{int(month):02d}-{int(day):02d}"


def parse_cover(text: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse cover-page text → ``(bulletin_no, bulletin_date_iso)``.

    Returns ``(None, None)`` when the cover does not contain the expected
    ``Sayı`` and ``Yayım Tarihi`` markers.
    """
    no_m = COVER_BULLETIN_NO_RE.search(text)
    date_m = COVER_DATE_RE.search(text)
    bulletin_no = int(no_m.group(1)) if no_m else None
    bulletin_date = _to_iso_date(date_m.group(1), date_m.group(2), date_m.group(3)) if date_m else None
    return bulletin_no, bulletin_date


def parse_toc(text: str) -> List[Dict[str, Any]]:
    """Parse TOC page → list of ``{section_number, title, start_page}`` entries.

    The title may span multiple lines on the rendered page; this collapses
    runs of whitespace into single spaces.
    """
    entries: List[Dict[str, Any]] = []
    for m in TOC_SECTION_RE.finditer(text):
        section_number = int(m.group(1))
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        start_page = int(m.group(3))
        entries.append({
            "section_number": section_number,
            "title": title,
            "start_page": start_page,
        })
    return entries


def classify_section_title(title: str) -> Optional[str]:
    """Return the semantic key (``examined``, ``registered``, ...) for a TOC
    entry's title text, or ``None`` for a section type we don't model."""
    for pat, key in SECTION_TITLE_PATTERNS:
        if pat.search(title):
            return key
    return None


def _parse_index_subsection(
    block_text: str,
    record_type: str,
    section_key: str,
) -> List[IndexEntry]:
    """Parse one (Coğrafi İşaretler | Geleneksel Ürün Adları) sub-table."""
    if INDEX_EMPTY_MSG.search(block_text):
        return []

    use_regno = section_key in SECTION_KEYS_USING_REGNO
    pattern = INDEX_ROW_REGNO if use_regno else INDEX_ROW_APPNO
    out: List[IndexEntry] = []
    for m in pattern.finditer(block_text):
        page_field = m.group(4).strip()
        # Page can be a single number ("8") or a range ("6-7"). Use the start.
        start_page = int(re.split(r"\s*-\s*", page_field, maxsplit=1)[0])
        if use_regno:
            entry = IndexEntry(
                record_type=record_type,
                section_key=section_key,
                name=m.group(3).strip(),
                start_page=start_page,
                registration_no=int(m.group(2)),
            )
        else:
            entry = IndexEntry(
                record_type=record_type,
                section_key=section_key,
                name=m.group(3).strip(),
                start_page=start_page,
                application_no=m.group(2).strip(),
            )
        out.append(entry)
    return out


def parse_index(text: str) -> List[IndexEntry]:
    """Parse the Section 2 page text into a list of ``IndexEntry`` rows.

    Each Section 2 page (or the merged text of all Section 2 pages) contains
    one or more sub-indices, each headed by a sentence like
    ``... İncelenen Başvuruların Listesi`` or
    ``Tescil Edilen Başvuruların Listesi``. Within each sub-index, two
    optional sub-sections follow: ``Coğrafi İşaretler`` (GI rows) and
    ``Geleneksel Ürün Adları`` (TPN rows).

    Entries are tagged with semantic ``section_key`` (string), not the
    bulletin's section number, since some bulletins skip section types
    (e.g. omit Article 40) and others add new ones (Article 42 finalized,
    corrections), which would shift the numbering.
    """
    # Some section titles (e.g. "42 nci Maddesi ... Kesinleşen ... Listesi")
    # also satisfy a less-specific pattern ("42 nci Maddesi ... Listesi"). The
    # patterns are listed most-specific-first; dedupe by start position so the
    # specific match wins and we don't emit a second phantom sub-index at the
    # same offset.
    seen_offsets: set = set()
    header_positions: List[Tuple[int, str]] = []  # (offset, section_key)
    for pat, key in INDEX_HEADER_TO_KEY:
        for m in pat.finditer(text):
            if m.start() in seen_offsets:
                continue
            seen_offsets.add(m.start())
            header_positions.append((m.start(), key))
    header_positions.sort()

    if not header_positions:
        return []

    out: List[IndexEntry] = []
    for i, (offset, section_key) in enumerate(header_positions):
        end = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(text)
        sub_block = text[offset:end]
        gi_m = INDEX_SUBSECTION_GI.search(sub_block)
        tpn_m = INDEX_SUBSECTION_TPN.search(sub_block)

        if gi_m:
            gi_end = tpn_m.start() if (tpn_m and tpn_m.start() > gi_m.start()) else len(sub_block)
            out.extend(_parse_index_subsection(
                sub_block[gi_m.start():gi_end], "GI", section_key,
            ))
        if tpn_m:
            out.extend(_parse_index_subsection(
                sub_block[tpn_m.start():], "TPN", section_key,
            ))
    return out


def _strip_trailing_label_artefacts(text: str) -> str:
    """Trim trailing whitespace and the page-number footer that can leak
    into a multi-line capture (e.g. ``\\n8\\n`` at end of address)."""
    text = text.rstrip()
    # Remove any trailing standalone digit-only line (page number) plus surrounding ws.
    text = re.sub(r"\s*\n\s*\d{1,4}\s*$", "", text)
    return text.strip()


def parse_record_header(text: str, *, is_section_4: bool = False) -> RecordHeader:
    """Parse the labelled header table at the top of a record body slice.

    Set ``is_section_4=True`` to also capture the Tescil No / Tescil Tarihi /
    Tescil Ettiren fields. The applicant name/address fields fall back to
    Tescil Ettiren / Tescil Ettirenin Adresi when ``is_section_4=True`` and
    the Başvuru Yapan equivalents are absent.
    """
    def _g1(pattern: re.Pattern) -> Optional[str]:
        m = pattern.search(text)
        return m.group(1).strip() if m else None

    def _multiline(pattern: re.Pattern) -> Optional[str]:
        m = pattern.search(text)
        if not m:
            return None
        raw = m.group(1)
        cleaned = re.sub(r"\s+", " ", raw).strip()
        return _strip_trailing_label_artefacts(cleaned) or None

    app_no = _g1(APPLICATION_NO_RE) or _g1(APPLICATION_NO_PROSE_RE)
    app_date_m = APPLICATION_DATE_RE.search(text)
    app_date = _to_iso_date(*app_date_m.groups()) if app_date_m else None
    name = _g1(GI_NAME_RE) or ""
    product_group = _g1(PRODUCT_GROUP_RE)
    gi_type = _g1(GI_TYPE_RE)

    applicant_name = _g1(APPLICANT_NAME_RE)
    applicant_addr = _multiline(APPLICANT_ADDR_RE)
    if is_section_4:
        registrant_name = _g1(REGISTRANT_NAME_RE)
        registrant_addr = _multiline(REGISTRANT_ADDR_RE)
        applicant_name = applicant_name or registrant_name
        applicant_addr = applicant_addr or registrant_addr

    header = RecordHeader(
        application_no=app_no,
        application_date=app_date,
        name=name,
        product_group=product_group,
        gi_type=gi_type,
        applicant_name=applicant_name,
        applicant_address=applicant_addr,
        agent=_g1(AGENT_RE),
        geographical_boundary=_multiline(GEOGRAPHICAL_BOUNDARY_RE),
        usage_description=_multiline(USAGE_DESCRIPTION_RE),
    )

    if is_section_4:
        reg_no_m = REGISTRATION_NO_RE.search(text)
        if reg_no_m:
            header.registration_no = int(reg_no_m.group(1))
        reg_date_m = REGISTRATION_DATE_RE.search(text)
        if reg_date_m:
            header.registration_date = _to_iso_date(*reg_date_m.groups())

    return header


def parse_change_request(text: str) -> Optional[ChangeRequest]:
    """Parse an Article 42 change-request or finalized-change block.

    Modern (B1) records use a structured "<old> ifadesi <new> şeklinde
    değiştirilmiştir" tuple per change. Legacy bulletins (e.g. 38) use a
    different preamble shape and free-form change text without explicit
    old/new separators — we still capture the registration reference and
    name; structured changes will be empty for legacy records (caller may
    treat that as "raw_text" fallback if desired).
    """
    ref_m = CHANGE_REQUEST_REGREF_RE.search(text)
    if not ref_m:
        ref_m = CHANGE_REQUEST_REGREF_LEGACY_RE.search(text)
    if not ref_m:
        return None
    cr = ChangeRequest(
        name=ref_m.group(2).strip(),
        existing_registration_no=int(ref_m.group(1)),
    )
    for change_m in CHANGE_TUPLE_RE.finditer(text):
        cr.changes.append({
            "field": change_m.group(1).strip(),
            "old": re.sub(r"\s+", " ", change_m.group(2)).strip(),
            "new": re.sub(r"\s+", " ", change_m.group(3)).strip(),
        })
    return cr


def parse_correction(text: str) -> Optional[CorrectionRecord]:
    """Parse a Section 8 (Düzeltmelerin Yayımı) correction block.

    Each record is a single sentence referring back to a previously
    published bulletin: ``<N> Sayılı ve <date> tarihli ... yayımlanmış olan
    <id> numaralı ve <name> ibareli coğrafi işaret başvurusunda geçen "X"
    ibareleri "Y" şeklinde düzeltilmiştir.``
    """
    m = CORRECTION_RE.search(text)
    if not m:
        return None
    return CorrectionRecord(
        name=m.group(4).strip(),
        referenced_bulletin_no=int(m.group(1)),
        referenced_bulletin_date=_to_iso_date(*m.group(2).split(".")),
        referenced_record_id=m.group(3).strip(),
        correction_old=re.sub(r"\s+", " ", m.group(5)).strip(),
        correction_new=re.sub(r"\s+", " ", m.group(6)).strip(),
    )


# Backwards-compatible alias kept until callers migrate.
parse_section6_change_request = parse_change_request


# ---------------------------------------------------------------------------
# Bulletin-level orchestration
# ---------------------------------------------------------------------------

def _read_pages(pdf_path: Path) -> List[str]:
    fitz = _get_fitz()
    doc = fitz.open(pdf_path)
    try:
        return [doc.load_page(i).get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


def _slice_record_body(
    pages: List[str],
    start_page_1based: int,
    next_start_page_1based: Optional[int],
    section_end_page_1based: int,
    section_start_page_1based: int,
) -> str:
    """Concatenate page texts for a single record's slice.

    The Section 2 index page numbers are occasionally off by one — the
    indexed start page can point at the *second* page of the record while
    the labelled header table sits on the prior page. We therefore extend
    the slice one page earlier when possible, clipped at the section's
    own start page so we don't bleed across section boundaries.
    """
    real_start = max(start_page_1based - 1, section_start_page_1based)
    end = next_start_page_1based - 1 if next_start_page_1based else section_end_page_1based
    end = min(end, section_end_page_1based)
    return "\n".join(pages[real_start - 1: end])


def extract_bulletin(pdf_path: Path) -> Dict[str, Any]:
    """Extract a single modern-format cografi bulletin PDF into the dict
    that becomes ``metadata.json``.

    Refuses to process bulletins below ``MIN_SUPPORTED_BULLETIN_NO`` since
    those use the legacy KHK 555 schema.
    """
    pages = _read_pages(pdf_path)
    if len(pages) < 5:
        raise ValueError(f"{pdf_path}: too few pages ({len(pages)}) for a cografi bulletin")

    bulletin_no, bulletin_date = parse_cover(pages[0])
    if bulletin_no is None:
        raise ValueError(f"{pdf_path}: cover page does not contain Sayı marker")
    if bulletin_no < MIN_SUPPORTED_BULLETIN_NO:
        raise ValueError(
            f"{pdf_path}: bulletin {bulletin_no} below MIN_SUPPORTED_BULLETIN_NO={MIN_SUPPORTED_BULLETIN_NO}"
        )

    toc = parse_toc(pages[1])
    # Per-bulletin map from section number -> semantic key, derived from
    # TOC titles. Section numbering shifts between bulletins (some omit
    # Article 40, some add Article 42 finalized + corrections), so we
    # cannot bake the mapping into the extractor.
    section_key_by_number: Dict[int, Optional[str]] = {}
    for e in toc:
        if e["section_number"] >= 3:
            section_key_by_number[e["section_number"]] = classify_section_title(e["title"])

    sections_present = sorted(n for n in section_key_by_number)

    # Merge the Section 2 pages (typically p4..p7 depending on how many
    # sub-indices the bulletin has) into one blob for index parsing.
    sec2_entry = next((e for e in toc if e["section_number"] == 2), None)
    sec3_entry = next((e for e in toc if e["section_number"] == 3), None)
    sec2_start = sec2_entry["start_page"] if sec2_entry else 4
    sec2_end = (sec3_entry["start_page"] - 1) if sec3_entry else sec2_start + 1
    index_text = "\n".join(pages[sec2_start - 1: sec2_end])
    index_entries = parse_index(index_text)

    # Per-section body extents (clipped to the next section's start page so
    # a record body cannot bleed into another section). Stored as a LIST
    # per key because some bulletins (e.g. 63, 105) have multiple sections
    # that classify to the same semantic key (KHK 555 examined + SMK 6769
    # examined both => "examined").
    body_extents_by_key: Dict[str, List[Tuple[int, int]]] = {}
    body_sections = sorted(
        [(n, k, next(e for e in toc if e["section_number"] == n)["start_page"])
         for n, k in section_key_by_number.items() if k is not None],
        key=lambda t: t[2],
    )
    for i, (n, key, start_page) in enumerate(body_sections):
        end = body_sections[i + 1][2] - 1 if i + 1 < len(body_sections) else len(pages)
        # Some bulletins' TOC page numbers for the section itself are off
        # by one (the section header sits on the page *before* the TOC's
        # claimed start). Allow per-record slices to look back one page
        # below this section's nominal start_page, but never below 1.
        body_extents_by_key.setdefault(key, []).append((max(start_page - 1, 1), end))

    records: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ALL_SECTION_KEYS}

    by_key: Dict[str, List[IndexEntry]] = {}
    for ie in index_entries:
        by_key.setdefault(ie.section_key, []).append(ie)
    for sec_entries in by_key.values():
        sec_entries.sort(key=lambda e: e.start_page)

    for section_key, sec_entries in by_key.items():
        extents = body_extents_by_key.get(section_key)
        if not extents:
            logger.warning("section_key %r has index entries but no body extents", section_key)
            continue
        for i, entry in enumerate(sec_entries):
            # Pick the body extent that contains this entry's start_page.
            # Bulletins with multiple sections per semantic key (KHK +
            # SMK examined) need this so each record's slice lands in the
            # right page range.
            matching = [(s, e) for s, e in extents if s <= entry.start_page <= e]
            sec_start, sec_end = matching[0] if matching else extents[0]
            # next_start: the next record's start_page IF it falls in
            # this same extent (don't clip across extents).
            next_in_extent = next(
                (e.start_page for e in sec_entries[i + 1:]
                 if sec_start <= e.start_page <= sec_end),
                None,
            )
            body = _slice_record_body(pages, entry.start_page, next_in_extent, sec_end, sec_start)
            record_dict: Dict[str, Any] = {
                "record_type": entry.record_type,
                "name": entry.name,
                "start_page": entry.start_page,
            }
            if section_key in (SECTION_KEY_ART42_REQUESTS, SECTION_KEY_ART42_FINALIZED):
                cr = parse_change_request(body)
                if cr is None:
                    logger.warning("%s entry %r at p%d: no change-request match",
                                   section_key, entry.name, entry.start_page)
                    record_dict["raw_text"] = body[:1000]
                else:
                    record_dict.update(asdict(cr))
            elif section_key == SECTION_KEY_CORRECTIONS:
                corr = parse_correction(body)
                if corr is None:
                    logger.warning("%s entry %r at p%d: no correction match",
                                   section_key, entry.name, entry.start_page)
                    record_dict["raw_text"] = body[:1000]
                else:
                    record_dict.update(asdict(corr))
            else:
                header = parse_record_header(body, is_section_4=(section_key == SECTION_KEY_REGISTERED))
                header_dict = asdict(header)
                # Index name is authoritative — only override if the header
                # actually parsed one out (and it's non-empty). This
                # handles records whose body header is on a different page
                # than the index's start_page advertised, where the header
                # regex misses but the index name is still trustworthy.
                if not header_dict.get("name"):
                    header_dict.pop("name", None)
                record_dict.update(header_dict)
            records[section_key].append(record_dict)

    return {
        "bulletin_no": bulletin_no,
        "bulletin_date": bulletin_date,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extractor_version": EXTRACTOR_VERSION,
        "sections_present": sections_present,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Per-PDF quality verifier
# ---------------------------------------------------------------------------

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _filename_bulletin_no(pdf_path: Path) -> Optional[int]:
    """Return the bulletin number implied by the filename, or ``None``.

    Accepts ``{N}.pdf`` (flat layout) and ``CI_{N}_{date}/bulletin.pdf``
    (subfolder layout). Returns ``None`` for legacy ``{N1}-{N2}.pdf``
    bundles since they have no single bulletin number.
    """
    if pdf_path.name == "bulletin.pdf":
        parent = pdf_path.parent.name
        m = re.match(r"^CI_(\d+)_", parent)
        return int(m.group(1)) if m else None
    stem = pdf_path.stem
    if "-" in stem:
        return None
    try:
        return int(stem)
    except ValueError:
        return None


def verify_extraction(pdf_path: Path, result: Dict[str, Any]) -> List[str]:
    """Run cheap programmatic checks on an extraction result.

    Returns a list of problem strings; empty list means the result passes
    every structural check. The check is intentionally about *structure*
    and *cross-consistency* (bulletin no in filename matches metadata,
    Section 2 index counts match per-section record counts, required
    fields are non-null) rather than semantic correctness — visual
    spot-checks remain the source of truth for that.
    """
    problems: List[str] = []

    expected_no = _filename_bulletin_no(pdf_path)
    if expected_no is not None and result.get("bulletin_no") != expected_no:
        problems.append(
            f"bulletin_no={result.get('bulletin_no')} but filename implies {expected_no}"
        )

    bdate = result.get("bulletin_date")
    if not bdate:
        problems.append("bulletin_date is empty")
    elif not ISO_DATE_RE.match(str(bdate)):
        problems.append(f"bulletin_date {bdate!r} not ISO YYYY-MM-DD")

    sp = result.get("sections_present") or []
    if any(not isinstance(s, int) or s < 3 or s > 9 for s in sp):
        problems.append(f"sections_present contains invalid values: {sp}")

    # Re-derive Section 2 index counts from the source PDF and cross-check
    # by semantic section_key (since section numbers shift between bulletins).
    try:
        pages = _read_pages(pdf_path)
        if len(pages) >= 5:
            toc = parse_toc(pages[1])
            sec2 = next((e for e in toc if e["section_number"] == 2), None)
            sec3 = next((e for e in toc if e["section_number"] == 3), None)
            if sec2 and sec3:
                idx_text = "\n".join(pages[sec2["start_page"] - 1: sec3["start_page"] - 1])
                idx_entries = parse_index(idx_text)
                idx_counts: Dict[str, int] = {}
                for e in idx_entries:
                    idx_counts[e.section_key] = idx_counts.get(e.section_key, 0) + 1
                records = result.get("records") or {}
                for key, expected in idx_counts.items():
                    got = len(records.get(key) or [])
                    if got != expected:
                        problems.append(
                            f"{key}: index says {expected} records, got {got}"
                        )
    except Exception as e:  # pragma: no cover - defensive
        problems.append(f"index re-parse failed: {e!r}")

    records = result.get("records") or {}
    for r in records.get("examined", []):
        nm = r.get("name") or "?"
        if not r.get("application_no"):
            problems.append(f"examined {nm!r}: missing application_no")
        if not r.get("application_date"):
            problems.append(f"examined {nm!r}: missing application_date")
        if not r.get("gi_type"):
            problems.append(f"examined {nm!r}: missing gi_type")
    for r in records.get("registered", []):
        nm = r.get("name") or "?"
        if not r.get("application_no"):
            problems.append(f"registered {nm!r}: missing application_no")
        if r.get("registration_no") is None:
            problems.append(f"registered {nm!r}: missing registration_no")
        if not r.get("registration_date"):
            problems.append(f"registered {nm!r}: missing registration_date")
    for r in records.get("article_40_modified", []):
        nm = r.get("name") or "?"
        if not r.get("application_no"):
            problems.append(f"art40 {nm!r}: missing application_no")
    for r in records.get("article_42_change_requests", []):
        nm = r.get("name") or "?"
        if r.get("existing_registration_no") is None:
            problems.append(f"art42 {nm!r}: missing existing_registration_no")
        # Legacy-era art42 records (bulletin 38-era) describe changes in
        # free-form prose with no "<old> ifadesi <new>" anchors; a record
        # whose preamble matched but yielded zero structured changes is
        # still a valid extraction. Don't flag those.

    return problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _is_pdf_magic(path: Path) -> bool:
    """True when the first 5 bytes are ``%PDF-`` (real PDF, not RAR-as-PDF)."""
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _output_path_for(pdf_path: Path) -> Path:
    """Sibling JSON path for an extractor input.

    For the post-migration subfolder layout (``CI_{N}_{date}/bulletin.pdf``)
    this lands next to the source as ``CI_{N}_{date}/metadata.json``. For
    the pre-migration flat layout (``{N}.pdf``) it lands as
    ``{N}_metadata.json`` next to the PDF. Either way the JSON is a sibling.
    """
    if pdf_path.name == "bulletin.pdf":
        return pdf_path.with_name("metadata.json")
    return pdf_path.with_suffix("").with_name(pdf_path.stem + "_metadata.json")


def _iter_inputs(bulletins_root: Path) -> List[Path]:
    """Yield every real cografi PDF under ``bulletins_root`` (both layouts).

    Files with a ``.pdf`` extension that are actually RAR archives (the
    legacy ``1-50.pdf`` / ``51-99.pdf`` bundles before migration) are
    filtered out via magic-byte check so they don't pollute the run with
    FileDataError noise.
    """
    found: List[Path] = []
    if not bulletins_root.is_dir():
        return found
    for entry in bulletins_root.iterdir():
        if entry.is_file() and entry.suffix.lower() == ".pdf":
            if _is_pdf_magic(entry):
                found.append(entry)
            else:
                logger.info("[skip] %s: not a real PDF (magic-byte check)", entry.name)
        elif entry.is_dir() and entry.name.startswith("CI_"):
            sub = entry / "bulletin.pdf"
            if sub.is_file():
                found.append(sub)
    return sorted(found)


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pdf_extract_cografi", add_help=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf", type=Path, help="extract a single PDF by direct path")
    src.add_argument("--issue", type=int, help="extract by bulletin number (requires --bulletins-root)")
    src.add_argument("--all", action="store_true", help="extract every modern bulletin under --bulletins-root")
    parser.add_argument(
        "--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"bulletins root (default: {_LOCAL_DEFAULT_BULLETINS_DIR})",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing metadata.json")
    return parser.parse_args(argv)


def _process_one(pdf_path: Path, *, force: bool) -> int:
    out = _output_path_for(pdf_path)
    if out.exists() and not force:
        logger.info("[=] %s already extracted, skipping", out.name)
        return 0
    try:
        result = extract_bulletin(pdf_path)
    except ValueError as e:
        logger.warning("[skip] %s", e)
        return 0
    except Exception as e:  # pragma: no cover - defensive at CLI boundary
        logger.error("[!] %s: %r", pdf_path, e)
        return 1
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[+] %s wrote %d examined / %d registered / %d art40 / %d art42",
                out.name,
                len(result["records"]["examined"]),
                len(result["records"]["registered"]),
                len(result["records"]["article_40_modified"]),
                len(result["records"]["article_42_change_requests"]))
    problems = verify_extraction(pdf_path, result)
    if problems:
        # Surface the bulletin_no in the log line. For subfolder-layout
        # inputs every path's stem is "bulletin", which is useless for
        # triage; the bulletin_no from the extracted result is what we want.
        ident = str(result.get("bulletin_no") or pdf_path.stem)
        for p in problems:
            logger.warning("[?] %s: %s", ident, p)
        return 2  # extracted, but quality issues
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    failures = 0

    if args.pdf:
        return _process_one(args.pdf, force=args.force)

    if args.issue is not None:
        # Try subfolder first, then flat-layout fallback (works pre/post migration).
        candidates = list(args.bulletins_root.glob(f"CI_{args.issue}_*/bulletin.pdf"))
        if not candidates:
            flat = args.bulletins_root / f"{args.issue}.pdf"
            if flat.is_file():
                candidates = [flat]
        if not candidates:
            logger.error("no PDF found for issue %d under %s", args.issue, args.bulletins_root)
            return 1
        return _process_one(candidates[0], force=args.force)

    # --all
    inputs = _iter_inputs(args.bulletins_root)
    logger.info("found %d input PDFs under %s", len(inputs), args.bulletins_root)
    started = time.time()
    quality_issues = 0
    for pdf in inputs:
        rc = _process_one(pdf, force=args.force)
        if rc == 1:
            failures += 1
        elif rc == 2:
            quality_issues += 1
    logger.info("done in %.1fs (failures=%d, quality_issues=%d, total=%d)",
                time.time() - started, failures, quality_issues, len(inputs))
    return 0 if failures == 0 and quality_issues == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
