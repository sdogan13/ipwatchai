"""
Launch the name_tr refresh as a detached background process on Windows.

This launcher is designed for long-running offline translation refreshes that
must survive the initiating terminal or Codex session closing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "translation_refresh"
STARTUP_GRACE_SECONDS = 20

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _background_python_executable() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return sys.executable


def build_refresh_args(args: argparse.Namespace) -> list[str]:
    command = [
        _background_python_executable(),
        "-u",
        str(PROJECT_ROOT / "scripts" / "regenerate_name_tr.py"),
        "--backend",
        args.backend,
    ]
    if args.skip_benchmark:
        command.append("--skip-benchmark")
    if args.null_only:
        command.append("--null-only")
    if args.dry_run:
        command.append("--dry-run")
    if args.resume_from_id:
        command.extend(["--resume-from-id", args.resume_from_id])
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.batch_size is not None:
        command.extend(["--batch-size", str(args.batch_size)])
    if args.translate_batch_size is not None:
        command.extend(["--translate-batch-size", str(args.translate_batch_size)])
    if args.ordering_mode:
        command.extend(["--ordering-mode", args.ordering_mode])
    if args.campaign_watermark:
        command.extend(["--campaign-watermark", args.campaign_watermark])
    if args.output_root:
        command.extend(["--output-root", str(Path(args.output_root).resolve())])
    if args.metadata_root:
        command.extend(["--metadata-root", str(Path(args.metadata_root).resolve())])
    return command


def launch_background(args: argparse.Namespace) -> dict:
    output_root = Path(args.output_root or DEFAULT_OUTPUT_ROOT).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    stamp = _timestamp()
    stdout_path = output_root / f"name_tr_refresh_bg_{stamp}.stdout.log"
    stderr_path = output_root / f"name_tr_refresh_bg_{stamp}.stderr.log"
    manifest_path = output_root / f"name_tr_refresh_bg_{stamp}.json"
    latest_manifest_path = output_root / "name_tr_refresh_bg_latest.json"

    command = build_refresh_args(args)
    creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

    stdout_handle = open(stdout_path, "a", encoding="utf-8")
    stderr_handle = open(stderr_path, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
            close_fds=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    deadline = time.time() + STARTUP_GRACE_SECONDS
    startup_returncode = process.poll()
    if startup_returncode is not None and not isinstance(startup_returncode, int):
        startup_returncode = None
    while startup_returncode is None and time.time() < deadline:
        time.sleep(0.5)
        startup_returncode = process.poll()
        if startup_returncode is not None and not isinstance(startup_returncode, int):
            startup_returncode = None

    payload = {
        "pid": process.pid,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cwd": str(PROJECT_ROOT),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "command": command,
        "creationflags": creationflags,
        "startup_wait_seconds": STARTUP_GRACE_SECONDS,
        "startup_returncode": startup_returncode,
        "startup_healthy": startup_returncode is None,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch name_tr refresh in the background")
    parser.add_argument("--backend", default="madlad", help="Translation backend to use for refresh")
    parser.add_argument("--skip-benchmark", action="store_true", help="Skip the refresh benchmark gate")
    parser.add_argument("--resume-from-id", type=str, default=None, help="Resume after this trademark UUID")
    parser.add_argument("--limit", type=int, default=None, help="Optional bounded run size")
    parser.add_argument("--batch-size", type=int, default=None, help="Refresh batch size override")
    parser.add_argument("--translate-batch-size", type=int, default=None, help="MADLAD generation microbatch override")
    parser.add_argument("--ordering-mode", type=str, default=None, help="Refresh row ordering mode")
    parser.add_argument("--campaign-watermark", type=str, default=None, help="Skip MADLAD rows updated at or after this UTC timestamp")
    parser.add_argument("--null-only", action="store_true", help="Only refresh rows with missing translation state")
    parser.add_argument("--dry-run", action="store_true", help="Run detached but without database writes")
    parser.add_argument("--output-root", type=str, default=str(DEFAULT_OUTPUT_ROOT), help="Directory for logs and manifest")
    parser.add_argument("--metadata-root", type=str, default=None, help="Optional bulletins root for metadata.json sync")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    payload = launch_background(args)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
