"""Shared path helpers for the Patent / Faydalı Model pipeline.

Each bulletin's per-stage outputs (CD metadata, PDF metadata, unified
metadata, figures, raw HSQLDB files, source PDF copy) live together in
a single parent folder named for the bulletin. Mirrors the Marka /
Tasarım convention of one folder per bulletin.

Folder shape::

    bulletins/Patent__Faydali_Model/
      PT_2025_8_2025-08-21/
        bulletin.pdf            ← copy of the source PDF (pdf_extract)
        ptbulletin.log          ← raw HSQLDB log    (cd_extract)
        ptbulletin.script       ← raw HSQLDB DDL    (cd_extract)
        ptbulletin.properties   ← raw HSQLDB props  (cd_extract)
        cd_metadata.json        ← cd_extract output
        pdf_metadata.json       ← pdf_extract output
        metadata.json           ← reconcile output (Stage 5 reads this)
        events.json             ← Stage 7 (deferred)
        figures/                ← extracted images (Stage 3 / Stage 6)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_BULLETIN_NO_RE = re.compile(r"^\s*(\d{4})[/-](\d{1,2})\s*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def bulletin_folder_name(bulletin_no: Optional[str], bulletin_date: Optional[str]) -> str:
    """Return the canonical parent folder name for a bulletin.

    Examples:
      ``("2025/8",  "2025-08-21")``  -> ``"PT_2025_8_2025-08-21"``
      ``("2025-08", "2025-08-21")``  -> ``"PT_2025_8_2025-08-21"``
      ``("2025/12", "2025-12-22")``  -> ``"PT_2025_12_2025-12-22"``

    The bulletin_no is canonicalised (``"2025/8"`` and ``"2025-08"`` both
    produce ``"2025_8"`` — matching the bulletin number printed on the
    cover, no leading zero). The date is required to disambiguate when
    a bulletin number repeats across years (rare but possible in the
    legacy archive) and to match Tasarım's ``TS_{N}_{date}`` convention.

    Raises:
      - ``ValueError`` if either argument is missing or unparseable.
    """
    if not bulletin_no:
        raise ValueError(f"bulletin_no is required (got {bulletin_no!r})")
    if not bulletin_date:
        raise ValueError(f"bulletin_date is required (got {bulletin_date!r})")

    match = _BULLETIN_NO_RE.match(bulletin_no)
    if not match:
        raise ValueError(f"bulletin_no must be 'YYYY/M' or 'YYYY-MM' (got {bulletin_no!r})")
    year, month = match.group(1), match.group(2).lstrip("0") or "0"

    if not _DATE_RE.match(bulletin_date.strip()):
        raise ValueError(
            f"bulletin_date must be ISO YYYY-MM-DD (got {bulletin_date!r})"
        )

    return f"PT_{year}_{month}_{bulletin_date.strip()}"


def bulletin_folder_path(
    bulletins_dir: Path,
    bulletin_no: Optional[str],
    bulletin_date: Optional[str],
) -> Path:
    """Return the absolute parent folder path under ``bulletins_dir``.

    The folder is NOT created — callers (extractors, reconciler) decide
    when to ``mkdir(parents=True, exist_ok=True)``.
    """
    return Path(bulletins_dir) / bulletin_folder_name(bulletin_no, bulletin_date)
