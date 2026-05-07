"""Compatibility wrapper for trademark ingest."""

# Allow running as both `python -m pipeline.ingest` and `python pipeline/ingest.py`.
if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipeline.ingest_rules import *  # noqa: F401,F403
    from pipeline.ingest_bootstrap import *  # noqa: F401,F403
    from pipeline.ingest_runtime import *  # noqa: F401,F403
else:
    from .ingest_rules import *  # noqa: F401,F403
    from .ingest_bootstrap import *  # noqa: F401,F403
    from .ingest_runtime import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
