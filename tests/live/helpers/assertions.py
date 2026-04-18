from __future__ import annotations

import sys


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class LiveReporter:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def _emit(self, msg: str) -> None:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            sys.stdout.buffer.write((msg + "\n").encode(encoding, errors="replace"))
            sys.stdout.buffer.flush()

    def ok(self, msg: str) -> None:
        self._emit(f"  {GREEN}[PASS]{RESET} {msg}")

    def fail(self, msg: str) -> None:
        self._emit(f"  {RED}[FAIL]{RESET} {msg}")

    def warn(self, msg: str) -> None:
        self._emit(f"  {YELLOW}[WARN]{RESET} {msg}")

    def info(self, msg: str) -> None:
        self._emit(f"  {CYAN}[INFO]{RESET} {msg}")

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append({"name": name, "passed": passed, "detail": detail})

    def expect_status(self, name: str, response, expected: int, *, record_pass: bool = False) -> bool:
        if response.status_code == expected:
            self.ok(f"{name} -> {expected}")
            if record_pass:
                self.record(name, True)
            return True
        response_text = getattr(response, "text", "")[:200]
        self.fail(f"{name} -> expected {expected}, got {response.status_code}: {response_text}")
        self.record(name, False, response_text)
        return False

    def print_heading(self, title: str, *, server: str | None = None, user: str | None = None) -> None:
        self._emit("")
        self._emit(f"{BOLD}{CYAN}{title}{RESET}")
        if server is not None:
            self._emit(f"  Server : {server}")
        if user is not None:
            self._emit(f"  User   : {user}")
        self._emit("")

    def print_section(self, title: str, width: int = 62) -> None:
        padding = "-" * max(0, width - len(title) - 4)
        self._emit(f"\n{BOLD}-- {title} {padding}{RESET}")

    def summary(self, title: str) -> int:
        passed = [item for item in self.results if item["passed"]]
        failed = [item for item in self.results if not item["passed"]]
        total = len(self.results)

        self._emit("")
        self._emit(f"{BOLD}{CYAN}{title}{RESET}")
        self._emit(f"  {GREEN}Passed:{RESET} {len(passed)}/{total}")
        self._emit(f"  {RED}Failed:{RESET} {len(failed)}/{total}")
        if failed:
            self._emit(f"\n{RED}Failures:{RESET}")
            for item in failed:
                detail = f" ({item['detail'][:140]})" if item["detail"] else ""
                self._emit(f"  - {item['name']}{detail}")
        self._emit("")
        return len(failed)
