"""Unit tests for ``pdf_extract_cografi`` helpers.

Fixture text strings are verbatim PyMuPDF output captured from
``bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/220.pdf`` on 2026-05-11.
This makes the regex assertions reflect what the extractor sees at
runtime, not what a human sees in a rendered PDF page.
"""

from __future__ import annotations

import pytest

from pdf_extract_cografi import (
    EXTRACTOR_VERSION,
    MIN_SUPPORTED_BULLETIN_NO,
    ChangeRequest,
    IndexEntry,
    RecordHeader,
    parse_cover,
    parse_index,
    parse_record_header,
    parse_section6_change_request,
    parse_toc,
)


# ---------------------------------------------------------------------------
# Verbatim fixture text from 220.pdf (PyMuPDF get_text())
# ---------------------------------------------------------------------------

COVER_220 = (
    " \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n \n"
    "Sayı 220 \nYayım Tarihi  \n04.05.2026 \n"
)

TOC_220 = """2026/220 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 04.05.2026
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



2

İÇİNDEKİLER

1.Bölüm
Duyuru  .............................................................................................................................. 3

2.Bölüm
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni 220. Sayıda Yayımlanan Başvuruların
Sıralı Listesi  ....................................................................................................................... 4

3.Bölüm
6769 Sayılı Sınai Mülkiyet Kanunu Kapsamında İncelenen Başvuruların Yayımı .............. 8

4.Bölüm
Tescil Edilen Başvuruların Yayımı  ...................................................................................27

5.Bölüm
6769 Sayılı Sınai Mülkiyet Kanununun 40 ıncı Maddesi Kapsamında Değişikliğe
Uğramış Başvuruların Yayımı  ..........................................................................................39

6.Bölüm
6769 Sayılı Sınai Mülkiyet Kanununun 42 nci Maddesi Kapsamında Değişiklik
Taleplerinin Yayımı  .........................................................................................................45
"""

INDEX_220_P4 = """2026/220 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 04.05.2026
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



4

2. Bölüm
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni 220. Sayıda Yayımlanan Başvuruların
Sıralı Listesi

6769 Sayılı Sınai Mülkiyet Kanunu Kapsamında İncelenen Başvuruların Listesi

Coğrafi İşaretler
Yayım
Numarası
Başvuru
Numarası
Coğrafi İşaret
Sayfa
1.
C2022/000469
Karapınar Halısı
8
2.
C2023/000116
Nevşehir Üzüm Turşusu
16
3.
C2024/000120
Osmancık Irgat Böreği
18
4.
C2025/000479
Bingöl Burma Kadayıfı
20
5.
C2025/000485
Gazik / Ğezık Kaymağı
23

Geleneksel Ürün Adları
Yayım
Numarası
Başvuru
Numarası
Geleneksel Ürün Adı
Sayfa
Bu Bültende yayımlanacak geleneksel ürün adı başvurusu bulunmamaktadır.
"""

INDEX_220_P5 = """2026/220 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 04.05.2026
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



5

 Tescil Edilen Başvuruların Listesi

Coğrafi İşaretler
Yayım
Numarası
Tescil
Numarası
Coğrafi İşaret
Sayfa
1.
1838
Osmancık Domates Kavurması
27
2.
1839
Erzurum Paça Çorbası
29
3.
1840
Ordu Çakıldak Fındığı
31
4.
1841
İskilip Su Kabağı Yemeği
35
5.
1842
İskilip Tepsisi
37

Geleneksel Ürün Adları
Yayım
Numarası
Tescil
Numarası
Geleneksel Ürün Adı
Sayfa
Bu Bültende yayımlanacak geleneksel ürün adı tescili bulunmamaktadır.
"""

RECORD_220_P8_KARAPINAR = """1. Karapınar Halısı
Başvuru No
: C2022/000469
Başvuru Tarihi
: 28.12.2022
Coğrafi İşaretin Adı
: Karapınar Halısı
Ürün / Ürün Grubu
: Halı / Halılar ve kilimler
Coğrafi İşaretin Türü
: Mahreç işareti
Başvuru Yapan
: Karapınar Ticaret ve Sanayi Odası
Başvuru Yapanın Adresi
: Hankapı Mah. Konya Cad. Yeni Belediye İş Hanı No: 4/204 Karapınar
KONYA
Vekil
: Hasan ATASEVEN (Söz Patent Ltd. Şti)
Coğrafi Sınır
: Konya ili Karapınar ilçesi
Kullanım Biçimi
 : Karapınar Halısı ibaresi ve mahreç işareti amblemi, ürünün kendisi veya
ambalajı üzerinde yer alır. Ürünün kendisi veya ambalajı üzerinde
kullanılamadığında, Karapınar Halısı ibaresi ve mahreç işareti amblemi,
işletmede kolayca görülecek şekilde bulundurulur.
Ürünün Tanımı ve Ayırt Edici Özellikleri:
"""

RECORD_220_P27_OSMANCIK = """1. Osmancık Domates Kavurması
Bu coğrafi işaret, 6769 sayılı Sınai Mülkiyet Kanununun 41 inci Maddesi kapsamında 03.02.2025 tarihinden
itibaren korunmak üzere 03.04.2026 tarihinde tescil edilmiştir.
Tescil No
: 1838
Tescil Tarihi
: 03.04.2026
Başvuru No
: C2025/000030
Başvuru Tarihi
: 03.02.2025
Coğrafi İşaretin Adı
: Osmancık Domates Kavurması
Ürün / Ürün Grubu
: Yemek/Yemekler ve çorbalar
Coğrafi İşaretin Türü
: Mahreç işareti
Tescil Ettiren
: Osmancık Kaymakamlığı
Tescil Ettirenin Adresi
: Yeni Mah. Ömer Derindere Blv. Hükümet Konağı Osmancık ÇORUM
Coğrafi Sınır
: Çorum ili Osmancık ilçesi
Kullanım Biçimi
: Osmancık Domates Kavurması ibaresi ve mahreç işareti amblemi ürünün
servisinin yapıldığı gıda işletmelerinde kolayca görülecek şekilde
bulundurulur.
Ürünün Tanımı ve Ayırt Edici Özellikleri:
"""

SEC6_220_IZMIR_KUMRUSU = """1. İzmir Kumrusu
262 tescil sayılı İzmir Kumrusu ibareli coğrafi işaretin tescil kayıtlarında yapılması uygun bulunan
değişiklikler aşağıda yer almaktadır.
 Denetleme:
“Denetimler; İzmir Ticaret Odasının koordinasyonunda ve İzmir İl Tarım ve Orman Müdürlüğünden 2, İzmir
Ekonomi Üniversitesi Mühendislik Fakültesi Gıda Mühendisliği Bölümünden 2, Türkiye Ekmek Sanayi İşverenler
Sendikasından 1 ve İzmir Ticaret Odasından 1 kişinin katılımıyla ürün konusunda uzman 6 kişiden oluşan denetim
mercii tarafından, düzenli olarak yılda bir defa, gerekli görülen hallerde ve şikâyet üzerine ise her zaman
gerçekleştirilir.”
ifadesi,
“Denetimler; İzmir Ticaret Odasının koordinatörlüğünde ve İzmir Ticaret Odası, İzmir İl Tarım ve Orman
Müdürlüğü ve İzmir Ekonomi Üniversitesi Mühendislik Fakültesi Gıda Mühendisliği Bölümünden konuda uzman
birer kişinin katılımıyla 3 kişiden oluşan denetim mercii tarafından gerçekleştirilir. Denetimler, düzenli olarak yılda
1 defa, ayrıca gerek görülmesi ve şikâyet halinde her zaman yapılabilir.”
şeklinde değiştirilmiştir.
"""


# ---------------------------------------------------------------------------
# parse_cover
# ---------------------------------------------------------------------------

def test_parse_cover_returns_bulletin_no_and_iso_date():
    bulletin_no, bulletin_date = parse_cover(COVER_220)
    assert bulletin_no == 220
    assert bulletin_date == "2026-05-04"


def test_parse_cover_handles_single_digit_day():
    """The cografi UI already taught us card 220 has '04.05.2026' on the
    cover (zero-padded), but legacy bulletins or hand-edited PDFs may
    render single-digit days. Cover regex must accept both."""
    text = "Sayı 7 \nYayım Tarihi: 4.5.2017 \n"
    bulletin_no, bulletin_date = parse_cover(text)
    assert bulletin_no == 7
    assert bulletin_date == "2017-05-04"


def test_parse_cover_returns_none_when_markers_missing():
    no, date = parse_cover("nothing relevant here")
    assert no is None and date is None


# ---------------------------------------------------------------------------
# parse_toc
# ---------------------------------------------------------------------------

def test_parse_toc_extracts_all_six_sections_for_220():
    entries = parse_toc(TOC_220)
    assert [e["section_number"] for e in entries] == [1, 2, 3, 4, 5, 6]
    assert entries[0]["title"] == "Duyuru"
    assert entries[0]["start_page"] == 3
    assert entries[2]["start_page"] == 8
    assert entries[3]["start_page"] == 27
    assert entries[4]["start_page"] == 39
    assert entries[5]["start_page"] == 45


def test_parse_toc_collapses_wrapped_titles():
    """Section 5 and 6 titles wrap across two lines; the parser must
    collapse them into a single space-separated string."""
    entries = parse_toc(TOC_220)
    sec5 = next(e for e in entries if e["section_number"] == 5)
    assert "Değişikliğe" in sec5["title"]
    assert "Uğramış" in sec5["title"]
    # No raw newlines remain in the title.
    assert "\n" not in sec5["title"]


def test_parse_toc_returns_empty_when_no_section_markers():
    assert parse_toc("İÇİNDEKİLER\n\nNo bölüm here.") == []


# ---------------------------------------------------------------------------
# parse_index
# ---------------------------------------------------------------------------

def test_parse_index_section3_yields_five_gi_rows():
    entries = parse_index(INDEX_220_P4)
    assert all(e.section_key == "examined" for e in entries)
    assert all(e.record_type == "GI" for e in entries)
    assert [e.application_no for e in entries] == [
        "C2022/000469",
        "C2023/000116",
        "C2024/000120",
        "C2025/000479",
        "C2025/000485",
    ]
    assert [e.start_page for e in entries] == [8, 16, 18, 20, 23]
    assert entries[0].name == "Karapınar Halısı"


def test_parse_index_section3_skips_empty_tpn_subsection():
    """The 220 bulletin has 'Bu Bültende ... bulunmamaktadır' for TPNs;
    those rows must be skipped, not emitted as malformed entries."""
    entries = parse_index(INDEX_220_P4)
    assert not any(e.record_type == "TPN" for e in entries)


def test_parse_index_section4_uses_registration_number():
    entries = parse_index(INDEX_220_P5)
    assert all(e.section_key == "registered" for e in entries)
    assert all(e.record_type == "GI" for e in entries)
    assert [e.registration_no for e in entries] == [1838, 1839, 1840, 1841, 1842]
    assert [e.application_no for e in entries] == [None] * 5
    assert entries[0].name == "Osmancık Domates Kavurması"


INDEX_220_P6_ART40 = """2026/220 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 04.05.2026
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



6

 6769 Sayılı Sınai Mülkiyet Kanununun 40 ıncı Maddesi Kapsamında Değişikliğe Uğramış
Başvuruların Listesi

Coğrafi İşaretler
Yayım
Numarası
Başvuru
Numarası
Coğrafi İşaret
Sayfa
1.
C2019/106
Şemdinli Balı
39
2.
C2024/000069
İbradı Enek Pekmezi
43

Geleneksel Ürün Adları
Yayım
Numarası
Başvuru / Tescil
Numarası
Geleneksel Ürün Adı
Sayfa
Bu Bültende yayımlanacak değişikliğe uğramış geleneksel ürün adı başvurusu
bulunmamaktadır.
"""

INDEX_220_P7_ART42 = """2026/220 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 04.05.2026
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



7

 6769 Sayılı Sınai Mülkiyet Kanununun 42 nci Maddesi Kapsamında Değişiklik
Taleplerinin Listesi

Coğrafi İşaretler
Yayım
Numarası
Tescil
Numarası
Coğrafi İşaret
Sayfa
1.
262
İzmir Kumrusu
45
2.
268
İzmir Boyozu
46
"""


def test_parse_index_section5_handles_wrapped_header_title():
    """Section 5 sub-index header title wraps across two lines (the
    'Başvuruların' word is on its own line). Regex must allow newlines
    between 'Maddesi' and 'Listesi'."""
    entries = parse_index(INDEX_220_P6_ART40)
    assert all(e.section_key == "article_40_modified" for e in entries)
    assert [e.application_no for e in entries] == ["C2019/106", "C2024/000069"]
    assert [e.start_page for e in entries] == [39, 43]


def test_parse_index_section6_uses_registration_number():
    """Section 6 cites existing registrations, so its index column is
    'Tescil Numarası', not 'Başvuru Numarası'. Sub-index header wraps."""
    entries = parse_index(INDEX_220_P7_ART42)
    assert all(e.section_key == "article_42_change_requests" for e in entries)
    assert all(e.record_type == "GI" for e in entries)
    assert [e.registration_no for e in entries] == [262, 268]
    assert [e.application_no for e in entries] == [None, None]
    assert [e.start_page for e in entries] == [45, 46]


def test_parse_index_combined_pages_recovers_both_indices():
    """Real cografi runs concatenate Section 2 pages (p4 + p5) before
    parsing. Ensure both sub-indices are extracted from the merged blob."""
    combined = INDEX_220_P4 + "\n" + INDEX_220_P5
    entries = parse_index(combined)
    keys = sorted({e.section_key for e in entries})
    assert keys == ["examined", "registered"]
    assert sum(1 for e in entries if e.section_key == "examined") == 5
    assert sum(1 for e in entries if e.section_key == "registered") == 5


def test_parse_index_empty_text_returns_empty_list():
    assert parse_index("") == []


# ---------------------------------------------------------------------------
# parse_record_header
# ---------------------------------------------------------------------------

def test_parse_record_header_section3_extracts_all_fields():
    h = parse_record_header(RECORD_220_P8_KARAPINAR, is_section_4=False)
    assert h.application_no == "C2022/000469"
    assert h.application_date == "2022-12-28"
    assert h.name == "Karapınar Halısı"
    assert h.product_group == "Halı / Halılar ve kilimler"
    assert h.gi_type == "Mahreç işareti"
    assert h.applicant_name == "Karapınar Ticaret ve Sanayi Odası"
    assert "Hankapı Mah." in (h.applicant_address or "")
    assert "KONYA" in (h.applicant_address or "")
    assert h.agent and "ATASEVEN" in h.agent
    assert h.geographical_boundary == "Konya ili Karapınar ilçesi"
    assert h.usage_description and h.usage_description.startswith("Karapınar Halısı ibaresi")
    # Section 3 record: no registration fields.
    assert h.registration_no is None
    assert h.registration_date is None


def test_parse_record_header_section4_picks_up_registration_and_registrant_fields():
    h = parse_record_header(RECORD_220_P27_OSMANCIK, is_section_4=True)
    assert h.application_no == "C2025/000030"
    assert h.application_date == "2025-02-03"
    assert h.registration_no == 1838
    assert h.registration_date == "2026-04-03"
    # Tescil Ettiren replaces Başvuru Yapan in section 4.
    assert h.applicant_name == "Osmancık Kaymakamlığı"
    assert "Yeni Mah." in (h.applicant_address or "")
    assert "ÇORUM" in (h.applicant_address or "")
    assert h.gi_type == "Mahreç işareti"
    assert h.product_group == "Yemek/Yemekler ve çorbalar"


def test_parse_record_header_section3_does_not_emit_registration_fields_in_strict_mode():
    h = parse_record_header(RECORD_220_P8_KARAPINAR, is_section_4=False)
    # Even if downstream text accidentally contained a 'Tescil No' line,
    # we should not surface it for non-section-4 records.
    assert h.registration_no is None


# ---------------------------------------------------------------------------
# parse_section6_change_request
# ---------------------------------------------------------------------------

def test_parse_section6_extracts_existing_registration_reference():
    cr = parse_section6_change_request(SEC6_220_IZMIR_KUMRUSU)
    assert cr is not None
    assert cr.existing_registration_no == 262
    assert cr.name == "İzmir Kumrusu"


def test_parse_section6_extracts_change_tuples():
    cr = parse_section6_change_request(SEC6_220_IZMIR_KUMRUSU)
    assert cr is not None
    assert len(cr.changes) == 1
    change = cr.changes[0]
    assert change["field"] == "Denetleme"
    assert change["old"].startswith("Denetimler; İzmir Ticaret Odasının koordinasyonunda")
    assert change["new"].startswith("Denetimler; İzmir Ticaret Odasının koordinatörlüğünde")
    # "ifadesi" must not leak into either side.
    assert "ifadesi" not in change["old"]
    assert "ifadesi" not in change["new"]
    assert "şeklinde değiştirilmiştir" not in change["new"]


def test_parse_section6_returns_none_when_no_registration_reference():
    assert parse_section6_change_request("nothing relevant here") is None


# ---------------------------------------------------------------------------
# Module-level constants sanity
# ---------------------------------------------------------------------------

def test_extractor_version_is_an_int():
    assert isinstance(EXTRACTOR_VERSION, int)
    assert EXTRACTOR_VERSION >= 1


def test_min_supported_bulletin_no_is_modern_format_threshold():
    """Cards 1-99 are KHK 555 (legacy); 100+ are SMK 6769 (modern). The
    extractor refuses to process below this threshold to avoid false
    positives on the legacy schema."""
    assert MIN_SUPPORTED_BULLETIN_NO == 100
