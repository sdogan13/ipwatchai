from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from ui_scrape_collection import collect_blt_issue


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


ROOT_DIR = _resolve_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)


async def _run_targets(targets: list[str], *, headless: bool, max_scroll_seconds: int, limit: int) -> None:
    for issue_no in targets:
        safe_val = re.sub(r"[^\w\s-]", "", issue_no).strip().replace(" ", "_")
        out_dir = ROOT_DIR / f"BLT_{safe_val}"
        result = await collect_blt_issue(
            issue_no,
            None,
            out_dir,
            headless=headless,
            max_scroll_seconds=max_scroll_seconds,
            limit=limit,
        )
        print(f"{issue_no}: {result}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--names", type=str, nargs="+", default=[], help="Bulletin numbers to search")
    parser.add_argument(
        "--range",
        type=int,
        nargs=2,
        metavar=("START", "END"),
        help="Search a numeric range of bulletin numbers (inclusive)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max rows")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument("--max-scroll-seconds", type=int, default=0, help="Max time (0 for infinite)")
    args = parser.parse_args()

    search_targets = list(args.names)
    if args.range:
        start, end = args.range
        search_targets.extend(str(i) for i in range(start, end + 1))
    if not search_targets:
        parser.error("Provide --names and/or --range with at least one bulletin number.")

    asyncio.run(
        _run_targets(
            search_targets,
            headless=args.headless,
            max_scroll_seconds=args.max_scroll_seconds,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
