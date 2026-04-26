from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from ui_scrape_collection import collect_gz_issue


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


def _expand_names(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        if "-" in value and re.fullmatch(r"\d+-\d+", value):
            start, end = map(int, value.split("-", 1))
            if start <= end:
                expanded.extend(str(i) for i in range(start, end + 1))
                continue
        expanded.append(value)
    return expanded


ROOT_DIR = _resolve_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)


async def _run_targets(targets: list[str], *, headless: bool, max_scroll_seconds: int, limit: int) -> None:
    for issue_no in targets:
        safe_val = re.sub(r"[^\w\s-]", "", issue_no).strip().replace(" ", "_")
        out_dir = ROOT_DIR / f"GZ_{safe_val}"
        result = await collect_gz_issue(
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
    parser.add_argument("--names", type=str, nargs="+", default=[], help="Gazette numbers to search")
    parser.add_argument("--limit", type=int, default=0, help="Max rows")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    parser.add_argument("--max-scroll-seconds", type=int, default=0, help="Max time (0 for infinite)")
    args = parser.parse_args()

    targets = _expand_names(list(args.names))
    if not targets:
        parser.error("Provide --names with at least one gazette number or range.")

    asyncio.run(
        _run_targets(
            targets,
            headless=args.headless,
            max_scroll_seconds=args.max_scroll_seconds,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
