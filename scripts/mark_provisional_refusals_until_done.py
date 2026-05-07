"""Chunk unchecked old live-status candidates into provisional refusals."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.pool import close_pool, get_connection, release_connection
from pipeline.repair import run_live_status_provisional_refusal_mark


def _write_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mark pending old published rows as provisional refused rows in chunks."
    )
    parser.add_argument("--limit", type=int, default=20000, help="Rows to update per committed chunk.")
    parser.add_argument("--log-file", required=True, help="JSONL log file for chunk events.")
    parser.add_argument("--max-chunks", type=int, default=1000, help="Safety cap for chunk loop.")
    args = parser.parse_args()

    log_file = Path(args.log_file)
    total_marked = 0
    try:
        for chunk in range(1, args.max_chunks + 1):
            conn = get_connection()
            try:
                summary = run_live_status_provisional_refusal_mark(conn=conn, limit=args.limit)
            finally:
                release_connection(conn)

            marked = int(summary.get("marked") or 0)
            total_marked += marked
            event = {
                "event": "provisional_refusal_chunk",
                "timestamp": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
                "chunk": chunk,
                "limit": args.limit,
                "total_marked": total_marked,
                **summary,
            }
            _write_event(log_file, event)
            if marked <= 0:
                return 0

        _write_event(
            log_file,
            {
                "event": "provisional_refusal_max_chunks",
                "timestamp": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
                "limit": args.limit,
                "max_chunks": args.max_chunks,
                "total_marked": total_marked,
            },
        )
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
