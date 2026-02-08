"""
Trademark Risk Assessment System - Main Application
Multi-tenant API with authentication and watchlist monitoring
"""
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pathlib import Path
from typing import Optional
from PIL import Image
import io
import tempfile
import os
import torch

from auth.authentication import CurrentUser, get_current_user, require_role
from config.settings import settings
# CENTRALIZED IDF SCORING - consistent across the entire system
from utils.idf_scoring import (
    normalize_turkish,
    calculate_text_similarity,
    calculate_combined_score,
    calculate_comprehensive_score,  # Multi-factor scoring (NEW)
    calculate_alert_risk_score,     # Alert risk scoring (NEW)
    get_risk_level,
    adjust_image_similarity,        # Curve for image scores (NEW)
    extract_ocr_text,               # OCR extraction from images (NEW)
    is_generic_word,    # Data-driven from word_idf table
    get_word_weight,    # 3-tier: 0.1 (generic), 0.5 (semi), 1.0 (distinctive)
    get_word_class,     # Get word classification
    analyze_query,      # Query analysis for debugging
    MAX_RESULTS         # Global constant for top N results (10)
)
# Class 99 (Global Brand) utilities - covers all 45 Nice classes
from utils.class_utils import (
    GLOBAL_CLASS,
    is_global_class,
    expand_classes,
    classes_overlap,
    get_overlapping_classes,
    format_class_display,
    should_include_in_class_filter,
    get_class_sql_condition,
    calculate_class_overlap_score
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==========================================
# SCORING FUNCTIONS - Now in shared module
# ==========================================
# normalize_turkish, calculate_text_similarity, calculate_combined_score,
# get_risk_level, is_generic_word, get_word_weight are imported from utils.scoring
# Uses data-driven IDF from word_idf table (run compute_idf.py monthly)
# This ensures consistent scoring between Search and Scanner


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events"""
    # Startup
    logger.info("🚀 Starting Trademark Risk Assessment System...")
    logger.info(f"   Environment: {settings.environment}")
    logger.info(f"   Version: {settings.app_version}")

    # Initialize IDF scoring system (sync mode - no async pool yet)
    from utils.idf_scoring import initialize_idf_scoring_sync, is_cache_loaded, get_cache_stats
    logger.info("   Loading IDF scoring data...")
    try:
        initialize_idf_scoring_sync()
        if is_cache_loaded():
            stats = get_cache_stats()
            logger.info(f"   IDF Scoring ready: {stats['word_count']:,} words loaded")
        else:
            logger.warning("   IDF Scoring: using fallback (run compute_idf.py to populate)")
    except Exception as e:
        logger.warning(f"   IDF Scoring init failed (non-fatal): {e}")

    # Initialize Gemini client for Creative Suite (Name Generator + Logo Studio)
    from ai.gemini_client import get_gemini_client
    try:
        gemini = get_gemini_client(settings.creative)
        if gemini.is_available():
            logger.info("   Gemini client ready (Creative Suite enabled)")
        else:
            logger.info("   Gemini client: no API key (Creative Suite disabled, set CREATIVE_GOOGLE_API_KEY)")
    except Exception as e:
        logger.warning(f"   Gemini client init failed (non-fatal): {e}")

    # Ensure reports table exists
    from migrations.run_reports_migration import ensure_reports_table
    try:
        if ensure_reports_table():
            logger.info("   Reports table ready")
        else:
            logger.warning("   Reports table migration skipped or failed (non-fatal)")
    except Exception as e:
        logger.warning(f"   Reports table check failed (non-fatal): {e}")

    # Seed superadmin user (idempotent)
    from utils.superadmin import seed_superadmin
    try:
        seed_superadmin()
    except Exception as e:
        logger.warning(f"   Superadmin seed failed (non-fatal): {e}")

    # Start APScheduler for daily watchlist auto-scan
    from workers.scheduler import start_scheduler, shutdown_scheduler
    try:
        start_scheduler()
        logger.info("   Scheduler started (daily watchlist scan at 03:00)")
    except Exception as e:
        logger.warning(f"   Scheduler init failed (non-fatal): {e}")

    yield

    # Shutdown
    try:
        shutdown_scheduler()
    except Exception:
        pass
    logger.info("👋 Shutting down...")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    description="""
    ## Trademark Risk Assessment System
    
    AI-powered trademark conflict detection with multi-tenant watchlist monitoring.
    
    ### Features
    - 🔐 **User Authentication** - JWT-based auth with organization support
    - 📋 **Watchlist Monitoring** - Monitor your trademarks against new filings
    - 🔔 **Smart Alerts** - Get notified of potential conflicts
    - 📊 **Reports** - Generate detailed risk assessment reports
    - 🔍 **AI Search** - Semantic and visual similarity search
    
    ### Authentication
    All endpoints except `/auth/*` require a valid JWT token.
    Include in header: `Authorization: Bearer <token>`
    """,
    version=settings.app_version,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

limiter = Limiter(
    key_func=_get_rate_limit_key,
    default_limits=[f"{settings.auth.api_rate_limit}/minute"]
)
app.state.limiter = limiter
async def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Log rate limit hits before returning 429."""
    ident = getattr(request.state, "_rate_limit_key", None)
    ip = request.client.host if request.client else "unknown"
    logger.warning(f"Rate limit hit: ident={ident} endpoint={request.url.path} IP={ip} limit={exc.detail}")
    return JSONResponse(
        status_code=429,
        content={"detail": {"message": "Rate limit exceeded", "limit": str(exc.detail)}},
    )

app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc) if settings.debug else None}
    )


# ==========================================
# Include Routers
# ==========================================

from api.routes import (
    auth_router,
    users_router,
    user_profile_router,
    org_router,
    watchlist_router,
    alerts_router,
    dashboard_router,
    usage_router,
)
from api.reports import router as reports_router
from api.upload import router as upload_router
from api.leads import router as leads_router
from api.holders import router as holders_router
from api.creative import router as creative_router
from api.pipeline import router as pipeline_router
from agentic_search import router as agentic_router

# Public routes (no auth required for some endpoints)
app.include_router(auth_router, prefix="/api/v1")

# Protected routes
app.include_router(users_router, prefix="/api/v1")
app.include_router(user_profile_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
app.include_router(watchlist_router, prefix="/api/v1")
app.include_router(alerts_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(leads_router, prefix="/api/v1")
app.include_router(holders_router, prefix="/api/v1")
app.include_router(usage_router, prefix="/api/v1")

# File upload routes
app.include_router(upload_router)

# Creative Suite (Name Generator + Logo Studio)
app.include_router(creative_router)

# Pipeline Management (admin-only)
app.include_router(pipeline_router)

# Agentic search (includes its own /api/v1/search prefix)
app.include_router(agentic_router)


# ==========================================
# Public config endpoint — exposes risk thresholds to frontend
# ==========================================
@app.get("/api/v1/config")
async def get_app_config():
    """Return application configuration for frontend alignment."""
    from risk_engine import RISK_THRESHOLDS
    return {
        "risk_thresholds": {k: int(v * 100) for k, v in RISK_THRESHOLDS.items()},
    }

# Static files (avatars, etc.)
import os
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR / "avatars", exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 Templates
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ==========================================
# Root & Health Endpoints
# ==========================================

# Frontend directory path
FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"


@app.get("/", tags=["Root"])
async def root():
    """Serve the frontend application"""
    frontend_file = FRONTEND_DIR / "index.html"
    if frontend_file.exists():
        return FileResponse(frontend_file)
    # Fallback to API info if frontend doesn't exist
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs" if settings.debug else "disabled",
        "health": "/health"
    }


@app.get("/dashboard", response_class=HTMLResponse, tags=["Root"])
async def serve_dashboard(request: Request):
    """Serve the dashboard via Jinja2 templates"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse, tags=["Root"])
async def serve_pricing(request: Request):
    """Serve the pricing page — renders limits dynamically from PLAN_FEATURES"""
    from utils.subscription import PLAN_FEATURES
    return templates.TemplateResponse("pricing.html", {
        "request": request,
        "plans": PLAN_FEATURES,
    })


@app.get("/api/info", tags=["Root"])
async def api_info():
    """API info endpoint - returns basic info"""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs" if settings.debug else "disabled",
        "health": "/health"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.app_version,
        "checks": {}
    }
    
    # Check database
    try:
        from database.crud import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        health_status["checks"]["database"] = "ok"
    except Exception as e:
        health_status["checks"]["database"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check Redis
    try:
        import redis
        r = redis.Redis(host=settings.redis.host, port=settings.redis.port)
        r.ping()
        health_status["checks"]["redis"] = "ok"
    except Exception as e:
        health_status["checks"]["redis"] = f"error: {str(e)}"
        health_status["status"] = "degraded"
    
    # Check GPU (optional)
    try:
        import torch
        if torch.cuda.is_available():
            health_status["checks"]["gpu"] = f"ok ({torch.cuda.get_device_name(0)})"
        else:
            health_status["checks"]["gpu"] = "cpu only"
    except Exception:
        health_status["checks"]["gpu"] = "not available"
    
    return health_status


# ==========================================
# IMAGE SERVING ENDPOINTS
# ==========================================

# Unified LOGOS directory for all trademark images
LOGOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bulletins", "Marka", "LOGOS")

# Supported image extensions (in order of preference)
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".gif", ".webp"]

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}


def find_trademark_image(image_path: str) -> str | None:
    """Find a trademark logo image in the unified LOGOS folder."""
    if not image_path:
        return None

    # Security: block directory traversal
    if ".." in image_path or "/" in image_path or "\\" in image_path:
        return None

    # Try each extension
    for ext in IMAGE_EXTENSIONS:
        full_path = os.path.join(LOGOS_DIR, f"{image_path}{ext}")
        if os.path.isfile(full_path):
            return full_path

    # Try as-is (might already have extension)
    full_path = os.path.join(LOGOS_DIR, image_path)
    if os.path.isfile(full_path):
        return full_path

    return None


@app.get("/api/trademark-image/{image_path:path}", tags=["Images"])
async def serve_trademark_image(image_path: str):
    """
    Serve a trademark logo image from the unified LOGOS folder.

    Example: /api/trademark-image/2005_28311
    Returns the image file (jpg, png, etc.) or 404 if not found.
    """
    # Strip extension if provided (lookup tries all extensions)
    clean_path = image_path.rsplit('.', 1)[0] if '.' in image_path else image_path

    # Handle legacy URLs: /api/trademark-image/{bulletin_no}/{image_path}
    # If clean_path contains a slash, take the last segment as the actual image_path
    if '/' in clean_path:
        clean_path = clean_path.rsplit('/', 1)[-1]

    file_path = find_trademark_image(clean_path)
    if not file_path:
        raise HTTPException(status_code=404, detail="Image not found")

    ext = os.path.splitext(file_path)[1].lower()
    media_type = MEDIA_TYPES.get(ext, "image/jpeg")

    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"}
    )


# ==========================================
# IMAGE SEARCH ENDPOINTS
# ==========================================

MAX_IMAGE_SIZE = 100 * 1024 * 1024  # 100MB max
WARNING_FILE_SIZE = 50 * 1024 * 1024  # 50MB warning threshold
ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp"]

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
    # Validate content type
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
            detail=f"Dosya cok buyuk ({file_size_mb:.1f} MB). Maksimum: 100 MB. Ipucu: Gorseli sikistirin veya boyutunu kucultun."
        )

    # Save to temp file
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    temp_file.write(content)
    temp_file.close()

    # Open as PIL Image
    try:
        pil_image = Image.open(io.BytesIO(content)).convert('RGB')
    except Exception as e:
        os.unlink(temp_file.name)
        raise HTTPException(status_code=400, detail=f"Gorsel acilamadi: {str(e)}")

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


@app.post("/api/search-by-image", tags=["Image Search"])
@limiter.limit("10/minute")
async def search_by_image(
    request: Request,
    image: UploadFile = File(..., description="Aranacak logo/marka gorseli"),
    classes: Optional[str] = Form(None, description="Nice siniflari (virgülle ayrilmis, orn: 9,35,42)"),
    limit: int = Form(MAX_RESULTS, description=f"Maksimum sonuc sayisi (max {MAX_RESULTS})")
):
    """
    Search for similar trademarks by uploading an image.

    This endpoint:
    1. Accepts an uploaded image file
    2. Generates CLIP embedding for the image
    3. Searches database for visually similar trademarks
    4. Returns results ranked by image similarity
    """
    import psycopg2
    import psycopg2.extras

    # Process uploaded image
    temp_path, pil_image = await process_uploaded_image(image)

    try:
        # Generate embedding for uploaded image
        query_embedding = get_image_embedding_for_search(temp_path)

        # Parse classes if provided
        class_list = []
        if classes:
            try:
                class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
                # Allow classes 1-45 and Class 99 (Global Brand)
                class_list = [c for c in class_list if (1 <= c <= 45) or c == GLOBAL_CLASS]
            except ValueError:
                pass

        # Get DB connection
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Convert embedding to string format for pgvector
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        # Check if we have any image embeddings
        cur.execute("SELECT COUNT(*) as cnt FROM trademarks WHERE image_embedding IS NOT NULL")
        embedding_count = cur.fetchone()['cnt']

        if embedding_count == 0:
            # No embeddings yet - fall back to random sample with warning
            logger.warning("No image embeddings in database - returning sample results")

            if class_list:
                # Class 99 (Global Brand) covers all classes - include in any class filter
                cur.execute("""
                    SELECT id, name, application_no, current_status, nice_class_numbers,
                           bulletin_no, image_path
                    FROM trademarks
                    WHERE (nice_class_numbers && %s::int[] OR 99 = ANY(nice_class_numbers))
                    AND bulletin_no IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT %s
                """, (class_list, limit))
            else:
                cur.execute("""
                    SELECT id, name, application_no, current_status, nice_class_numbers,
                           bulletin_no, image_path
                    FROM trademarks
                    WHERE bulletin_no IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT %s
                """, (limit,))

            rows = cur.fetchall()
            results = []
            for row in rows:
                image_url = None
                img = row.get('image_path')
                if img:
                    image_url = f"/api/trademark-image/{img}"

                results.append({
                    "id": str(row['id']),
                    "name": row['name'] or '-',
                    "application_no": row['application_no'],
                    "status": row['current_status'] or '-',
                    "nice_classes": row['nice_class_numbers'] or [],
                    "image_url": image_url,
                    "similarity": 0,  # No real similarity without embeddings
                    "image_similarity": 0,
                    "risk_level": "unknown",
                    "note": "Gorsel embedding veritabaninda bulunamadi - ornek sonuclar"
                })

            cur.close()
            conn.close()

            return {
                "success": True,
                "search_type": "image",
                "warning": "Gorsel embeddingler henuz olusturulmamis. Ornek sonuclar gosteriliyor.",
                "total_results": len(results),
                "classes_filtered": class_list if class_list else None,
                "results": results
            }

        # ═══════════════════════════════════════════════════════════════
        # STEP 1: Extract OCR from uploaded query image
        # ═══════════════════════════════════════════════════════════════
        query_ocr_text = ""
        try:
            query_ocr_text = extract_ocr_text(temp_path)
            if query_ocr_text:
                logger.info(f"OCR extracted from query image: '{query_ocr_text[:50]}...'")
        except Exception as e:
            logger.warning(f"OCR extraction failed for query image: {e}")

        # ═══════════════════════════════════════════════════════════════
        # STEP 2: Search database (include logo_ocr_text for comparison)
        # ═══════════════════════════════════════════════════════════════
        if class_list:
            # Class 99 (Global Brand) covers all classes - include in any class filter
            cur.execute("""
                SELECT
                    t.id,
                    t.name,
                    t.application_no,
                    t.current_status,
                    t.nice_class_numbers,
                    t.bulletin_no,
                    t.image_path,
                    t.logo_ocr_text,
                    1 - (t.image_embedding <=> %s::vector) AS image_similarity
                FROM trademarks t
                WHERE
                    t.image_embedding IS NOT NULL
                    AND (t.nice_class_numbers && %s::int[] OR 99 = ANY(t.nice_class_numbers))
                ORDER BY image_similarity DESC
                LIMIT %s
            """, (embedding_str, class_list, limit))
        else:
            cur.execute("""
                SELECT
                    t.id,
                    t.name,
                    t.application_no,
                    t.current_status,
                    t.nice_class_numbers,
                    t.bulletin_no,
                    t.image_path,
                    t.logo_ocr_text,
                    1 - (t.image_embedding <=> %s::vector) AS image_similarity
                FROM trademarks t
                WHERE t.image_embedding IS NOT NULL
                ORDER BY image_similarity DESC
                LIMIT %s
            """, (embedding_str, limit))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        # ═══════════════════════════════════════════════════════════════
        # STEP 3: Build results with OCR-enhanced scoring
        # ═══════════════════════════════════════════════════════════════
        results = []
        for row in rows:
            image_url = None
            img = row.get('image_path')
            if img:
                image_url = f"/api/trademark-image/{img}"

            # Get raw image similarity
            raw_image_sim = float(row.get('image_similarity', 0))

            # Get trademark's stored OCR text
            trademark_ocr_text = row.get('logo_ocr_text') or ""

            # Unified visual similarity (OCR compares logo text vs logo text ONLY)
            from risk_engine import calculate_visual_similarity, get_risk_level as _get_risk_level
            final_score = calculate_visual_similarity(
                clip_sim=raw_image_sim,
                ocr_text_a=query_ocr_text,
                ocr_text_b=trademark_ocr_text,
            )

            # Compute OCR similarity for display
            from difflib import SequenceMatcher
            ocr_sim = 0.0
            if query_ocr_text and trademark_ocr_text:
                ocr_sim = SequenceMatcher(None, query_ocr_text.lower().strip(), trademark_ocr_text.lower().strip()).ratio()

            results.append({
                "id": str(row['id']),
                "name": row['name'] or '-',
                "application_no": row['application_no'],
                "status": row['current_status'] or '-',
                "nice_classes": row['nice_class_numbers'] or [],
                "image_url": image_url,
                "similarity": round(final_score * 100, 1),
                "image_similarity": round(raw_image_sim * 100, 1),
                "raw_image_score": round(raw_image_sim * 100, 1),
                "ocr_boost": round(ocr_sim * 0.20 * 100, 1),
                "ocr_similarity": round(ocr_sim * 100, 1),
                "final_score": final_score,
                "risk_level": _get_risk_level(final_score)
            })

        # Re-sort by final score (OCR boost may change ranking)
        results.sort(key=lambda x: x['final_score'], reverse=True)

        return {
            "success": True,
            "search_type": "image",
            "ocr_enabled": True,
            "query_ocr_text": query_ocr_text[:100] if query_ocr_text else None,
            "total_results": len(results),
            "classes_filtered": class_list if class_list else None,
            "results": results
        }

    finally:
        # Clean up temp file
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@app.get("/api/v1/status", tags=["Status"])
async def api_status():
    """API status with statistics"""
    from database.crud import Database, get_db_connection
    
    try:
        with Database(get_db_connection()) as db:
            cur = db.cursor()
            
            # Get counts
            cur.execute("SELECT COUNT(*) FROM trademarks")
            trademark_count = cur.fetchone()['count']
            
            cur.execute("SELECT COUNT(*) FROM users WHERE is_active = TRUE")
            user_count = cur.fetchone()['count']
            
            cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE is_active = TRUE")
            watchlist_count = cur.fetchone()['count']

            cur.execute("SELECT COUNT(*) FROM alerts_mt WHERE status = 'new'")
            pending_alerts = cur.fetchone()['count']
            
            return {
                "status": "operational",
                "statistics": {
                    "total_trademarks": trademark_count,
                    "active_users": user_count,
                    "active_watchlist_items": watchlist_count,
                    "pending_alerts": pending_alerts
                },
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


# ==========================================
# Simple Public Search (for landing page)
# ==========================================

from fastapi import Query
from typing import Optional, List
from pydantic import BaseModel, Field

@app.get("/api/search/simple", tags=["Search"])
@limiter.limit("10/minute")
async def simple_search(
    request: Request,
    q: str = Query(..., description="Trademark name to search"),
    limit: int = Query(MAX_RESULTS, ge=1, le=MAX_RESULTS, description="Number of results (max 10)"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Simple trademark search.
    Returns similar trademarks from database.
    Requires authentication.
    """
    import os
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Normalize query for Turkish character matching
        q_normalized = normalize_turkish(q)

        # SQL function to normalize Turkish characters
        normalize_sql = """
            LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
            'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
            'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
        """

        # Fetch 100 candidates, then apply comprehensive scoring
        cur.execute(f"""
            SELECT
                t.id,
                t.application_no,
                t.name,
                t.current_status,
                t.nice_class_numbers,
                t.application_date,
                t.bulletin_no,
                t.image_path,
                t.holder_name,
                t.holder_tpe_client_id,
                GREATEST(
                    similarity(LOWER(t.name), LOWER(%s)),
                    similarity({normalize_sql}, LOWER(%s)),
                    CASE WHEN LOWER(t.name) LIKE LOWER(%s) THEN 0.9 ELSE 0 END,
                    CASE WHEN {normalize_sql} LIKE LOWER(%s) THEN 0.9 ELSE 0 END
                ) as score
            FROM trademarks t
            WHERE
                LOWER(t.name) LIKE LOWER(%s)
                OR {normalize_sql} LIKE LOWER(%s)
                OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                OR similarity({normalize_sql}, LOWER(%s)) > 0.2
            ORDER BY score DESC, t.name
            LIMIT 100
        """, (q, q_normalized, f'%{q}%', f'%{q_normalized}%',
              f'%{q}%', f'%{q_normalized}%', q, q_normalized))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        results = []
        for row in rows:
            # Generate image URL
            image_path = row.get('image_path')
            app_no = row['application_no']

            image_url = None
            if image_path:
                image_url = f"/api/trademark-image/{image_path}"
            elif app_no:
                safe_app_no = app_no.replace('/', '_')
                image_url = f"/api/trademark-image/{safe_app_no}"

            # Use comprehensive scoring for consistency
            target_name = row['name'] or ""
            scoring = calculate_comprehensive_score(q, target_name)
            final_score = scoring['final_score']

            results.append({
                "id": row['id'],
                "name": row['name'],
                "application_no": app_no,
                "nice_classes": row['nice_class_numbers'] or [],
                "current_status": row['current_status'],
                "application_date": str(row['application_date']) if row['application_date'] else None,
                "holder_name": row.get('holder_name'),
                "holder_tpe_client_id": row.get('holder_tpe_client_id'),
                "bulletin_no": row.get('bulletin_no'),
                "image_url": image_url,
                "score": round(final_score, 4),
                "risk_level": scoring['risk_level']
            })

        # Re-sort by comprehensive score and limit to requested count
        results.sort(key=lambda x: x['score'], reverse=True)
        results = results[:limit]

        return {
            "query": q,
            "count": len(results),
            "results": results
        }

    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# UNIFIED SEARCH (Form Data with Optional Image)
# ==========================================

@app.post("/api/search/unified", tags=["Unified Search"])
@limiter.limit("10/minute")
async def unified_search(
    request: Request,
    name: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    classes: Optional[str] = Form(None),
    goods_description: Optional[str] = Form(None),
    limit: int = Form(MAX_RESULTS)
):
    """
    Unified search endpoint supporting text, image, or combined search.

    Accepts multipart form data with:
    - name: Trademark name (optional if image provided)
    - image: Logo image file (optional)
    - classes: Comma-separated Nice class numbers, e.g., "9,35,42"
    - goods_description: For auto class suggestion
    - limit: Max results (default 10)
    """
    import time
    import tempfile
    import psycopg2
    import psycopg2.extras

    start_time = time.time()

    # Parse classes from comma-separated string
    class_list = []
    if classes:
        try:
            class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
            # Allow classes 1-45 and Class 99 (Global Brand)
            class_list = [c for c in class_list if (1 <= c <= 45) or c == GLOBAL_CLASS]
        except ValueError:
            pass

    # Check what we have to search with
    has_name = bool(name and name.strip())
    has_image = image is not None and image.filename

    # Must have at least name or image
    if not has_name and not has_image:
        raise HTTPException(status_code=400, detail="Marka adi veya gorsel gerekli")

    # Initialize
    image_embedding = None
    temp_path = None
    search_type = "text"
    warning_msg = None

    try:
        # =========================================================
        # PROCESS IMAGE IF PROVIDED
        # =========================================================
        if has_image:
            # Validate file type
            content_type = image.content_type or ""
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail="Gecersiz dosya turu. Sadece gorsel dosyalari kabul edilir.")

            # Read and validate size
            content = await image.read()
            if len(content) > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail=f"Dosya cok buyuk. Maksimum {MAX_IMAGE_SIZE // (1024*1024)}MB.")

            # Save to temp file and get embedding
            try:
                _load_ai_models()

                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                temp_file.write(content)
                temp_file.close()
                temp_path = temp_file.name

                # Get CLIP embedding
                from PIL import Image as PILImage
                import torch

                pil_image = PILImage.open(temp_path).convert("RGB")
                image_tensor = _clip_preprocess(pil_image).unsqueeze(0).to(_device)

                # Always convert to half precision since model is loaded in FP16
                if _device == 'cuda' or (hasattr(_device, 'type') and _device.type == 'cuda'):
                    image_tensor = image_tensor.half()

                with torch.no_grad():
                    image_embedding = _clip_model.encode_image(image_tensor)
                    image_embedding = image_embedding / image_embedding.norm(dim=-1, keepdim=True)
                    image_embedding = image_embedding.cpu().float().numpy().flatten().tolist()

                search_type = "combined" if has_name else "image"

            except Exception as e:
                logger.error(f"Image processing error: {e}")
                warning_msg = "Gorsel islenemedi, sadece metin aramasi yapilacak."
                has_image = False
                search_type = "text"

        # =========================================================
        # AUTO CLASS SUGGESTION (if no classes and description provided)
        # =========================================================
        auto_suggested = []
        classes_were_auto_suggested = False

        if not class_list and goods_description and len(goods_description) >= 10:
            try:
                suggestions = get_class_suggestions_internal(
                    goods_description=goods_description,
                    trademark_name=name or "",
                    limit=5
                )
                top_suggestions = [s for s in suggestions if s['similarity'] > 0.3][:3]

                if top_suggestions:
                    class_list = [s['class_number'] for s in top_suggestions]
                    classes_were_auto_suggested = True
                    auto_suggested = top_suggestions
                    logger.info(f"Auto-suggested classes: {class_list}")
            except Exception as e:
                logger.error(f"Class suggestion failed: {e}")

        # =========================================================
        # DATABASE SEARCH
        # =========================================================
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        results = []

        if has_image and image_embedding:
            # Check if we have image embeddings in DB
            cur.execute("SELECT COUNT(*) FROM trademarks WHERE image_embedding IS NOT NULL")
            embedding_count = cur.fetchone()['count']

            if embedding_count == 0:
                # No embeddings - fall back to text search or return sample results
                warning_msg = "Gorsel embeddingler henuz olusturulmamis. Metin aramasi yapiliyor."

                if has_name:
                    # Do text search instead
                    search_type = "text"
                    has_image = False
                else:
                    # Return sample results
                    class_filter = "AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))" if class_list else ""
                    query = f"""
                        SELECT id, application_no, name, current_status, nice_class_numbers,
                               application_date, bulletin_no, image_path
                        FROM trademarks
                        WHERE name IS NOT NULL {class_filter}
                        ORDER BY RANDOM()
                        LIMIT %s
                    """
                    params = [class_list, limit] if class_list else [limit]
                    cur.execute(query, params)
                    rows = cur.fetchall()

                    for row in rows:
                        results.append({
                            "id": str(row['id']),
                            "name": row['name'],
                            "application_no": row['application_no'],
                            "status": row['current_status'] or 'Bilinmiyor',
                            "nice_classes": row['nice_class_numbers'] or [],
                            "bulletin_no": row.get('bulletin_no'),
                            "image_url": get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                            "similarity": 0,
                            "image_similarity": 0,
                            "text_similarity": None,
                            "note": "Gorsel embedding veritabaninda bulunamadi - ornek sonuclar"
                        })
            else:
                # Do image search
                embedding_str = "[" + ",".join(map(str, image_embedding)) + "]"

                class_filter = "AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))" if class_list else ""

                # Normalize query for Turkish character matching
                name_normalized = normalize_turkish(name) if has_name else name

                # SQL function to normalize Turkish characters in database names
                normalize_sql = """
                    LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                    REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                    'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                    'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
                """

                if has_name:
                    # Combined search with Turkish normalization
                    query = f"""
                        SELECT id, application_no, name, current_status, nice_class_numbers,
                               application_date, bulletin_no, image_path,
                               1 - (image_embedding <=> %s::vector) AS image_sim,
                               GREATEST(
                                   similarity(LOWER(name), LOWER(%s)),
                                   similarity({normalize_sql}, LOWER(%s))
                               ) AS text_sim,
                               (0.4 * (1 - (image_embedding <=> %s::vector)) +
                                0.6 * GREATEST(
                                    similarity(LOWER(name), LOWER(%s)),
                                    similarity({normalize_sql}, LOWER(%s))
                                )) AS combined_score
                        FROM trademarks
                        WHERE image_embedding IS NOT NULL {class_filter}
                        ORDER BY combined_score DESC
                        LIMIT 100
                    """
                    # Fetch 100 candidates for comprehensive scoring, return top N
                    params = [embedding_str, name, name_normalized, embedding_str, name, name_normalized]
                    if class_list:
                        params.append(class_list)
                else:
                    # Image only search - include logo_ocr_text for OCR comparison
                    query = f"""
                        SELECT id, application_no, name, current_status, nice_class_numbers,
                               application_date, bulletin_no, image_path, logo_ocr_text,
                               1 - (image_embedding <=> %s::vector) AS image_sim
                        FROM trademarks
                        WHERE image_embedding IS NOT NULL {class_filter}
                        ORDER BY image_sim DESC
                        LIMIT %s
                    """
                    params = [embedding_str]
                    if class_list:
                        params.append(class_list)
                    params.append(limit)

                    # Extract OCR from uploaded query image for comparison
                    query_ocr_text = ""
                    if temp_path:
                        try:
                            query_ocr_text = extract_ocr_text(temp_path)
                            if query_ocr_text:
                                logger.info(f"OCR from query image: '{query_ocr_text[:50]}...'")
                        except Exception as e:
                            logger.warning(f"OCR extraction failed: {e}")

                cur.execute(query, params)
                rows = cur.fetchall()

                for row in rows:
                    # Get raw image similarity
                    raw_image_sim = float(row.get('image_sim', 0)) if row.get('image_sim') else 0

                    if has_name:
                        # ═══════════════════════════════════════════════════════════
                        # COMBINED SEARCH (text + image)
                        # ═══════════════════════════════════════════════════════════
                        image_sim = adjust_image_similarity(raw_image_sim)
                        pg_text_sim = float(row.get('text_sim', 0)) if row.get('text_sim') else 0

                        # COMPREHENSIVE MULTI-FACTOR SCORING
                        target_name = row['name'] or ""
                        scoring = calculate_comprehensive_score(
                            query_text=name,
                            result_text=target_name,
                            include_details=False
                        )
                        text_sim = scoring['final_score']

                        # Combined score with smart weighting
                        combined_scoring = calculate_combined_score(
                            text_similarity=text_sim,
                            image_similarity=image_sim,
                            search_type='combined'
                        )
                        combined = combined_scoring['overall_score']

                        results.append({
                            "id": str(row['id']),
                            "name": row['name'],
                            "application_no": row['application_no'],
                            "status": row['current_status'] or 'Bilinmiyor',
                            "nice_classes": row['nice_class_numbers'] or [],
                            "bulletin_no": row.get('bulletin_no'),
                            "image_url": get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                            "similarity": round(combined * 100, 1),
                            "image_similarity": round(image_sim * 100, 1),
                            "raw_image_score": round(raw_image_sim * 100, 1),
                            "text_similarity": round(text_sim * 100, 1),
                            "risk_level": combined_scoring['risk_level'],
                            "scoring_factors": scoring['factors']
                        })
                    else:
                        # ═══════════════════════════════════════════════════════════
                        # IMAGE-ONLY SEARCH with OCR enhancement
                        # ═══════════════════════════════════════════════════════════
                        trademark_ocr_text = row.get('logo_ocr_text') or ""

                        # Unified visual similarity (OCR compares logo text vs logo text ONLY)
                        from risk_engine import calculate_visual_similarity, get_risk_level as _get_risk_level
                        final_score = calculate_visual_similarity(
                            clip_sim=raw_image_sim,
                            ocr_text_a=query_ocr_text,
                            ocr_text_b=trademark_ocr_text,
                        )
                        from difflib import SequenceMatcher
                        ocr_sim = 0.0
                        if query_ocr_text and trademark_ocr_text:
                            ocr_sim = SequenceMatcher(None, query_ocr_text.lower().strip(), trademark_ocr_text.lower().strip()).ratio()

                        results.append({
                            "id": str(row['id']),
                            "name": row['name'],
                            "application_no": row['application_no'],
                            "status": row['current_status'] or 'Bilinmiyor',
                            "nice_classes": row['nice_class_numbers'] or [],
                            "bulletin_no": row.get('bulletin_no'),
                            "image_url": get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                            "similarity": round(final_score * 100, 1),
                            "image_similarity": round(raw_image_sim * 100, 1),
                            "raw_image_score": round(raw_image_sim * 100, 1),
                            "ocr_boost": round(ocr_sim * 0.20 * 100, 1),
                            "ocr_similarity": round(ocr_sim * 100, 1),
                            "risk_level": _get_risk_level(final_score)
                        })

        # Text search (no image or fallback)
        if not results and has_name:
            search_type = "text"

            class_filter = "AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))" if class_list else ""

            # Normalize the search query for Turkish character matching
            name_normalized = normalize_turkish(name)

            # SQL function to normalize Turkish characters in database names
            # This allows "dogan" to match "doğan" in the database
            normalize_sql = """
                LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
            """

            query = f"""
                SELECT id, application_no, name, current_status, nice_class_numbers,
                       application_date, bulletin_no, image_path,
                       GREATEST(
                           similarity(LOWER(name), LOWER(%s)),
                           similarity({normalize_sql}, LOWER(%s)),
                           CASE WHEN LOWER(name) LIKE LOWER(%s) THEN 0.9 ELSE 0 END,
                           CASE WHEN {normalize_sql} LIKE LOWER(%s) THEN 0.9 ELSE 0 END
                       ) as score
                FROM trademarks
                WHERE (
                    LOWER(name) LIKE LOWER(%s)
                    OR {normalize_sql} LIKE LOWER(%s)
                    OR similarity(LOWER(name), LOWER(%s)) > 0.2
                    OR similarity({normalize_sql}, LOWER(%s)) > 0.2
                )
                {class_filter}
                ORDER BY score DESC
                LIMIT 100
            """
            # Fetch 100 candidates, apply comprehensive scoring, then return top N
            params = [name, name_normalized, f'%{name}%', f'%{name_normalized}%',
                      f'%{name}%', f'%{name_normalized}%', name, name_normalized]
            if class_list:
                params.append(class_list)

            cur.execute(query, params)
            rows = cur.fetchall()

            searched_classes_set = set(class_list) if class_list else set()

            for row in rows:
                result_classes = row['nice_class_numbers'] or []
                overlap_count = len(searched_classes_set.intersection(set(result_classes))) if searched_classes_set else 0
                pg_score = float(row['score']) if row['score'] else 0.0

                # COMPREHENSIVE MULTI-FACTOR SCORING
                # Uses word boundary matching, length ratio, coverage, and IDF
                # This correctly handles "dogan patent" vs "erdogan patent ofisi"
                # NOTE: Do NOT pass raw_similarity - let the function calculate it
                # using SequenceMatcher for consistency with test endpoint
                target_name = row['name'] or ""
                scoring = calculate_comprehensive_score(
                    query_text=name,
                    result_text=target_name,
                    include_details=False
                )
                score = scoring['final_score']

                results.append({
                    "id": str(row['id']),
                    "name": row['name'],
                    "application_no": row['application_no'],
                    "status": row['current_status'] or 'Bilinmiyor',
                    "nice_classes": result_classes,
                    "bulletin_no": row.get('bulletin_no'),
                    "image_url": get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                    "similarity": round(score * 100, 1),
                    "name_similarity": round(score * 100, 1),
                    "class_overlap_count": overlap_count,
                    "risk_level": scoring['risk_level'],
                    "scoring_factors": scoring['factors']
                })

        cur.close()
        conn.close()

        # Re-sort results by similarity after Turkish normalization recalculation
        results.sort(key=lambda x: x.get('similarity', 0), reverse=True)

        # Apply TOP 10 limit
        total_found = len(results)
        results = results[:MAX_RESULTS]

        search_time = (time.time() - start_time) * 1000

        # =========================================================
        # BUILD RESPONSE
        # =========================================================
        response = {
            "success": True,
            "results": results,
            "search_type": search_type,
            "search_context": {
                "searched_name": name if has_name else None,
                "searched_classes": class_list,
                "total_results": len(results),
                "total_found": total_found,
                "search_time_ms": round(search_time, 2)
            },
            "classes_were_auto_suggested": classes_were_auto_suggested
        }

        if auto_suggested:
            response["auto_suggested_classes"] = [
                {
                    "class_number": s['class_number'],
                    "class_name": s['class_name'],
                    "similarity_score": round(s['similarity'], 4)
                }
                for s in auto_suggested
            ]

        if warning_msg:
            response["warning"] = warning_msg

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unified search error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Arama hatasi: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


# ==========================================
# Enhanced Search with Auto Class Suggestion
# ==========================================

# Pydantic models for enhanced search
class SearchRequest(BaseModel):
    """Enhanced search request with auto class suggestion."""
    name: str = Field(..., min_length=1, description="Trademark name to search")
    classes: Optional[List[int]] = Field(None, description="Manually selected Nice classes (1-45)")
    goods_description: Optional[str] = Field(
        None,
        min_length=10,
        description="Plain text description of goods/services for auto class suggestion"
    )
    auto_suggest_classes: bool = Field(
        default=True,
        description="If true and no classes provided, auto-suggest based on goods_description"
    )
    include_suggested_in_response: bool = Field(
        default=True,
        description="Include class suggestion details in response"
    )
    limit: int = Field(20, ge=1, le=100, description="Maximum number of results")


class AutoSuggestedClass(BaseModel):
    """Class that was auto-suggested for the search."""
    class_number: int
    class_name: str
    similarity_score: float


class TrademarkResult(BaseModel):
    """Enhanced search result with all detail fields for expandable view."""

    # Basic identification
    id: str = Field(..., description="Unique identifier for the trademark")
    name: str = Field(..., description="Trademark name/text")

    # Application details
    application_no: str = Field(..., description="Application number")
    application_date: Optional[str] = Field(None, description="Application date (YYYY-MM-DD)")
    registration_date: Optional[str] = Field(None, description="Registration date if registered")

    # Status
    status: str = Field(..., description="Human-readable status (Tescilli, Başvuru, etc.)")
    status_code: str = Field(default="unknown", description="Status code (registered, pending, rejected, published)")

    # Classification
    nice_classes: List[int] = Field(default=[], description="List of Nice class numbers")

    # Ownership
    owner: Optional[str] = Field(None, description="Trademark owner/applicant name")
    attorney: Optional[str] = Field(None, description="Patent attorney/representative")

    # Publication
    bulletin_no: Optional[str] = Field(None, description="Publication bulletin number")

    # Image
    image_url: Optional[str] = Field(None, description="URL to trademark image")

    # Similarity scores (0-100)
    similarity: float = Field(..., ge=0, le=100, description="Overall similarity percentage")
    name_similarity: Optional[float] = Field(None, description="Text/name similarity (0-100)")

    # Computed fields
    class_overlap_count: int = Field(default=0, description="Number of overlapping classes with search")


class SearchContext(BaseModel):
    """Context about the search that was performed."""
    searched_name: str
    searched_classes: List[int] = []
    goods_description: Optional[str] = None
    total_results: int
    search_time_ms: float


class EnhancedSearchResponse(BaseModel):
    """Enhanced search response with results and context."""
    results: List[TrademarkResult]
    search_context: SearchContext
    # Keep old fields for backward compatibility
    query: str
    total_results: int
    search_time_ms: float
    search_classes: List[int]
    classes_were_auto_suggested: bool
    auto_suggested_classes: Optional[List[AutoSuggestedClass]] = None
    suggestion_query: Optional[str] = None


# ==========================================
# Helper Functions for Search Results
# ==========================================

def format_date(date_val) -> Optional[str]:
    """Format date to string (YYYY-MM-DD)."""
    if date_val is None:
        return None
    if isinstance(date_val, str):
        return date_val
    try:
        return date_val.strftime('%Y-%m-%d')
    except Exception:
        return str(date_val)


def get_status_code(status_text: Optional[str]) -> str:
    """Convert Turkish status to standardized code."""
    if not status_text:
        return 'unknown'

    status_map = {
        'Registered': 'registered',
        'Tescilli': 'registered',
        'Tescil': 'registered',
        'Published': 'published',
        'Yayın': 'published',
        'Yayında': 'published',
        'Pending': 'pending',
        'Başvuru': 'pending',
        'İnceleme': 'pending',
        'İncelemede': 'pending',
        'Rejected': 'rejected',
        'Reddedildi': 'rejected',
        'Red': 'rejected',
        'Cancelled': 'cancelled',
        'İptal': 'cancelled',
        'İptal Edildi': 'cancelled',
        'Expired': 'expired',
        'Süresi Doldu': 'expired',
        'Withdrawn': 'withdrawn',
        'Geri Çekildi': 'withdrawn'
    }
    return status_map.get(status_text, 'unknown')


def get_image_url(image_path: Optional[str], application_no: str, bulletin_no: Optional[str] = None) -> Optional[str]:
    """Get image URL for trademark using the image serving endpoint."""
    # Use image_path if available, otherwise construct from application_no
    if image_path:
        return f"/api/trademark-image/{image_path}"

    if application_no:
        safe_app_no = application_no.replace('/', '_')
        return f"/api/trademark-image/{safe_app_no}"

    return None


# Helper function for internal class suggestions
def get_class_suggestions_internal(goods_description: str, trademark_name: str = None, limit: int = 5) -> List[dict]:
    """
    Internal helper to get class suggestions without going through HTTP.
    Returns list of dicts with class_number, class_name, similarity.
    """
    import os
    import psycopg2
    import psycopg2.extras
    from ai import get_text_embedding_cached

    # Build query text - combine trademark name with description for better context
    query_text = goods_description
    if trademark_name:
        query_text = f"{trademark_name}: {query_text}"

    try:
        query_embedding = get_text_embedding_cached(query_text)

        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT
                class_number,
                1 - (description_embedding <=> %s::halfvec) as similarity
            FROM nice_classes_lookup
            WHERE description_embedding IS NOT NULL
            ORDER BY description_embedding <=> %s::halfvec
            LIMIT %s
        """, (query_embedding, query_embedding, limit))

        results = []
        for row in cur.fetchall():
            results.append({
                "class_number": row['class_number'],
                "class_name": NICE_CLASS_NAMES.get(row['class_number'], f"Class {row['class_number']}"),
                "similarity": float(row['similarity'])
            })

        cur.close()
        conn.close()
        return results

    except Exception as e:
        logger.error(f"Internal class suggestion error: {e}")
        return []


@app.post("/api/search", response_model=EnhancedSearchResponse, tags=["Enhanced Search"])
@limiter.limit(f"{settings.auth.api_rate_limit}/minute")
async def enhanced_search(request: Request, search_request: SearchRequest):
    """
    Enhanced trademark search with auto class suggestion.

    **Auto-Suggestion Behavior:**
    - If `classes` is empty/null AND `goods_description` is provided AND `auto_suggest_classes` is true:
      - System automatically suggests relevant Nice classes based on the description
      - Only classes with similarity > 0.3 are used (max 3 classes)
    - If `classes` is provided, those classes are used directly (no auto-suggestion)

    **Example with auto-suggestion:**
    ```json
    {
      "name": "QUICKBITE",
      "goods_description": "Mobile app for food delivery and restaurant reservations"
    }
    ```

    **Example with manual classes:**
    ```json
    {
      "name": "QUICKBITE",
      "classes": [9, 43]
    }
    ```
    """
    import time
    import os
    import psycopg2
    import psycopg2.extras

    start_time = time.time()

    search_classes = search_request.classes or []
    auto_suggested = []
    classes_were_auto_suggested = False
    suggestion_query = None

    # =========================================================
    # AUTO CLASS SUGGESTION LOGIC
    # =========================================================
    if not search_classes and search_request.goods_description and search_request.auto_suggest_classes:
        logger.info(f"Auto-suggesting classes for: {search_request.goods_description[:50]}...")

        try:
            # Get suggestions internally
            suggestions = get_class_suggestions_internal(
                goods_description=search_request.goods_description,
                trademark_name=search_request.name,
                limit=5
            )

            # Use top 3 classes with similarity > 0.3
            top_suggestions = [s for s in suggestions if s['similarity'] > 0.3][:3]

            if top_suggestions:
                search_classes = [s['class_number'] for s in top_suggestions]
                classes_were_auto_suggested = True
                suggestion_query = f"{search_request.name}: {search_request.goods_description}"

                auto_suggested = [
                    AutoSuggestedClass(
                        class_number=s['class_number'],
                        class_name=s['class_name'],
                        similarity_score=round(s['similarity'], 4)
                    )
                    for s in top_suggestions
                ]

                logger.info(f"Auto-suggested classes: {search_classes}")
            else:
                logger.warning("No classes met similarity threshold (0.3)")

        except Exception as e:
            logger.error(f"Class suggestion failed: {e}")
            # Continue without class filtering rather than failing

    # =========================================================
    # TRADEMARK SEARCH LOGIC
    # =========================================================
    try:
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Normalize query for Turkish character matching
        name_normalized = normalize_turkish(search_request.name)

        # SQL function to normalize Turkish characters in database names
        normalize_sql = """
            LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
            'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
            'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
        """

        # Build query with optional class filtering
        # Enhanced query to fetch all detail fields with Turkish normalization
        base_select = f"""
            SELECT
                t.id,
                t.application_no,
                t.name,
                t.current_status,
                t.nice_class_numbers,
                t.application_date,
                t.registration_date,
                t.bulletin_no,
                t.image_path,
                t.holder_name,
                t.holder_tpe_client_id,
                GREATEST(
                    similarity(LOWER(t.name), LOWER(%s)),
                    similarity({normalize_sql}, LOWER(%s)),
                    CASE WHEN LOWER(t.name) LIKE LOWER(%s) THEN 0.9 ELSE 0 END,
                    CASE WHEN {normalize_sql} LIKE LOWER(%s) THEN 0.9 ELSE 0 END
                ) as score,
                GREATEST(
                    similarity(LOWER(t.name), LOWER(%s)),
                    similarity({normalize_sql}, LOWER(%s))
                ) as name_sim
            FROM trademarks t
        """

        if search_classes:
            # Filter by Nice classes using array overlap
            # Class 99 (Global Brand) covers all classes - include in any class filter
            cur.execute(base_select + f"""
                WHERE
                    (LOWER(t.name) LIKE LOWER(%s)
                     OR {normalize_sql} LIKE LOWER(%s)
                     OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                     OR similarity({normalize_sql}, LOWER(%s)) > 0.2)
                    AND (t.nice_class_numbers && %s::integer[] OR 99 = ANY(t.nice_class_numbers))
                ORDER BY score DESC, t.name
                LIMIT 100
            """, (search_request.name, name_normalized, f'%{search_request.name}%', f'%{name_normalized}%',
                  search_request.name, name_normalized,
                  f'%{search_request.name}%', f'%{name_normalized}%', search_request.name, name_normalized,
                  search_classes))
        else:
            # No class filtering - search all
            cur.execute(base_select + f"""
                WHERE
                    LOWER(t.name) LIKE LOWER(%s)
                    OR {normalize_sql} LIKE LOWER(%s)
                    OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                    OR similarity({normalize_sql}, LOWER(%s)) > 0.2
                ORDER BY score DESC, t.name
                LIMIT 100
            """, (search_request.name, name_normalized, f'%{search_request.name}%', f'%{name_normalized}%',
                  search_request.name, name_normalized,
                  f'%{search_request.name}%', f'%{name_normalized}%', search_request.name, name_normalized))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        # =========================================================
        # BUILD RESULTS WITH ALL DETAIL FIELDS
        # =========================================================
        searched_classes_set = set(search_classes) if search_classes else set()
        results = []

        for row in rows:
            # Get Nice classes for this result
            result_classes = row['nice_class_numbers'] or []

            # Calculate class overlap count
            overlap_count = len(searched_classes_set.intersection(set(result_classes))) if searched_classes_set else 0

            # CRITICAL FIX: Use multi-factor comprehensive scoring
            # This applies 4 factors: word match, length ratio, coverage, IDF weight
            # - "dogan patent" vs "erdogan patent ofisi" = LOW (prefix mismatch)
            # - "dogan patent" vs "d.p dogan patent" = HIGH (distinctive word match)
            target_name = row['name'] or ""
            scoring = calculate_comprehensive_score(search_request.name, target_name)
            score = scoring['final_score']
            similarity_pct = round(score * 100, 1)

            # Name similarity uses the same comprehensive score
            name_similarity_pct = similarity_pct

            results.append(TrademarkResult(
                id=str(row['id']),
                name=row['name'],
                application_no=row['application_no'],
                application_date=format_date(row.get('application_date')),
                registration_date=format_date(row.get('registration_date')),
                status=row['current_status'] or 'Bilinmiyor',
                status_code=get_status_code(row['current_status']),
                nice_classes=result_classes,
                owner=row.get('holder_name'),
                attorney=None,  # TODO: Join with attorneys table when relationship is established
                bulletin_no=row.get('bulletin_no'),
                image_url=get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                similarity=similarity_pct,
                name_similarity=name_similarity_pct,
                class_overlap_count=overlap_count
            ))

        # Re-sort results by similarity after Turkish normalization recalculation
        results.sort(key=lambda x: x.similarity, reverse=True)

        # Apply TOP 10 limit
        total_found = len(results)
        results = results[:MAX_RESULTS]

        search_time = (time.time() - start_time) * 1000

        # =========================================================
        # BUILD SEARCH CONTEXT
        # =========================================================
        search_context = SearchContext(
            searched_name=search_request.name,
            searched_classes=search_classes,
            goods_description=search_request.goods_description,
            total_results=len(results),
            search_time_ms=round(search_time, 2)
        )

        # =========================================================
        # BUILD RESPONSE
        # =========================================================
        return EnhancedSearchResponse(
            results=results,
            search_context=search_context,
            # Backward compatibility fields
            query=search_request.name,
            total_results=len(results),
            search_time_ms=round(search_time, 2),
            search_classes=search_classes,
            classes_were_auto_suggested=classes_were_auto_suggested,
            auto_suggested_classes=auto_suggested if search_request.include_suggested_in_response and auto_suggested else None,
            suggestion_query=suggestion_query if search_request.include_suggested_in_response else None
        )

    except Exception as e:
        logger.error(f"Enhanced search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# Nice Class Suggestion Endpoint
# ==========================================

# Pydantic models for class suggestion
class ClassSuggestionRequest(BaseModel):
    description: str = Field(..., description="Description of goods/services in Turkish or English", min_length=3, max_length=2000)
    top_k: int = Field(5, ge=1, le=45, description="Number of classes to return")


class SuggestedClass(BaseModel):
    class_number: int
    class_name: str
    similarity: float
    description: str


class ClassSuggestionResponse(BaseModel):
    query: str
    suggestions: List[SuggestedClass]
    processing_time_ms: float


# Human-friendly Nice Class names
NICE_CLASS_NAMES = {
    1: "Chemicals",
    2: "Paints & Varnishes",
    3: "Cosmetics & Cleaning",
    4: "Industrial Oils & Fuels",
    5: "Pharmaceuticals",
    6: "Common Metals",
    7: "Machines & Machine Tools",
    8: "Hand Tools",
    9: "Electronics & Software",
    10: "Medical Apparatus",
    11: "Lighting & Heating",
    12: "Vehicles",
    13: "Firearms & Explosives",
    14: "Jewelry & Watches",
    15: "Musical Instruments",
    16: "Paper & Office Supplies",
    17: "Rubber & Plastic",
    18: "Leather Goods",
    19: "Building Materials",
    20: "Furniture",
    21: "Household Utensils",
    22: "Ropes & Textile Fibers",
    23: "Yarns & Threads",
    24: "Textiles & Bedding",
    25: "Clothing & Footwear",
    26: "Haberdashery",
    27: "Floor Coverings",
    28: "Games & Sporting Goods",
    29: "Meat & Processed Foods",
    30: "Staple Foods",
    31: "Agricultural Products",
    32: "Beers & Beverages",
    33: "Alcoholic Beverages",
    34: "Tobacco",
    35: "Advertising & Business",
    36: "Insurance & Finance",
    37: "Construction & Repair",
    38: "Telecommunications",
    39: "Transport & Storage",
    40: "Material Treatment",
    41: "Education & Entertainment",
    42: "Scientific & Tech Services",
    43: "Food & Accommodation",
    44: "Medical & Beauty Services",
    45: "Legal & Security Services",
    99: "Global Brand (All Classes)"  # Special: covers all 45 classes
}

# Turkish Nice Class names
NICE_CLASS_NAMES_TR = {
    1: "Kimyasallar",
    2: "Boyalar",
    3: "Kozmetikler",
    4: "Yaglar ve Yakitlar",
    5: "Eczacilik Urunleri",
    6: "Metaller",
    7: "Makineler",
    8: "El Aletleri",
    9: "Bilgisayar ve Elektronik",
    10: "Tibbi Cihazlar",
    11: "Aydinlatma ve Isitma",
    12: "Tasitlar",
    13: "Atesli Silahlar",
    14: "Mucevherat",
    15: "Muzik Aletleri",
    16: "Kagit ve Ofis",
    17: "Kaucuk ve Plastik",
    18: "Deri Urunleri",
    19: "Yapi Malzemeleri",
    20: "Mobilya",
    21: "Ev Esyalari",
    22: "Halatlar ve Cadirlar",
    23: "Iplikler",
    24: "Tekstil",
    25: "Giyim",
    26: "Aksesuarlar",
    27: "Halilar",
    28: "Oyunlar ve Oyuncaklar",
    29: "Et ve Sut Urunleri",
    30: "Gida Urunleri",
    31: "Tarim Urunleri",
    32: "Icecekler",
    33: "Alkollü Icecekler",
    34: "Tutun",
    35: "Reklamcilik",
    36: "Sigortacilik ve Finans",
    37: "Insaat",
    38: "Telekomünikasyon",
    39: "Tasimacilik",
    40: "Uretim",
    41: "Egitim ve Eglence",
    42: "Bilimsel ve Teknolojik Hizmetler",
    43: "Yiyecek ve Konaklama",
    44: "Tibbi Hizmetler",
    45: "Hukuki Hizmetler",
    99: "Global Marka (Tum Siniflar)"  # Ozel: tum 45 sinifi kapsar
}


def parse_classes_text(text: str) -> list:
    """
    Parse classes from text input.
    Accepts formats: "9,35,42" or "9, 35, 42" or "9 35 42"
    Supports Class 99 (Global Brand) which covers all 45 classes.
    """
    import re
    if not text:
        return []

    # Split by comma or whitespace
    parts = re.split(r'[,\s]+', text.strip())

    classes = []
    for part in parts:
        part = part.strip()
        if part:
            try:
                num = int(part)
                # Allow classes 1-45 and Class 99 (Global Brand)
                if (1 <= num <= 45) or num == GLOBAL_CLASS:
                    classes.append(num)
            except ValueError:
                pass

    return sorted(list(set(classes)))


def get_class_name(class_num: int, lang: str = "tr") -> str:
    """Get name for a Nice class in specified language."""
    if lang == "tr":
        return NICE_CLASS_NAMES_TR.get(class_num, f"Sinif {class_num}")
    return NICE_CLASS_NAMES.get(class_num, f"Class {class_num}")


@app.post("/api/validate-classes", tags=["Nice Classification"])
async def validate_classes(classes_text: str = Form(..., description="Nice siniflari (ornek: 9, 35, 42)")):
    """
    Validate and parse Nice class input.
    Returns parsed classes and any validation errors.

    Example input: "9, 35, 42, 99, abc"
    Returns valid classes and identifies invalid entries.
    """
    import re

    # Split input by comma or whitespace
    parts = re.split(r'[,\s]+', classes_text.strip())

    valid_classes = []
    invalid_entries = []

    for part in parts:
        part = part.strip()
        if not part:
            continue

        try:
            num = int(part)
            if 1 <= num <= 45:
                valid_classes.append(num)
            else:
                invalid_entries.append({"value": num, "reason": "1-45 arasi olmali"})
        except ValueError:
            invalid_entries.append({"value": part, "reason": "Gecerli sayi degil"})

    # Remove duplicates and sort
    valid_classes = sorted(list(set(valid_classes)))

    # Build message
    if not valid_classes:
        message = "Gecerli sinif bulunamadi"
    elif invalid_entries:
        invalid_str = ", ".join(str(e["value"]) for e in invalid_entries)
        message = f"{len(valid_classes)} gecerli sinif, {len(invalid_entries)} gecersiz ({invalid_str})"
    else:
        class_names = [f"{c} ({get_class_name(c)})" for c in valid_classes]
        message = f"{len(valid_classes)} sinif secildi: {', '.join(class_names)}"

    return {
        "valid": len(valid_classes) > 0,
        "classes": valid_classes,
        "classes_with_names": [
            {"number": c, "name_tr": get_class_name(c, "tr"), "name_en": get_class_name(c, "en")}
            for c in valid_classes
        ],
        "invalid": invalid_entries,
        "count": len(valid_classes),
        "message": message
    }


@app.get("/api/nice-classes", tags=["Nice Classification"])
async def get_nice_classes(lang: str = "tr"):
    """
    Return all Nice classes with names for reference.
    Supports Turkish (tr) and English (en).
    Includes Class 99 (Global Brand) which covers all 45 classes.
    """
    names = NICE_CLASS_NAMES_TR if lang == "tr" else NICE_CLASS_NAMES

    # Separate standard classes (1-45) and special classes (99)
    standard_classes = [(num, name) for num, name in sorted(names.items()) if num <= 45]
    special_classes = [(num, name) for num, name in sorted(names.items()) if num > 45]

    return {
        "language": lang,
        "total": 45,  # Standard Nice classes
        "total_with_special": len(names),  # Including Class 99
        "classes": [
            {"number": num, "name": name}
            for num, name in standard_classes
        ],
        "special_classes": [
            {"number": num, "name": name, "description": "Covers all 45 classes"}
            for num, name in special_classes
        ]
    }


@app.post("/api/suggest-classes", response_model=ClassSuggestionResponse, tags=["Nice Classification"])
async def suggest_nice_classes(request: ClassSuggestionRequest):
    """
    Suggest relevant Nice classes based on goods/services description.

    Uses semantic embedding similarity against Nice class descriptions.
    Supports both Turkish and English input (multilingual model).

    **Example:**
    ```json
    {"description": "yazılım geliştirme ve mobil uygulama hizmetleri", "top_k": 5}
    ```

    Returns top matching classes with similarity scores.
    """
    import time
    import os
    import psycopg2
    import psycopg2.extras

    start_time = time.time()

    try:
        # Generate embedding for input description
        from ai import get_text_embedding_cached
        query_embedding = get_text_embedding_cached(request.description)

        # Connect to database
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Query pgvector for similar classes
        # Using cosine distance (<=>), lower is more similar
        # Similarity = 1 - distance
        cur.execute("""
            SELECT
                class_number,
                description,
                1 - (description_embedding <=> %s::halfvec) as similarity
            FROM nice_classes_lookup
            WHERE description_embedding IS NOT NULL
            ORDER BY description_embedding <=> %s::halfvec
            LIMIT %s
        """, (query_embedding, query_embedding, request.top_k))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        # Build response
        suggestions = []
        for row in rows:
            suggestions.append(SuggestedClass(
                class_number=row['class_number'],
                class_name=NICE_CLASS_NAMES.get(row['class_number'], f"Class {row['class_number']}"),
                similarity=round(float(row['similarity']), 4),
                description=row['description'][:200] + "..." if len(row['description']) > 200 else row['description']
            ))

        processing_time = (time.time() - start_time) * 1000

        return ClassSuggestionResponse(
            query=request.description[:100] + "..." if len(request.description) > 100 else request.description,
            suggestions=suggestions,
            processing_time_ms=round(processing_time, 2)
        )

    except Exception as e:
        logger.error(f"Class suggestion error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# Admin: Test Scoring Endpoint
# ==========================================

class TestScoringRequest(BaseModel):
    query: str = Field(..., description="Query trademark name", min_length=1, max_length=200)
    target: str = Field(..., description="Target trademark name to compare", min_length=1, max_length=200)
    include_details: bool = Field(True, description="Include breakdown of scoring factors")


class TestScoringResponse(BaseModel):
    query: str
    target: str
    final_score: float
    final_score_pct: str
    risk_level: dict
    factors: Optional[dict] = None


@app.post("/api/admin/test-scoring", response_model=TestScoringResponse, tags=["Admin"])
async def test_scoring(
    request: TestScoringRequest,
    current_user: CurrentUser = Depends(require_role(["owner", "admin"]))
):
    """
    Test the multi-factor scoring system with any two trademark names.

    This endpoint helps verify scoring behavior for:
    - Word boundary matching (e.g., "dogan" vs "erdogan")
    - Length ratio penalty
    - Distinctive word coverage
    - IDF weighting

    Example test cases:
    - "dogan patent" vs "erdogan patent ofisi" = LOW (prefix mismatch)
    - "dogan patent" vs "d.p dogan patent" = HIGH (distinctive match)
    - "nike" vs "nike sports" = HIGH (containment)
    """
    try:
        # Calculate comprehensive score with details
        result = calculate_comprehensive_score(
            request.query,
            request.target,
            include_details=request.include_details
        )

        # Result is always a dict with structure:
        # {'raw_score', 'final_score', 'factors': {...}, 'combined_factor', 'risk_level', 'details': {...}}
        final_score = result.get('final_score', 0)
        factor_data = result.get('factors', {})

        factors = {
            'raw_similarity': round(result.get('raw_score', 0), 4),
            'word_match_factor': round(factor_data.get('word_match', 0), 4),
            'length_ratio_factor': round(factor_data.get('length_ratio', 0), 4),
            'coverage_factor': round(factor_data.get('coverage', 0), 4),
            'idf_factor': round(factor_data.get('idf', 0), 4),
            'combined_factor': round(result.get('combined_factor', 0), 4),
        }

        # Add details if requested
        if request.include_details and 'details' in result:
            factors['word_details'] = result['details']

        risk = get_risk_level(final_score)

        return TestScoringResponse(
            query=request.query,
            target=request.target,
            final_score=round(final_score, 4),
            final_score_pct=f"{final_score * 100:.1f}%",
            risk_level=risk,
            factors=factors
        )

    except Exception as e:
        logger.error(f"Test scoring error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
