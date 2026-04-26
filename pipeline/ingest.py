"""Compatibility wrapper for trademark ingest."""

from .ingest_rules import *  # noqa: F401,F403
from .ingest_bootstrap import *  # noqa: F401,F403
from .ingest_runtime import *  # noqa: F401,F403


if __name__ == "__main__":
    main()
