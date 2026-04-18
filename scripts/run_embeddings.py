"""Standalone embedding generation for specific folders."""

import os
import sys
import time
from pathlib import Path

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"
_LOCAL_DEFAULT_FOLDER_NAMES = (
    "BLT_485_2026-01-27",
    "GZ_499_2026-01-30",
)

AI_ENTRYPOINT = _LOCAL_PROJECT_ROOT / "pipeline" / "ai.py"
LOG_PATH = Path(__file__).resolve().with_name("embedding_run.log")


def _resolve_local_run_embeddings_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


BULLETINS_ROOT = _resolve_local_run_embeddings_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)
FOLDERS = [BULLETINS_ROOT / folder_name for folder_name in _LOCAL_DEFAULT_FOLDER_NAMES]

_LOG_FILE = None


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)

    if _LOG_FILE is not None:
        _LOG_FILE.write(line + "\n")
        _LOG_FILE.flush()


def _configure_runtime():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    os.environ["ENVIRONMENT"] = "development"
    os.environ["OCR_LANGUAGES"] = '["en","tr"]'


def main():
    global _LOG_FILE

    _configure_runtime()

    with LOG_PATH.open("w", encoding="utf-8") as log_file:
        _LOG_FILE = log_file

        try:
            log("Loading pipeline/ai.py (all models)...")
            import importlib.util

            spec = importlib.util.spec_from_file_location("ai_mod", AI_ENTRYPOINT)
            ai_mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(ai_mod)
            log("All models loaded.")

            for folder in FOLDERS:
                log(f"Starting {folder.name}...")
                t0 = time.time()
                try:
                    ai_mod.process_folder(folder)
                    elapsed = time.time() - t0
                    log(f"DONE {folder.name} in {elapsed:.0f}s ({elapsed/60:.1f} min)")
                except Exception as exc:
                    import traceback

                    elapsed = time.time() - t0
                    log(f"ERROR {folder.name} after {elapsed:.0f}s: {exc}")
                    log(traceback.format_exc())

            log("All folders complete.")
        finally:
            _LOG_FILE = None


if __name__ == "__main__":
    main()
