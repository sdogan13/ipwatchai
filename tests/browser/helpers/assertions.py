from __future__ import annotations

from collections.abc import Callable, Iterable

from tests.browser.helpers.artifacts import capture_failure_artifacts
from tests.browser.helpers.config import BrowserConfig
from tests.browser.helpers.session import BrowserMonitor
from tests.live.helpers.assertions import LiveReporter


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(pattern in value for pattern in patterns)


def run_browser_step(
    name: str,
    reporter: LiveReporter,
    page,
    monitor: BrowserMonitor,
    config: BrowserConfig,
    action: Callable[[], None],
    *,
    allow_console_errors: tuple[str, ...] = (),
    allow_request_failures: tuple[str, ...] = (),
) -> bool:
    monitor.clear()
    try:
        action()

        issues: list[str] = []
        issues.extend(monitor.page_errors)
        issues.extend(
            error for error in monitor.console_errors if not _matches_any(error, allow_console_errors)
        )
        issues.extend(
            failure for failure in monitor.request_failures if not _matches_any(failure, allow_request_failures)
        )

        if issues:
            raise AssertionError("; ".join(issues[:3]))

        reporter.ok(name)
        reporter.record(name, True)
        return True
    except Exception as exc:
        artifacts = capture_failure_artifacts(
            page,
            config,
            name,
            error=str(exc),
            console_errors=monitor.console_errors,
            page_errors=monitor.page_errors,
            request_failures=monitor.request_failures,
            warnings=monitor.warnings,
        )
        detail = f"{exc} | screenshot={artifacts['screenshot']}"
        reporter.fail(f"{name} -> {exc}")
        reporter.record(name, False, detail)
        return False
