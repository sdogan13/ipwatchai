"""
Trademark Risk Assessment System - Main Application
Multi-tenant API with authentication and watchlist monitoring
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, UploadFile
from slowapi.util import get_remote_address
from pathlib import Path
from PIL import Image
import io
import tempfile
import torch

from config.settings import settings
from services.scoring_service import extract_ocr_text
# CENTRALIZED IDF SCORING - consistent across the entire system
from utils.idf_scoring import (
    normalize_turkish,
    calculate_comprehensive_score,  # Multi-factor scoring (NEW)
    MAX_RESULTS         # Global constant for top N results (10)
)
# Class 99 (Global Brand) utilities - covers all 45 Nice classes
from utils.class_utils import (
    GLOBAL_CLASS
)

# Unified scoring engine - single source of truth for all search paths
from risk_engine import (
    score_pair,
    calculate_visual_similarity,
    get_risk_level as risk_get_risk_level,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
from app_admin_scoring_routes import (
    register_admin_scoring_routes,
)
from app_lifecycle import run_shutdown_tasks, run_startup_tasks
from app_assets import configure_static_assets, mount_static_assets, register_asset_routes
from app_factory import create_fastapi_app
from app_enhanced_search_routes import (
    SearchRequest,
    get_status_code,
    register_enhanced_search_routes,
)
from app_design_search_routes import register_design_search_routes
from app_patent_search_routes import register_patent_search_routes
from app_registry_search_routes import register_registry_search_routes
from app_design_watchlist_routes import register_design_watchlist_routes
from app_design_alert_routes import register_design_alert_routes
from app_image_routes import register_trademark_image_routes
from app_image_search_routes import register_image_search_routes
from app_middleware import configure_middleware
from app_nice_class_routes import (
    NICE_CLASS_NAMES,
    register_nice_class_routes,
)
from app_errors import configure_exception_handlers
from app_public_portfolio_routes import register_public_portfolio_routes
from app_public_search_routes import register_public_search_routes
from app_rate_limit import configure_rate_limiting
from app_router_registry import register_application_routers
from app_legacy_search_routes import register_legacy_search_utility_routes
from app_legacy_rollback_routes import register_legacy_rollback_routes
from app_system_routes import register_system_routes


# ==========================================
# SCORING FUNCTIONS - Now in shared module
# ==========================================
# normalize_turkish, calculate_text_similarity, calculate_combined_score,
# Scoring functions imported from utils.idf_scoring (centralized) and risk_engine (score_pair)
# Uses data-driven IDF from word_idf table (run compute_idf.py monthly)
# This ensures consistent scoring between Search and Scanner


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events"""
    run_startup_tasks(logger, settings)
    yield
    run_shutdown_tasks(logger)
app = create_fastapi_app(settings, lifespan)


configure_middleware(app, settings)


# Rate Limiting
def _get_rate_limit_key(request: Request) -> str:
    """Use authenticated user_id if available, otherwise IP address."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            from auth.authentication import decode_token
            payload = decode_token(auth_header[7:])
            if payload and payload.sub:
                return f"user:{payload.sub}"
        except Exception:
            pass
    return get_remote_address(request)

limiter = configure_rate_limiting(app, settings, logger, _get_rate_limit_key)


configure_exception_handlers(app, settings, logger)


# Each step below is logged as both a banner print (always visible in stderr,
# survives any logging-config issue) and a structured logger line. If startup
# 404s come back in prod, the last banner that printed shows which step
# crashed.
def _step(name):
    print(f"[STARTUP] {name}", flush=True)
    logger.info("startup step", extra={"step": name})


_step("register_application_routers")
register_application_routers(app, logger)

_step("register_system_routes")
register_system_routes(app, settings)

_step("register_admin_scoring_routes")
register_admin_scoring_routes(app)

_step("register_nice_class_routes")
register_nice_class_routes(app, limiter)

_step("register_public_portfolio_routes")
public_portfolio, public_portfolio_csv = register_public_portfolio_routes(app, limiter, logger)

_step("router-registration-complete")


# Static files and templates
STATIC_DIR, templates = configure_static_assets(Path(__file__).parent)
register_asset_routes(app, templates, STATIC_DIR)
mount_static_assets(app, STATIC_DIR)

# ==========================================
# IMAGE SERVING ENDPOINTS
# ==========================================
register_trademark_image_routes(app, logger)


# ==========================================
# IMAGE SEARCH ENDPOINTS
# ==========================================

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB max (images shouldn't be 100MB)
WARNING_FILE_SIZE = 5 * 1024 * 1024  # 5MB warning threshold
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp"]

# Magic byte signatures for image validation
IMAGE_MAGIC_BYTES = {
    b'\xff\xd8\xff': 'image/jpeg',
    b'\x89PNG\r\n\x1a\n': 'image/png',
    b'GIF87a': 'image/gif',
    b'GIF89a': 'image/gif',
    b'BM': 'image/bmp',
    b'RIFF': 'image/webp',  # RIFF....WEBP
}


def validate_image_magic_bytes(content: bytes) -> bool:
    """Validate image file by checking magic bytes, not just Content-Type header."""
    for magic, _ in IMAGE_MAGIC_BYTES.items():
        if content[:len(magic)] == magic:
            return True
    return False


public_search, public_search_post, _do_public_search = register_public_search_routes(
    app=app,
    limiter=limiter,
    logger=logger,
    status_code_getter=lambda status: get_status_code(status),
    rate_limit_getter=lambda key, default: _get_rl_value(key, default),
    allowed_image_types=ALLOWED_IMAGE_TYPES,
    max_image_size=MAX_IMAGE_SIZE,
    validate_image_magic_bytes=validate_image_magic_bytes,
)

# Design (Tasarım) search routes — sister to the trademark search above.
register_design_search_routes(app, limiter)

# Patent / Faydalı Model search routes — sister to design + trademark search.
register_patent_search_routes(app, limiter)

# Cross-registry unified search — discovery surface across both registries.
register_registry_search_routes(app, limiter)

# Design (Tasarım) watchlist + alerts — sister to the trademark watchlist+alerts.
register_design_watchlist_routes(app, limiter)
register_design_alert_routes(app, limiter)

# Lazy load AI models only when needed
_ai_models_loaded = False
_clip_model = None
_clip_preprocess = None
_device = None


def _load_ai_models():
    """Lazy load CLIP model for image search."""
    global _ai_models_loaded, _clip_model, _clip_preprocess, _device

    if _ai_models_loaded:
        return

    try:
        from ai import clip_model, clip_preprocess, device
        _clip_model = clip_model
        _clip_preprocess = clip_preprocess
        _device = device
        _ai_models_loaded = True
        logger.info("AI models loaded for image search")
    except ImportError as e:
        logger.error(f"Failed to load AI models: {e}")
        raise HTTPException(status_code=503, detail="AI models not available")


async def process_uploaded_image(file: UploadFile) -> tuple:
    """
    Validate and process uploaded image.
    Returns tuple of (temp_file_path, PIL_Image).
    """
    # Validate content type header (first layer)
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Gecersiz dosya turu. Izin verilen: {', '.join(ALLOWED_IMAGE_TYPES)}"
        )

    # Read file content
    content = await file.read()

    # Check file size
    file_size_mb = len(content) / (1024 * 1024)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Dosya cok buyuk ({file_size_mb:.1f} MB). Maksimum: 10 MB."
        )

    # Validate magic bytes (second layer - prevents Content-Type spoofing)
    if not validate_image_magic_bytes(content):
        raise HTTPException(
            status_code=400,
            detail="Dosya icerigi gecerli bir gorsel degil. Dosya basligi dogrulanamadi."
        )

    # Open as PIL Image (third layer - actual image parsing)
    try:
        pil_image = Image.open(io.BytesIO(content)).convert('RGB')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Gorsel acilamadi: {str(e)}")

    # Save to temp file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_file.write(content)
    temp_file.close()

    return temp_file.name, pil_image


@torch.inference_mode()
def get_image_embedding_for_search(image_path: str) -> list:
    """
    Generate CLIP embedding for an uploaded image.
    """
    _load_ai_models()

    try:
        pil_image = Image.open(image_path).convert('RGB')
        tensor = _clip_preprocess(pil_image).unsqueeze(0).to(_device)
        if _device == 'cuda':
            tensor = tensor.half()

        feat = _clip_model.encode_image(tensor)
        feat /= feat.norm(dim=-1, keepdim=True)
        embedding = feat.float().cpu().squeeze().tolist()

        return embedding
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding olusturulamadi: {str(e)}")


from utils.settings_manager import get_rate_limit_value as _get_rl_value


def encode_query_image(temp_path: str) -> dict:
    """
    Encode a query image into all visual vectors + OCR text using ai.py models.
    Returns dict with clip_vec, dino_vec, color_vec, ocr_text.
    """
    _load_ai_models()

    from ai import dinov2_model, dinov2_preprocess, device as ai_device

    pil_img = Image.open(temp_path).convert('RGB')

    # CLIP embedding
    clip_tensor = _clip_preprocess(pil_img).unsqueeze(0).to(_device)
    if _device == 'cuda' or (hasattr(_device, 'type') and getattr(_device, 'type', '') == 'cuda'):
        clip_tensor = clip_tensor.half()
    with torch.inference_mode():
        clip_feat = _clip_model.encode_image(clip_tensor)
        clip_feat /= clip_feat.norm(dim=-1, keepdim=True)
        clip_vec = clip_feat.float().cpu().squeeze().tolist()

    # DINOv2 embedding
    dino_tensor = dinov2_preprocess(pil_img).unsqueeze(0).to(ai_device)
    if str(ai_device) == 'cuda':
        dino_tensor = dino_tensor.half()
    with torch.inference_mode():
        dino_vec = dinov2_model(dino_tensor).float().flatten().tolist()

    # Color histogram (8x8x8 HSV = 512-dim, matching ai.py)
    import cv2
    import numpy as np
    color_vec = None
    try:
        cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        hsv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
        cv2.normalize(hist, hist)
        color_vec = hist.flatten().tolist()
    except Exception:
        pass

    # OCR text extraction
    ocr_text = ""
    try:
        ocr_text = extract_ocr_text(temp_path) or ""
    except Exception:
        pass

    return {
        "clip_vec": clip_vec,
        "dino_vec": dino_vec,
        "color_vec": color_vec,
        "ocr_text": ocr_text,
    }
search_by_image = register_image_search_routes(
    app=app,
    limiter=limiter,
    rate_limit_getter=_get_rl_value,
    max_results=MAX_RESULTS,
    process_uploaded_image_handler=process_uploaded_image,
    settings=settings,
    logger=logger,
    global_class=GLOBAL_CLASS,
    score_pair_fn=score_pair,
    visual_similarity_fn=calculate_visual_similarity,
    risk_level_getter=risk_get_risk_level,
    encode_query_image_handler=encode_query_image,
    get_image_embedding_handler=get_image_embedding_for_search,
    extract_ocr_text_handler=extract_ocr_text,
)

enhanced_search = register_enhanced_search_routes(
    app=app,
    limiter=limiter,
    rate_limit=f"{settings.auth.api_rate_limit}/minute",
    settings=settings,
    logger=logger,
    normalize_turkish_fn=normalize_turkish,
    score_pair_fn=score_pair,
    visual_similarity_fn=calculate_visual_similarity,
    class_name_lookup=NICE_CLASS_NAMES,
    encode_query_image_handler=encode_query_image,
)


simple_search, unified_search = register_legacy_search_utility_routes(
    app=app,
    limiter=limiter,
    rate_limit_getter=_get_rl_value,
    max_results=MAX_RESULTS,
    search_request_factory=SearchRequest,
    enhanced_search_handler=enhanced_search,
    search_by_image_handler=search_by_image,
    risk_level_getter=risk_get_risk_level,
    logger=logger,
)

legacy_text_search = register_legacy_rollback_routes(
    app=app,
    limiter=limiter,
    rate_limit=f"{settings.auth.api_rate_limit}/minute",
    search_request_model=SearchRequest,
    settings=settings,
    normalize_turkish_fn=normalize_turkish,
    score_calculator=calculate_comprehensive_score,
    max_results=MAX_RESULTS,
)


# ==========================================
# Run Application
# ==========================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        workers=1 if settings.debug else settings.workers,
        log_level="debug" if settings.debug else "info"
    )
