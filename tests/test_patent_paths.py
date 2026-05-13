"""Unit tests for ``patent_paths`` — bulletin folder naming."""
from __future__ import annotations

from pathlib import Path

import pytest

from patent_paths import bulletin_folder_name, bulletin_folder_path


def test_bulletin_folder_name_canonicalises_slash_and_dash() -> None:
    """CD ships '2025/8'; PDF ships '2025-08'. Both -> PT_2025_8_*."""
    assert bulletin_folder_name("2025/8", "2025-08-21") == "PT_2025_8_2025-08-21"
    assert bulletin_folder_name("2025-08", "2025-08-21") == "PT_2025_8_2025-08-21"
    assert bulletin_folder_name("2025/12", "2025-12-22") == "PT_2025_12_2025-12-22"
    assert bulletin_folder_name("2025-12", "2025-12-22") == "PT_2025_12_2025-12-22"


def test_bulletin_folder_name_strips_leading_month_zero() -> None:
    """Bulletin no canonical form has no leading zero — matches the
    printed cover-page format."""
    assert bulletin_folder_name("2025/08", "2025-08-21") == "PT_2025_8_2025-08-21"
    assert bulletin_folder_name("2025-08", "2025-08-21") == "PT_2025_8_2025-08-21"


def test_bulletin_folder_name_rejects_missing_inputs() -> None:
    with pytest.raises(ValueError, match="bulletin_no is required"):
        bulletin_folder_name(None, "2025-08-21")
    with pytest.raises(ValueError, match="bulletin_no is required"):
        bulletin_folder_name("", "2025-08-21")
    with pytest.raises(ValueError, match="bulletin_date is required"):
        bulletin_folder_name("2025/8", None)
    with pytest.raises(ValueError, match="bulletin_date is required"):
        bulletin_folder_name("2025/8", "")


def test_bulletin_folder_name_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="must be 'YYYY/M' or 'YYYY-MM'"):
        bulletin_folder_name("not-a-bulletin", "2025-08-21")
    with pytest.raises(ValueError, match="must be 'YYYY/M' or 'YYYY-MM'"):
        bulletin_folder_name("2025", "2025-08-21")
    with pytest.raises(ValueError, match="must be ISO YYYY-MM-DD"):
        bulletin_folder_name("2025/8", "21-08-2025")
    with pytest.raises(ValueError, match="must be ISO YYYY-MM-DD"):
        bulletin_folder_name("2025/8", "2025/08/21")


def test_bulletin_folder_path_joins_under_bulletins_dir(tmp_path: Path) -> None:
    out = bulletin_folder_path(tmp_path, "2025/8", "2025-08-21")
    assert out == tmp_path / "PT_2025_8_2025-08-21"
    assert not out.exists()      # helper does not create the folder
