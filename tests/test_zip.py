import zip


def test_doc_prefix_from_text_supports_canonical_issue_prefixes():
    assert zip.doc_prefix_from_text("BLT_490_2026-04-13") == "BLT_"
    assert zip.doc_prefix_from_text("GZ_500_2026-04-13") == "GZ_"


def test_extract_number_from_text_supports_canonical_issue_stems():
    assert zip.extract_number_from_text("BLT_490_2026-04-13") == 490
    assert zip.extract_number_from_text("GZ_500_2026-04-13") == 500


def test_find_archives_classifies_canonical_issue_archives_as_direct_cd(tmp_path):
    bulletin = tmp_path / "BLT_490_2026-04-13.zip"
    bulletin.write_bytes(b"bulletin")
    gazette = tmp_path / "GZ_500_2026-04-13.zip"
    gazette.write_bytes(b"gazette")

    direct_cd, single_issue, group_ranges = zip.find_archives(tmp_path)

    assert direct_cd == [
        (490, bulletin, "BLT_", "2026-04-13"),
        (500, gazette, "GZ_", "2026-04-13"),
    ]
    assert single_issue == []
    assert group_ranges == []


def test_find_archives_keeps_legacy_single_issue_archives_supported(tmp_path):
    legacy_bulletin = tmp_path / "490_2026-04-13.zip"
    legacy_bulletin.write_bytes(b"legacy-bulletin")
    legacy_gazette = tmp_path / "500_Gazete_2026-04-13.zip"
    legacy_gazette.write_bytes(b"legacy-gazette")

    direct_cd, single_issue, group_ranges = zip.find_archives(tmp_path)

    assert direct_cd == []
    assert single_issue == [
        (490, legacy_bulletin, "BLT_", "2026-04-13"),
        (500, legacy_gazette, "GZ_", "2026-04-13"),
    ]
    assert group_ranges == []
