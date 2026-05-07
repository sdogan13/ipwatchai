import subprocess
import sys
import textwrap
from pathlib import Path


def _run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_exports_fastapi_app():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from fastapi import FastAPI
        from main import app

        print(json.dumps({
            "is_fastapi": isinstance(app, FastAPI),
            "has_health": any(route.path == "/health" for route in app.routes),
        }))
        """
    )

    assert output == '{"is_fastapi": true, "has_health": true}'


def test_package_exports_fastapi_app():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from fastapi import FastAPI
        from turk_patent.app import app

        print(json.dumps({
            "is_fastapi": isinstance(app, FastAPI),
            "has_health": any(route.path == "/health" for route in app.routes),
        }))
        """
    )

    assert output == '{"is_fastapi": true, "has_health": true}'


def test_main_wrapper_reexports_private_search_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import _do_public_search, public_search, public_search_post

        print(json.dumps({
            "callable": callable(_do_public_search),
            "public_search": callable(public_search),
            "public_search_post": callable(public_search_post),
        }))
        """
    )

    assert output == '{"callable": true, "public_search": true, "public_search_post": true}'


def test_main_wrapper_reexports_image_lookup_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import find_trademark_image

        print(json.dumps({
            "callable": callable(find_trademark_image),
        }))
        """
    )

    assert output == '{"callable": true}'


def test_main_wrapper_reexports_search_credits_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import get_search_credits

        print(json.dumps({
            "callable": callable(get_search_credits),
        }))
        """
    )

    assert output == '{"callable": true}'


def test_main_wrapper_reexports_nice_class_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import get_class_name

        print(json.dumps({
            "callable": callable(get_class_name),
            "sample": get_class_name(35, "en"),
        }))
        """
    )

    assert output == '{"callable": true, "sample": "Advertising & Business"}'


def test_main_wrapper_reexports_admin_scoring_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import test_scoring

        print(json.dumps({
            "callable": callable(test_scoring),
        }))
        """
    )

    assert output == '{"callable": true}'


def test_main_wrapper_reexports_deprecated_search_helpers():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import simple_search, unified_search

        print(json.dumps({
            "simple_search": callable(simple_search),
            "unified_search": callable(unified_search),
        }))
        """
    )

    assert output == '{"simple_search": true, "unified_search": true}'


def test_main_wrapper_reexports_legacy_rollback_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import legacy_text_search

        print(json.dumps({
            "callable": callable(legacy_text_search),
        }))
        """
    )

    assert output == '{"callable": true}'


def test_main_wrapper_reexports_public_portfolio_helpers():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import public_portfolio, public_portfolio_csv

        print(json.dumps({
            "public_portfolio": callable(public_portfolio),
            "public_portfolio_csv": callable(public_portfolio_csv),
        }))
        """
    )

    assert output == '{"public_portfolio": true, "public_portfolio_csv": true}'


def test_main_wrapper_reexports_image_search_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        mock_torch.inference_mode.return_value = lambda func: func
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import search_by_image

        print(json.dumps({
            "callable": callable(search_by_image),
        }))
        """
    )

    assert output == '{"callable": true}'


def test_main_wrapper_reexports_enhanced_search_helper():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        mock_torch.inference_mode.return_value = lambda func: func
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        ensure_mock("apscheduler")
        ensure_mock("apscheduler.schedulers")
        ensure_mock("apscheduler.schedulers.asyncio")
        ensure_mock("apscheduler.triggers")
        ensure_mock("apscheduler.triggers.cron")
        ensure_mock("playwright")
        ensure_mock("playwright.sync_api")
        ensure_mock("playwright.async_api")

        from main import SearchRequest, enhanced_search, get_status_code

        print(json.dumps({
            "callable": callable(enhanced_search),
            "limit": SearchRequest(name="nike").limit,
            "status_code": get_status_code("Tescil Edildi"),
        }))
        """
    )

    assert output == '{"callable": true, "limit": 20, "status_code": "registered"}'


def test_risk_engine_legacy_entrypoints_import():
    output = _run_python(
        """
        import json
        import sys
        from unittest.mock import MagicMock

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")
        ensure_mock("pipeline.ai", MagicMock())

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        from risk_engine import get_risk_level, get_status_category, score_pair

        print(json.dumps({
            "get_risk_level": callable(get_risk_level),
            "get_status_category": callable(get_status_category),
            "score_pair": callable(score_pair),
        }))
        """
    )

    assert output == '{"get_risk_level": true, "get_status_category": true, "score_pair": true}'


def test_scoring_service_exports_expected_helpers():
    output = _run_python(
        """
        import json
        from services.scoring_service import compute_idf_weighted_score, tokenize

        print(json.dumps({
            "compute_idf_weighted_score": callable(compute_idf_weighted_score),
            "tokenize": callable(tokenize),
        }))
        """
    )

    assert output == '{"compute_idf_weighted_score": true, "tokenize": true}'


def test_pipeline_ingest_packaged_module_exports_expected_helpers():
    output = _run_python(
        """
        import json
        from pipeline import ingest

        print(json.dumps({
            "has_sanitize": callable(ingest.sanitize),
            "has_resolve_image_path": callable(ingest._resolve_image_path),
            "has_main": callable(ingest.main),
        }))
        """
    )

    assert output == '{"has_sanitize": true, "has_resolve_image_path": true, "has_main": true}'


def test_pipeline_ingest_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib
        import json
        import os
        import sys
        from pathlib import Path

        project_root = Path("pipeline/ingest.py").resolve().parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module():
            # Pop all pipeline submodules so ROOT_DIR is recomputed against the
            # current env vars instead of returning a cached value.
            for name in (
                "pipeline.ingest",
                "pipeline.ingest_runtime",
                "pipeline.ingest_bootstrap",
                "pipeline.ingest_helpers",
                "pipeline.ingest_rules",
                "pipeline",
            ):
                sys.modules.pop(name, None)
            return importlib.import_module("pipeline.ingest")

        # ingest_helpers calls load_dotenv() which fills missing vars from .env.
        # Use "" (empty) instead of pop so dotenv treats them as already set
        # but the falsy check in default_ingest_root() still falls through.
        os.environ["PIPELINE_BULLETINS_ROOT"] = ""
        os.environ["DATA_ROOT"] = ""
        default_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ["DATA_ROOT"] = ""
        pipeline_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = ""
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module()

        source = Path("pipeline/ingest.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_pipeline_ingest_script_entrypoint_supports_direct_cli_execution():
    result = _run_command([sys.executable, "pipeline/ingest.py", "--help"])

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_ingest_events_root_uses_local_project_boundary_and_env_overrides():
    default_output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)

        import ingest_events

        project_root = Path("ingest_events.py").resolve().parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        source = Path("ingest_events.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(ingest_events.ROOT_DIR.resolve()) == default_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )
    pipeline_output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)

        import ingest_events

        project_root = Path("ingest_events.py").resolve().parent
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())

        print(json.dumps({
            "pipeline_env_root_matches": str(ingest_events.ROOT_DIR.resolve()) == pipeline_expected,
        }))
        """
    )
    data_output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"

        import ingest_events

        project_root = Path("ingest_events.py").resolve().parent
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        print(json.dumps({
            "data_env_root_matches": str(ingest_events.ROOT_DIR.resolve()) == data_expected,
        }))
        """
    )

    assert default_output == '{"default_root_matches_local_project": true, "hardcoded_literal_absent": true}'
    assert pipeline_output == '{"pipeline_env_root_matches": true}'
    assert data_output == '{"data_env_root_matches": true}'


def test_pipeline_parallel_packaged_module_exports_expected_helpers():
    output = _run_python(
        """
        import json
        from pipeline import parallel

        print(json.dumps({
            "has_load_ai_module": callable(parallel.load_ai_module),
            "has_extract_folder_number": callable(parallel._extract_folder_number),
            "has_folder_sort_key": callable(parallel.folder_sort_key),
            "has_main": callable(parallel.main),
        }))
        """
    )

    assert output == '{"has_load_ai_module": true, "has_extract_folder_number": true, "has_folder_sort_key": true, "has_main": true}'


def test_pipeline_parallel_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib
        import json
        import os
        import sys
        from pathlib import Path

        project_root = Path("pipeline/parallel.py").resolve().parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module():
            sys.modules.pop("pipeline.parallel", None)
            sys.modules.pop("pipeline", None)
            return importlib.import_module("pipeline.parallel")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module()

        source = Path("pipeline/parallel.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_pipeline_ai_packaged_module_exports_expected_helpers():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )
        os.environ["AI_SKIP_MODEL_LOAD"] = "1"

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("open_clip")
        ensure_mock("redis")
        ensure_mock("easyocr")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("PIL.UnidentifiedImageError", type("UnidentifiedImageError", (Exception,), {}))
        ensure_mock("PIL.ImageFile", MagicMock(LOAD_TRUNCATED_IMAGES=False))
        ensure_mock("sentence_transformers")
        ensure_mock("tqdm")

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.log_batch_stats = MagicMock()
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        import pipeline.ai as canonical

        print(json.dumps({
            "has_run_embedding_generation": callable(canonical.run_embedding_generation),
            "has_process_folder": callable(canonical.process_folder),
            "has_main": callable(canonical.main),
        }))
        """
    )

    assert output == '{"has_run_embedding_generation": true, "has_process_folder": true, "has_main": true}'


def test_pipeline_ai_fallback_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import builtins
        import importlib
        import json
        import os
        import sys
        from pathlib import Path
        from unittest.mock import MagicMock

        os.environ["AI_SKIP_MODEL_LOAD"] = "1"

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("open_clip")
        ensure_mock("redis")
        ensure_mock("easyocr")
        ensure_mock("numpy")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("PIL.UnidentifiedImageError", type("UnidentifiedImageError", (Exception,), {}))
        ensure_mock("PIL.ImageFile", MagicMock(LOAD_TRUNCATED_IMAGES=False))
        ensure_mock("sentence_transformers")
        ensure_mock("tqdm")

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.log_batch_stats = MagicMock()
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        real_import = builtins.__import__

        def force_settings_import_error(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "config.settings":
                raise ImportError("forced settings import failure")
            if name == "config" and fromlist and "settings" in fromlist:
                raise ImportError("forced settings import failure")
            return real_import(name, globals, locals, fromlist, level)

        def load_module():
            sys.modules.pop("pipeline.ai", None)
            builtins.__import__ = force_settings_import_error
            try:
                return importlib.import_module("pipeline.ai")
            finally:
                builtins.__import__ = real_import

        project_root = Path("pipeline/ai.py").resolve().parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module()

        source = Path("pipeline/ai.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_blt_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        script_path = Path(".py/blt.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        mock_sync_api = types.ModuleType("playwright.sync_api")
        mock_sync_api.sync_playwright = MagicMock()
        sys.modules["playwright"] = types.ModuleType("playwright")
        sys.modules["playwright.sync_api"] = mock_sync_api

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_blt_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_blt_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_blt_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_blt_scrap_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        script_path = Path(".py/blt_scrap.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        mock_collection = types.ModuleType("ui_scrape_collection")
        mock_collection.collect_blt_issue = MagicMock()
        mock_collection.collect_gz_issue = MagicMock()
        sys.modules["ui_scrape_collection"] = mock_collection

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_blt_scrap_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_blt_scrap_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_blt_scrap_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_tescil_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        script_path = Path(".py/tescil_test.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        mock_collection = types.ModuleType("ui_scrape_collection")
        mock_collection.collect_blt_issue = MagicMock()
        mock_collection.collect_gz_issue = MagicMock()
        sys.modules["ui_scrape_collection"] = mock_collection

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_tescil_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_tescil_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_tescil_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_test_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path(".py/test.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_test_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_test_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_test_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_clean_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path(".py/clean.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_clean_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_clean_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_clean_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_images_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path(".py/images.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_images_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_images_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_images_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(Path(default_module.ROOT_DIR).resolve()) == default_expected,
            "pipeline_env_root_matches": str(Path(pipeline_module.ROOT_DIR).resolve()) == pipeline_expected,
            "data_env_root_matches": str(Path(data_module.ROOT_DIR).resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_gz_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        import sys
        import types
        from pathlib import Path

        send2trash_module = types.ModuleType("send2trash")
        send2trash_module.send2trash = lambda path: None
        sys.modules["send2trash"] = send2trash_module

        script_path = Path(".py/gz.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_gz_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_env_value = os.environ["PIPELINE_BULLETINS_ROOT"]
        pipeline_module = load_module("phase10_gz_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_env_value = os.environ["DATA_ROOT"]
        data_module = load_module("phase10_gz_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": default_module._resolve_local_gz_root(None, default_module._LOCAL_DEFAULT_BULLETINS_ROOT) == default_expected,
            "pipeline_env_root_matches": pipeline_module._resolve_local_gz_root(pipeline_env_value, pipeline_module._LOCAL_DEFAULT_BULLETINS_ROOT) == pipeline_expected,
            "data_env_root_matches": data_module._resolve_local_gz_root(data_env_value, data_module._LOCAL_DEFAULT_BULLETINS_ROOT) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_legacy_merge_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path(".py/merge.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_merge_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_env_value = os.environ["PIPELINE_BULLETINS_ROOT"]
        pipeline_module = load_module("phase10_merge_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_env_value = os.environ["DATA_ROOT"]
        data_module = load_module("phase10_merge_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": default_module._resolve_local_merge_root(None, default_module._LOCAL_DEFAULT_BULLETINS_ROOT) == default_expected,
            "pipeline_env_root_matches": pipeline_module._resolve_local_merge_root(pipeline_env_value, pipeline_module._LOCAL_DEFAULT_BULLETINS_ROOT) == pipeline_expected,
            "data_env_root_matches": data_module._resolve_local_merge_root(data_env_value, data_module._LOCAL_DEFAULT_BULLETINS_ROOT) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_pipeline_worker_uses_packaged_pipeline_modules():
    output = _run_python(
        """
        import json
        from pathlib import Path

        content = Path("workers/pipeline_worker.py").read_text(encoding="utf-8")

        print(json.dumps({
            "uses_packaged_ai": "from pipeline.ai import run_embedding_generation" in content,
            "uses_packaged_ingest": "from pipeline.ingest import run_ingest" in content,
            "uses_legacy_ai": "from ai import run_embedding_generation" in content,
            "uses_legacy_ingest": "from ingest import run_ingest" in content,
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"uses_packaged_ai": true, "uses_packaged_ingest": true, "uses_legacy_ai": false, "uses_legacy_ingest": false, "has_path_hack": false}'


def test_pipeline_worker_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import workers.pipeline_worker as worker

        print(json.dumps({
            "main_callable": callable(worker.main),
            "worker_class": hasattr(worker, "PipelineWorker"),
        }))
        """
    )

    assert output == '{"main_callable": true, "worker_class": true}'


def test_pipeline_scheduler_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import sys
        from pathlib import Path
        from unittest.mock import MagicMock

        sys.modules.setdefault("schedule", MagicMock())
        import workers.pipeline_scheduler as scheduler

        content = Path("workers/pipeline_scheduler.py").read_text(encoding="utf-8")

        print(json.dumps({
            "main_callable": callable(scheduler.main),
            "start_scheduler_callable": callable(scheduler.start_scheduler),
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"main_callable": true, "start_scheduler_callable": true, "has_path_hack": false}'


def test_scheduler_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import sys
        import types
        from pathlib import Path

        apscheduler = types.ModuleType("apscheduler")
        schedulers = types.ModuleType("apscheduler.schedulers")
        background = types.ModuleType("apscheduler.schedulers.background")
        triggers = types.ModuleType("apscheduler.triggers")
        cron = types.ModuleType("apscheduler.triggers.cron")

        class BackgroundScheduler:
            def __init__(self, *args, **kwargs):
                self.running = False

            def add_job(self, *args, **kwargs):
                return None

            def start(self):
                self.running = True

            def shutdown(self, wait=False):
                self.running = False

            def get_job(self, job_id):
                return None

        class CronTrigger:
            def __init__(self, *args, **kwargs):
                pass

        background.BackgroundScheduler = BackgroundScheduler
        cron.CronTrigger = CronTrigger

        sys.modules["apscheduler"] = apscheduler
        sys.modules["apscheduler.schedulers"] = schedulers
        sys.modules["apscheduler.schedulers.background"] = background
        sys.modules["apscheduler.triggers"] = triggers
        sys.modules["apscheduler.triggers.cron"] = cron

        import workers.scheduler as scheduler

        content = Path("workers/scheduler.py").read_text(encoding="utf-8")

        print(json.dumps({
            "start_scheduler_callable": callable(scheduler.start_scheduler),
            "shutdown_scheduler_callable": callable(scheduler.shutdown_scheduler),
            "daily_watchlist_scan_callable": callable(scheduler.daily_watchlist_scan),
            "weekly_watchlist_scan_callable": callable(scheduler.weekly_watchlist_scan),
            "weekly_universal_scan_callable": callable(scheduler.weekly_universal_scan),
            "watchlist_scan_day": scheduler.WATCHLIST_SCAN_DAY,
            "watchlist_scan_hour": scheduler.WATCHLIST_SCAN_HOUR,
            "universal_scan_day": scheduler.UNIVERSAL_SCAN_DAY,
            "universal_scan_hour": scheduler.UNIVERSAL_SCAN_HOUR,
            "scan_timezone": scheduler.SCAN_TIMEZONE_NAME,
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"start_scheduler_callable": true, "shutdown_scheduler_callable": true, "daily_watchlist_scan_callable": true, "weekly_watchlist_scan_callable": true, "weekly_universal_scan_callable": true, "watchlist_scan_day": "mon", "watchlist_scan_hour": 0, "universal_scan_day": "tue", "universal_scan_hour": 0, "scan_timezone": "Europe/London", "has_path_hack": false}'


def test_monitoring_worker_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        config_package = types.ModuleType("config")
        config_settings = types.ModuleType("config.settings")
        config_settings.settings = MagicMock()

        database_package = types.ModuleType("database")
        database_crud = types.ModuleType("database.crud")
        database_crud.Database = MagicMock()
        database_crud.get_db_connection = MagicMock()
        database_crud.WatchlistCRUD = MagicMock()
        database_crud.AlertCRUD = MagicMock()

        watchlist_package = types.ModuleType("watchlist")
        watchlist_scanner = types.ModuleType("watchlist.scanner")
        watchlist_scanner.get_scanner = MagicMock(return_value=MagicMock())

        notifications_package = types.ModuleType("notifications")
        notifications_service = types.ModuleType("notifications.service")
        notifications_service.NotificationWorker = MagicMock()

        sys.modules["config"] = config_package
        sys.modules["config.settings"] = config_settings
        sys.modules["database"] = database_package
        sys.modules["database.crud"] = database_crud
        sys.modules["watchlist"] = watchlist_package
        sys.modules["watchlist.scanner"] = watchlist_scanner
        sys.modules["notifications"] = notifications_package
        sys.modules["notifications.service"] = notifications_service
        sys.modules.setdefault("schedule", MagicMock())

        import workers.monitoring_worker as worker

        content = Path("workers/monitoring_worker.py").read_text(encoding="utf-8")

        print(json.dumps({
            "monitoring_worker_class": hasattr(worker, "MonitoringWorker"),
            "single_scan_worker_class": hasattr(worker, "SingleScanWorker"),
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"monitoring_worker_class": true, "single_scan_worker_class": true, "has_path_hack": false}'


def test_credit_reset_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        psycopg2 = types.ModuleType("psycopg2")
        psycopg2.connect = MagicMock()

        psycopg2_extras = types.ModuleType("psycopg2.extras")
        psycopg2_extras.RealDictCursor = object()

        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = MagicMock()

        sys.modules["psycopg2"] = psycopg2
        sys.modules["psycopg2.extras"] = psycopg2_extras
        sys.modules["dotenv"] = dotenv

        import workers.credit_reset as worker

        content = Path("workers/credit_reset.py").read_text(encoding="utf-8")

        print(json.dumps({
            "main_callable": callable(worker.main),
            "reset_monthly_credits_callable": callable(worker.reset_monthly_credits),
            "run_daemon_callable": callable(worker.run_daemon),
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"main_callable": true, "reset_monthly_credits_callable": true, "run_daemon_callable": true, "has_path_hack": false}'


def test_universal_scanner_imports_as_package_entrypoint():
    output = _run_python(
        """
        import json
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        psycopg2 = types.ModuleType("psycopg2")
        psycopg2.connect = MagicMock()

        psycopg2_extras = types.ModuleType("psycopg2.extras")
        psycopg2_extras.RealDictCursor = object()
        psycopg2_extras.execute_values = MagicMock()

        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = MagicMock()

        risk_engine = types.ModuleType("risk_engine")
        risk_engine.get_risk_level = MagicMock(return_value="medium")
        risk_engine.RISK_THRESHOLDS = {"medium": 0.5}

        utils_package = types.ModuleType("utils")
        utils_deadline = types.ModuleType("utils.deadline")
        utils_deadline.calculate_appeal_deadline = MagicMock(return_value=None)

        sys.modules["psycopg2"] = psycopg2
        sys.modules["psycopg2.extras"] = psycopg2_extras
        sys.modules["dotenv"] = dotenv
        sys.modules["risk_engine"] = risk_engine
        sys.modules["utils"] = utils_package
        sys.modules["utils.deadline"] = utils_deadline

        import workers.universal_scanner as worker

        content = Path("workers/universal_scanner.py").read_text(encoding="utf-8")

        print(json.dumps({
            "universal_scanner_class": hasattr(worker, "UniversalScanner"),
            "main_callable": callable(worker.main),
            "queue_poll_interval": worker.QUEUE_POLL_INTERVAL,
            "has_path_hack": "sys.path.insert" in content,
        }))
        """
    )

    assert output == '{"universal_scanner_class": true, "main_callable": true, "queue_poll_interval": 30, "has_path_hack": false}'


def test_universal_scanner_conflict_upsert_refreshes_snapshot_fields():
    content = Path("workers/universal_scanner.py").read_text(encoding="utf-8")

    for field in [
        "new_mark_name",
        "new_mark_app_no",
        "new_mark_holder_name",
        "new_mark_nice_classes",
        "existing_mark_name",
        "existing_mark_app_no",
        "existing_mark_holder_id",
        "existing_mark_holder_name",
        "existing_mark_nice_classes",
        "conflict_type",
        "overlapping_classes",
        "conflict_reasons",
        "bulletin_no",
        "bulletin_date",
        "opposition_deadline",
    ]:
        assert f"{field} = EXCLUDED.{field}" in content


def test_settings_normalize_bulletin_roots_from_project_root():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["DATA_ROOT"] = "bulletins/Marka"
        os.environ["PIPELINE_BULLETINS_ROOT"] = "bulletins/Marka"

        from config.settings import PathSettings, PipelineSettings

        expected = str((Path("config/settings.py").resolve().parent.parent / "bulletins" / "Marka").resolve())
        paths = PathSettings()
        pipeline = PipelineSettings()

        print(json.dumps({
            "data_root_matches": paths.data_root == expected,
            "bulletins_root_matches": pipeline.bulletins_root == expected,
            "roots_match_each_other": paths.data_root == pipeline.bulletins_root,
        }))
        """
    )

    assert output == '{"data_root_matches": true, "bulletins_root_matches": true, "roots_match_each_other": true}'


def test_settings_normalize_runtime_dirs_from_project_root():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["UPLOAD_DIR"] = "uploads"
        os.environ["REPORT_DIR"] = "reports"
        os.environ["LOG_DIR"] = "logs"
        os.environ["CREATIVE_LOGO_OUTPUT_DIR"] = "uploads/generated/logos"

        from config.settings import CreativeSettings, PathSettings

        project_root = Path("config/settings.py").resolve().parent.parent
        paths = PathSettings()
        creative = CreativeSettings()

        print(json.dumps({
            "upload_dir_matches": paths.upload_dir == str((project_root / "uploads").resolve()),
            "report_dir_matches": paths.report_dir == str((project_root / "reports").resolve()),
            "log_dir_matches": paths.log_dir == str((project_root / "logs").resolve()),
            "logo_output_dir_matches": creative.logo_output_dir == str((project_root / "uploads" / "generated" / "logos").resolve()),
        }))
        """
    )

    assert output == '{"upload_dir_matches": true, "report_dir_matches": true, "log_dir_matches": true, "logo_output_dir_matches": true}'


def test_settings_default_report_dir_uses_uploads_boundary():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ.pop("UPLOAD_DIR", None)
        os.environ.pop("REPORT_DIR", None)
        os.environ.pop("LOG_DIR", None)
        os.environ.pop("CREATIVE_LOGO_OUTPUT_DIR", None)

        from config.settings import PathSettings

        project_root = Path("config/settings.py").resolve().parent.parent
        paths = PathSettings()

        print(json.dumps({
            "report_dir_matches": paths.report_dir == str((project_root / "uploads" / "reports").resolve()),
        }))
        """
    )

    assert output == '{"report_dir_matches": true}'


def test_creative_settings_openai_image_defaults_and_standard_key_fallback():
    output = _run_python(
        """
        import json
        import os

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ.pop("CREATIVE_OPENAI_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "standard-openai-key"
        os.environ.pop("CREATIVE_OPENAI_IMAGE_MODEL", None)
        os.environ.pop("CREATIVE_GEMINI_IMAGE_MODEL", None)

        from config.settings import CreativeSettings

        # _env_file=None bypasses the project .env so the fallback path
        # (CREATIVE_OPENAI_API_KEY unset → OPENAI_API_KEY used) is actually exercised.
        creative = CreativeSettings(_env_file=None)

        print(json.dumps({
            "openai_key_matches": creative.openai_api_key == "standard-openai-key",
            "openai_model_matches": creative.openai_image_model == "gpt-image-2",
            "gemini_backup_matches": creative.gemini_image_model == "gemini-3-pro-image-preview",
        }))
        """
    )

    assert output == '{"openai_key_matches": true, "openai_model_matches": true, "gemini_backup_matches": true}'


def test_settings_preserve_absolute_runtime_dir_overrides():
    output = _run_python(
        """
        import json
        import os
        import shutil
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"

        temp_dir = (Path(".phase0_runtime_abs_test") / "absolute_override").resolve()

        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)

            upload_dir = str((Path(temp_dir) / "uploads").resolve())
            report_dir = str((Path(temp_dir) / "reports").resolve())
            log_dir = str((Path(temp_dir) / "logs").resolve())
            logo_dir = str((Path(temp_dir) / "logos").resolve())

            os.environ["UPLOAD_DIR"] = upload_dir
            os.environ["REPORT_DIR"] = report_dir
            os.environ["LOG_DIR"] = log_dir
            os.environ["CREATIVE_LOGO_OUTPUT_DIR"] = logo_dir

            from config.settings import CreativeSettings, PathSettings

            paths = PathSettings()
            creative = CreativeSettings()

            print(json.dumps({
                "upload_dir_matches": paths.upload_dir == upload_dir,
                "report_dir_matches": paths.report_dir == report_dir,
                "log_dir_matches": paths.log_dir == log_dir,
                "logo_output_dir_matches": creative.logo_output_dir == logo_dir,
            }))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        """
    )

    assert output == '{"upload_dir_matches": true, "report_dir_matches": true, "log_dir_matches": true, "logo_output_dir_matches": true}'


def test_settings_normalize_pipeline_seven_zip_path():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["PIPELINE_SEVEN_ZIP_PATH"] = "tools/7zip/7z.exe"

        from config.settings import DEFAULT_SEVEN_ZIP_PATH, DEFAULT_WINDOWS_7Z_PATH, PipelineSettings

        project_root = Path("config/settings.py").resolve().parent.parent
        pipeline = PipelineSettings()
        settings_source = Path("config/settings.py").read_text(encoding="utf-8")
        zip_source = Path("zip.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Program Files\\7-Zip\\7z.exe"

        print(json.dumps({
            "relative_override_resolves_from_project_root": pipeline.seven_zip_path == str((project_root / "tools" / "7zip" / "7z.exe").resolve()),
            "default_is_nonempty": bool(DEFAULT_SEVEN_ZIP_PATH),
            "windows_fallback_uses_install_suffix": DEFAULT_WINDOWS_7Z_PATH.as_posix().endswith("/7-Zip/7z.exe"),
            "hardcoded_literal_absent": hardcoded_literal not in settings_source and hardcoded_literal not in zip_source,
        }))
        """
    )

    assert output == '{"relative_override_resolves_from_project_root": true, "default_is_nonempty": true, "windows_fallback_uses_install_suffix": true, "hardcoded_literal_absent": true}'


def test_zip_find_7z_accepts_path_lookup_command_names():
    output = _run_python(
        """
        import json
        from pathlib import Path
        from unittest.mock import patch

        import zip

        with patch("zip.shutil.which", side_effect=lambda name: "C:/Tooling/7-Zip/7z.exe" if name == "7z" else None):
            resolved = zip.find_7z("7z")

        print(json.dumps({
            "path_lookup_supported": Path(resolved).as_posix() == "C:/Tooling/7-Zip/7z.exe",
        }))
        """
    )

    assert output == '{"path_lookup_supported": true}'


def test_zip_fallback_root_uses_local_project_boundary_when_settings_fail():
    output = _run_python(
        """
        import builtins
        import importlib
        import json
        import sys
        from pathlib import Path

        project_root = Path("zip.py").resolve().parent
        expected = str((project_root / "bulletins" / "Marka").resolve())

        sys.modules.pop("zip", None)
        sys.modules.pop("config", None)
        sys.modules.pop("config.settings", None)

        real_import = builtins.__import__

        def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "config.settings":
                raise RuntimeError("blocked-settings-import")
            return real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = blocked_import
        try:
            zip_module = importlib.import_module("zip")
        finally:
            builtins.__import__ = real_import

        zip_source = Path("zip.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "fallback_root_matches_local_project": str(Path(zip_module._DEFAULT_ROOT).resolve()) == expected,
            "hardcoded_literal_absent": hardcoded_literal not in zip_source,
        }))
        """
    )

    assert output == '{"fallback_root_matches_local_project": true, "hardcoded_literal_absent": true}'


def test_data_collection_fallback_root_uses_local_project_boundary_when_settings_fail():
    output = _run_python(
        """
        import builtins
        import importlib
        import json
        import os
        import sys
        import types
        from pathlib import Path

        project_root = Path("data_collection.py").resolve().parent
        default_expected = str((project_root / "bulletins").resolve())
        pipeline_expected = str((project_root / "custom_bulletins").resolve())
        data_expected = str((project_root / "archive_bulletins").resolve())

        playwright_pkg = types.ModuleType("playwright")
        playwright_async = types.ModuleType("playwright.async_api")
        playwright_async.async_playwright = object()
        sys.modules["playwright"] = playwright_pkg
        sys.modules["playwright.async_api"] = playwright_async

        real_import = builtins.__import__

        def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "config.settings":
                raise RuntimeError("blocked-settings-import")
            return real_import(name, globals, locals, fromlist, level)

        def load_module():
            sys.modules.pop("data_collection", None)
            sys.modules.pop("config", None)
            sys.modules.pop("config.settings", None)
            builtins.__import__ = blocked_import
            try:
                return importlib.import_module("data_collection")
            finally:
                builtins.__import__ = real_import

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module()

        source = Path("data_collection.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins"

        print(json.dumps({
            "default_root_matches_local_project": default_module.BASE_DOWNLOAD_DIR == default_expected,
            "pipeline_env_root_matches": pipeline_module.BASE_DOWNLOAD_DIR == pipeline_expected,
            "data_env_root_matches": data_module.BASE_DOWNLOAD_DIR == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_metadata_fallback_root_uses_local_project_boundary_when_settings_fail():
    output = _run_python(
        """
        import builtins
        import importlib
        import json
        import os
        import sys
        from pathlib import Path

        project_root = Path("metadata.py").resolve().parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        real_import = builtins.__import__

        def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "config.settings":
                raise RuntimeError("blocked-settings-import")
            return real_import(name, globals, locals, fromlist, level)

        def load_module():
            sys.modules.pop("metadata", None)
            sys.modules.pop("config", None)
            sys.modules.pop("config.settings", None)
            builtins.__import__ = blocked_import
            try:
                return importlib.import_module("metadata")
            finally:
                builtins.__import__ = real_import

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module()

        source = Path("metadata.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(Path(default_module.ROOT).resolve()) == default_expected,
            "pipeline_env_root_matches": str(Path(pipeline_module.ROOT).resolve()) == pipeline_expected,
            "data_env_root_matches": str(Path(data_module.ROOT).resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_scrapper_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib
        import json
        import os
        import shutil
        import sys
        import types
        from pathlib import Path

        project_root = Path("scrapper.py").resolve().parent
        temp_root = project_root / ".phase0_tmp_scrapper"
        pipeline_relative = Path(".phase0_tmp_scrapper") / "custom_bulletins" / "Marka"
        data_relative = Path(".phase0_tmp_scrapper") / "archive_bulletins" / "Marka"
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / pipeline_relative).resolve())
        data_expected = str((project_root / data_relative).resolve())

        dotenv_module = types.ModuleType("dotenv")
        dotenv_module.load_dotenv = lambda *args, **kwargs: None
        sys.modules["dotenv"] = dotenv_module

        tenacity_module = types.ModuleType("tenacity")
        tenacity_module.retry = lambda *args, **kwargs: (lambda func: func)
        tenacity_module.retry_if_not_exception_type = lambda *args, **kwargs: None
        tenacity_module.stop_after_attempt = lambda *args, **kwargs: None
        tenacity_module.wait_exponential = lambda *args, **kwargs: None
        sys.modules["tenacity"] = tenacity_module

        playwright_module = types.ModuleType("playwright")
        sync_api_module = types.ModuleType("playwright.sync_api")

        def sync_playwright():
            return None

        class FakeTimeoutError(Exception):
            pass

        sync_api_module.sync_playwright = sync_playwright
        sync_api_module.TimeoutError = FakeTimeoutError
        playwright_module.sync_api = sync_api_module
        sys.modules["playwright"] = playwright_module
        sys.modules["playwright.sync_api"] = sync_api_module

        def load_module():
            sys.modules.pop("scrapper", None)
            return importlib.import_module("scrapper")

        try:
            os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
            os.environ.pop("DATA_ROOT", None)
            default_module = load_module()

            os.environ["PIPELINE_BULLETINS_ROOT"] = pipeline_relative.as_posix()
            os.environ.pop("DATA_ROOT", None)
            pipeline_module = load_module()

            os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
            os.environ["DATA_ROOT"] = data_relative.as_posix()
            data_module = load_module()

            source = Path("scrapper.py").read_text(encoding="utf-8")
            hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

            print(json.dumps({
                "default_root_matches_local_project": str(default_module.ROOT_DIR.resolve()) == default_expected,
                "pipeline_env_root_matches": str(pipeline_module.ROOT_DIR.resolve()) == pipeline_expected,
                "data_env_root_matches": str(data_module.ROOT_DIR.resolve()) == data_expected,
                "hardcoded_literal_absent": hardcoded_literal not in source,
            }))
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_agentic_search_paths_use_local_project_boundary_and_env_overrides():
    default_output = _run_python(
        """
        import json
        import os
        import sys
        import types
        from pathlib import Path

        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ.pop("PROJECT_ROOT", None)
        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)

        redis_module = types.ModuleType("redis")

        class FakeRedis:
            def __init__(self, *args, **kwargs):
                pass

            def ping(self):
                return True

        redis_module.Redis = FakeRedis
        sys.modules["redis"] = redis_module

        project_root = Path("agentic_search.py").resolve().parent
        source = Path("agentic_search.py").read_text(encoding="utf-8")
        hardcoded_project_literal = r"C:\\Users\\701693\\turk_patent"
        hardcoded_bulletins_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        import agentic_search

        print(json.dumps({
            "project_root_matches_local_project": str(agentic_search.PROJECT_ROOT.resolve()) == str(project_root.resolve()),
            "data_root_matches_local_project": str(agentic_search.DATA_ROOT.resolve()) == str((project_root / "bulletins" / "Marka").resolve()),
            "hardcoded_project_literal_absent": hardcoded_project_literal not in source,
            "hardcoded_bulletins_literal_absent": hardcoded_bulletins_literal not in source,
        }))
        """
    )

    pipeline_output = _run_python(
        """
        import json
        import os
        import sys
        import types
        from pathlib import Path

        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ.pop("PROJECT_ROOT", None)
        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)

        redis_module = types.ModuleType("redis")

        class FakeRedis:
            def __init__(self, *args, **kwargs):
                pass

            def ping(self):
                return True

        redis_module.Redis = FakeRedis
        sys.modules["redis"] = redis_module

        project_root = Path("agentic_search.py").resolve().parent

        import agentic_search

        print(json.dumps({
            "pipeline_env_root_matches": str(agentic_search.DATA_ROOT.resolve()) == str((project_root / "custom_bulletins" / "Marka").resolve()),
        }))
        """
    )

    data_output = _run_python(
        """
        import json
        import os
        import sys
        import types
        from pathlib import Path

        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ.pop("PROJECT_ROOT", None)
        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"

        redis_module = types.ModuleType("redis")

        class FakeRedis:
            def __init__(self, *args, **kwargs):
                pass

            def ping(self):
                return True

        redis_module.Redis = FakeRedis
        sys.modules["redis"] = redis_module

        project_root = Path("agentic_search.py").resolve().parent

        import agentic_search

        print(json.dumps({
            "data_env_root_matches": str(agentic_search.DATA_ROOT.resolve()) == str((project_root / "archive_bulletins" / "Marka").resolve()),
        }))
        """
    )

    project_output = _run_python(
        """
        import json
        import os
        import sys
        import types
        from pathlib import Path

        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["PROJECT_ROOT"] = ".phase0_tmp_agentic_project"
        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)

        redis_module = types.ModuleType("redis")

        class FakeRedis:
            def __init__(self, *args, **kwargs):
                pass

            def ping(self):
                return True

        redis_module.Redis = FakeRedis
        sys.modules["redis"] = redis_module

        project_root = Path("agentic_search.py").resolve().parent

        import agentic_search

        print(json.dumps({
            "project_env_root_matches": str(agentic_search.PROJECT_ROOT.resolve()) == str((project_root / ".phase0_tmp_agentic_project").resolve()),
        }))
        """
    )

    assert default_output == '{"project_root_matches_local_project": true, "data_root_matches_local_project": true, "hardcoded_project_literal_absent": true, "hardcoded_bulletins_literal_absent": true}'
    assert pipeline_output == '{"pipeline_env_root_matches": true}'
    assert data_output == '{"data_env_root_matches": true}'
    assert project_output == '{"project_env_root_matches": true}'


def test_risk_engine_data_root_uses_local_project_boundary_and_env_overrides():
    default_output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock
        from pathlib import Path

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        project_root = Path("risk_engine.py").resolve().parent
        source = Path("risk_engine.py").read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        import risk_engine

        print(json.dumps({
            "default_root_matches_local_project": str(risk_engine.DATA_ROOT.resolve()) == str((project_root / "bulletins" / "Marka").resolve()),
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    pipeline_output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock
        from pathlib import Path

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        project_root = Path("risk_engine.py").resolve().parent

        import risk_engine

        print(json.dumps({
            "pipeline_env_root_matches": str(risk_engine.DATA_ROOT.resolve()) == str((project_root / "custom_bulletins" / "Marka").resolve()),
        }))
        """
    )

    data_output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock
        from pathlib import Path

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("sentence_transformers")
        ensure_mock("open_clip")
        ensure_mock("easyocr")
        ensure_mock("transformers")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        project_root = Path("risk_engine.py").resolve().parent

        import risk_engine

        print(json.dumps({
            "data_env_root_matches": str(risk_engine.DATA_ROOT.resolve()) == str((project_root / "archive_bulletins" / "Marka").resolve()),
        }))
        """
    )

    assert default_output == '{"default_root_matches_local_project": true, "hardcoded_literal_absent": true}'
    assert pipeline_output == '{"pipeline_env_root_matches": true}'
    assert data_output == '{"data_env_root_matches": true}'


def test_application_service_uses_settings_backed_upload_root():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["UPLOAD_DIR"] = "uploads"

        from services.application_service import APPLICATION_UPLOAD_DIR

        project_root = Path("config/settings.py").resolve().parent.parent
        expected = str((project_root / "uploads" / "applications").resolve())
        service_source = Path("services/application_service.py").read_text(encoding="utf-8")
        route_source = Path("api/applications.py").read_text(encoding="utf-8")

        print(json.dumps({
            "upload_root_matches": str(APPLICATION_UPLOAD_DIR.resolve()) == expected,
            "service_hardcoded_path_absent": "static/uploads/applications" not in service_source,
            "route_hardcoded_path_absent": "static/uploads/applications" not in route_source,
        }))
        """
    )

    assert output == '{"upload_root_matches": true, "service_hardcoded_path_absent": true, "route_hardcoded_path_absent": true}'


def test_watchlist_service_uses_settings_backed_logo_root():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["UPLOAD_DIR"] = "uploads"

        from services.watchlist_service import WATCHLIST_LOGOS_DIR

        project_root = Path("config/settings.py").resolve().parent.parent
        expected = str((project_root / "uploads" / "watchlist_logos").resolve())
        service_source = Path("services/watchlist_service.py").read_text(encoding="utf-8")
        route_source = Path("api/watchlist_routes.py").read_text(encoding="utf-8")

        print(json.dumps({
            "logo_root_matches": str(Path(WATCHLIST_LOGOS_DIR).resolve()) == expected,
            "service_constant_uses_settings": 'WATCHLIST_LOGOS_DIR = os.path.join(settings.paths.upload_dir, "watchlist_logos")' in service_source,
            "service_default_uses_constant": "logos_dir = WATCHLIST_LOGOS_DIR" in service_source,
            "service_uses_project_root_constant": 'from config.settings import PROJECT_ROOT, settings' in service_source and "project_root = PROJECT_ROOT" in service_source,
            "route_hardcoded_path_absent": "uploads\\n    \\\"watchlist_logos\\\"" not in route_source and "WATCHLIST_LOGOS_DIR =" not in route_source,
            "route_no_longer_passes_logo_dir": "logos_dir=" not in route_source,
            "route_dead_fallback_removed": 'return FR(payload["path"], media_type=payload["media_type"])\\n\\n    with Database() as db:' not in route_source and "project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))" not in route_source,
        }))
        """
    )

    assert output == '{"logo_root_matches": true, "service_constant_uses_settings": true, "service_default_uses_constant": true, "service_uses_project_root_constant": true, "route_hardcoded_path_absent": true, "route_no_longer_passes_logo_dir": true, "route_dead_fallback_removed": true}'


def test_user_profile_service_uses_settings_backed_avatar_root():
    output = _run_python(
        """
        import json
        import os
        from pathlib import Path

        os.environ["DB_PASSWORD"] = "test-db-password"
        os.environ["AUTH_SECRET_KEY"] = "test-auth-secret-key-with-at-least-32-characters"
        os.environ["UPLOAD_DIR"] = "uploads"

        from app_assets import AVATAR_STATIC_PATH, AVATAR_UPLOAD_DIR as ASSET_AVATAR_UPLOAD_DIR
        from services.user_profile_service import AVATAR_UPLOAD_DIR as SERVICE_AVATAR_UPLOAD_DIR

        project_root = Path("config/settings.py").resolve().parent.parent
        expected = str((project_root / "uploads" / "avatars").resolve())
        service_source = Path("services/user_profile_service.py").read_text(encoding="utf-8")
        assets_source = Path("app_assets.py").read_text(encoding="utf-8")

        print(json.dumps({
            "service_root_matches": str(SERVICE_AVATAR_UPLOAD_DIR.resolve()) == expected,
            "asset_root_matches": str(ASSET_AVATAR_UPLOAD_DIR.resolve()) == expected,
            "service_constant_uses_settings": 'AVATAR_UPLOAD_DIR = Path(settings.paths.upload_dir) / "avatars"' in service_source,
            "avatar_url_stays_static": 'avatar_url = f"/static/avatars/{filename}"' in service_source,
            "asset_mount_path_matches": AVATAR_STATIC_PATH == "/static/avatars",
            "asset_mount_uses_settings": 'AVATAR_UPLOAD_DIR = Path(settings.paths.upload_dir) / "avatars"' in assets_source,
            "asset_mount_registered": 'app.mount(AVATAR_STATIC_PATH, StaticFiles(directory=str(AVATAR_UPLOAD_DIR)), name="static-avatars")' in assets_source,
        }))
        """
    )

    assert output == '{"service_root_matches": true, "asset_root_matches": true, "service_constant_uses_settings": true, "avatar_url_stays_static": true, "asset_mount_path_matches": true, "asset_mount_uses_settings": true, "asset_mount_registered": true}'


def test_api_routes_reexports_watchlist_router():
    output = _run_python(
        """
        import json
        from api.routes import watchlist_router

        print(json.dumps({
            "has_stats": any(route.path == "/watchlist/stats" for route in watchlist_router.routes),
            "has_list": any(route.path == "/watchlist" for route in watchlist_router.routes),
            "has_upload": any(route.path == "/watchlist/upload" for route in watchlist_router.routes),
            "has_template": any(route.path == "/watchlist/upload/template" for route in watchlist_router.routes),
            "has_logo": any(route.path == "/watchlist/{item_id}/logo" for route in watchlist_router.routes),
            "has_scan_all": any(route.path == "/watchlist/scan-all" for route in watchlist_router.routes),
            "has_scan_status": any(route.path == "/watchlist/scan-status" for route in watchlist_router.routes),
            "has_delete_all": any(route.path == "/watchlist/all" for route in watchlist_router.routes),
            "has_rescan": any(route.path == "/watchlist/rescan" for route in watchlist_router.routes),
            "has_bulk_threshold": any(route.path == "/watchlist/bulk-threshold" for route in watchlist_router.routes),
        }))
        """
    )

    assert output == '{"has_stats": true, "has_list": true, "has_upload": true, "has_template": true, "has_logo": true, "has_scan_all": true, "has_scan_status": true, "has_delete_all": true, "has_rescan": true, "has_bulk_threshold": true}'


def test_database_crud_reexports_application_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import ApplicationCRUD as FacadeApplicationCRUD
        from database.repositories.application_repository import ApplicationCRUD as RepoApplicationCRUD

        print(json.dumps({
            "same_object": FacadeApplicationCRUD is RepoApplicationCRUD,
            "has_get_by_id": callable(getattr(FacadeApplicationCRUD, "get_by_id", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_get_by_id": true}'


def test_database_crud_reexports_alert_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import AlertCRUD as FacadeAlertCRUD
        from database.repositories.alert_repository import AlertCRUD as RepoAlertCRUD

        print(json.dumps({
            "same_object": FacadeAlertCRUD is RepoAlertCRUD,
            "has_update_status": callable(getattr(FacadeAlertCRUD, "update_status", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_update_status": true}'


def test_database_crud_reexports_watchlist_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import WatchlistCRUD as FacadeWatchlistCRUD
        from database.repositories.watchlist_repository import WatchlistCRUD as RepoWatchlistCRUD

        print(json.dumps({
            "same_object": FacadeWatchlistCRUD is RepoWatchlistCRUD,
            "has_get_by_id": callable(getattr(FacadeWatchlistCRUD, "get_by_id", None)),
            "has_get_by_org": callable(getattr(FacadeWatchlistCRUD, "get_by_organization", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_get_by_id": true, "has_get_by_org": true}'


def test_risk_engine_reexports_scoring_service():
    output = _run_python(
        """
        import json
        import os
        import sys
        from unittest.mock import MagicMock

        os.environ.setdefault("AI_SKIP_MODEL_LOAD", "1")

        def ensure_mock(name, mock_obj=None):
            sys.modules.setdefault(name, mock_obj or MagicMock())

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
        ensure_mock("torch", mock_torch)
        ensure_mock("torchvision")
        ensure_mock("torchvision.transforms")
        ensure_mock("cv2")
        ensure_mock("PIL")
        ensure_mock("PIL.Image")
        ensure_mock("scrapper")
        ensure_mock("pipeline.ingest")

        mock_ai = MagicMock()
        mock_ai.device = "cpu"
        ensure_mock("pipeline.ai", mock_ai)

        mock_logging = MagicMock()
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.setup_logging = MagicMock()
        sys.modules.setdefault("logging_config", mock_logging)

        mock_pool = MagicMock()
        mock_pool.get_connection = MagicMock(return_value=MagicMock())
        mock_pool.release_connection = MagicMock()
        mock_pool.connection_context = MagicMock()
        mock_pool.close_pool = MagicMock()
        sys.modules.setdefault("db", MagicMock())
        sys.modules.setdefault("db.pool", mock_pool)

        from risk_engine import (
            score_pair as facade_score_pair,
            get_risk_level as facade_get_risk_level,
            calculate_visual_similarity as facade_visual_similarity,
            calculate_name_similarity as facade_name_similarity,
            calculate_multilevel_similarity as facade_multilevel_similarity,
            calculate_token_overlap as facade_token_overlap,
            check_substring_containment as facade_containment,
        )
        from services.scoring_service import (
            score_pair as service_score_pair,
            get_risk_level as service_get_risk_level,
            calculate_visual_similarity as service_visual_similarity,
            calculate_name_similarity as service_name_similarity,
            calculate_multilevel_similarity as service_multilevel_similarity,
            calculate_token_overlap as service_token_overlap,
            check_substring_containment as service_containment,
        )

        print(json.dumps({
            "score_pair_same": facade_score_pair is service_score_pair,
            "get_risk_level_same": facade_get_risk_level is service_get_risk_level,
            "visual_same": facade_visual_similarity is service_visual_similarity,
            "name_same": facade_name_similarity is service_name_similarity,
            "multilevel_same": facade_multilevel_similarity is service_multilevel_similarity,
            "token_overlap_same": facade_token_overlap is service_token_overlap,
            "containment_same": facade_containment is service_containment,
        }))
        """
    )

    assert output == '{"score_pair_same": true, "get_risk_level_same": true, "visual_same": true, "name_same": true, "multilevel_same": true, "token_overlap_same": true, "containment_same": true}'


def test_scoring_service_exports_expected_waterfall_surface():
    output = _run_python(
        """
        import json

        from services.scoring_service import (
            HierarchicalTextScorer,
            compute_idf_weighted_score,
            compute_idf_weighted_score_tr,
            normalize_turkish,
            tokenize,
        )

        print(json.dumps({
            "has_compute": callable(compute_idf_weighted_score),
            "has_compute_tr": callable(compute_idf_weighted_score_tr),
            "has_tokenize": callable(tokenize),
            "has_normalize": callable(normalize_turkish),
            "has_scorer": HierarchicalTextScorer.__name__ == "HierarchicalTextScorer",
        }))
        """
    )

    assert output == '{"has_compute": true, "has_compute_tr": true, "has_tokenize": true, "has_normalize": true, "has_scorer": true}'


def test_utils_idf_scoring_delegates_deprecated_helpers_to_scoring_service():
    output = _run_python(
        """
        import json
        from unittest.mock import patch

        from utils import idf_scoring as facade

        expected_adjusted = {"adjusted_score": 0.42}
        expected_combined = {"overall_score": 0.77}
        expected_comprehensive = {"final_score": 0.81}
        expected_alert = {"overall_score": 0.68}
        expected_risk = {"overall_score": 0.59}

        with patch("services.scoring_service.calculate_adjusted_score", return_value=expected_adjusted) as mock_adjusted, \\
             patch("services.scoring_service.calculate_text_similarity", return_value=0.33) as mock_text, \\
             patch("services.scoring_service.calculate_risk_score", return_value=expected_risk) as mock_risk, \\
             patch("services.scoring_service.calculate_combined_score", return_value=expected_combined) as mock_combined, \\
             patch("services.scoring_service.calculate_comprehensive_score", return_value=expected_comprehensive) as mock_comprehensive, \\
             patch("services.scoring_service.calculate_alert_risk_score", return_value=expected_alert) as mock_alert:
            print(json.dumps({
                "adjusted": facade.calculate_adjusted_score(0.1, "a", "b") == expected_adjusted and mock_adjusted.called,
                "text": facade.calculate_text_similarity("a", "b") == 0.33 and mock_text.called,
                "risk": facade.calculate_risk_score(0.1, 0.2, 0.3, "a", "b") == expected_risk and mock_risk.called,
                "combined": facade.calculate_combined_score(0.1, 0.2, "combined") == expected_combined and mock_combined.called,
                "comprehensive": facade.calculate_comprehensive_score("a", "b") == expected_comprehensive and mock_comprehensive.called,
                "alert": facade.calculate_alert_risk_score("a", "b", 0.1, 0.2, 0.3) == expected_alert and mock_alert.called,
            }))
        """
    )

    assert output == '{"adjusted": true, "text": true, "risk": true, "combined": true, "comprehensive": true, "alert": true}'


def test_utils_idf_scoring_delegates_visual_helpers_to_scoring_service():
    output = _run_python(
        """
        import json
        from unittest.mock import patch

        from utils import idf_scoring as facade

        expected_combined = {"combined_score": 0.55}
        expected_image_score = {"final_score": 0.66}

        with patch("services.scoring_service.adjust_image_similarity", return_value=0.91) as mock_adjust, \\
             patch("services.scoring_service.extract_ocr_text", return_value="brand") as mock_extract, \\
             patch("services.scoring_service.calculate_ocr_similarity", return_value=0.44) as mock_ocr, \\
             patch("services.scoring_service.combine_visual_scores", return_value=expected_combined) as mock_combine, \\
             patch("services.scoring_service.calculate_image_score_with_ocr", return_value=expected_image_score) as mock_image:
            print(json.dumps({
                "adjust": facade.adjust_image_similarity(0.8) == 0.91 and mock_adjust.called,
                "extract": facade.extract_ocr_text("logo.png") == "brand" and mock_extract.called,
                "ocr": facade.calculate_ocr_similarity("brand", "mark") == 0.44 and mock_ocr.called,
                "combine": facade.combine_visual_scores(0.1, 0.2, 0.3, "a", "b") == expected_combined and mock_combine.called,
                "image": facade.calculate_image_score_with_ocr(0.7, "a", "b") == expected_image_score and mock_image.called,
            }))
        """
    )

    assert output == '{"adjust": true, "extract": true, "ocr": true, "combine": true, "image": true}'


def test_database_crud_reexports_organization_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import OrganizationCRUD as FacadeOrganizationCRUD
        from database.repositories.organization_repository import OrganizationCRUD as RepoOrganizationCRUD

        print(json.dumps({
            "same_object": FacadeOrganizationCRUD is RepoOrganizationCRUD,
            "has_check_limits": callable(getattr(FacadeOrganizationCRUD, "check_limits", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_check_limits": true}'


def test_database_crud_reexports_user_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import UserCRUD as FacadeUserCRUD
        from database.repositories.user_repository import UserCRUD as RepoUserCRUD

        print(json.dumps({
            "same_object": FacadeUserCRUD is RepoUserCRUD,
            "has_get_by_email": callable(getattr(FacadeUserCRUD, "get_by_email", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_get_by_email": true}'


def test_database_crud_reexports_scan_log_repository():
    output = _run_python(
        """
        import json
        import os

        os.environ.setdefault("DB_PASSWORD", "test-db-password")
        os.environ.setdefault(
            "AUTH_SECRET_KEY",
            "test-auth-secret-key-with-at-least-32-characters",
        )

        from database.crud import ScanLogCRUD as FacadeScanLogCRUD
        from database.repositories.scan_log_repository import ScanLogCRUD as RepoScanLogCRUD

        print(json.dumps({
            "same_object": FacadeScanLogCRUD is RepoScanLogCRUD,
            "has_complete": callable(getattr(FacadeScanLogCRUD, "complete", None)),
        }))
        """
    )

    assert output == '{"same_object": true, "has_complete": true}'


def test_legacy_ai_test_fallback_root_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import builtins
        import importlib.util
        import json
        import os
        import sys
        import types
        from pathlib import Path
        from unittest.mock import MagicMock

        os.environ["AI_SKIP_MODEL_LOAD"] = "1"

        clip_model = MagicMock()
        clip_model.eval.return_value = clip_model
        clip_model.half.return_value = clip_model

        dinov2_model = MagicMock()
        dinov2_model.to.return_value = dinov2_model
        dinov2_model.eval.return_value = dinov2_model
        dinov2_model.half.return_value = dinov2_model

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.hub.load.return_value = dinov2_model
        sys.modules["torch"] = mock_torch

        mock_open_clip = MagicMock()
        mock_open_clip.create_model_and_transforms.return_value = (clip_model, None, MagicMock())
        sys.modules["open_clip"] = mock_open_clip
        sys.modules["numpy"] = MagicMock()
        sys.modules["cv2"] = MagicMock()

        mock_redis = MagicMock()
        mock_redis.Redis.return_value = MagicMock(ping=MagicMock())
        sys.modules["redis"] = mock_redis

        mock_easyocr = MagicMock()
        mock_easyocr.Reader.return_value = MagicMock()
        sys.modules["easyocr"] = mock_easyocr

        pil_module = types.ModuleType("PIL")
        pil_image = MagicMock()
        pil_imagefile = MagicMock(LOAD_TRUNCATED_IMAGES=False)
        pil_module.Image = pil_image
        pil_module.UnidentifiedImageError = type("UnidentifiedImageError", (Exception,), {})
        pil_module.ImageFile = pil_imagefile
        sys.modules["PIL"] = pil_module
        sys.modules["PIL.Image"] = pil_image
        sys.modules["PIL.ImageFile"] = pil_imagefile

        mock_transforms = MagicMock()
        mock_transforms.Compose.return_value = MagicMock()
        mock_transforms.Resize.return_value = MagicMock()
        mock_transforms.ToTensor.return_value = MagicMock()
        mock_transforms.Normalize.return_value = MagicMock()
        mock_transforms.InterpolationMode = types.SimpleNamespace(BICUBIC="bicubic")
        mock_transforms.functional = types.SimpleNamespace(pad=MagicMock(return_value=MagicMock()))
        torchvision_module = types.ModuleType("torchvision")
        torchvision_module.transforms = mock_transforms
        sys.modules["torchvision"] = torchvision_module
        sys.modules["torchvision.transforms"] = mock_transforms

        sentence_transformers_module = types.ModuleType("sentence_transformers")
        sentence_transformers_module.SentenceTransformer = MagicMock(return_value=MagicMock())
        sys.modules["sentence_transformers"] = sentence_transformers_module

        tqdm_module = types.ModuleType("tqdm")
        tqdm_module.tqdm = MagicMock(side_effect=lambda iterable=None, *args, **kwargs: iterable if iterable is not None else [])
        sys.modules["tqdm"] = tqdm_module

        mock_logging = types.ModuleType("logging_config")
        mock_logging.get_logger = MagicMock(return_value=MagicMock())
        mock_logging.log_timing = lambda name: (lambda f: f)
        mock_logging.log_batch_stats = MagicMock()
        mock_logging.setup_logging = MagicMock()
        sys.modules["logging_config"] = mock_logging

        translation_module = types.ModuleType("utils.translation")
        translation_module.get_translations = lambda text: {"original": text, "detected_lang": "unknown", "tr": None}
        translation_module.detect_language_fasttext = lambda text: ("en", "eng_Latn", 0.0)
        translation_module.initialize = lambda device=None: False
        translation_module.is_ready = lambda: False
        translation_module.translate = lambda text, src, tgt: None
        translation_module.translate_to_turkish = lambda text: text.lower() if text else ""
        translation_module.batch_translate_to_turkish = lambda texts: [(t.lower() if t else "", "en") for t in texts]
        utils_module = types.ModuleType("utils")
        utils_module.translation = translation_module
        sys.modules["utils"] = utils_module
        sys.modules["utils.translation"] = translation_module

        real_import = builtins.__import__

        def force_settings_import_error(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "config.settings":
                raise ImportError("forced settings import failure")
            if name == "config" and fromlist and "settings" in fromlist:
                raise ImportError("forced settings import failure")
            return real_import(name, globals, locals, fromlist, level)

        script_path = Path(".py/ai_test.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            builtins.__import__ = force_settings_import_error
            try:
                assert spec.loader is not None
                spec.loader.exec_module(module)
                return module
            finally:
                builtins.__import__ = real_import

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_ai_test_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_ai_test_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_ai_test_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_root_matches_local_project": str(default_module.ROOT.resolve()) == default_expected,
            "pipeline_env_root_matches": str(pipeline_module.ROOT.resolve()) == pipeline_expected,
            "data_env_root_matches": str(data_module.ROOT.resolve()) == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_root_matches_local_project": true, "pipeline_env_root_matches": true, "data_env_root_matches": true, "hardcoded_literal_absent": true}'


def test_find_duplicates_script_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import contextlib
        import importlib.util
        import io
        import json
        import os
        from pathlib import Path

        script_path = Path("scripts/find_duplicates.py").resolve()
        project_root = script_path.parent.parent
        default_expected = os.path.join(str((project_root / "bulletins" / "Marka").resolve()), "*", "metadata.json")
        pipeline_expected = os.path.join(str((project_root / "custom_bulletins" / "Marka").resolve()), "*", "metadata.json")
        data_expected = os.path.join(str((project_root / "archive_bulletins" / "Marka").resolve()), "*", "metadata.json")

        spec = importlib.util.spec_from_file_location("phase10_find_duplicates", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        captured_patterns = []
        module.glob.glob = lambda pattern: captured_patterns.append(pattern) or []

        def run_for_current_env():
            with contextlib.redirect_stdout(io.StringIO()):
                module.main()
            return captured_patterns.pop()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_pattern = run_for_current_env()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_pattern = run_for_current_env()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_pattern = run_for_current_env()

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_pattern_matches_local_project": default_pattern == default_expected,
            "pipeline_env_pattern_matches": pipeline_pattern == pipeline_expected,
            "data_env_pattern_matches": data_pattern == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_pattern_matches_local_project": true, "pipeline_env_pattern_matches": true, "data_env_pattern_matches": true, "hardcoded_literal_absent": true}'


def test_find_duplicates2_script_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import contextlib
        import importlib.util
        import io
        import json
        import os
        from pathlib import Path

        script_path = Path("scripts/find_duplicates2.py").resolve()
        project_root = script_path.parent.parent
        default_expected = os.path.join(str((project_root / "bulletins" / "Marka").resolve()), "*", "metadata.json")
        pipeline_expected = os.path.join(str((project_root / "custom_bulletins" / "Marka").resolve()), "*", "metadata.json")
        data_expected = os.path.join(str((project_root / "archive_bulletins" / "Marka").resolve()), "*", "metadata.json")

        spec = importlib.util.spec_from_file_location("phase10_find_duplicates2", script_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        captured_patterns = []
        module.glob.glob = lambda pattern: captured_patterns.append(pattern) or []

        def run_for_current_env():
            with contextlib.redirect_stdout(io.StringIO()):
                module.main()
            return captured_patterns.pop()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_pattern = run_for_current_env()

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_pattern = run_for_current_env()

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_pattern = run_for_current_env()

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_pattern_matches_local_project": default_pattern == default_expected,
            "pipeline_env_pattern_matches": pipeline_pattern == pipeline_expected,
            "data_env_pattern_matches": data_pattern == data_expected,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_pattern_matches_local_project": true, "pipeline_env_pattern_matches": true, "data_env_pattern_matches": true, "hardcoded_literal_absent": true}'


def test_run_sample_test_script_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path("scripts/run_sample_test.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka" / "BLT_485_2026-01-27").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka" / "BLT_485_2026-01-27").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka" / "BLT_485_2026-01-27").resolve())
        ai_expected = str((project_root / "pipeline" / "ai.py").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_run_sample_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_run_sample_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_run_sample_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka\\BLT_485_2026-01-27"

        print(json.dumps({
            "default_folder_matches_local_project": str(default_module.FOLDER.resolve()) == default_expected,
            "pipeline_env_folder_matches": str(pipeline_module.FOLDER.resolve()) == pipeline_expected,
            "data_env_folder_matches": str(data_module.FOLDER.resolve()) == data_expected,
            "ai_entrypoint_matches_project_root": str(default_module.AI_ENTRYPOINT.resolve()) == ai_expected,
            "has_main_guard": 'if __name__ == "__main__":' in source,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_folder_matches_local_project": true, "pipeline_env_folder_matches": true, "data_env_folder_matches": true, "ai_entrypoint_matches_project_root": true, "has_main_guard": true, "hardcoded_literal_absent": true}'


def test_run_embeddings_script_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path("scripts/run_embeddings.py").resolve()
        project_root = script_path.parent.parent
        folder_names = ["BLT_485_2026-01-27", "GZ_499_2026-01-30"]
        default_expected = [
            str((project_root / "bulletins" / "Marka" / folder_name).resolve())
            for folder_name in folder_names
        ]
        pipeline_expected = [
            str((project_root / "custom_bulletins" / "Marka" / folder_name).resolve())
            for folder_name in folder_names
        ]
        data_expected = [
            str((project_root / "archive_bulletins" / "Marka" / folder_name).resolve())
            for folder_name in folder_names
        ]
        ai_expected = str((project_root / "pipeline" / "ai.py").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_run_embeddings_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_run_embeddings_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_run_embeddings_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka"

        print(json.dumps({
            "default_folders_match_local_project": [str(path.resolve()) for path in default_module.FOLDERS] == default_expected,
            "pipeline_env_folders_match": [str(path.resolve()) for path in pipeline_module.FOLDERS] == pipeline_expected,
            "data_env_folders_match": [str(path.resolve()) for path in data_module.FOLDERS] == data_expected,
            "ai_entrypoint_matches_project_root": str(default_module.AI_ENTRYPOINT.resolve()) == ai_expected,
            "has_main_guard": 'if __name__ == "__main__":' in source,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_folders_match_local_project": true, "pipeline_env_folders_match": true, "data_env_folders_match": true, "ai_entrypoint_matches_project_root": true, "has_main_guard": true, "hardcoded_literal_absent": true}'


def test_test_1_script_uses_local_project_boundary_and_env_overrides():
    output = _run_python(
        """
        import importlib.util
        import json
        import os
        from pathlib import Path

        script_path = Path(".py/test_1.py").resolve()
        project_root = script_path.parent.parent
        default_expected = str((project_root / "bulletins" / "Marka" / "BLT_327" / "metadata.json").resolve())
        pipeline_expected = str((project_root / "custom_bulletins" / "Marka" / "BLT_327" / "metadata.json").resolve())
        data_expected = str((project_root / "archive_bulletins" / "Marka" / "BLT_327" / "metadata.json").resolve())

        def load_module(name):
            spec = importlib.util.spec_from_file_location(name, script_path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ.pop("DATA_ROOT", None)
        default_module = load_module("phase10_test1_default")

        os.environ["PIPELINE_BULLETINS_ROOT"] = "custom_bulletins/Marka"
        os.environ.pop("DATA_ROOT", None)
        pipeline_module = load_module("phase10_test1_pipeline")

        os.environ.pop("PIPELINE_BULLETINS_ROOT", None)
        os.environ["DATA_ROOT"] = "archive_bulletins/Marka"
        data_module = load_module("phase10_test1_data")

        source = script_path.read_text(encoding="utf-8")
        hardcoded_literal = r"C:\\Users\\701693\\turk_patent\\bulletins\\Marka\\BLT_327\\metadata.json"

        print(json.dumps({
            "default_file_matches_local_project": str(default_module.FILE_PATH.resolve()) == default_expected,
            "pipeline_env_file_matches": str(pipeline_module.FILE_PATH.resolve()) == pipeline_expected,
            "data_env_file_matches": str(data_module.FILE_PATH.resolve()) == data_expected,
            "has_main_guard": 'if __name__ == "__main__":' in source,
            "hardcoded_literal_absent": hardcoded_literal not in source,
        }))
        """
    )

    assert output == '{"default_file_matches_local_project": true, "pipeline_env_file_matches": true, "data_env_file_matches": true, "has_main_guard": true, "hardcoded_literal_absent": true}'

def test_dashboard_leads_panel_uses_helper_for_nice_class_options():
    leads_panel = Path("templates/dashboard/partials/_leads_panel.html").read_text(encoding="utf-8")
    dashboard_app = Path("static/js/dashboard/app.js").read_text(encoding="utf-8")
    assert 'populateNiceClassOptions($el)' in leads_panel
    assert 'x-init="for(var i=1;i<=45;i++)' not in leads_panel
    assert "function populateNiceClassOptions(selectEl)" in dashboard_app
