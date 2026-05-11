"""One-shot CLI wrapper around ``data_collection_cografi.migrate_to_subfolder_layout``.

Converts the legacy flat-layout cografi bulletins folder
(``{N}.pdf`` plus mis-named ``{N1}-{N2}.pdf`` RAR bundles) into the
subfolder layout (``CI_{N}_{date}/bulletin.pdf``) the modern collector
writes to.

Usage::

    python scripts/migrate_cografi_layout.py
    python scripts/migrate_cografi_layout.py --dry-run
    python scripts/migrate_cografi_layout.py --bulletins-root C:/path/to/Cografi_Isaret...
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from data_collection_cografi import (  # noqa: E402
    _LOCAL_DEFAULT_BULLETINS_DIR,
    migrate_to_subfolder_layout,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [CI-MIGRATE] - %(levelname)s - %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_cografi_layout")
    parser.add_argument(
        "--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"bulletins root (default: {_LOCAL_DEFAULT_BULLETINS_DIR})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would happen without moving anything on disk",
    )
    args = parser.parse_args(argv)
    report = migrate_to_subfolder_layout(args.bulletins_root, dry_run=args.dry_run)
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
