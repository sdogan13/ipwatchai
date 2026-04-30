"""Run live status and Nice-class repair batches until no candidates remain."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.pool import close_pool, get_connection, release_connection
from pipeline.repair import run_live_class_repair, run_live_status_repair


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> str:
    return str(value)


def _emit(log_path: Path, event: dict[str, Any]) -> None:
    event.setdefault("ts", _now_iso())
    line = json.dumps(event, ensure_ascii=False, default=_json_default)
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    conn = get_connection()
    try:
        status_summary = run_live_status_repair(
            conn=conn,
            limit=args.status_batch_size,
            artifact_dir=args.artifact_dir,
        )
        class_summary = run_live_class_repair(
            conn=conn,
            limit=args.class_batch_size,
            artifact_dir=args.artifact_dir,
        )
        return {"live_status": status_summary, "live_classes": class_summary}
    finally:
        release_connection(conn)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-batch-size", type=int, default=20)
    parser.add_argument("--class-batch-size", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--empty-cycles-to-stop", type=int, default=1)
    parser.add_argument("--max-checks", type=int, default=None)
    parser.add_argument("--max-errors", type=int, default=20)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("artifacts/repair/live_trademark_checks"),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("artifacts/repair/live_repair_until_done.jsonl"),
    )
    args = parser.parse_args()

    total_checked = 0
    total_repaired = 0
    total_failed = 0
    consecutive_empty = 0
    consecutive_errors = 0
    cycle = 0

    _emit(
        args.log_file,
        {
            "event": "start",
            "status_batch_size": args.status_batch_size,
            "class_batch_size": args.class_batch_size,
            "artifact_dir": str(args.artifact_dir),
            "max_checks": args.max_checks,
        },
    )

    try:
        while True:
            cycle += 1
            started = time.monotonic()
            try:
                summaries = _run_cycle(args)
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                _emit(
                    args.log_file,
                    {
                        "event": "cycle_error",
                        "cycle": cycle,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "consecutive_errors": consecutive_errors,
                    },
                )
                if consecutive_errors >= args.max_errors:
                    _emit(
                        args.log_file,
                        {
                            "event": "stop",
                            "reason": "max_errors",
                            "cycle": cycle,
                            "total_checked": total_checked,
                            "total_repaired": total_repaired,
                            "total_failed": total_failed,
                        },
                    )
                    return 2
                time.sleep(max(args.sleep_seconds, 5.0))
                continue

            status_summary = summaries["live_status"]
            class_summary = summaries["live_classes"]
            checked = int(status_summary.get("checked", 0)) + int(class_summary.get("checked", 0))
            repaired = int(status_summary.get("repaired", 0)) + int(class_summary.get("repaired", 0))
            failed = int(status_summary.get("failed", 0)) + int(class_summary.get("failed", 0))
            total_checked += checked
            total_repaired += repaired
            total_failed += failed
            consecutive_empty = consecutive_empty + 1 if checked == 0 else 0

            _emit(
                args.log_file,
                {
                    "event": "cycle_complete",
                    "cycle": cycle,
                    "duration_seconds": round(time.monotonic() - started, 1),
                    "checked": checked,
                    "repaired": repaired,
                    "failed": failed,
                    "total_checked": total_checked,
                    "total_repaired": total_repaired,
                    "total_failed": total_failed,
                    "live_status": status_summary,
                    "live_classes": class_summary,
                },
            )

            if args.max_checks is not None and total_checked >= args.max_checks:
                _emit(
                    args.log_file,
                    {
                        "event": "stop",
                        "reason": "max_checks",
                        "cycle": cycle,
                        "total_checked": total_checked,
                        "total_repaired": total_repaired,
                        "total_failed": total_failed,
                    },
                )
                return 0

            if consecutive_empty >= args.empty_cycles_to_stop:
                _emit(
                    args.log_file,
                    {
                        "event": "stop",
                        "reason": "no_candidates",
                        "cycle": cycle,
                        "total_checked": total_checked,
                        "total_repaired": total_repaired,
                        "total_failed": total_failed,
                    },
                )
                return 0

            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
