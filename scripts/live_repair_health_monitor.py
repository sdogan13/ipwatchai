from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import time
from pathlib import Path


def _process_running(pid: int) -> bool:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ '1' }} else {{ '0' }}",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result.stdout.strip() == "1"


def _last_json_event(path: Path) -> dict | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            return json.loads(line)
        except Exception:
            return {"raw": line[-1000:]}
    return None


def _last_text_line(path: Path) -> str | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-1][-1000:] if lines else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-pid", type=int, required=True)
    parser.add_argument("--repair-log", required=True)
    parser.add_argument("--stderr-log", required=True)
    parser.add_argument("--health-log", required=True)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--max-samples", type=int, default=1440)
    args = parser.parse_args()

    repair_log = Path(args.repair_log)
    stderr_log = Path(args.stderr_log)
    health_log = Path(args.health_log)
    health_log.parent.mkdir(parents=True, exist_ok=True)

    for _ in range(args.max_samples):
        running = _process_running(args.repair_pid)
        event = {
            "timestamp": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
            "repair_pid": args.repair_pid,
            "running": running,
            "repair_log_exists": repair_log.exists(),
            "repair_log_size": repair_log.stat().st_size if repair_log.exists() else 0,
            "last_event": _last_json_event(repair_log),
            "last_stderr": _last_text_line(stderr_log),
        }
        with health_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if not running:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
