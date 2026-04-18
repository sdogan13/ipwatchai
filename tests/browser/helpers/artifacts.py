from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tests.browser.helpers.config import BrowserConfig


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "step"


def capture_failure_artifacts(
    page,
    config: BrowserConfig,
    name: str,
    *,
    error: str,
    console_errors: list[str],
    page_errors: list[str],
    request_failures: list[str],
    warnings: list[str],
) -> dict[str, Path]:
    run_dir = config.artifacts_dir / datetime.now(timezone.utc).strftime("%Y%m%d")
    run_dir.mkdir(parents=True, exist_ok=True)

    stem = datetime.now(timezone.utc).strftime("%H%M%S") + "_" + _slugify(name)
    screenshot_path = run_dir / f"{stem}.png"
    html_path = run_dir / f"{stem}.html"
    log_path = run_dir / f"{stem}.json"

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
    except Exception:
        screenshot_path.write_bytes(b"")

    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception as exc:
        html_path.write_text(f"Failed to capture page HTML: {exc}", encoding="utf-8")

    log_payload = {
        "name": name,
        "url": getattr(page, "url", ""),
        "title": page.title() if hasattr(page, "title") else "",
        "error": error,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "request_failures": request_failures,
        "warnings": warnings,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "screenshot": screenshot_path,
        "html": html_path,
        "log": log_path,
    }
