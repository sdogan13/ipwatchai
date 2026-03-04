"""
Shared test fixtures and module mocks.

IMPORTANT: This file mocks heavy modules (ai, scrapper, ingest) so tests
run without GPU models, database, or network. Module mocks must happen
BEFORE any project code is imported.
"""
import sys
import os
import uuid
import time
import json
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, date, timedelta

import pytest

# ============================================================
# 1. Add project root to path
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================
# 2. Mock heavy modules BEFORE any project imports
#    This prevents GPU model loading during tests
# ============================================================

def _ensure_mock(name, mock_obj=None):
    """Install a mock module if not already present."""
    if name not in sys.modules:
        sys.modules[name] = mock_obj or MagicMock()

# --- PyTorch ecosystem (not installed in test Python 3.13) ---
_mock_torch = MagicMock()
_mock_torch.cuda.is_available.return_value = False
_mock_torch.no_grad.return_value.__enter__ = MagicMock()
_mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)
_ensure_mock("torch", _mock_torch)
_ensure_mock("torchvision")
_ensure_mock("torchvision.transforms")

# --- Computer vision ---
_ensure_mock("cv2")
# numpy is installed (required by pandas), no mock needed

# --- PIL (Pillow) ---
_ensure_mock("PIL")
_ensure_mock("PIL.Image")

# --- ML libraries ---
_ensure_mock("sentence_transformers")
_ensure_mock("open_clip")
_ensure_mock("easyocr")
_ensure_mock("transformers")

# --- Project heavy modules ---
# Mock the 'ai' module (loads CLIP, DINOv2, MiniLM at import time)
_mock_ai = MagicMock()
_mock_ai.device = "cpu"
_mock_ai.text_model = MagicMock()
_mock_ai.clip_model = MagicMock()
_mock_ai.clip_preprocess = MagicMock()
_mock_ai.dinov2_model = MagicMock()
_mock_ai.dinov2_preprocess = MagicMock()
_mock_ai.process_folder = MagicMock()
_ensure_mock("ai", _mock_ai)

# Mock scrapper (Playwright, browser automation)
_ensure_mock("scrapper")

# Mock ingest (DB bulk writes)
_ensure_mock("ingest")

# Mock logging_config (structured logging setup)
_mock_logging = MagicMock()
_mock_logging.get_logger = MagicMock(return_value=MagicMock())
_mock_logging.log_timing = lambda name: (lambda f: f)  # no-op decorator
_mock_logging.setup_logging = MagicMock()
if "logging_config" not in sys.modules:
    sys.modules["logging_config"] = _mock_logging

# Mock db.pool (DB connection pool)
_mock_pool = MagicMock()
_mock_pool.get_connection = MagicMock(return_value=MagicMock())
_mock_pool.release_connection = MagicMock()
_mock_pool.connection_context = MagicMock()
_mock_pool.close_pool = MagicMock()
if "db.pool" not in sys.modules:
    sys.modules["db"] = MagicMock()
    sys.modules["db.pool"] = _mock_pool

# Mock sentence_transformers CrossEncoder
if "sentence_transformers" not in sys.modules:
    _mock_st = MagicMock()
    sys.modules["sentence_transformers"] = _mock_st

# --- Scheduler / Playwright (needed for main.py import) ---
_ensure_mock("apscheduler")
_ensure_mock("apscheduler.schedulers")
_ensure_mock("apscheduler.schedulers.asyncio")
_ensure_mock("apscheduler.triggers")
_ensure_mock("apscheduler.triggers.cron")
_ensure_mock("playwright")
_ensure_mock("playwright.sync_api")
_ensure_mock("playwright.async_api")


# ============================================================
# 3. IDFLookup cache seeding fixture
# ============================================================

@pytest.fixture(autouse=True)
def seed_idf_lookup():
    """
    Pre-populate IDFLookup cache so tests don't need a live DB.
    Uses realistic IDF values for the 3-tier classification:
      - GENERIC: IDF < 5.3 (weight=0.1)
      - SEMI_GENERIC: 5.3 <= IDF < 6.9 (weight=0.5)
      - DISTINCTIVE: IDF >= 6.9 (weight=1.0)
    """
    from idf_lookup import IDFLookup

    IDFLookup._loaded = True
    IDFLookup._total_docs = 2_300_000

    # Distinctive words (IDF >= 6.9)
    distinctive = [
        "nike", "nikea", "nyke", "adidas", "samsung", "apple", "bmw",
        "elma", "kaplan", "dogan", "motors", "dunya", "coca", "cola",
        "kirmizi", "yesil", "gunes", "aslan", "kartal", "pepsi",
        "gucci", "puma", "zara", "ferrari", "tesla", "kent",
        "dünyası", "vatan", "yildiz", "su", "ay", "ayakkabi",
    ]
    for w in distinctive:
        IDFLookup._cache[w] = {"idf": 9.0, "is_generic": False, "doc_freq": 50}

    # Semi-generic words (5.3 <= IDF < 6.9)
    semi_generic = [
        "patent", "marka", "grup", "digital", "global", "spor",
        "teknoloji", "insaat", "gida", "tekstil", "turizm", "enerji",
        "fashion", "sports", "tech",
    ]
    for w in semi_generic:
        IDFLookup._cache[w] = {"idf": 7.0, "is_generic": False, "doc_freq": 5_000}

    # Generic words (IDF < 5.3)
    generic = [
        "ve", "ltd", "sti", "san", "tic", "as", "limited", "plus",
        "market", "group", "company", "the", "and", "of",
    ]
    for w in generic:
        IDFLookup._cache[w] = {"idf": 2.0, "is_generic": True, "doc_freq": 500_000}

    yield

    IDFLookup._loaded = False
    IDFLookup._cache = {}


# ============================================================
# 4. Mock settings_manager to avoid DB calls
# ============================================================

@pytest.fixture(autouse=True)
def mock_settings_manager():
    """Mock settings_manager.get() to return None (use code defaults)."""
    with patch("utils.settings_manager.settings_manager") as mock_sm:
        mock_sm.get.return_value = None
        mock_sm._initialized = True
        yield mock_sm


# ============================================================
# 5. Sample data factories
# ============================================================

@pytest.fixture
def sample_trademark():
    """A realistic trademark record."""
    return {
        "id": str(uuid.uuid4()),
        "application_no": "2024/123456",
        "name": "NIKEA",
        "current_status": "Published",
        "holder_name": "ACME TEXTILES LTD",
        "holder_tpe_client_id": "12345",
        "nice_class_numbers": [25, 35],
        "application_date": date(2024, 1, 15),
        "registration_date": None,
        "bulletin_no": "BLT2024005",
        "bulletin_date": date(2024, 6, 1),
        "image_path": "2024/123456.jpg",
        "name_tr": "nikea",
        "text_embedding": None,
        "image_embedding": None,
        "dinov2_embedding": None,
        "color_histogram": None,
        "logo_ocr_text": None,
    }


@pytest.fixture
def sample_trademarks(sample_trademark):
    """Multiple trademarks for search result testing."""
    base = sample_trademark.copy()
    variants = [
        {"name": "NIKE", "application_no": "2020/000001", "current_status": "Registered", "name_tr": "nike"},
        {"name": "NIKEA", "application_no": "2024/123456", "current_status": "Published", "name_tr": "nikea"},
        {"name": "NIKE SPORTS", "application_no": "2022/050000", "current_status": "Registered", "name_tr": "nike spor"},
        {"name": "NYKE", "application_no": "2023/080000", "current_status": "Published", "name_tr": "nyke"},
        {"name": "MARKET PLUS", "application_no": "2024/200000", "current_status": "Published", "name_tr": "market plus"},
        {"name": "ELMA", "application_no": "2023/090000", "current_status": "Registered", "name_tr": "elma"},
        {"name": "APPLE TECH", "application_no": "2024/300000", "current_status": "Published", "name_tr": "elma teknoloji"},
        {"name": "GÜNEŞ", "application_no": "2021/040000", "current_status": "Registered", "name_tr": "güneş"},
    ]
    trademarks = []
    for v in variants:
        tm = base.copy()
        tm.update(v)
        tm["id"] = str(uuid.uuid4())
        trademarks.append(tm)
    return trademarks


@pytest.fixture
def sample_user():
    """A realistic user record."""
    return {
        "id": str(uuid.uuid4()),
        "email": "test@example.com",
        "organization_id": str(uuid.uuid4()),
        "role": "owner",
        "plan": "professional",
        "is_active": True,
        "is_superadmin": False,
        "created_at": datetime(2024, 1, 1),
    }


@pytest.fixture
def sample_superadmin(sample_user):
    """A superadmin user."""
    admin = sample_user.copy()
    admin["is_superadmin"] = True
    admin["email"] = "admin@example.com"
    return admin


@pytest.fixture
def sample_watchlist_item():
    """A realistic watchlist item."""
    return {
        "id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "brand_name": "MY BRAND",
        "application_no": "2023/001",
        "nice_class_numbers": [25, 35],
        "similarity_threshold": 0.7,
        "is_active": True,
        "created_at": datetime(2024, 1, 1),
    }


@pytest.fixture
def sample_alert():
    """A realistic alert record."""
    return {
        "id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "watchlist_id": str(uuid.uuid4()),
        "watched_brand_name": "MY BRAND",
        "conflicting_application_no": "2024/999",
        "conflicting_name": "MYBRAND",
        "overall_risk_score": 0.82,
        "text_similarity": 0.75,
        "semantic_similarity": 0.80,
        "visual_similarity": 0.0,
        "translation_similarity": 0.0,
        "severity": "very_high",
        "status": "new",
        "detected_at": datetime(2024, 7, 1),
    }


@pytest.fixture
def mock_db():
    """Mock Database context manager matching database/crud.py pattern."""
    db = MagicMock()
    cursor = MagicMock()
    db.cursor.return_value = cursor
    db.commit = MagicMock()
    db.rollback = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    return db


# ============================================================
# 6. FastAPI TestClient
# ============================================================

@pytest.fixture
def client():
    """
    FastAPI TestClient with lifespan replaced by a no-op.
    Auth dependency overridden to return a mock CurrentUser.
    """
    from fastapi.testclient import TestClient
    from auth.authentication import get_current_user, CurrentUser
    from contextlib import asynccontextmanager

    # Import app after all mocks are in place
    from main import app

    # Replace lifespan to avoid DB migrations / scheduler startup
    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    original_router_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    mock_user = CurrentUser(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="test@example.com",
        first_name="Test",
        last_name="User",
        role="owner",
        is_superadmin=False,
        permissions=["watchlist.write", "watchlist.read"],
    )

    app.dependency_overrides[get_current_user] = lambda: mock_user

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
    app.router.lifespan_context = original_router_lifespan


@pytest.fixture
def superadmin_client():
    """TestClient authenticated as superadmin."""
    from fastapi.testclient import TestClient
    from auth.authentication import get_current_user, CurrentUser
    from contextlib import asynccontextmanager

    from main import app

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    original_router_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan

    mock_admin = CurrentUser(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        role="owner",
        is_superadmin=True,
        permissions=[],
    )

    app.dependency_overrides[get_current_user] = lambda: mock_admin

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
    app.router.lifespan_context = original_router_lifespan
