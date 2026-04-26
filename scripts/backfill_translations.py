"""Compatibility wrapper for the canonical name_tr refresh script."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from regenerate_name_tr import main


if __name__ == "__main__":
    raise SystemExit(main(["--null-only"]))
