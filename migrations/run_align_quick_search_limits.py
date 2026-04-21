"""Manual entrypoint for aligning legacy quick-search plan overrides."""

from __future__ import annotations

import sys
from pathlib import Path


project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from utils.seed_settings import align_legacy_quick_search_limits


def main() -> int:
    success = align_legacy_quick_search_limits()
    print("Quick-search limits aligned" if success else "Quick-search limit alignment failed")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
