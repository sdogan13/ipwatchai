"""Unit tests for ``pdf_extract_tasarim_events`` pure helpers.

Smoke against real bulletin PDFs is exercised separately. These tests
target the per-event-type parsers and their sub-helpers.
"""

import pytest

from pdf_extract_tasarim_events import (
    CourtRef,
    HolderRef,
    TasarimEvent,
    clean_text,
    detect_section_for_page,
    extract_bulletin_metadata,
    fingerprint_event,
    normalize_tr_date,
    parse_board_event,
    parse_design_indices,
    parse_holder_with_address,
    parse_inid_event,
    parse_inid_fields,
)


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def test_clean_text_collapses_whitespace():
    assert clean_text(" foo  bar\n\tbaz ") == "foo bar baz"
    assert clean_text("") == ""
    assert clean_text(None) == ""


def test_normalize_tr_date():
    assert normalize_tr_date("13.04.2026") == "2026-04-13"
    assert normalize_tr_date("(58) 25.02.2026") == "2026-02-25"
    assert normalize_tr_date(None) is None
    assert normalize_tr_date("garbage") is None


# ---------------------------------------------------------------------------
# Section header detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("DEVİR", "transfer"),
    ("HACİZ KONULAN TESCİLLER", "seizure"),
    ("İHTİYATİ HACİZ KOYULAN TESCİLLER", "provisional_seizure"),
    ("İHTİYATİ TEDBİRİ KALDIRILAN TESCİLLER", "provisional_injunction_lifted"),
    ("YENİLENEN TESCİLLER", "renewal"),
    ("KISMI YENILEME", "partial_renewal"),
    ("KISMİ YENİLEME", "partial_renewal"),
    ("SAHİBİNİN TALEBİ İLE KISMI İPTAL", "partial_cancellation_owner"),
    ("KISMİ İHTİYATİ TEDBİR KONAN TASARIMLAR", "partial_provisional_injunction"),
    ("YİDK KARARI İLE İPTAL EDİLEN TESCİLLER", "full_cancellation_board"),
    ("YİDK KARARI İLE KISMI İPTAL EDİLEN TESCİLLER", "partial_cancellation_board"),
    ("BAŞVURU SAHİBİNİN TALEBİ İLE İPTAL", "full_cancellation_applicant"),
    ("BAŞVURU SAHİBİNİN TALEBİ İLE KISMI İPTAL", "partial_cancellation_applicant"),
])
def test_section_header_detection(header, expected):
    assert detect_section_for_page(header, None) == expected


def test_section_sticky_when_no_header():
    assert detect_section_for_page("body content with no header", "transfer") == "transfer"


def test_section_resets_on_lahey():
    assert detect_section_for_page("LAHEY ANLAŞMASI ÇERÇEVESİNDE TÜRKİYE'YE", "transfer") is None


# ---------------------------------------------------------------------------
# Holder parsing (multiple shapes)
# ---------------------------------------------------------------------------

def test_parse_holder_with_address_transfer_style():
    raw = '"FİKİRTEPE MAH. YILDIRIM SK. NO 22 Kadıköy İstanbul" adresinde mukim "DOĞA ŞAHİN"'
    h = parse_holder_with_address(raw)
    assert h is not None
    assert h.name == "DOĞA ŞAHİN"
    assert h.address and "FİKİRTEPE" in h.address


def test_parse_holder_with_address_standard():
    raw = "TIM MİMARLIK MUHENDISLIK (7610221) HARBIYE MAH. KASIM SOK Şişli İstanbul TÜRKİYE"
    h = parse_holder_with_address(raw)
    assert h is not None
    assert "TIM MİMARLIK" in h.name
    assert h.country == "TÜRKİYE"
    assert h.address and "HARBIYE" in h.address


def test_parse_holder_with_address_parens_around_address():
    raw = "GAMAS AYAKKABI (CEBECİ MAH. 2500. SK. 23-25 Sultangazi İstanbul)"
    h = parse_holder_with_address(raw)
    assert h is not None
    assert h.name == "GAMAS AYAKKABI"
    assert h.address == "CEBECİ MAH. 2500. SK. 23-25 Sultangazi İstanbul"


def test_parse_holder_with_address_empty():
    assert parse_holder_with_address("") is None
    assert parse_holder_with_address("   ") is None


# ---------------------------------------------------------------------------
# Design index list
# ---------------------------------------------------------------------------

def test_parse_design_indices_simple():
    assert parse_design_indices("4,12,13,15") == [4, 12, 13, 15]


def test_parse_design_indices_with_spaces():
    assert parse_design_indices("1, 2, 8, 10, 12, 13") == [1, 2, 8, 10, 12, 13]


def test_parse_design_indices_empty():
    assert parse_design_indices("") == []
    assert parse_design_indices("no numbers") == []


# ---------------------------------------------------------------------------
# parse_inid_event — per event type
# ---------------------------------------------------------------------------

def test_parse_inid_event_transfer():
    block = (
        '(11) 2021 012413 (15) 26.11.2021 (58) 13.04.2026 '
        '(73) "ATAKENT MAH. GÖKTÜRK SK. 28 A Ümraniye İstanbul" adresinde mukim "TIREBOLU ÇAY" '
        '(78) "FİKİRTEPE MAH. NO 22 Kadıköy İstanbul" adresinde mukim "DOĞA ŞAHİN" ne devretmiştir.'
    )
    e = parse_inid_event(block, event_type="transfer", event_index=1, page=441)
    assert e.event_type == "transfer"
    assert e.registration_no == "2021 012413"
    assert e.registration_date == "2021-11-26"
    assert e.event_date == "2026-04-13"
    assert e.previous_holder is not None
    assert e.previous_holder.name == "TIREBOLU ÇAY"
    assert e.new_holder is not None
    assert e.new_holder.name == "DOĞA ŞAHİN"
    assert e.holder is e.previous_holder  # alias for indexing


def test_parse_inid_event_seizure():
    block = (
        "(11) 2022 011231 (15) 23.09.2022 "
        "(73) KANTEC OTOMASYON (12345) YEŞİLTEPE MAH. ULUYOL CAD. 35 Erenler Sakarya TÜRKİYE "
        "(203) İSTANBUL ANADOLU 2. BANKA ALACAKLARI İCRA DAİRESİ MÜDÜRLÜĞÜ "
        "(204) 2026/13529 (205) 30.01.2026 tarihinde haciz konulmuştur."
    )
    e = parse_inid_event(block, event_type="seizure", event_index=2, page=442)
    assert e.event_type == "seizure"
    assert e.registration_no == "2022 011231"
    assert e.holder is not None and "KANTEC" in e.holder.name
    assert e.court is not None
    assert e.court.name and "İSTANBUL ANADOLU" in e.court.name
    assert e.court.case_no == "2026/13529"
    assert e.event_date == "2026-01-30"


def test_parse_inid_event_renewal():
    block = (
        "(11) 2006 00650 (15) 28.05.2021 "
        "(73) GOLD KİMYA (5006001) SELİMPAŞA MAH. Silivri İstanbul TÜRKİYE "
        "(58) 31.12.2025"
    )
    e = parse_inid_event(block, event_type="renewal", event_index=3, page=444)
    assert e.event_type == "renewal"
    assert e.registration_no == "2006 00650"
    assert e.event_date == "2025-12-31"
    assert e.holder is not None and "GOLD KİMYA" in e.holder.name
    assert e.design_indices == []
    assert e.court is None


def test_parse_inid_event_partial_renewal_with_design_indices():
    block = (
        "(11) 2020 09900 (15) 29.12.2020 "
        "(73) İMAMOĞLU MENSUCAT (4242666) KATİP KASIM MAH. Fatih İstanbul TÜRKİYE "
        "(58) 29.12.2025 (100) 1,2,8,10,12,13"
    )
    e = parse_inid_event(block, event_type="partial_renewal", event_index=4, page=460)
    assert e.event_type == "partial_renewal"
    assert e.design_indices == [1, 2, 8, 10, 12, 13]
    assert e.event_date == "2025-12-29"


def test_parse_inid_event_partial_cancellation_owner():
    block = (
        "(11) 2024 011197 (15) 26.12.2024 "
        "(73) MUSTAFA ALVER "
        "(58) 25.02.2026 (100) 4,12,13,15"
    )
    e = parse_inid_event(block, event_type="partial_cancellation_owner", event_index=5, page=460)
    assert e.event_type == "partial_cancellation_owner"
    assert e.design_indices == [4, 12, 13, 15]
    assert e.event_date == "2026-02-25"


def test_parse_inid_event_full_cancellation_applicant():
    block = (
        "(11) 2025 005164 (15) 14.07.2025 "
        "(73) FLO MAĞAZACILIK (1234567) MAH. ADRES TÜRKİYE "
        "(58) 10.04.2026"
    )
    e = parse_inid_event(block, event_type="full_cancellation_applicant", event_index=6, page=471)
    assert e.event_type == "full_cancellation_applicant"
    assert e.event_date == "2026-04-10"
    assert e.holder is not None and "FLO" in e.holder.name


def test_parse_inid_event_provisional_seizure():
    block = (
        "(11) 2021 006316 (15) 24.05.2021 "
        "(73) ATLANTİK İPLİK (1111111) BAŞPINAR Şehitkamil Gaziantep TÜRKİYE "
        "(203) İSTANBUL BANKA ALACAKLARI İCRA DAİRESİ MÜDÜRLÜĞÜ "
        "(204) 2026/110087 (205) 22.03.2026 tarihinde ihtiyati haciz konulmuştur."
    )
    e = parse_inid_event(block, event_type="provisional_seizure", event_index=7, page=444)
    assert e.event_type == "provisional_seizure"
    assert e.court is not None
    assert e.court.case_no == "2026/110087"
    assert e.event_date == "2026-03-22"


# ---------------------------------------------------------------------------
# parse_board_event — YİDK narrative
# ---------------------------------------------------------------------------

def test_parse_board_event_full_cancellation_board():
    block = (
        "09.05.2025 tarih ve 460 sayılı Resmi Tasarımlar Bülteninde yayınlanan, "
        '26.12.2024 tarih ve 2024 011153 sayı ile "WAI SHING PLASTIC PRODUCTS LIMITED" '
        "adına tescilli tasarımları Yeniden İnceleme ve Değerlendirme Kurulu'nun "
        "07.04.2026 tarih ve 2026/T-237 sayılı kararı ile iptal edilmiştir. "
        "Şerh ve ilan olunur."
    )
    e = parse_board_event(block, event_type="full_cancellation_board", event_index=1, page=466)
    assert e is not None
    assert e.event_type == "full_cancellation_board"
    assert e.referenced_bulletin_no == 460
    assert e.referenced_bulletin_date == "2025-05-09"
    assert e.registration_no == "2024 011153"
    assert e.registration_date == "2024-12-26"
    assert e.holder is not None
    assert e.holder.name and "WAI SHING" in e.holder.name
    assert e.decision_date == "2026-04-07"
    assert e.decision_no == "2026/T-237"
    assert e.event_date == "2026-04-07"


def test_parse_board_event_unparseable_returns_none():
    e = parse_board_event(
        "this is not a YİDK narrative", event_type="full_cancellation_board",
        event_index=1, page=466,
    )
    assert e is None


# ---------------------------------------------------------------------------
# Bulletin metadata + fingerprinting
# ---------------------------------------------------------------------------

def test_extract_bulletin_metadata_from_footer():
    text = "body... 2026/483  Tasarımlar Bülteni / Yayın Tarihi : 24.04.2026 ...body"
    no, date = extract_bulletin_metadata(text)
    assert no == 483
    assert date == "2026-04-24"


def test_fingerprint_event_stable_and_unique():
    e1 = TasarimEvent(event_type="transfer", event_index=1, page=441,
                      registration_no="2021 012413", event_date="2026-04-13",
                      free_text="some text")
    e2 = TasarimEvent(event_type="transfer", event_index=2, page=441,
                      registration_no="2021 012413", event_date="2026-04-13",
                      free_text="some text")
    e3 = TasarimEvent(event_type="seizure", event_index=3, page=441,
                      registration_no="2021 012413", event_date="2026-04-13",
                      free_text="some text")
    fp1 = fingerprint_event(e1, 483)
    fp2 = fingerprint_event(e2, 483)
    fp3 = fingerprint_event(e3, 483)
    assert fp1 == fp2  # event_index is not part of the fingerprint
    assert fp1 != fp3  # event_type differs
    assert len(fp1) == 16


# ---------------------------------------------------------------------------
# parse_inid_fields sanity (mirror of metadata parser)
# ---------------------------------------------------------------------------

def test_parse_inid_fields_in_event_block():
    block = "(11) 2021 012413 (15) 26.11.2021 (58) 13.04.2026 (73) NAME (78) NAME2"
    fields = parse_inid_fields(block)
    assert fields["11"] == ["2021 012413"]
    assert fields["15"] == ["26.11.2021"]
    assert fields["58"] == ["13.04.2026"]
    assert fields["73"] == ["NAME"]
    assert fields["78"] == ["NAME2"]
