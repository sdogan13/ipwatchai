"""Unit tests for ``pipeline.reconcile_patent`` helpers.

Built one helper at a time. Each step adds its own test block so failures
point cleanly at the unit under test.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pipeline.reconcile_patent import (
    CLIArgs,
    CanonicalRecord,
    _build_stats,
    _cd_figures,
    _dmy_to_iso,
    _group_by_bulletin,
    _merge_figures,
    _normalise_bulletin_no,
    _normalize_cd_attorney,
    _normalize_cd_party,
    _normalize_cd_priority,
    _normalize_pdf_attorney_to_list,
    _normalize_pdf_figure,
    _normalize_pdf_party,
    _normalize_pdf_priority,
    _page_range_or_none,
    _pick_longer_title,
    _process_one,
    _record_to_dict,
    classify_metadata_json,
    load_cd_metadata,
    load_pdf_metadata,
    main,
    merge_records,
    normalize_cd_record,
    normalize_pdf_record,
    parse_argv,
    reconcile_metadata,
    unified_filename,
)


# ---------------------------------------------------------------------------
# Step 4.1 — JSON loaders + CanonicalRecord dataclass
# ---------------------------------------------------------------------------


def _write_json(tmp_path: Path, name: str, doc: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_load_cd_metadata_returns_doc(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [{"application_no": "2017/15048"}],
        "stats": {"patents": 1},
    }
    path = _write_json(tmp_path, "cd.json", doc)

    assert load_cd_metadata(path) == doc


def test_load_pdf_metadata_returns_doc(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "records": [{"application_no": "2021/011498"}],
        "stats": {"records": 1},
    }
    path = _write_json(tmp_path, "pdf.json", doc)

    assert load_pdf_metadata(path) == doc


def test_load_cd_metadata_rejects_pdf_shape(tmp_path: Path) -> None:
    """A swapped --cd-json/--pdf-json must fail loud, not silently merge."""
    pdf_doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "records": [],          # PDF key
        "stats": {},
    }
    path = _write_json(tmp_path, "swapped.json", pdf_doc)

    with pytest.raises(ValueError, match="not a CD metadata doc"):
        load_cd_metadata(path)


def test_load_pdf_metadata_rejects_cd_shape(tmp_path: Path) -> None:
    cd_doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [],          # CD key
        "stats": {},
    }
    path = _write_json(tmp_path, "swapped.json", cd_doc)

    with pytest.raises(ValueError, match="not a PDF metadata doc"):
        load_pdf_metadata(path)


def test_load_cd_metadata_rejects_non_object(tmp_path: Path) -> None:
    path = _write_json(tmp_path, "list.json", ["not", "a", "dict"])
    with pytest.raises(ValueError, match="expected JSON object"):
        load_cd_metadata(path)


def test_load_cd_metadata_rejects_non_list_patents(tmp_path: Path) -> None:
    doc = {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": {},          # wrong type
        "stats": {},
    }
    path = _write_json(tmp_path, "bad.json", doc)
    with pytest.raises(ValueError, match="'patents' must be a list"):
        load_cd_metadata(path)


def test_canonical_record_defaults() -> None:
    """All collection fields default to empty list — no None-vs-[] traps."""
    rec = CanonicalRecord(application_no="2017/15048")

    assert rec.application_no == "2017/15048"
    assert rec.ipc_classes == []
    assert rec.holders == []
    assert rec.inventors == []
    assert rec.attorneys == []
    assert rec.priorities == []
    assert rec.figures == []
    assert rec.source_format == "CD"
    assert rec.title is None
    assert rec.page_range is None


def test_canonical_record_asdict_is_jsonable() -> None:
    """asdict() output must roundtrip through json.dumps without TypeError."""
    rec = CanonicalRecord(
        application_no="2021/011498",
        application_date="2014-01-10",
        publication_no="TR 2021 011498 B",
        kind_code="B",
        record_type="GRANTED_PATENT",
        title="Test",
        ipc_classes=["A61M 5/31"],
        holders=[{"name": "ACME", "country": "TR"}],
        page_range=[117, 117],
        source_format="BOTH",
    )

    payload = asdict(rec)
    encoded = json.dumps(payload, ensure_ascii=False)

    decoded = json.loads(encoded)
    assert decoded["application_no"] == "2021/011498"
    assert decoded["holders"] == [{"name": "ACME", "country": "TR"}]
    assert decoded["page_range"] == [117, 117]
    assert decoded["source_format"] == "BOTH"


# ---------------------------------------------------------------------------
# Step 4.2 — normalize_cd_record
# ---------------------------------------------------------------------------


def test_dmy_to_iso_happy_path() -> None:
    assert _dmy_to_iso("22/12/2025") == "2025-12-22"
    assert _dmy_to_iso("05/10/2017") == "2017-10-05"
    assert _dmy_to_iso(" 1/3/2020 ") == "2020-03-01"  # tolerates 1-digit + outer ws


def test_dmy_to_iso_returns_none_on_garbage() -> None:
    assert _dmy_to_iso(None) is None
    assert _dmy_to_iso("") is None
    assert _dmy_to_iso("not-a-date") is None
    assert _dmy_to_iso("2025-12-22") is None      # ISO not accepted (use is for CD only)
    assert _dmy_to_iso("31/02/2020") is None      # invalid Feb 31


def test_normalize_cd_party_renames_title_to_name() -> None:
    party = {
        "title": "ACME ANONİM ŞİRKETİ",
        "address": "A St 1",
        "state": "Tire",
        "postal_code": "",                # empty — should be dropped
        "city": "İzmir",
        "country": "TR",
    }
    out = _normalize_cd_party(party)
    assert out == {
        "name": "ACME ANONİM ŞİRKETİ",
        "address": "A St 1",
        "state": "Tire",
        "city": "İzmir",
        "country": "TR",
    }
    assert "postal_code" not in out
    assert "title" not in out


def test_normalize_cd_party_handles_empty_dict() -> None:
    """Defensive: stays-shaped even if CD ships an empty holder."""
    out = _normalize_cd_party({})
    assert out == {"name": ""}


def test_normalize_cd_attorney_drops_empty_fields() -> None:
    attorney = {
        "no": "361",
        "name": "ERDEM KAYA",
        "address": "",                    # empty — dropped
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }
    out = _normalize_cd_attorney(attorney)
    assert out == {
        "no": "361",
        "name": "ERDEM KAYA",
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }
    assert "address" not in out


def test_normalize_cd_priority_iso_date() -> None:
    out = _normalize_cd_priority({
        "priority_no": "2020/05105",
        "priority_date": "31/03/2020",
        "country": "TR",
    })
    assert out == {
        "priority_no": "2020/05105",
        "priority_date": "2020-03-31",
        "country": "TR",
    }


def test_normalize_cd_priority_skips_unparseable_date() -> None:
    out = _normalize_cd_priority({
        "priority_no": "X",
        "priority_date": "",
        "country": "TR",
    })
    assert "priority_date" not in out
    assert out["priority_no"] == "X"


def test_cd_figures_wraps_image_path() -> None:
    assert _cd_figures("data/images/2017/15048.tif") == [
        {"image_path": "data/images/2017/15048.tif"},
    ]


def test_cd_figures_empty_for_missing_path() -> None:
    assert _cd_figures("") == []
    assert _cd_figures(None) == []
    assert _cd_figures("   ") == []


def test_normalize_cd_record_full() -> None:
    """Real shape pulled from 2025_07_metadata.json (bulletin 2025/8)."""
    cd = {
        "application_no": "2017/15048",
        "application_date": "05/10/2017",
        "patent_no": "",
        "patent_date": "",
        "ipc_codes": ["A61M 5/31", "A61J 1/14"],
        "publication_no": "TR 2017 15048 U3",
        "publication_type": "73",
        "publication_date": "22/12/2025",
        "title": "EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ VE KİLİTLEME YÖNTEMİ",
        "patent_type": "2",
        "abstract": "Kısa özet.",
        "image_path": "data/images/2017/15048.tif",
        "holders": [{
            "title": "ACME",
            "address": "X St 1",
            "state": "",
            "postal_code": "",
            "city": "İzmir",
            "country": "TR",
        }],
        "inventors": [{
            "title": "JANE DOE",
            "address": "",
            "state": "",
            "postal_code": "",
            "city": "",
            "country": "",
        }],
        "attorneys": [{
            "no": "361",
            "name": "ERDEM KAYA",
            "address": "",
            "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
        }],
        "priorities": [{
            "priority_no": "2020/05105",
            "priority_date": "31/03/2020",
            "country": "TR",
        }],
    }

    rec = normalize_cd_record(cd)

    assert rec.application_no == "2017/15048"
    assert rec.application_date == "2017-10-05"          # DD/MM -> ISO
    assert rec.publication_no == "TR 2017 15048 U3"
    assert rec.publication_date == "2025-12-22"
    assert rec.kind_code == "U3"                          # extracted
    assert rec.record_type == "UNKNOWN"                   # gap: U3 not mapped (memory note)
    assert rec.title.startswith("EMNİYET")
    assert rec.abstract == "Kısa özet."
    assert rec.ipc_classes == ["A61M 5/31", "A61J 1/14"]  # renamed key
    assert rec.holders == [{
        "name": "ACME",
        "address": "X St 1",
        "city": "İzmir",
        "country": "TR",
    }]
    assert rec.inventors == [{"name": "JANE DOE"}]
    assert rec.attorneys == [{
        "no": "361",
        "name": "ERDEM KAYA",
        "firm": "ERDEM KAYA PATENT VE DAN. A.Ş.",
    }]
    assert rec.priorities == [{
        "priority_no": "2020/05105",
        "priority_date": "2020-03-31",
        "country": "TR",
    }]
    assert rec.figures == [{"image_path": "data/images/2017/15048.tif"}]
    assert rec.patent_type == "2"
    assert rec.source_format == "CD"
    assert rec.page_range is None                          # PDF-only field


def test_normalize_cd_record_classify_known_kind() -> None:
    """T4 should classify cleanly even though A3/U3/T7 don't (gap memory)."""
    cd = {
        "application_no": "2022/014462",
        "publication_no": "TR 2022 014462 T4",
        "publication_date": "21/08/2025",
        "application_date": "10/01/2014",
        "ipc_codes": [],
    }
    rec = normalize_cd_record(cd)
    assert rec.kind_code == "T4"
    assert rec.record_type == "GRANTED_PATENT"


def test_normalize_cd_record_handles_missing_publication_no() -> None:
    """Some CD rows ship blank publication_no — must not crash."""
    cd = {
        "application_no": "2017/15048",
        "publication_no": "",
        "ipc_codes": [],
    }
    rec = normalize_cd_record(cd)
    assert rec.publication_no is None
    assert rec.kind_code is None
    assert rec.record_type is None
    assert rec.application_date is None
    assert rec.holders == []
    assert rec.figures == []


# ---------------------------------------------------------------------------
# Step 4.3 — normalize_pdf_record
# ---------------------------------------------------------------------------


def test_normalize_pdf_party_drops_empty_fields() -> None:
    party = {"name": "ACME", "address": "", "country": "TR"}
    assert _normalize_pdf_party(party) == {"name": "ACME", "country": "TR"}


def test_normalize_pdf_party_handles_null_address() -> None:
    """PDF parser ships null for absent address — must not survive output."""
    party = {"name": "ACME", "address": None, "country": None}
    assert _normalize_pdf_party(party) == {"name": "ACME"}


def test_normalize_pdf_attorney_object_to_list() -> None:
    """Single attorney object wraps into a 1-element list."""
    attorney = {"name": "OYA YALVAÇ", "firm": "DERİŞ PATENT VE MARKA ACENTALIĞI A.Ş."}
    out = _normalize_pdf_attorney_to_list(attorney)
    assert out == [{
        "name": "OYA YALVAÇ",
        "firm": "DERİŞ PATENT VE MARKA ACENTALIĞI A.Ş.",
    }]


def test_normalize_pdf_attorney_missing_returns_empty_list() -> None:
    """No attorney = []; not [None], not {}, not [{}]."""
    assert _normalize_pdf_attorney_to_list(None) == []
    assert _normalize_pdf_attorney_to_list({}) == []
    assert _normalize_pdf_attorney_to_list({"name": "", "firm": None}) == []


def test_normalize_pdf_priority_passes_iso_through() -> None:
    """PDF priority date is already ISO — don't re-parse."""
    priority = {
        "priority_no": "2013-007188",
        "priority_date": "2013-01-18",
        "country": "JP",
    }
    assert _normalize_pdf_priority(priority) == priority


def test_normalize_pdf_figure_passes_known_fields() -> None:
    figure = {
        "image_path": "2025_08_figures/0042.jpg",
        "page": 117,
        "image_xref": 4204,
        "bbox": [10.0, 20.0, 100.0, 200.0],
    }
    out = _normalize_pdf_figure(figure)
    assert out == {
        "image_path": "2025_08_figures/0042.jpg",
        "page": 117,
        "image_xref": 4204,
        "bbox": [10.0, 20.0, 100.0, 200.0],
    }


def test_normalize_pdf_figure_drops_empty_fields() -> None:
    """Empty image_path or absent bbox must not survive."""
    figure = {"image_path": "", "page": 117, "image_xref": None, "bbox": []}
    out = _normalize_pdf_figure(figure)
    assert out == {"page": 117}


def test_page_range_or_none_happy_path() -> None:
    assert _page_range_or_none([117, 118]) == [117, 118]
    assert _page_range_or_none((117, 117)) == [117, 117]


def test_page_range_or_none_rejects_garbage() -> None:
    assert _page_range_or_none(None) is None
    assert _page_range_or_none([]) is None
    assert _page_range_or_none([117]) is None
    assert _page_range_or_none([117, 118, 119]) is None
    assert _page_range_or_none(["a", "b"]) is None


def test_normalize_pdf_record_full() -> None:
    """Real shape pulled from 2025_08_pdf_metadata.json."""
    pdf = {
        "record_index": 1,
        "page_range": [117, 117],
        "publication_no": "TR 2021 011498 B",
        "kind_code": "B",
        "record_type": "GRANTED_PATENT",
        "publication_kind_label": "İncelemeli Patent",
        "application_no": "2021/011498",
        "application_date": "2014-01-10",
        "publication_date": "2023-01-23",
        "grant_date": "2025-08-21",
        "title": "KONVERTÖR İÇİNDE ÇELİK YAPIM YÖNTEMİ.",
        "abstract": "[Problem] ...",
        "ipc_classes": ["C21C 5/28", "C21C 1/02"],
        "holders": [
            {
                "name": "JFE STEEL CORPORATION",
                "address": "TOKYO 1000011",
                "country": "JAPONYA",
            }
        ],
        "inventors": [{"name": "NAOKI KIKUCHI"}],
        "attorney": {
            "name": "OYA YALVAÇ",
            "firm": "DERİŞ PATENT VE MARKA ACENTALIĞI A.Ş.",
        },
        "priorities": [
            {
                "priority_no": "2013-007188",
                "priority_date": "2013-01-18",
                "country": "JP",
            }
        ],
        "figures": [],
    }

    rec = normalize_pdf_record(pdf)

    assert rec.application_no == "2021/011498"
    assert rec.application_date == "2014-01-10"          # already ISO
    assert rec.publication_no == "TR 2021 011498 B"
    assert rec.publication_date == "2023-01-23"
    assert rec.grant_date == "2025-08-21"                # PDF-only, preserved
    assert rec.kind_code == "B"
    assert rec.record_type == "GRANTED_PATENT"
    assert rec.title.startswith("KONVERTÖR")
    assert rec.abstract.startswith("[Problem]")
    assert rec.ipc_classes == ["C21C 5/28", "C21C 1/02"]
    assert rec.holders == [{
        "name": "JFE STEEL CORPORATION",
        "address": "TOKYO 1000011",
        "country": "JAPONYA",
    }]
    assert rec.inventors == [{"name": "NAOKI KIKUCHI"}]
    assert rec.attorneys == [{
        "name": "OYA YALVAÇ",
        "firm": "DERİŞ PATENT VE MARKA ACENTALIĞI A.Ş.",
    }]
    assert rec.priorities == [{
        "priority_no": "2013-007188",
        "priority_date": "2013-01-18",
        "country": "JP",
    }]
    assert rec.figures == []
    assert rec.patent_type is None                       # CD-only; PDF never sets
    assert rec.page_range == [117, 117]
    assert rec.source_format == "PDF"


def test_normalize_pdf_record_no_attorney() -> None:
    """Some PDF records have no attorney; canonical attorneys must be []."""
    pdf = {
        "application_no": "2013/11111",
        "publication_no": "TR 2013 11111 B",
        "kind_code": "B",
        "record_type": "GRANTED_PATENT",
        "ipc_classes": [],
        "holders": [],
        "inventors": [],
        "priorities": [],
        "figures": [],
    }
    rec = normalize_pdf_record(pdf)
    assert rec.attorneys == []
    assert rec.application_date is None
    assert rec.page_range is None


def test_normalize_pdf_record_drops_holder_address_null() -> None:
    """Real PDF data includes holders with address=null. Must not survive."""
    pdf = {
        "application_no": "2013/11111",
        "holders": [{"name": "JİANZHONG SHANG", "address": None, "country": None}],
        "inventors": [],
        "priorities": [],
        "figures": [],
        "ipc_classes": [],
    }
    rec = normalize_pdf_record(pdf)
    assert rec.holders == [{"name": "JİANZHONG SHANG"}]


# ---------------------------------------------------------------------------
# Step 4.4 — merge_records (CD ↔ PDF precedence)
# ---------------------------------------------------------------------------


def test_pick_longer_title_pdf_wins_when_longer() -> None:
    assert _pick_longer_title("Short", "Much longer title text") == "Much longer title text"


def test_pick_longer_title_cd_wins_on_tie() -> None:
    """Equal-length titles -> CD (it's the typed source)."""
    assert _pick_longer_title("ABCDE", "VWXYZ") == "ABCDE"


def test_pick_longer_title_falls_back_to_present_side() -> None:
    assert _pick_longer_title("CD only", None) == "CD only"
    assert _pick_longer_title(None, "PDF only") == "PDF only"
    assert _pick_longer_title(None, None) is None


def test_merge_figures_concats_with_dedup() -> None:
    cd = [{"image_path": "data/images/2017/15048.tif"}]
    pdf = [
        {"image_path": "2025_08_figures/0042.jpg", "page": 117, "image_xref": 4204},
        {"image_path": "2025_08_figures/0043.jpg", "page": 118, "image_xref": 4205},
    ]
    out = _merge_figures(cd, pdf)
    assert len(out) == 3
    assert out[0]["image_path"] == "data/images/2017/15048.tif"   # CD first
    assert out[1]["image_path"] == "2025_08_figures/0042.jpg"
    assert out[2]["image_path"] == "2025_08_figures/0043.jpg"


def test_merge_figures_dedups_by_image_path() -> None:
    """Defensive dedup — paths can't collide today (.tif vs .jpg) but
    a future change might surface overlap. Don't double-emit."""
    same = "shared/path.jpg"
    cd = [{"image_path": same}]
    pdf = [{"image_path": same, "page": 1}]
    out = _merge_figures(cd, pdf)
    assert len(out) == 1
    assert out[0]["image_path"] == same      # CD (first) wins


def test_merge_figures_handles_empty() -> None:
    assert _merge_figures([], []) == []
    assert _merge_figures([{"image_path": "a.tif"}], []) == [{"image_path": "a.tif"}]


def _cd_canonical() -> CanonicalRecord:
    """Reusable CD-side fixture for merge tests."""
    return CanonicalRecord(
        application_no="2017/15048",
        application_date="2017-10-05",
        publication_no="TR 2017 15048 U3",
        publication_date="2025-12-22",
        kind_code="U3",
        record_type="UNKNOWN",                 # gap memory
        title="EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ",
        abstract="CD truncated abstract...",
        ipc_classes=["A61M 5/31"],
        holders=[{"name": "ACME", "city": "İzmir", "country": "TR"}],
        inventors=[{"name": "JANE DOE"}],
        attorneys=[{"no": "361", "name": "ERDEM KAYA"}],
        priorities=[{"priority_no": "X", "priority_date": "2020-03-31"}],
        figures=[{"image_path": "data/images/2017/15048.tif"}],
        patent_type="2",
        source_format="CD",
    )


def _pdf_canonical() -> CanonicalRecord:
    """Reusable PDF-side fixture matching _cd_canonical's application_no."""
    return CanonicalRecord(
        application_no="2017/15048",
        application_date="2017-10-05",
        publication_no="TR 2017 15048 U3",
        publication_date="2025-12-22",
        grant_date="2025-12-22",                # PDF-only
        kind_code="U3",
        record_type="UNKNOWN",
        title="EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ VE KİLİTLEME YÖNTEMİ",  # longer
        abstract="Full PDF abstract — much longer than CD truncation.",
        ipc_classes=["A61M 5/31", "A61J 1/14"],
        holders=[{"name": "ACME", "country": "TR"}],   # less detail than CD
        inventors=[{"name": "JANE DOE"}],
        attorneys=[{"name": "ERDEM KAYA"}],            # no `no` field
        priorities=[],
        figures=[{"image_path": "2025_08_figures/0042.jpg", "page": 117}],
        patent_type=None,                              # CD-only
        page_range=[120, 121],                          # PDF-only
        source_format="PDF",
    )


def test_merge_records_full_precedence() -> None:
    """Walks the precedence table: every rule fires on a single record."""
    merged = merge_records(_cd_canonical(), _pdf_canonical())

    # Structured fields -> CD wins
    assert merged.application_date == "2017-10-05"
    assert merged.publication_no == "TR 2017 15048 U3"
    assert merged.publication_date == "2025-12-22"
    assert merged.ipc_classes == ["A61M 5/31"]                 # CD's narrower list
    # Holders: CD has city + country; PDF has only country. CD wins.
    assert merged.holders == [{"name": "ACME", "city": "İzmir", "country": "TR"}]
    # Attorneys: CD's list is non-empty -> CD wins (preserves `no` field)
    assert merged.attorneys == [{"no": "361", "name": "ERDEM KAYA"}]
    assert merged.priorities[0]["priority_no"] == "X"

    # Title: PDF longer -> PDF wins
    assert merged.title == "EMNİYET BELİRTEÇLİ ENJEKTÖR KİLİDİ VE KİLİTLEME YÖNTEMİ"
    # Abstract: PDF wins (CD truncated)
    assert merged.abstract.startswith("Full PDF abstract")

    # PDF-only fields preserved
    assert merged.grant_date == "2025-12-22"
    assert merged.page_range == [120, 121]

    # CD-only field preserved
    assert merged.patent_type == "2"

    # Figures unioned
    assert len(merged.figures) == 2
    assert merged.figures[0]["image_path"] == "data/images/2017/15048.tif"
    assert merged.figures[1]["image_path"] == "2025_08_figures/0042.jpg"

    # Source flag flipped
    assert merged.source_format == "BOTH"


def test_merge_records_pdf_attorney_used_when_cd_empty() -> None:
    """Edge case: CD didn't ship an attorney; PDF's becomes the merged value."""
    cd = _cd_canonical()
    cd.attorneys = []                               # simulate missing CD attorney
    pdf = _pdf_canonical()
    merged = merge_records(cd, pdf)
    assert merged.attorneys == [{"name": "ERDEM KAYA"}]


def test_merge_records_falls_back_to_pdf_kind_when_cd_unknown() -> None:
    """CD's publication_no may be malformed (no kind suffix) — PDF rescues."""
    cd = _cd_canonical()
    cd.kind_code = None
    cd.record_type = None
    pdf = _pdf_canonical()
    pdf.kind_code = "B"
    pdf.record_type = "GRANTED_PATENT"
    merged = merge_records(cd, pdf)
    assert merged.kind_code == "B"
    assert merged.record_type == "GRANTED_PATENT"


def test_merge_records_cd_kind_used_when_pdf_missing() -> None:
    """And the symmetric path: PDF didn't see this record (e.g. event index page)."""
    cd = _cd_canonical()
    cd.kind_code = "T4"
    cd.record_type = "GRANTED_PATENT"
    pdf = _pdf_canonical()
    pdf.kind_code = None
    pdf.record_type = None
    merged = merge_records(cd, pdf)
    assert merged.kind_code == "T4"
    assert merged.record_type == "GRANTED_PATENT"


def test_merge_records_title_tiebreak_goes_to_cd() -> None:
    cd = _cd_canonical()
    cd.title = "Same length"
    pdf = _pdf_canonical()
    pdf.title = "Other words"          # same length
    merged = merge_records(cd, pdf)
    assert merged.title == "Same length"


def test_merge_records_raises_on_app_no_mismatch() -> None:
    cd = _cd_canonical()
    pdf = _pdf_canonical()
    pdf.application_no = "9999/99999"
    with pytest.raises(ValueError, match="mismatched application_no"):
        merge_records(cd, pdf)


# ---------------------------------------------------------------------------
# Step 4.5 — reconcile_metadata orchestrator
# ---------------------------------------------------------------------------


def test_normalise_bulletin_no_canonicalises_both_formats() -> None:
    """CD ships '2025/8'; PDF ships '2025-08'. Both -> '2025/8' canonical."""
    assert _normalise_bulletin_no("2025/8") == "2025/8"
    assert _normalise_bulletin_no("2025-08") == "2025/8"
    assert _normalise_bulletin_no("2025/12") == "2025/12"
    assert _normalise_bulletin_no("2025-12") == "2025/12"


def test_normalise_bulletin_no_returns_none_on_empty() -> None:
    assert _normalise_bulletin_no(None) is None
    assert _normalise_bulletin_no("") is None
    assert _normalise_bulletin_no("   ") is None


def test_normalise_bulletin_no_passes_unrecognised_through_stripped() -> None:
    """Defensive: keep the raw value if it doesn't match either format."""
    assert _normalise_bulletin_no("  weird-value  ") == "weird-value"


def test_record_to_dict_drops_none_scalars_keeps_empty_lists() -> None:
    """JSON output should be tidy but distinguishable from absent."""
    rec = CanonicalRecord(application_no="X", title=None, ipc_classes=[])
    out = _record_to_dict(rec)
    assert "application_no" in out and out["application_no"] == "X"
    assert "title" not in out                  # None scalars dropped
    assert out["ipc_classes"] == []            # empty list preserved


def test_build_stats_counts_source_format_and_record_type() -> None:
    records = [
        CanonicalRecord(application_no="A", source_format="BOTH", record_type="GRANTED_PATENT"),
        CanonicalRecord(application_no="B", source_format="CD", record_type="UNKNOWN"),
        CanonicalRecord(application_no="C", source_format="PDF", record_type="PUBLISHED_APP"),
        CanonicalRecord(application_no="D", source_format="CD", record_type=None,
                         figures=[{"image_path": "x.tif"}, {"image_path": "y.jpg"}]),
    ]
    stats = _build_stats(records)
    assert stats["records"] == 4
    assert stats["by_source_format"] == {"CD": 2, "PDF": 1, "BOTH": 1}
    assert stats["by_record_type"]["GRANTED_PATENT"] == 1
    assert stats["by_record_type"]["PUBLISHED_APP"] == 1
    assert stats["by_record_type"]["UNKNOWN"] == 2     # explicit + None-coerced
    assert stats["figures_total"] == 2


def _cd_doc(records=None, bulletin_no="2025/8") -> dict:
    return {
        "bulletin_no": bulletin_no,
        "bulletin_date": "2025-08-21",
        "source_archive": "2025_07_CD.rar",
        "stats": {"patents": len(records or [])},
        "patents": records or [],
    }


def _pdf_doc(records=None, bulletin_no="2025-08") -> dict:
    return {
        "bulletin_no": bulletin_no,
        "bulletin_date": "2025-08-21",
        "source_pdf": "2025_08.pdf",
        "stats": {"records": len(records or [])},
        "records": records or [],
    }


def test_reconcile_metadata_pairs_overlap_on_application_no() -> None:
    """Records sharing application_no merge to BOTH; one-side stays one-side."""
    cd = _cd_doc(records=[
        {
            "application_no": "2017/15048",
            "publication_no": "TR 2017 15048 U3",
            "title": "CD title",
            "ipc_codes": ["A61M 5/31"],
            "holders": [{"title": "ACME"}],
        },
        {
            "application_no": "2018/22222",          # CD-only (PDF didn't see)
            "publication_no": "TR 2018 22222 U3",
            "title": "Only in CD",
            "ipc_codes": [],
            "holders": [],
        },
    ])
    pdf = _pdf_doc(records=[
        {
            "application_no": "2017/15048",
            "publication_no": "TR 2017 15048 U3",
            "kind_code": "U3",
            "record_type": "UNKNOWN",
            "title": "PDF title is much longer than CD title",
            "abstract": "Long PDF abstract.",
            "ipc_classes": ["A61M 5/31"],
            "holders": [{"name": "ACME"}],
            "inventors": [],
            "priorities": [],
            "figures": [],
        },
        {
            "application_no": "2019/33333",          # PDF-only
            "publication_no": "TR 2019 33333 B",
            "kind_code": "B",
            "record_type": "GRANTED_PATENT",
            "title": "Only in PDF",
            "abstract": "PDF.",
            "ipc_classes": [],
            "holders": [],
            "inventors": [],
            "priorities": [],
            "figures": [],
        },
    ])

    doc = reconcile_metadata(cd, pdf)

    assert doc["bulletin_no"] == "2025/8"          # canonicalised
    assert doc["bulletin_date"] == "2025-08-21"
    assert doc["source_archive"] == "2025_07_CD.rar"
    assert doc["source_pdf"] == "2025_08.pdf"
    assert "reconciled_at" in doc

    # 3 records: 1 BOTH + 1 CD-only + 1 PDF-only
    assert doc["stats"]["records"] == 3
    assert doc["stats"]["by_source_format"] == {"CD": 1, "PDF": 1, "BOTH": 1}

    # Sorted by application_no for determinism
    app_nos = [r["application_no"] for r in doc["records"]]
    assert app_nos == ["2017/15048", "2018/22222", "2019/33333"]

    # Spot-check the merged record
    both = doc["records"][0]
    assert both["source_format"] == "BOTH"
    assert both["title"] == "PDF title is much longer than CD title"   # PDF longer
    assert both["abstract"] == "Long PDF abstract."                     # PDF wins


def test_reconcile_metadata_raises_on_bulletin_mismatch() -> None:
    cd = _cd_doc(bulletin_no="2025/8")
    pdf = _pdf_doc(bulletin_no="2025-09")           # different bulletin
    with pytest.raises(ValueError, match="bulletin_no mismatch"):
        reconcile_metadata(cd, pdf)


def test_reconcile_metadata_accepts_format_difference() -> None:
    """CD '2025/8' and PDF '2025-08' are the same bulletin — must NOT raise."""
    cd = _cd_doc(bulletin_no="2025/8")
    pdf = _pdf_doc(bulletin_no="2025-08")
    doc = reconcile_metadata(cd, pdf)              # no exception
    assert doc["bulletin_no"] == "2025/8"


def test_reconcile_metadata_drops_records_without_application_no_from_index() -> None:
    """Defensive: a CD row with blank application_no must not pair on falsy key."""
    cd = _cd_doc(records=[
        {"application_no": "", "publication_no": "TR x", "ipc_codes": [], "holders": []},
    ])
    pdf = _pdf_doc(records=[
        {"application_no": "", "publication_no": "TR y", "kind_code": "B",
         "record_type": "GRANTED_PATENT", "title": "x", "abstract": "y",
         "ipc_classes": [], "holders": [], "inventors": [], "priorities": [], "figures": []},
    ])
    doc = reconcile_metadata(cd, pdf)
    # Both should appear separately (CD-only + PDF-only), NOT merged on blank
    assert doc["stats"]["by_source_format"]["BOTH"] == 0


def test_reconcile_metadata_record_count_deterministic_sort() -> None:
    """Output ordering must be stable across runs (diff-of-runs friendliness)."""
    cd = _cd_doc(records=[
        {"application_no": "2017/99999", "publication_no": "x", "ipc_codes": [], "holders": []},
        {"application_no": "2017/00001", "publication_no": "y", "ipc_codes": [], "holders": []},
    ])
    pdf = _pdf_doc(records=[])
    doc = reconcile_metadata(cd, pdf)
    assert [r["application_no"] for r in doc["records"]] == ["2017/00001", "2017/99999"]


# ---------------------------------------------------------------------------
# Step 4.6 — edge cases (CD-only / PDF-only / both-missing)
# ---------------------------------------------------------------------------


def test_reconcile_metadata_cd_only() -> None:
    """Pre-PDF-cutover months ship CD only. Output must still be canonical."""
    cd = _cd_doc(records=[
        {
            "application_no": "2014/00001",
            "publication_no": "TR 2014 00001 B",
            "title": "Old grant",
            "ipc_codes": ["A01B 1/00"],
            "holders": [{"title": "ANCIENT CO"}],
        },
    ])

    doc = reconcile_metadata(cd_doc=cd, pdf_doc=None)

    assert doc["bulletin_no"] == "2025/8"
    assert doc["source_archive"] == "2025_07_CD.rar"
    assert doc["source_pdf"] is None
    assert doc["stats"]["records"] == 1
    assert doc["stats"]["by_source_format"] == {"CD": 1, "PDF": 0, "BOTH": 0}
    assert doc["records"][0]["application_no"] == "2014/00001"
    assert doc["records"][0]["source_format"] == "CD"


def test_reconcile_metadata_pdf_only() -> None:
    """Rare: a month where the CD download failed. Should still reconcile."""
    pdf = _pdf_doc(records=[
        {
            "application_no": "2025/00001",
            "publication_no": "TR 2025 00001 B",
            "kind_code": "B",
            "record_type": "GRANTED_PATENT",
            "title": "PDF only",
            "abstract": "abstract",
            "ipc_classes": [],
            "holders": [],
            "inventors": [],
            "priorities": [],
            "figures": [],
        }
    ])

    doc = reconcile_metadata(cd_doc=None, pdf_doc=pdf)

    assert doc["bulletin_no"] == "2025/8"
    assert doc["source_archive"] is None
    assert doc["source_pdf"] == "2025_08.pdf"
    assert doc["stats"]["by_source_format"] == {"CD": 0, "PDF": 1, "BOTH": 0}
    assert doc["records"][0]["source_format"] == "PDF"


def test_reconcile_metadata_raises_when_both_missing() -> None:
    with pytest.raises(ValueError, match="requires at least one of"):
        reconcile_metadata(cd_doc=None, pdf_doc=None)


def test_reconcile_metadata_cd_only_skips_bulletin_check() -> None:
    """No bulletin_no equivalence to check when only one side is present —
    must not raise even when the other side would have been mismatched."""
    cd = _cd_doc(bulletin_no="2025/8")
    # No pdf_doc — just verify no spurious mismatch error
    doc = reconcile_metadata(cd_doc=cd, pdf_doc=None)
    assert doc["bulletin_no"] == "2025/8"


def test_reconcile_metadata_one_side_empty_records_list_still_pairs() -> None:
    """Empty records[] is not the same as None: the side IS present, just empty.
    Should NOT trigger the "both None" guard, and should still validate
    bulletin_no equivalence."""
    cd = _cd_doc(records=[
        {"application_no": "X", "publication_no": "x", "ipc_codes": [], "holders": []},
    ])
    pdf = _pdf_doc(records=[])
    doc = reconcile_metadata(cd, pdf)        # both present, PDF empty
    assert doc["stats"]["records"] == 1
    assert doc["stats"]["by_source_format"]["CD"] == 1


# ---------------------------------------------------------------------------
# Step 4.7 — CLI entrypoint + filename derivation
# ---------------------------------------------------------------------------


def test_unified_filename_canonical_cases() -> None:
    """bulletin_no -> canonical {YYYY_MM}_metadata.json filename."""
    assert unified_filename("2025/8") == "2025_08_metadata.json"
    assert unified_filename("2025-08") == "2025_08_metadata.json"
    assert unified_filename("2025/12") == "2025_12_metadata.json"
    assert unified_filename("2025-12") == "2025_12_metadata.json"


def test_unified_filename_raises_on_invalid() -> None:
    with pytest.raises(ValueError, match="cannot derive filename"):
        unified_filename(None)
    with pytest.raises(ValueError, match="cannot derive filename"):
        unified_filename("")
    with pytest.raises(ValueError, match="cannot derive filename"):
        unified_filename("not-a-bulletin")


def test_classify_metadata_json_distinguishes_kinds(tmp_path: Path) -> None:
    cd_path = _write_json(tmp_path, "x_metadata.json", {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [],
        "stats": {},
    })
    pdf_path = _write_json(tmp_path, "x_pdf_metadata.json", {
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "records": [],
        "stats": {},
    })
    unified_path = _write_json(tmp_path, "x_unified_metadata.json", {
        "bulletin_no": "2025/8",
        "records": [],
        "reconciled_at": "2026-05-08T22:00:00+00:00",
        "stats": {},
    })

    assert classify_metadata_json(cd_path) == "cd"
    assert classify_metadata_json(pdf_path) == "pdf"     # by suffix
    assert classify_metadata_json(unified_path) == "unified"


def test_classify_metadata_json_raises_on_unknown_shape(tmp_path: Path) -> None:
    bad = _write_json(tmp_path, "weird_metadata.json", {"unrelated": True})
    with pytest.raises(ValueError, match="not a recognised"):
        classify_metadata_json(bad)


def test_group_by_bulletin_pairs_cd_and_pdf(tmp_path: Path) -> None:
    """The headline --all behaviour: pair across filename offset by bulletin_no."""
    # Mimic the real disk shape: 2025_07_metadata.json IS bulletin 2025/8.
    _write_json(tmp_path, "2025_07_metadata.json", {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [],
        "stats": {},
    })
    _write_json(tmp_path, "2025_08_pdf_metadata.json", {
        "bulletin_no": "2025-08",        # different format, same bulletin
        "bulletin_date": "2025-08-21",
        "records": [],
        "stats": {},
    })
    # Plus a CD-only month.
    _write_json(tmp_path, "2025_11_metadata.json", {
        "bulletin_no": "2025/12",
        "bulletin_date": "2025-12-22",
        "patents": [],
        "stats": {},
    })
    # Plus a stray unified output from a prior run — must be skipped.
    _write_json(tmp_path, "2025_08_metadata.json", {
        "bulletin_no": "2025/8",
        "records": [],
        "reconciled_at": "2026-05-08T22:00:00+00:00",
        "stats": {},
    })

    groups = _group_by_bulletin(tmp_path)

    assert "2025/8" in groups
    assert "cd" in groups["2025/8"]
    assert "pdf" in groups["2025/8"]
    assert groups["2025/8"]["cd"].name == "2025_07_metadata.json"
    assert groups["2025/8"]["pdf"].name == "2025_08_pdf_metadata.json"
    assert "2025/12" in groups
    assert "cd" in groups["2025/12"]
    assert "pdf" not in groups["2025/12"]


def test_process_one_writes_unified_with_canonical_filename(tmp_path: Path) -> None:
    cd_path = _write_json(tmp_path, "2025_07_metadata.json", {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "source_archive": "2025_07_CD.rar",
        "patents": [{
            "application_no": "X1",
            "publication_no": "TR X1 B",
            "ipc_codes": [],
            "holders": [],
        }],
        "stats": {},
    })
    pdf_path = _write_json(tmp_path, "2025_08_pdf_metadata.json", {
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "source_pdf": "2025_08.pdf",
        "records": [{
            "application_no": "X1", "publication_no": "TR X1 B",
            "kind_code": "B", "record_type": "GRANTED_PATENT",
            "title": "T", "abstract": "A", "ipc_classes": [],
            "holders": [], "inventors": [], "priorities": [], "figures": [],
        }],
        "stats": {},
    })

    result = _process_one(cd_path, pdf_path, tmp_path, force=False)

    out_path = tmp_path / "2025_08_metadata.json"
    assert out_path.exists()
    assert result["out"] == "2025_08_metadata.json"        # filename comes from bulletin_no
    assert result["bulletin_no"] == "2025/8"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["stats"]["records"] == 1
    assert payload["stats"]["by_source_format"]["BOTH"] == 1


def test_process_one_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """Overwriting the CD intermediate (or any prior unified) needs --force."""
    pdf_path = _write_json(tmp_path, "2025_08_pdf_metadata.json", {
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "records": [],
        "stats": {},
    })
    # Pre-create the would-be output to simulate a prior run.
    target = tmp_path / "2025_08_metadata.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--force"):
        _process_one(None, pdf_path, tmp_path, force=False)


def test_process_one_overwrites_with_force(tmp_path: Path) -> None:
    pdf_path = _write_json(tmp_path, "2025_08_pdf_metadata.json", {
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "records": [],
        "stats": {},
    })
    target = tmp_path / "2025_08_metadata.json"
    target.write_text("STALE", encoding="utf-8")

    _process_one(None, pdf_path, tmp_path, force=True)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["bulletin_no"] == "2025/8"


def test_parse_argv_single_pair_mode() -> None:
    args = parse_argv([
        "--cd-json", "a.json",
        "--pdf-json", "b.json",
        "--out-dir", "/tmp/out",
    ])
    assert args.cd_json == Path("a.json")
    assert args.pdf_json == Path("b.json")
    assert args.out_dir == Path("/tmp/out")
    assert args.all_mode is False


def test_parse_argv_all_mode_default_out() -> None:
    args = parse_argv(["--all", "--bulletins-dir", "/data/bulletins"])
    assert args.all_mode is True
    assert args.bulletins_dir == Path("/data/bulletins")
    assert args.out_dir == Path("/data/bulletins")     # default = bulletins_dir


def test_parse_argv_no_args_errors() -> None:
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_all_and_pair_mutex() -> None:
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--cd-json", "a.json"])


def test_main_returns_zero_on_success(tmp_path: Path) -> None:
    """End-to-end: CLI args -> file written -> exit 0."""
    cd_path = _write_json(tmp_path, "2025_07_metadata.json", {
        "bulletin_no": "2025/8",
        "bulletin_date": "2025-08-21",
        "patents": [],
        "stats": {},
    })
    pdf_path = _write_json(tmp_path, "2025_08_pdf_metadata.json", {
        "bulletin_no": "2025-08",
        "bulletin_date": "2025-08-21",
        "records": [],
        "stats": {},
    })

    rc = main([
        "--cd-json", str(cd_path),
        "--pdf-json", str(pdf_path),
        "--out-dir", str(tmp_path),
    ])
    assert rc == 0
    assert (tmp_path / "2025_08_metadata.json").exists()


def test_main_returns_one_on_failure(tmp_path: Path) -> None:
    """Missing input -> non-zero exit (don't mask failures)."""
    rc = main([
        "--cd-json", str(tmp_path / "missing.json"),
        "--out-dir", str(tmp_path),
    ])
    assert rc == 1
