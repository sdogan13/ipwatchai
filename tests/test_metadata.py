import json
from pathlib import Path
from unittest.mock import Mock, mock_open

import metadata


class _FakePath:
    def __init__(self, name: str, *, is_dir: bool = True, exists: bool = True):
        self.name = name
        self._is_dir = is_dir
        self._exists = exists
        self.children = {}

    def is_dir(self):
        return self._is_dir

    def exists(self):
        return self._exists

    def __truediv__(self, child: str):
        if child not in self.children:
            self.children[child] = _FakePath(child, is_dir=False, exists=False)
        return self.children[child]

    def __fspath__(self):
        return self.name

    def __str__(self):
        return self.name


def test_process_single_folder_overwrites_existing_metadata_when_db_files_present(monkeypatch):
    folder = _FakePath("BLT_490_2026-04-13")
    metadata_path = folder / "metadata.json"
    metadata_path._exists = True
    fake_script = _FakePath("tmbulletin.script", is_dir=False, exists=True)

    find_db_files = Mock(return_value=[fake_script])
    parse_tmbulletin_files = Mock(
        return_value=[
            {
                "APPLICATIONNO": "2026/123456",
                "STATUS": "Application/Published",
                "TRADEMARK": {
                    "BULLETIN_NO": "490",
                    "BULLETIN_DATE": "2026-04-13",
                },
            }
        ]
    )
    mocked_open = mock_open()

    monkeypatch.setattr(metadata, "find_db_files", find_db_files)
    monkeypatch.setattr(metadata, "parse_tmbulletin_files", parse_tmbulletin_files)
    monkeypatch.setattr("builtins.open", mocked_open)

    result = metadata.process_single_folder(folder, skip_existing=True)

    assert result["status"] == "success"
    assert result["records"] == 1
    find_db_files.assert_called_once_with(folder)
    parse_tmbulletin_files.assert_called_once_with(
        [fake_script],
        "Application/Published",
        "490",
        "2026-04-13",
    )
    mocked_open.assert_any_call(metadata_path, "w", encoding="utf-8")


def test_process_single_folder_preserves_ai_fields_when_inputs_match(monkeypatch, tmp_path: Path):
    folder = tmp_path / "BLT_490_2026-04-13"
    folder.mkdir()
    metadata_path = folder / metadata.OUTPUT_NAME
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026 / 123456",
                    "IMAGE": "2026_123456",
                    "TRADEMARK": {"NAME": "ORION"},
                    "image_embedding": [0.2],
                    "dinov2_embedding": [0.3],
                    "color_histogram": [0.4],
                    "logo_ocr_text": "",
                    "name_tr": "ORION",
                    "detected_lang": "tr",
                    "name_tr_backend": "local",
                    "name_tr_model": "model",
                    "name_tr_updated_at": "2026-04-29T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(metadata, "find_db_files", Mock(return_value=[folder / "tmbulletin.script"]))
    monkeypatch.setattr(
        metadata,
        "parse_tmbulletin_files",
        Mock(
            return_value=[
                {
                    "APPLICATIONNO": "2026/123456",
                    "IMAGE": "2026_123456",
                    "STATUS": "Application/Published",
                    "TRADEMARK": {"NAME": "ORION"},
                }
            ]
        ),
    )

    result = metadata.process_single_folder(folder, skip_existing=True)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))[0]

    assert result["status"] == "success"
    assert saved["image_embedding"] == [0.2]
    assert saved["logo_ocr_text"] == ""
    assert saved["name_tr_backend"] == "local"


def test_process_single_folder_does_not_preserve_stale_text_ai_when_name_changes(monkeypatch, tmp_path: Path):
    folder = tmp_path / "BLT_490_2026-04-13"
    folder.mkdir()
    metadata_path = folder / metadata.OUTPUT_NAME
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026/123456",
                    "IMAGE": "2026_123456",
                    "TRADEMARK": {"NAME": "OLD"},
                    "image_embedding": [0.2],
                    "name_tr": "OLD",
                    "detected_lang": "tr",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(metadata, "find_db_files", Mock(return_value=[folder / "tmbulletin.script"]))
    monkeypatch.setattr(
        metadata,
        "parse_tmbulletin_files",
        Mock(
            return_value=[
                {
                    "APPLICATIONNO": "2026/123456",
                    "IMAGE": "2026_123456",
                    "STATUS": "Application/Published",
                    "TRADEMARK": {"NAME": "NEW"},
                }
            ]
        ),
    )

    result = metadata.process_single_folder(folder, skip_existing=True)
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))[0]

    assert result["status"] == "success"
    assert "name_tr" not in saved
    assert "detected_lang" not in saved
    assert saved["image_embedding"] == [0.2]


def test_process_single_folder_skips_existing_metadata_when_no_db_files_present(monkeypatch):
    folder = _FakePath("GZ_500_2026-04-13")
    metadata_path = folder / "metadata.json"
    metadata_path._exists = True

    find_db_files = Mock(return_value=[])
    parse_tmbulletin_files = Mock()
    mocked_open = mock_open()

    monkeypatch.setattr(metadata, "find_db_files", find_db_files)
    monkeypatch.setattr(metadata, "parse_tmbulletin_files", parse_tmbulletin_files)
    monkeypatch.setattr("builtins.open", mocked_open)

    result = metadata.process_single_folder(folder, skip_existing=True)

    assert result["status"] == "skipped"
    find_db_files.assert_called_once_with(folder)
    parse_tmbulletin_files.assert_not_called()
    mocked_open.assert_not_called()


def test_merge_scraped_records_overwrites_allowed_fields_but_preserves_canonical_nice_classes():
    canonical = [
        {
            "APPLICATIONNO": "2026/123456",
            "STATUS": "Application/Published",
            "IMAGE": "old_image",
            "TRADEMARK": {
                "APPLICATIONDATE": "2026-01-01",
                "REGISTERNO": "",
                "NAME": "OLD NAME",
                "NICECLASSES_RAW": "1, 2, 3, 4, 5, 6, 7",
                "NICECLASSES_LIST": ["1", "2", "3", "4", "5", "6", "7"],
            },
            "HOLDERS": [
                {
                    "TITLE": "OLD HOLDER",
                    "ADDRESS": "Old Address",
                }
            ],
            "GOODS": [{"TEXT": "canonical goods"}],
            "EXTRACTEDGOODS": ["canonical extracted goods"],
        }
    ]
    scraped = [
        {
            "APPLICATIONNO": "2026/123456",
            "STATUS": "Registered",
            "IMAGE": "new_image",
            "TRADEMARK": {
                "APPLICATIONDATE": "2026-02-02",
                "REGISTERNO": "12345",
                "NAME": "NEW NAME",
                "NICECLASSES_RAW": "1, 2, 3, 4, 5, 6",
                "NICECLASSES_LIST": ["1", "2", "3", "4", "5", "6"],
            },
            "HOLDERS": [
                {
                    "TITLE": "NEW HOLDER",
                    "ADDRESS": "Scraped Address",
                }
            ],
            "GOODS": [{"TEXT": "scraped goods"}],
            "EXTRACTEDGOODS": ["scraped extracted goods"],
        }
    ]

    result = metadata.merge_scraped_records(canonical, scraped)
    merged = result["records"][0]

    assert result["changed_records"] == 1
    assert result["unmatched"] == 0
    assert merged["STATUS"] == "Registered"
    assert merged["IMAGE"] == "new_image"
    assert merged["TRADEMARK"]["APPLICATIONDATE"] == "2026-02-02"
    assert merged["TRADEMARK"]["REGISTERNO"] == "12345"
    assert merged["TRADEMARK"]["NAME"] == "NEW NAME"
    assert merged["TRADEMARK"]["NICECLASSES_RAW"] == "1, 2, 3, 4, 5, 6, 7"
    assert merged["TRADEMARK"]["NICECLASSES_LIST"] == ["1", "2", "3", "4", "5", "6", "7"]
    assert merged["HOLDERS"][0]["TITLE"] == "NEW HOLDER"
    assert merged["HOLDERS"][0]["ADDRESS"] == "Old Address"
    assert merged["GOODS"] == [{"TEXT": "canonical goods"}]
    assert merged["EXTRACTEDGOODS"] == ["canonical extracted goods"]


def test_merge_scraped_records_clears_dependent_ai_fields_when_inputs_change():
    canonical = [
        {
            "APPLICATIONNO": "2026/123456",
            "IMAGE": "old_image",
            "TRADEMARK": {"NAME": "OLD NAME"},
            "name_tr": "OLD NAME",
            "detected_lang": "tr",
            "image_embedding": [0.2],
            "dinov2_embedding": [0.3],
            "color_histogram": [0.4],
            "logo_ocr_text": "old ocr",
        }
    ]
    scraped = [
        {
            "APPLICATIONNO": "2026/123456",
            "IMAGE": "new_image",
            "TRADEMARK": {"NAME": "NEW NAME"},
        }
    ]

    result = metadata.merge_scraped_records(canonical, scraped)
    merged = result["records"][0]

    assert result["changed_records"] == 1
    assert merged["IMAGE"] == "new_image"
    assert merged["TRADEMARK"]["NAME"] == "NEW NAME"
    for field in metadata.AI_TEXT_FIELDS + metadata.AI_IMAGE_FIELDS:
        assert field not in merged


def test_db_image_matches_folder_accepts_same_image_stem_from_different_source_folder():
    assert metadata.db_image_matches_folder(
        "bulletins/Marka/GZ_457_2019-01-31/images/2018_74350.jpg",
        "BLT_309_2018-09-27",
        "2018_74350",
    )


def test_merge_scraped_records_ignores_empty_values_and_unmatched_records():
    canonical = [
        {
            "APPLICATIONNO": "2026/123456",
            "STATUS": "Application/Published",
            "IMAGE": "old_image",
            "TRADEMARK": {
                "APPLICATIONDATE": "2026-01-01",
                "REGISTERNO": "555",
                "NAME": "OLD NAME",
                "NICECLASSES_RAW": "1, 2, 3, 4, 5, 6, 7",
                "NICECLASSES_LIST": ["1", "2", "3", "4", "5", "6", "7"],
            },
            "HOLDERS": [{"TITLE": "OLD HOLDER", "ADDRESS": "Old Address"}],
        }
    ]
    scraped = [
        {
            "APPLICATIONNO": "2026/123456",
            "STATUS": " ",
            "IMAGE": "null",
            "TRADEMARK": {
                "APPLICATIONDATE": "",
                "REGISTERNO": None,
                "NAME": "N/A",
            },
            "HOLDERS": [{"TITLE": " "}],
        },
        {
            "APPLICATIONNO": "2026/999999",
            "STATUS": "Registered",
            "TRADEMARK": {"NAME": "UNMATCHED"},
        },
    ]

    result = metadata.merge_scraped_records(canonical, scraped)
    merged = result["records"][0]

    assert result["changed_records"] == 0
    assert result["unmatched"] == 1
    assert merged["STATUS"] == "Application/Published"
    assert merged["IMAGE"] == "old_image"
    assert merged["TRADEMARK"]["APPLICATIONDATE"] == "2026-01-01"
    assert merged["TRADEMARK"]["REGISTERNO"] == "555"
    assert merged["TRADEMARK"]["NAME"] == "OLD NAME"
    assert merged["HOLDERS"][0]["TITLE"] == "OLD HOLDER"


def test_merge_scraped_records_keeps_canonical_name_when_scraped_name_has_embedded_question_mark_artifact():
    canonical = [
        {
            "APPLICATIONNO": "2026/039807",
            "TRADEMARK": {"NAME": "sensum"},
            "HOLDERS": [{"TITLE": "SISTEMI Z RAČUNALNIŠKIM VIDOM D.O.O."}],
        }
    ]
    scraped = [
        {
            "APPLICATIONNO": "2026/039807",
            "TRADEMARK": {"NAME": "w?m the"},
            "HOLDERS": [{"TITLE": "SISTEMI Z RA?UNALNI?KIM VIDOM D.O.O."}],
        }
    ]

    result = metadata.merge_scraped_records(canonical, scraped)
    merged = result["records"][0]

    assert result["changed_records"] == 0
    assert merged["TRADEMARK"]["NAME"] == "sensum"
    assert merged["HOLDERS"][0]["TITLE"] == "SISTEMI Z RAČUNALNIŠKIM VIDOM D.O.O."


def test_merge_scraped_records_allows_legitimate_terminal_question_mark_in_name():
    canonical = [
        {
            "APPLICATIONNO": "2026/009978",
            "TRADEMARK": {"NAME": "bosmu"},
            "HOLDERS": [{"TITLE": "ÖMER FARUK AYGÜN"}],
        }
    ]
    scraped = [
        {
            "APPLICATIONNO": "2026/009978",
            "TRADEMARK": {"NAME": "boşmu?"},
            "HOLDERS": [{"TITLE": "ÖMER FARUK AYGÜN"}],
        }
    ]

    result = metadata.merge_scraped_records(canonical, scraped)
    merged = result["records"][0]

    assert result["changed_records"] == 1
    assert merged["TRADEMARK"]["NAME"] == "boşmu?"


def test_merge_scraped_sidecars_leaves_scrape_only_folder_pending(tmp_path: Path):
    folder = tmp_path / "BLT_490_2026-04-13"
    folder.mkdir()
    (folder / metadata.SCRAPED_OUTPUT_NAME).write_text("[]", encoding="utf-8")

    result = metadata.merge_scraped_sidecars(root_dir=tmp_path, verbose=False)

    assert result["folders_merged"] == 0
    assert result["pending"] == 1
    assert result["failed"] == 0


def test_merge_scraped_sidecars_writes_merged_metadata(tmp_path: Path):
    folder = tmp_path / "GZ_500_2026-03-31"
    folder.mkdir()
    metadata_path = folder / metadata.OUTPUT_NAME
    scraped_path = folder / metadata.SCRAPED_OUTPUT_NAME
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026/123456",
                    "STATUS": "Application/Published",
                    "IMAGE": "old_image",
                    "TRADEMARK": {
                        "APPLICATIONDATE": "2026-01-01",
                        "REGISTERNO": "",
                        "NAME": "OLD NAME",
                        "NICECLASSES_RAW": "1, 2, 3, 4, 5, 6, 7",
                        "NICECLASSES_LIST": ["1", "2", "3", "4", "5", "6", "7"],
                    },
                    "HOLDERS": [{"TITLE": "OLD HOLDER", "ADDRESS": "Old Address"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    scraped_path.write_text(
        json.dumps(
            [
                {
                    "APPLICATIONNO": "2026/123456",
                    "STATUS": "Registered",
                    "IMAGE": "new_image",
                    "TRADEMARK": {
                        "APPLICATIONDATE": "2026-02-02",
                        "REGISTERNO": "12345",
                        "NAME": "NEW NAME",
                    },
                    "HOLDERS": [{"TITLE": "NEW HOLDER"}],
                }
            ]
        ),
        encoding="utf-8",
    )

    result = metadata.merge_scraped_sidecars(root_dir=tmp_path, verbose=False)
    merged = json.loads(metadata_path.read_text(encoding="utf-8"))[0]

    assert result["folders_merged"] == 1
    assert result["records_merged"] == 1
    assert merged["STATUS"] == "Registered"
    assert merged["IMAGE"] == "new_image"
    assert merged["TRADEMARK"]["NAME"] == "NEW NAME"
    assert merged["TRADEMARK"]["NICECLASSES_LIST"] == ["1", "2", "3", "4", "5", "6", "7"]
    assert merged["HOLDERS"][0]["TITLE"] == "NEW HOLDER"
