"""Guard source files against accidental mojibake in user-facing strings."""

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_EXTENSIONS = {
    ".bat",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
EXCLUDED_PREFIXES = (
    ".git/",
    ".phase0_",
    ".pytest_cache/",
    ".tmp_",
    "archive_bulletins/",
    "artifacts/",
    "bulletins/",
    "custom_bulletins/",
    "pgdata/",
    "pytest-cache-files-",
    "static/avatars/",
    "temp_uploads/",
    "tests/fixtures/model_cache/",
    "uploads/",
)
SUSPECT_PATTERNS = (
    "\u00c3",
    "\u00c4\u00b0",
    "\u00c4\u00b1",
    "\u00c4\u0178",
    "\u00c4\u017e",
    "\u00c5\u0178",
    "\u00c5\u017e",
    "\u00e2\u20ac",
)
MOJIBAKE_C3 = "\u00c3"
ALLOWLIST_MARKERS = {
    "metadata.py": ("_SCRAPED_MOJIBAKE_TOKENS",),
    "pipeline/ingest_helpers.py": ("if not any(ch in repaired",),
    "pipeline/ingest_rules.py": ("if not any(ch in repaired",),
    "tests/test_scoring_engine.py": (f'assert "{MOJIBAKE_C3}" not',),
    "tests/test_source_encoding_audit.py": ("SUSPECT_PATTERNS", "ALLOWLIST_MARKERS"),
    "utils/deadline.py": ("if not any(ch in repaired",),
}


def _source_files():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        )
        paths = [
            path
            for path in result.stdout.decode("utf-8", errors="replace").split("\0")
            if path
        ]
    except Exception:
        paths = [str(path.relative_to(PROJECT_ROOT)).replace("\\", "/") for path in PROJECT_ROOT.rglob("*")]

    for path in paths:
        normalized = path.replace("\\", "/")
        if any(normalized.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
            continue
        if Path(normalized).suffix not in SOURCE_EXTENSIONS:
            continue
        absolute = PROJECT_ROOT / normalized
        if absolute.is_file():
            yield normalized, absolute


def _is_allowed(path: str, line: str) -> bool:
    return any(marker in line for marker in ALLOWLIST_MARKERS.get(path, ()))


def test_source_files_do_not_contain_accidental_mojibake():
    findings = []
    for path, absolute in _source_files():
        text = absolute.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(pattern in line for pattern in SUSPECT_PATTERNS) and not _is_allowed(path, line):
                findings.append(f"{path}:{line_no}: {line.strip()}")

    assert not findings, "Potential mojibake found:\n" + "\n".join(findings[:50])
