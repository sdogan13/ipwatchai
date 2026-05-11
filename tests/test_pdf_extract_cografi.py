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


def test_min_supported_bulletin_no_covers_full_archive():
    """B1.5 added legacy KHK 555 support; the extractor now accepts the
    full archive (cards 1-220). Bulletins below this number would be
    pre-2017 / pre-archive and never surface from the live site."""
    assert MIN_SUPPORTED_BULLETIN_NO == 1


# ---------------------------------------------------------------------------
# B1.5 — legacy KHK 555 era format support
# ---------------------------------------------------------------------------

# Verbatim PyMuPDF text from bulletin 1 p2 (TOC, KHK 555 era, 4 sections
# including the legacy-only "Resmi Gazetede İlan Edilmiş..." section).
TOC_LEGACY_001 = """2017/1 Sayılı Resmi
Türk Patent ve Marka Kurumu
Yayım Tarihi: 15.03.2017
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni



2

İÇİNDEKİLER

1.Bölüm
Duyuru.................................................................................................................................3

2.Bölüm
Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni 1. Sayıda Yayımlanan Başvuruların Sıralı
Listesi..................................................................................................................................4

3.Bölüm
555 Sayılı Coğrafi İşaretlerin Korunması Hakkında Kanun Hükmünde Kararname
Gereğince İncelenen Coğrafi İşaret Başvurularının Yayımı...............................................6

4.Bölüm
Resmi Gazetede İlan Edilmiş Ancak Yerel ya da Ulusal Gazetede İlan Edilmemiş Coğrafi
İşaret Başvurularının Yayımı...........................................................................................33
"""

# Verbatim text fragment of bulletin 38 art42 record (legacy preamble:
# "<reg_no> sayı ile <date> tarihinde tescil edilen <name> tescil metninde").
LEGACY_ART42_PREAMBLE_038 = """1. Kangal Koyunu
47 sayı ile 03.08.2002 tarihinde tescil edilen Kangal Koyunu tescil metninde yer alan "Denetim" başlığının
6769 Sayılı Sınai Mülkiyet Kanununun "Değişiklik talepleri" başlıklı 42 nci maddesi gereğince değiştirilmesine
ilişkin ilan aşağıdaki gibi olup ...
"""


def test_classify_section_title_handles_khk_555_examined():
    from pdf_extract_cografi import classify_section_title
    title = (
        "555 Sayılı Coğrafi İşaretlerin Korunması Hakkında Kanun Hükmünde "
        "Kararname Gereğince İncelenen Coğrafi İşaret Başvurularının Yayımı"
    )
    assert classify_section_title(title) == "examined"


def test_classify_section_title_handles_khk_555_short_no_yayimi():
    """Legacy bulletin 60+ TOC variant ends with 'Başvurular' (no Yayımı)."""
    from pdf_extract_cografi import classify_section_title
    title = (
        "555 Sayılı Coğrafi İşaretlerin Korunması Hakkında Kanun Hükmünde "
        "Kararname Kapsamında İncelenen Başvurular"
    )
    assert classify_section_title(title) == "examined"


def test_classify_section_title_handles_khk_555_article_12_as_art40():
    """KHK 555 Article 12 modifications are the legacy equivalent of SMK
    Article 40 modifications and must classify to the same semantic key."""
    from pdf_extract_cografi import classify_section_title
    title = (
        "555 Sayılı Coğrafi İşaretlerin Korunması Hakkında Kanun Hükmünde "
        "Kararnamenin 12 nci Maddesi Kapsamında Değişikliğe Uğramış Başvurular"
    )
    assert classify_section_title(title) == "article_40_modified"


def test_classify_section_title_handles_art42_legacy_short_form():
    """Legacy bulletin 25 + 38 use 'Talepleri' (no Yayımı) and
    'Uyarınca' (instead of 'Kapsamında')."""
    from pdf_extract_cografi import classify_section_title
    assert classify_section_title(
        "6769 Sayılı Sınai Mülkiyet Kanununun 42 nci Maddesi Uyarınca Değişiklik Talepleri"
    ) == "article_42_change_requests"


def test_classify_section_title_handles_art42_finalized_legacy_wording():
    """Legacy bulletin 43 uses 'Değişikliğe Uğramış Tesciller' instead of
    'Kesinleşen Değişikliklerin Yayımı'."""
    from pdf_extract_cografi import classify_section_title
    assert classify_section_title(
        "6769 Sayılı Sınai Mülkiyet Kanununun 42 nci Maddesi Kapsamında "
        "Değişikliğe Uğramış Tesciller"
    ) == "article_42_finalized"


def test_classify_section_title_handles_article_43():
    from pdf_extract_cografi import classify_section_title
    title = "6769 Sayılı Sınai Mülkiyet Kanununun 43 üncü Maddesi Kapsamında Değişiklikler"
    assert classify_section_title(title) == "article_43_modified"


def test_classify_section_title_handles_corrections_legacy_short():
    """Legacy bulletins use just 'Düzeltmeler'; modern uses 'Düzeltmelerin Yayımı'."""
    from pdf_extract_cografi import classify_section_title
    assert classify_section_title("Düzeltmeler") == "corrections"
    assert classify_section_title("Düzeltmelerin Yayımı") == "corrections"


def test_classify_section_title_handles_gazette_only_legacy():
    """Bulletin 1's special 'Resmi Gazetede İlan Edilmiş Ancak ...' section."""
    from pdf_extract_cografi import classify_section_title
    title = (
        "Resmi Gazetede İlan Edilmiş Ancak Yerel ya da Ulusal Gazetede "
        "İlan Edilmemiş Coğrafi İşaret Başvurularının Yayımı"
    )
    assert classify_section_title(title) == "gazette_only_announcements"


def test_parse_toc_legacy_bulletin_1():
    """Legacy KHK-only bulletin TOC parses cleanly; sections classify via
    title content (KHK -> examined, gazette-only -> gazette_only)."""
    from pdf_extract_cografi import classify_section_title
    entries = parse_toc(TOC_LEGACY_001)
    assert [e["section_number"] for e in entries] == [1, 2, 3, 4]
    sec3 = next(e for e in entries if e["section_number"] == 3)
    sec4 = next(e for e in entries if e["section_number"] == 4)
    assert sec3["start_page"] == 6
    assert sec4["start_page"] == 33
    assert classify_section_title(sec3["title"]) == "examined"
    assert classify_section_title(sec4["title"]) == "gazette_only_announcements"


def test_parse_record_header_aliases_resolve_legacy_labels():
    """Legacy KHK-era bulletins use 'Başvuru Sahibinin Adı/Adresi' and
    'Ürünün Adı'; the alias regex must resolve them to the same fields
    as the modern 'Başvuru Yapan/Yapanın Adresi/Ürün / Ürün Grubu'."""
    legacy_record = """1. Yozgat Çanak Peyniri
Başvuru No
: C2011/026
Başvuru Tarihi
: 14.03.2011
Coğrafi İşaretin Adı
: Yozgat Çanak Peyniri
Ürünün Adı
: Peynir
Coğrafi İşaretin Türü
: Mahreç İşareti
Başvuru Sahibinin Adı
: Yozgat Belediye Başkanlığı
Başvuru Sahibinin Adresi
: Yozgat Belediyesi Hizmet Binası Merkez/YOZGAT
Coğrafi Sınır
: Yozgat ili
Kullanım Biçimi
: Yozgat Çanak Peyniri ibaresi ürünün ambalajı üzerinde yer alır.
Ürünün Tanımı ve Ayırt Edici Özellikleri:
"""
    h = parse_record_header(legacy_record, is_section_4=False)
    assert h.application_no == "C2011/026"
    assert h.application_date == "2011-03-14"
    assert h.name == "Yozgat Çanak Peyniri"
    assert h.product_group == "Peynir"  # legacy "Ürünün Adı" alias
    assert h.gi_type == "Mahreç İşareti"
    assert h.applicant_name == "Yozgat Belediye Başkanlığı"  # legacy alias
    assert h.applicant_address and "YOZGAT" in h.applicant_address
    assert h.geographical_boundary == "Yozgat ili"


def test_parse_change_request_handles_legacy_preamble():
    """Bulletin 38-era art42 records use a different preamble shape:
    '<reg_no> sayı ile <date> tarihinde tescil edilen <name> tescil
    metninde ...'. The parser must capture reg_no and name even when
    the change body is free-form prose without structured tuples."""
    cr = parse_section6_change_request(LEGACY_ART42_PREAMBLE_038)
    assert cr is not None
    assert cr.existing_registration_no == 47
    assert cr.name == "Kangal Koyunu"
    # Legacy art42 has no structured "old / new" tuples — empty list is OK.
    assert cr.changes == []


# ---------------------------------------------------------------------------
# B2 — body free-text section extraction
# ---------------------------------------------------------------------------

RECORD_220_P8_BODY_TAIL = """1. Karapınar Halısı
Başvuru No
: C2022/000469
Coğrafi İşaretin Adı
: Karapınar Halısı
Coğrafi Sınır
: Konya ili Karapınar ilçesi
Ürünün Tanımı ve Ayırt Edici Özellikleri:
Karapınar Halısı; saf yün kullanılarak Türk düğüm tekniği ile dokunan bir halı türüdür. Anadolu Selçuklular
dönemine dayanan bu halılar genellikle büyük ebatlı olup, dönemin sanat anlayışı ve yaratıcılığıyla şekillenmiştir.
Üretim Metodu:
Halı dokumacılığı yünün eğirilmesinden başlar. Karapınar ve yöresinde dokumacılıkta saf yün kullanılır.
Coğrafi Sınır İçerisinde Gerçekleşmesi Gereken Üretim, İşleme ve Diğer İşlemler:
Üretim metodunda yer alan tüm aşamalar coğrafi sınır içerisinde gerçekleşmelidir.
Denetleme:
Denetimler; Karapınar Ticaret ve Sanayi Odasının koordinasyonunda yapılır.
"""


def test_parse_body_sections_captures_all_four_known_subsections():
    from pdf_extract_cografi import parse_body_sections
    sections = parse_body_sections(RECORD_220_P8_BODY_TAIL)
    assert set(sections.keys()) == {
        "product_description",
        "production_method",
        "boundary_processing",
        "inspection",
    }
    assert sections["product_description"].startswith("Karapınar Halısı; saf yün")
    assert sections["production_method"].startswith("Halı dokumacılığı")
    assert sections["boundary_processing"].startswith("Üretim metodunda yer alan")
    assert sections["inspection"].startswith("Denetimler;")


def test_parse_body_sections_strips_page_headers():
    """Multi-page body slices contain repeating page-header lines that
    leak into captured subsections. The parser must drop them."""
    from pdf_extract_cografi import parse_body_sections
    with_header = (
        "Ürünün Tanımı ve Ayırt Edici Özellikleri:\n"
        "Karapınar Halısı; saf yün kullanılarak dokunur.\n"
        "2026/220 Sayılı Resmi\n"
        "Türk Patent ve Marka Kurumu\n"
        "Yayım Tarihi: 04.05.2026\n"
        "Coğrafi İşaret ve Geleneksel Ürün Adı Bülteni\n"
        "\n"
        "9\n"
        "Devam metni.\n"
        "Üretim Metodu:\n"
        "Devam.\n"
    )
    sections = parse_body_sections(with_header)
    assert "product_description" in sections
    assert "Türk Patent" not in sections["product_description"]
    assert "Yayım Tarihi" not in sections["product_description"]
    assert sections["product_description"].endswith("Devam metni.")


def test_parse_body_sections_returns_empty_when_no_known_headers():
    from pdf_extract_cografi import parse_body_sections
    assert parse_body_sections("just a record with no subsection headers") == {}


# ---------------------------------------------------------------------------
# B2 — figure helpers
# ---------------------------------------------------------------------------

def test_figure_slug_prefers_application_no():
    from pdf_extract_cografi import figure_slug_for_record
    assert figure_slug_for_record(application_no="C2022/000469") == "C2022_000469"
    assert figure_slug_for_record(application_no="  C2025 / 000485 ") == "C2025_000485"


def test_figure_slug_falls_back_to_registration_no():
    from pdf_extract_cografi import figure_slug_for_record
    assert figure_slug_for_record(registration_no=1838) == "reg_1838"


def test_figure_slug_falls_back_to_index_when_no_ids():
    from pdf_extract_cografi import figure_slug_for_record
    assert figure_slug_for_record(fallback_index=3) == "c_3"
    assert figure_slug_for_record() == "_unknown"


def test_is_template_image_flags_high_prevalence_xrefs():
    from pdf_extract_cografi import is_template_image
    # An xref that appears on 18 of 20 body pages (90%) is a header logo.
    assert is_template_image(xref=5, page_prevalence={5: 18}, total_body_pages=20) is True


def test_is_template_image_keeps_record_specific_xrefs():
    from pdf_extract_cografi import is_template_image
    # An xref that appears on 2 of 40 body pages (5%) is record-specific.
    assert is_template_image(xref=42, page_prevalence={42: 2}, total_body_pages=40) is False


def test_is_template_image_handles_unknown_xref():
    from pdf_extract_cografi import is_template_image
    assert is_template_image(xref=999, page_prevalence={1: 10}, total_body_pages=20) is False


def test_is_template_image_safe_when_no_body_pages():
    from pdf_extract_cografi import is_template_image
    assert is_template_image(xref=1, page_prevalence={1: 5}, total_body_pages=0) is False
