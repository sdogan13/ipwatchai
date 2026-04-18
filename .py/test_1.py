import json
import os
from pathlib import Path

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"
_LOCAL_SAMPLE_FOLDER_NAME = "BLT_327"


def _resolve_local_test_1_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


BULLETINS_ROOT = _resolve_local_test_1_root(
    os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)
FILE_PATH = BULLETINS_ROOT / _LOCAL_SAMPLE_FOLDER_NAME / "metadata.json"


def main():
    if FILE_PATH.exists():
        with FILE_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            print("--- RESULTS ---")
            print(f"Folder: {FILE_PATH.parent.name}")
            print(f"Total Records: {len(data)}")
            print("---------------")
    else:
        print(f"Error: File not found at {FILE_PATH}")


if __name__ == "__main__":
    main()
