"""
Trademark Risk Assessment System - Main Application
Multi-tenant API with authentication and watchlist monitoring
"""
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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

# Unified scoring engine — single source of truth for all search paths
from risk_engine import (
    score_pair,
    calculate_visual_similarity,
    get_risk_level as risk_get_risk_level,
    normalize_turkish as risk_normalize_turkish,
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
# Scoring functions imported from utils.idf_scoring (centralized) and risk_engine (score_pair)
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
    from generative_ai.gemini_client import get_gemini_client
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

    # Ensure payments table exists
    try:
        from migrations.run_payments_migration import ensure_payments_table
        if ensure_payments_table():
            logger.info("   Payments table ready")
        else:
            logger.warning("   Payments table migration skipped or failed (non-fatal)")
    except Exception as e:
        logger.warning(f"   Payments table check failed (non-fatal): {e}")

    # Ensure payment refund columns exist
    try:
        from migrations.run_add_payment_refunds import ensure_payment_refund_columns
        if ensure_payment_refund_columns():
            logger.info("   Payment refund columns ready")
        else:
            logger.warning("   Payment refund columns migration skipped or failed (non-fatal)")
    except Exception as e:
        logger.warning(f"   Payment refund columns check failed (non-fatal): {e}")

    # Initialize settings manager (in-memory cache for app_settings table)
    from utils.settings_manager import settings_manager
    try:
        from migrations.run_add_app_settings import ensure_app_settings_table
        ensure_app_settings_table()
        settings_manager.init()
        logger.info("   Settings manager ready")
    except Exception as e:
        logger.warning(f"   Settings manager init failed (non-fatal): {e}")

    # Seed default settings into app_settings (idempotent, won't overwrite)
    from utils.seed_settings import seed_default_settings
    try:
        seed_default_settings()
    except Exception as e:
        logger.warning(f"   Default settings seed failed (non-fatal): {e}")

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
    openapi_url="/openapi.json" if settings.debug else None,
    lifespan=lifespan
)

# GZip Middleware — compresses responses >500 bytes (critical for mobile bandwidth)
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS Middleware — restrict methods/headers to what's actually needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Accept-Language", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
    max_age=600,  # Cache preflight for 10 minutes
)


# Security Headers Middleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not settings.debug:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app.add_middleware(SecurityHeadersMiddleware)


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


# Global exception handler — never leak internal errors to users
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    content = {"detail": "Internal server error"}
    if settings.debug:
        content["debug_error"] = str(exc)
    return JSONResponse(status_code=500, content=content)


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
try:
    from api.attorneys import router as attorneys_router
except Exception as e:
    logger.warning(f"Could not load attorneys router: {e}")
    attorneys_router = None
from api.creative import router as creative_router
from api.pipeline import router as pipeline_router
from api.admin import router as admin_router
from api.billing import router as billing_router
try:
    from api.payments import router as payments_router
except Exception as e:
    logger.warning(f"Could not load payments router: {e}")
    payments_router = None
try:
    from api.applications import router as applications_router
except Exception as e:
    logger.warning(f"Could not load applications router: {e}")
    applications_router = None
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
if attorneys_router:
    app.include_router(attorneys_router, prefix="/api/v1")
app.include_router(usage_router, prefix="/api/v1")

# File upload routes
app.include_router(upload_router)

# Creative Suite (Name Generator + Logo Studio)
app.include_router(creative_router)

# Pipeline Management (admin-only)
app.include_router(pipeline_router)

# Superadmin panel (is_superadmin required)
app.include_router(admin_router)

# Billing (discount code validation)
app.include_router(billing_router)

# Payments (iyzico checkout)
if payments_router:
    app.include_router(payments_router)

# Trademark applications
if applications_router:
    app.include_router(applications_router)

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


# ==========================================
# Search credits — authenticated, returns plan info + reset dates
# ==========================================
@app.get("/api/v1/search/credits", tags=["Search"])
async def get_search_credits(current_user: CurrentUser = Depends(get_current_user)):
    """Return search credit info: plan display name and next reset date."""
    from utils.subscription import get_user_plan
    from database.crud import Database
    import datetime

    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))

    # Compute next daily reset (midnight tomorrow)
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return {
        "display_name": plan.get("display_name", plan.get("plan_name", "Free")),
        "resets_on": tomorrow.isoformat(),
    }


# ==========================================
# Public search endpoint — unauthenticated, rate-limited
# ==========================================
@app.get("/api/v1/search/public", tags=["Search"])
@limiter.limit("3/minute")
async def public_search(
    request: Request,
    query: str = Query(..., min_length=2, max_length=100, description="Trademark name to search"),
):
    """
    Public (unauthenticated) trademark search for landing page.
    Rate limited to 3 requests per minute per IP.
    Returns max 10 results with limited fields.
    """
    return await _do_public_search(query=query)


@app.post("/api/v1/search/public", tags=["Search"])
@limiter.limit("3/minute")
async def public_search_post(
    request: Request,
    query: Optional[str] = Form(None, max_length=100, description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual search"),
    classes: Optional[str] = Form(None, description="Nice classes, comma-separated (e.g. 9,35,42)"),
):
    """
    Public (unauthenticated) trademark search with optional image upload.
    At least one of query or image must be provided.
    Rate limited to 3 requests per minute per IP.
    Returns max 10 results with limited fields.
    """
    has_image = image is not None and image.filename
    has_query = query and len(query.strip()) >= 2
    if not has_query and not has_image:
        raise HTTPException(status_code=422, detail="Provide a brand name (min 2 chars) or upload a logo image")
    query = query.strip() if query else ""

    # Parse classes
    class_list = None
    if classes:
        try:
            class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
            class_list = [c for c in class_list if 1 <= c <= 45] or None
        except ValueError:
            class_list = None

    # Handle image upload
    temp_path = None
    has_image = image is not None and image.filename
    if has_image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid image type")
        content = await image.read()
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
        if not validate_image_magic_bytes(content):
            raise HTTPException(status_code=400, detail="Invalid image file content")
        # Verify PIL can actually parse it
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception:
            raise HTTPException(status_code=400, detail="Corrupted image file")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_file.write(content)
        temp_file.close()
        temp_path = temp_file.name

    try:
        return await _do_public_search(
            query=query,
            image_path=temp_path,
            nice_classes=class_list,
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


async def _do_public_search(
    query: str,
    image_path: str = None,
    nice_classes: list = None,
):
    """Shared implementation for GET and POST public search."""
    from agentic_search import AgenticTrademarkSearch

    try:
        with AgenticTrademarkSearch(
            confidence_threshold=0.75,
            auto_scrape=False
        ) as searcher:
            result = searcher.search(
                query=query,
                nice_classes=nice_classes,
                force_scrape=False,
                image_path=image_path,
            )

        # Deduplicate by normalized trademark name — keep highest-scoring entry per unique name
        all_results = result.get("results") or []
        seen_names = {}
        deduped_results = []
        # DEBUG: log first 15 raw results + any naki/nike entries
        for idx, r in enumerate(all_results):
            rn = (r.get("name") or "?")
            rs = (r.get("scores") or {}).get("total", 0)
            rpath = (r.get("scores") or {}).get("scoring_path", "")
            if idx < 15 or rn.lower() in ('naki', 'nike'):
                logger.info(f"  RAW[{idx}] name={rn!r} score={rs:.4f} path={rpath}")
        for r in all_results:
            raw_name = (r.get("trademark_name") or r.get("name") or "").strip().lower()
            score = (r.get("scores") or {}).get("total", 0)
            if raw_name not in seen_names or score > seen_names[raw_name]:
                if raw_name in seen_names:
                    # Remove the older, lower-scoring entry
                    deduped_results = [d for d in deduped_results if (d.get("trademark_name") or d.get("name") or "").strip().lower() != raw_name]
                seen_names[raw_name] = score
                deduped_results.append(r)
        # DEBUG: log first 15 deduped results
        for idx, r in enumerate(deduped_results[:15]):
            rn = (r.get("name") or "?")
            rs = (r.get("scores") or {}).get("total", 0)
            logger.info(f"  DEDUP[{idx}] name={rn!r} score={rs:.4f}")

        # Strip sensitive fields, return max 10 results
        safe_results = []
        for r in deduped_results[:10]:
            scores = r.get("scores") or {}
            # Build image URL if available
            img_path = r.get("image_path")
            image_url = f"/api/trademark-image/{img_path}" if img_path else None
            safe_results.append({
                "trademark_name": r.get("trademark_name") or r.get("name"),
                "application_no": r.get("application_no"),
                "status": r.get("status"),
                "risk_score": scores.get("total") if scores.get("total") is not None else r.get("risk_score", 0),
                "nice_classes": r.get("classes") or r.get("nice_classes") or [],
                "image_url": image_url,
                "name_tr": r.get("name_tr"),
                "holder_name": r.get("holder_name"),
                "holder_tpe_client_id": r.get("holder_tpe_client_id"),
                "attorney_name": r.get("attorney_name"),
                "attorney_no": r.get("attorney_no"),
                "application_date": r.get("application_date"),
                "registration_no": r.get("registration_no"),
                "scoring_path": scores.get("scoring_path"),
                "text_similarity": round(scores.get("text_similarity", 0), 3),
                "visual_similarity": round(scores.get("visual_similarity", 0), 3),
                "translation_similarity": round(scores.get("translation_similarity", 0), 3),
                "phonetic_similarity": round(scores.get("phonetic_similarity", 0), 3),
                "has_extracted_goods": r.get("has_extracted_goods", False),
                "extracted_goods": r.get("extracted_goods"),
            })

        return {
            "query": query,
            "results": safe_results,
            "total": len(safe_results),
        }
    except Exception as e:
        logger.error(f"Public search failed: {e}")
        raise HTTPException(status_code=500, detail="Search temporarily unavailable")


# Public portfolio endpoint — unauthenticated, rate-limited
@app.get("/api/v1/portfolio/public", tags=["Search"])
@limiter.limit("5/minute")
async def public_portfolio(
    request: Request,
    holder_id: str = Query(None, max_length=50, description="Holder TPE Client ID"),
    attorney_no: str = Query(None, max_length=50, description="Attorney number"),
):
    """
    Public portfolio lookup for landing page.
    Returns max 10 trademarks by holder or attorney.
    """
    if not holder_id and not attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    from database.crud import Database

    try:
        with Database() as db:
            cur = db.cursor()

            if holder_id:
                where_col = "holder_tpe_client_id"
                entity_type = "holder"
            else:
                where_col = "attorney_no"
                entity_type = "attorney"

            from psycopg2 import sql as psql
            param = holder_id or attorney_no

            # Get real total count
            cur.execute(
                psql.SQL("SELECT COUNT(*) as cnt FROM trademarks WHERE {} = %s").format(
                    psql.Identifier(where_col)
                ), (param,)
            )
            total_count = cur.fetchone()['cnt']

            # Get up to 100 trademarks for display + CSV
            cur.execute(psql.SQL("""
                SELECT application_no, name, current_status, nice_class_numbers,
                       application_date, image_path, holder_name, holder_tpe_client_id,
                       attorney_name, attorney_no, registration_no
                FROM trademarks
                WHERE {} = %s
                ORDER BY application_date DESC NULLS LAST
                LIMIT 100
            """).format(psql.Identifier(where_col)), (param,))

            rows = cur.fetchall()

            results = []
            for tm in rows:
                img_path = tm.get('image_path')
                image_url = f"/api/trademark-image/{img_path}" if img_path else None
                results.append({
                    "trademark_name": tm.get('name'),
                    "application_no": tm.get('application_no'),
                    "status": tm.get('current_status'),
                    "nice_classes": tm.get('nice_class_numbers') or [],
                    "image_url": image_url,
                    "holder_name": tm.get('holder_name'),
                    "holder_tpe_client_id": tm.get('holder_tpe_client_id'),
                    "attorney_name": tm.get('attorney_name'),
                    "attorney_no": tm.get('attorney_no'),
                    "application_date": tm['application_date'].isoformat() if tm.get('application_date') else None,
                    "registration_no": tm.get('registration_no'),
                })

            entity_name = ""
            if rows:
                if entity_type == "holder":
                    entity_name = rows[0].get('holder_name') or ""
                else:
                    entity_name = rows[0].get('attorney_name') or ""

            return {
                "entity_type": entity_type,
                "entity_name": entity_name,
                "entity_id": holder_id or attorney_no,
                "results": results,
                "total_count": total_count,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Public portfolio failed: {e}")
        raise HTTPException(status_code=500, detail="Portfolio lookup temporarily unavailable")


@app.get("/api/v1/portfolio/public/csv", tags=["Search"])
@limiter.limit("3/minute")
async def public_portfolio_csv(
    request: Request,
    holder_id: str = Query(None, max_length=50),
    attorney_no: str = Query(None, max_length=50),
):
    """Public CSV export — all trademarks by holder or attorney."""
    if not holder_id and not attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    import csv as _csv
    import io as _io
    from database.crud import Database

    try:
        with Database() as db:
            cur = db.cursor()
            from psycopg2 import sql as psql
            if holder_id:
                where_col, param = "holder_tpe_client_id", holder_id
            else:
                where_col, param = "attorney_no", attorney_no

            cur.execute(psql.SQL("""
                SELECT application_no, name, current_status,
                       nice_class_numbers, application_date, registration_date,
                       registration_no, holder_name, attorney_name, attorney_no,
                       bulletin_no, gazette_no
                FROM trademarks
                WHERE {} = %s
                ORDER BY application_date DESC NULLS LAST, application_no DESC
            """).format(psql.Identifier(where_col)), (param,))
            rows = cur.fetchall()

            # Determine entity name for filename
            if rows:
                entity_name = rows[0].get('holder_name' if holder_id else 'attorney_name') or param
            else:
                entity_name = param

        buf = _io.StringIO()
        buf.write('\ufeff')
        writer = _csv.writer(buf)
        writer.writerow(['Marka Adi', 'Basvuru No', 'Durum', 'Siniflar',
                         'Basvuru Tarihi', 'Tescil Tarihi', 'Tescil No',
                         'Sahip', 'Vekil', 'Vekil No', 'Bulten No', 'Gazete No'])
        for tm in rows:
            writer.writerow([
                tm.get('name') or '',
                tm.get('application_no') or '',
                tm.get('current_status') or '',
                '; '.join(str(c) for c in (tm.get('nice_class_numbers') or [])),
                tm['application_date'].isoformat() if tm.get('application_date') else '',
                tm['registration_date'].isoformat() if tm.get('registration_date') else '',
                tm.get('registration_no') or '',
                tm.get('holder_name') or '',
                tm.get('attorney_name') or '',
                tm.get('attorney_no') or '',
                tm.get('bulletin_no') or '',
                tm.get('gazette_no') or '',
            ])

        safe_name = ''.join(c if c.isascii() and (c.isalnum() or c in ' _-') else '_' for c in entity_name)[:50]
        buf.seek(0)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_portfolio.csv"'}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Public portfolio CSV failed: {e}")
        raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")


# Static files (avatars, etc.)
import os
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR / "avatars", exist_ok=True)


@app.get("/static/sw.js", tags=["Root"], include_in_schema=False)
async def serve_service_worker():
    """Serve SW with no-cache headers so browsers always check for updates."""
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 Templates
TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ==========================================
# Root & Health Endpoints
# ==========================================

@app.get("/", response_class=HTMLResponse, tags=["Root"])
async def root(request: Request):
    """Serve the landing page"""
    from utils.subscription import PLAN_FEATURES
    public_plans = {k: v for k, v in PLAN_FEATURES.items() if k != "superadmin"}
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "plans": public_plans,
    })


@app.get("/dashboard", response_class=HTMLResponse, tags=["Root"])
async def serve_dashboard(request: Request):
    """Serve the dashboard via Jinja2 templates"""
    response = templates.TemplateResponse("dashboard.html", {"request": request})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.get("/admin", response_class=HTMLResponse, tags=["Root"])
async def serve_admin(request: Request):
    """Serve admin panel. Full auth enforced client-side + API-side."""
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse, tags=["Root"])
async def serve_pricing(request: Request):
    """Serve the pricing page — renders limits dynamically from PLAN_FEATURES"""
    from utils.subscription import PLAN_FEATURES
    public_plans = {k: v for k, v in PLAN_FEATURES.items() if k != "superadmin"}
    return templates.TemplateResponse("pricing.html", {
        "request": request,
        "plans": public_plans,
    })


@app.get("/checkout", response_class=HTMLResponse, tags=["Root"])
async def serve_checkout(request: Request, plan: str = "free", billing: str = "monthly"):
    """Serve the checkout page — plan selection → register/login → pay"""
    from utils.subscription import PLAN_FEATURES
    public_plans = {k: v for k, v in PLAN_FEATURES.items() if k != "superadmin"}
    # Validate plan param
    if plan not in public_plans:
        plan = "free"
    if billing not in ("monthly", "annual"):
        billing = "monthly"
    return templates.TemplateResponse("checkout.html", {
        "request": request,
        "plans": public_plans,
        "selected_plan": plan,
        "selected_billing": billing,
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

# Project root — used for resolving relative image paths
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Legacy LOGOS directory (fallback for old bare-filename image_path values)
LOGOS_DIR = os.path.join(PROJECT_ROOT, "bulletins", "Marka", "LOGOS")

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
    """Resolve an image_path to an absolute filesystem path.

    Handles two formats:
    1. Full relative path (new): "bulletins/Marka/BLT_253/images/2011_41714.jpg"
       → resolved relative to PROJECT_ROOT
    2. Bare filename (legacy): "2011_41714"
       → looked up in LOGOS_DIR with extension probing
    """
    if not image_path:
        return None

    # Security: block directory traversal
    if ".." in image_path:
        return None

    # --- New format: full relative path (contains /) ---
    if "/" in image_path:
        full_path = os.path.join(PROJECT_ROOT, image_path.replace("/", os.sep))
        if os.path.isfile(full_path):
            return full_path
        # Path has extension already but file missing — try without extension in LOGOS
        basename = os.path.splitext(os.path.basename(image_path))[0]
        return _find_in_logos(basename)

    # --- Legacy format: bare filename (no slashes) ---
    return _find_in_logos(image_path)


def _find_in_logos(basename: str) -> str | None:
    """Try to find an image by bare filename in the LOGOS folder."""
    if not basename:
        return None
    # Try each extension
    for ext in IMAGE_EXTENSIONS:
        full_path = os.path.join(LOGOS_DIR, f"{basename}{ext}")
        if os.path.isfile(full_path):
            return full_path
    # Try as-is (might already have extension)
    full_path = os.path.join(LOGOS_DIR, basename)
    if os.path.isfile(full_path):
        return full_path
    return None


@app.get("/api/trademark-image/{image_path:path}", tags=["Images"])
async def serve_trademark_image(image_path: str):
    """
    Serve a trademark logo image.

    Accepts both formats:
    - New: /api/trademark-image/bulletins/Marka/BLT_253/images/2011_41714.jpg
    - Legacy: /api/trademark-image/2005_28311
    """
    file_path = find_trademark_image(image_path)
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

@app.post("/api/search-by-image", tags=["Image Search"])
@limiter.limit(lambda: _get_rl_value("rate_limit.public_search", "10/minute"))
async def search_by_image(
    request: Request,
    image: UploadFile = File(..., description="Aranacak logo/marka gorseli"),
    name: Optional[str] = Form(None, description="Optional trademark name for combined search"),
    classes: Optional[str] = Form(None, description="Nice siniflari (virgülle ayrilmis, orn: 9,35,42)"),
    limit: int = Form(MAX_RESULTS, description=f"Maksimum sonuc sayisi (max {MAX_RESULTS})")
):
    """
    Search for similar trademarks by uploading an image.
    Routes through score_pair() for unified scoring.

    Supports image-only and image+text (combined) modes.
    """
    import psycopg2
    import psycopg2.extras

    temp_path, pil_image = await process_uploaded_image(image)

    try:
        # Parse classes if provided
        class_list = []
        if classes:
            try:
                class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
                class_list = [c for c in class_list if (1 <= c <= 45) or c == GLOBAL_CLASS]
            except ValueError:
                pass

        use_unified = settings.use_unified_scoring

        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Check if we have any image embeddings
        cur.execute("SELECT COUNT(*) as cnt FROM trademarks WHERE image_embedding IS NOT NULL")
        embedding_count = cur.fetchone()['cnt']

        if embedding_count == 0:
            logger.warning("No image embeddings in database - returning sample results")
            class_filter = "AND (nice_class_numbers && %s::int[] OR 99 = ANY(nice_class_numbers))" if class_list else ""
            q = f"""
                SELECT id, name, application_no, current_status, nice_class_numbers,
                       bulletin_no, image_path
                FROM trademarks
                WHERE bulletin_no IS NOT NULL {class_filter}
                ORDER BY RANDOM() LIMIT %s
            """
            params = ([class_list, limit] if class_list else [limit])
            cur.execute(q, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                image_url = f"/api/trademark-image/{row['image_path']}" if row.get('image_path') else None
                results.append({
                    "id": str(row['id']),
                    "name": row['name'] or '-',
                    "application_no": row['application_no'],
                    "status": row['current_status'] or '-',
                    "nice_classes": row['nice_class_numbers'] or [],
                    "image_url": image_url,
                    "similarity": 0, "image_similarity": 0,
                    "risk_level": "unknown",
                    "note": "Gorsel embedding veritabaninda bulunamadi - ornek sonuclar"
                })
            cur.close()
            conn.close()
            return {
                "success": True, "search_type": "image",
                "warning": "Gorsel embeddingler henuz olusturulmamis. Ornek sonuclar gosteriliyor.",
                "total_results": len(results),
                "classes_filtered": class_list if class_list else None,
                "results": results
            }

        # =========================================================
        # ENCODE QUERY IMAGE — all visual vectors + OCR
        # =========================================================
        if use_unified:
            query_img_data = encode_query_image(temp_path)
            clip_vec_str = "[" + ",".join(str(x) for x in query_img_data['clip_vec']) + "]"
            dino_vec_str = "[" + ",".join(str(x) for x in query_img_data['dino_vec']) + "]" if query_img_data.get('dino_vec') else None
            query_ocr_text = query_img_data.get('ocr_text', '')
        else:
            # Legacy: CLIP only
            query_embedding = get_image_embedding_for_search(temp_path)
            clip_vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
            dino_vec_str = None
            query_ocr_text = ""
            try:
                query_ocr_text = extract_ocr_text(temp_path) or ""
            except Exception:
                pass

        # =========================================================
        # CANDIDATE RETRIEVAL — CLIP + DINOv2 merged set
        # =========================================================
        class_filter_sql = "AND (t.nice_class_numbers && %s::int[] OR 99 = ANY(t.nice_class_numbers))" if class_list else ""

        # Query 1: Top 100 by CLIP cosine distance
        clip_sql = f"""
            SELECT t.id, t.name, t.application_no, t.current_status, t.nice_class_numbers,
                   t.bulletin_no, t.image_path, t.logo_ocr_text, t.name_tr,
                   t.text_embedding, t.image_embedding, t.dinov2_embedding, t.color_histogram,
                   t.holder_name, t.holder_tpe_client_id,
                   t.attorney_name, t.attorney_no, t.registration_no,
                   t.application_date, t.expiry_date,
                   1 - (t.image_embedding <=> %s::halfvec) AS clip_sim
            FROM trademarks t
            WHERE t.image_embedding IS NOT NULL {class_filter_sql}
            ORDER BY t.image_embedding <=> %s::halfvec
            LIMIT 100
        """
        clip_params = [clip_vec_str]
        if class_list:
            clip_params.append(class_list)
        clip_params.append(clip_vec_str)
        cur.execute(clip_sql, clip_params)
        clip_rows = {str(r['id']): r for r in cur.fetchall()}

        # Query 2: Top 100 by DINOv2 cosine distance (if available)
        dino_rows = {}
        if use_unified and dino_vec_str:
            dino_sql = f"""
                SELECT t.id, t.name, t.application_no, t.current_status, t.nice_class_numbers,
                       t.bulletin_no, t.image_path, t.logo_ocr_text, t.name_tr,
                       t.text_embedding, t.image_embedding, t.dinov2_embedding, t.color_histogram,
                       t.holder_name, t.holder_tpe_client_id,
                       t.attorney_name, t.attorney_no, t.registration_no,
                       t.application_date, t.expiry_date,
                       1 - (t.dinov2_embedding <=> %s::halfvec) AS dino_sim
                FROM trademarks t
                WHERE t.dinov2_embedding IS NOT NULL {class_filter_sql}
                ORDER BY t.dinov2_embedding <=> %s::halfvec
                LIMIT 100
            """
            dino_params = [dino_vec_str]
            if class_list:
                dino_params.append(class_list)
            dino_params.append(dino_vec_str)
            cur.execute(dino_sql, dino_params)
            dino_rows = {str(r['id']): r for r in cur.fetchall()}

        # Merge both sets, deduplicate
        merged = {**dino_rows, **clip_rows}  # clip_rows takes priority for shared keys

        # If name provided, also encode text for combined scoring
        query_text_vec = None
        has_name = bool(name and name.strip())
        if has_name:
            from ai import get_text_embedding_cached
            query_text_vec = get_text_embedding_cached(name)

        cur.close()
        conn.close()

        # =========================================================
        # SCORE EACH CANDIDATE
        # =========================================================
        import numpy as np

        def _cosine(a, b_raw):
            if a is None or b_raw is None:
                return 0.0
            a_arr = np.array(a, dtype=np.float32)
            if isinstance(b_raw, str):
                b_arr = np.array([float(x) for x in b_raw.strip('[]').split(',')], dtype=np.float32)
            else:
                b_arr = np.array(list(b_raw), dtype=np.float32)
            dot = np.dot(a_arr, b_arr)
            norms = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
            return float(dot / norms) if norms > 0 else 0.0

        results = []
        for tid, row in merged.items():
            candidate_name = row['name'] or ""
            candidate_ocr = (row.get('logo_ocr_text') or "").strip()
            image_url = f"/api/trademark-image/{row['image_path']}" if row.get('image_path') else None

            if use_unified:
                # Compute visual sub-scores from full candidate data
                clip_sim = _cosine(query_img_data['clip_vec'], row.get('image_embedding'))
                dino_sim = _cosine(query_img_data.get('dino_vec'), row.get('dinov2_embedding'))
                color_sim = _cosine(query_img_data.get('color_vec'), row.get('color_histogram'))

                vis_sim = calculate_visual_similarity(
                    clip_sim=clip_sim, dinov2_sim=dino_sim, color_sim=color_sim,
                    ocr_text_a=query_ocr_text, ocr_text_b=candidate_ocr,
                )

                # Text signals (if name provided)
                text_sim = 0.0
                semantic_sim = 0.0
                phon_sim = 0.0
                if has_name:
                    from risk_engine import calculate_name_similarity
                    text_sim = calculate_name_similarity(name, candidate_name)
                    semantic_sim = _cosine(query_text_vec, row.get('text_embedding'))
                    # Simple phonetic: dmetaphone not available in Python, use text_sim > 0.8 as proxy
                    phon_sim = 0.0

                score_breakdown = score_pair(
                    query_name=name or "",
                    candidate_name=candidate_name,
                    text_sim=text_sim,
                    semantic_sim=semantic_sim,
                    visual_sim=vis_sim,
                    phonetic_sim=phon_sim,
                    candidate_translations={'name_tr': row.get('name_tr') or ''}
                )

                total = score_breakdown['total']

                results.append({
                    "id": tid,
                    "name": candidate_name or '-',
                    "name_tr": row.get('name_tr') or None,
                    "application_no": row['application_no'],
                    "status": row['current_status'] or '-',
                    "nice_classes": row['nice_class_numbers'] or [],
                    "image_url": image_url,
                    "application_date": str(row['application_date']) if row.get('application_date') else None,
                    "expiry_date": str(row['expiry_date']) if row.get('expiry_date') else None,
                    "similarity": round(total * 100, 1),
                    "image_similarity": round(clip_sim * 100, 1),
                    "visual_similarity": round(vis_sim * 100, 1),
                    "text_similarity": round(text_sim * 100, 1) if has_name else None,
                    "final_score": total,
                    "risk_level": risk_get_risk_level(total),
                    "scores": score_breakdown,
                })
            else:
                # --- LEGACY SCORING ---
                raw_image_sim = float(row.get('clip_sim', 0) or row.get('dino_sim', 0) or 0)
                final_score = calculate_visual_similarity(
                    clip_sim=raw_image_sim,
                    ocr_text_a=query_ocr_text,
                    ocr_text_b=candidate_ocr,
                )
                from difflib import SequenceMatcher
                ocr_sim = 0.0
                if query_ocr_text and candidate_ocr:
                    ocr_sim = SequenceMatcher(None, query_ocr_text.lower().strip(), candidate_ocr.lower().strip()).ratio()

                results.append({
                    "id": tid,
                    "name": candidate_name or '-',
                    "name_tr": row.get('name_tr') or None,
                    "application_no": row['application_no'],
                    "status": row['current_status'] or '-',
                    "nice_classes": row['nice_class_numbers'] or [],
                    "image_url": image_url,
                    "application_date": str(row['application_date']) if row.get('application_date') else None,
                    "expiry_date": str(row['expiry_date']) if row.get('expiry_date') else None,
                    "similarity": round(final_score * 100, 1),
                    "image_similarity": round(raw_image_sim * 100, 1),
                    "raw_image_score": round(raw_image_sim * 100, 1),
                    "ocr_boost": round(ocr_sim * 0.20 * 100, 1),
                    "ocr_similarity": round(ocr_sim * 100, 1),
                    "final_score": final_score,
                    "risk_level": risk_get_risk_level(final_score),
                })

        # Sort by score and limit
        results.sort(key=lambda x: x.get('final_score', 0), reverse=True)
        results = results[:limit]

        return {
            "success": True,
            "search_type": "combined" if has_name else "image",
            "scoring_engine": "unified" if use_unified else "legacy",
            "ocr_enabled": True,
            "query_ocr_text": query_ocr_text[:100] if query_ocr_text else None,
            "total_results": len(results),
            "classes_filtered": class_list if class_list else None,
            "results": results
        }

    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@app.get("/api/v1/status", tags=["Status"])
async def api_status():
    """API status with database statistics"""
    from database.crud import Database, get_db_connection

    try:
        with Database(get_db_connection()) as db:
            cur = db.cursor()

            # Total trademarks in the shared database (public info — not per-tenant)
            cur.execute("SELECT COUNT(*) FROM trademarks")
            trademark_count = cur.fetchone()['count']

            # Last bulletin date for freshness indicator
            cur.execute("SELECT MAX(bulletin_date) as latest FROM trademarks WHERE bulletin_date IS NOT NULL")
            row = cur.fetchone()
            last_bulletin = row['latest'].isoformat() if row and row['latest'] else None

            return {
                "status": "operational",
                "statistics": {
                    "total_trademarks": trademark_count,
                    "last_bulletin_date": last_bulletin
                },
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        return {
            "status": "error",
            "statistics": {"total_trademarks": 0, "last_bulletin_date": None},
            "timestamp": datetime.utcnow().isoformat()
        }


# ==========================================
# Simple Public Search (for landing page)
# ==========================================

from fastapi import Query
from typing import Optional, List
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Enhanced search request with auto class suggestion and optional image URL."""
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
    image_url: Optional[str] = Field(None, description="Optional image URL for combined text+image search")
    status: Optional[str] = Field(None, description="Filter by trademark status (e.g. Published, Registered)")
    attorney_no: Optional[str] = Field(None, description="Filter by attorney number")
    limit: int = Field(20, ge=1, le=100, description="Maximum number of results")



@app.get("/api/search/simple", tags=["Search"], deprecated=True)
@limiter.limit(lambda: _get_rl_value("rate_limit.public_search", "10/minute"))
async def simple_search(
    request: Request,
    q: str = Query(..., description="Trademark name to search"),
    limit: int = Query(MAX_RESULTS, ge=1, le=MAX_RESULTS, description="Number of results (max 10)"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    DEPRECATED: Use POST /api/search instead.
    Simple trademark search — internally redirects to enhanced_search with score_pair().
    """
    logger.warning("Deprecated /api/search/simple called — redirect to /api/search")
    search_req = SearchRequest(name=q, limit=limit)
    result = await enhanced_search(request, search_req)

    # Convert EnhancedSearchResponse to the old simple format
    simple_results = []
    for r in result.results:
        simple_results.append({
            "id": r.id,
            "name": r.name,
            "application_no": r.application_no,
            "nice_classes": r.nice_classes,
            "current_status": r.status,
            "application_date": r.application_date,
            "holder_name": r.owner,
            "bulletin_no": r.bulletin_no,
            "image_url": r.image_url,
            "score": round(r.similarity / 100, 4),
            "risk_level": risk_get_risk_level(r.similarity / 100),
        })

    response = JSONResponse(content={
        "query": q,
        "count": len(simple_results),
        "results": simple_results
    })
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "2026-03-10"
    return response


# ==========================================
# UNIFIED SEARCH (Form Data with Optional Image)
# ==========================================

@app.post("/api/search/unified", tags=["Unified Search"], deprecated=True)
@limiter.limit(lambda: _get_rl_value("rate_limit.public_search", "10/minute"))
async def unified_search(
    request: Request,
    name: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    classes: Optional[str] = Form(None),
    goods_description: Optional[str] = Form(None),
    limit: int = Form(MAX_RESULTS)
):
    """
    DEPRECATED: Use POST /api/search (text) or POST /api/search-by-image (image) instead.
    Internally redirects to the appropriate unified endpoint.
    """
    logger.warning("Deprecated /api/search/unified called — redirecting")

    has_image = image is not None and image.filename

    if has_image:
        # Redirect to image search endpoint
        result = await search_by_image(
            request=request, image=image, name=name, classes=classes, limit=limit
        )
        if isinstance(result, dict):
            resp = JSONResponse(content=result)
        else:
            resp = result
    elif name and name.strip():
        # Redirect to text search endpoint
        class_list = None
        if classes:
            try:
                class_list = [int(c.strip()) for c in classes.split(",") if c.strip()]
            except ValueError:
                class_list = None

        search_req = SearchRequest(
            name=name,
            classes=class_list,
            goods_description=goods_description,
            limit=limit
        )
        enhanced_result = await enhanced_search(request, search_req)

        # Convert EnhancedSearchResponse to unified format
        results_dicts = []
        for r in enhanced_result.results:
            results_dicts.append({
                "id": r.id, "name": r.name, "application_no": r.application_no,
                "status": r.status, "nice_classes": r.nice_classes,
                "bulletin_no": r.bulletin_no, "image_url": r.image_url,
                "similarity": r.similarity, "name_similarity": r.name_similarity,
                "risk_level": risk_get_risk_level(r.similarity / 100) if r.similarity else "low",
            })

        resp = JSONResponse(content={
            "success": True, "results": results_dicts,
            "search_type": "text",
            "search_context": {
                "searched_name": name,
                "searched_classes": class_list or [],
                "total_results": len(results_dicts),
                "search_time_ms": enhanced_result.search_time_ms,
            },
            "classes_were_auto_suggested": enhanced_result.classes_were_auto_suggested,
        })
    else:
        raise HTTPException(status_code=400, detail="Marka adi veya gorsel gerekli")

    if isinstance(resp, JSONResponse):
        resp.headers["Deprecation"] = "true"
        resp.headers["Sunset"] = "2026-03-10"
    return resp


# ==========================================
# LEGACY ROLLBACK ENDPOINTS (temporary, remove after 4 weeks)
# Force USE_UNIFIED_SCORING=false path for regression testing
# ==========================================

@app.post("/api/v1/search/legacy", tags=["Legacy"])
@limiter.limit(f"{settings.auth.api_rate_limit}/minute")
async def legacy_text_search(request: Request, search_request: SearchRequest):
    """
    Temporary legacy rollback endpoint for text search.
    Always uses calculate_comprehensive_score() regardless of feature flag.
    Remove after 2026-03-10.
    """
    import time
    import psycopg2
    import psycopg2.extras

    start_time = time.time()
    search_classes = search_request.classes or []

    try:
        conn = psycopg2.connect(
            host=settings.database.host, port=settings.database.port,
            database=settings.database.name, user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        name_normalized = normalize_turkish(search_request.name)
        normalize_sql = """
            LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
            'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
            'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
        """

        sql = f"""
            SELECT t.id, t.application_no, t.name, t.current_status,
                   t.nice_class_numbers, t.application_date, t.registration_date,
                   t.bulletin_no, t.image_path, t.holder_name,
                   t.holder_tpe_client_id, t.attorney_name, t.attorney_no,
                   t.registration_no
            FROM trademarks t
            WHERE LOWER(t.name) LIKE LOWER(%s)
                OR {normalize_sql} LIKE LOWER(%s)
                OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                OR similarity({normalize_sql}, LOWER(%s)) > 0.2
            ORDER BY GREATEST(
                similarity(LOWER(t.name), LOWER(%s)),
                similarity({normalize_sql}, LOWER(%s))
            ) DESC LIMIT 100
        """
        params = [f'%{search_request.name}%', f'%{name_normalized}%',
                  search_request.name, name_normalized,
                  search_request.name, name_normalized]

        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        results = []
        for row in rows:
            target_name = row['name'] or ""
            scoring = calculate_comprehensive_score(search_request.name, target_name)
            score = scoring['final_score']
            results.append({
                "id": str(row['id']), "name": row['name'],
                "application_no": row['application_no'],
                "status": row['current_status'] or 'Bilinmiyor',
                "nice_classes": row['nice_class_numbers'] or [],
                "similarity": round(score * 100, 1),
                "risk_level": scoring['risk_level'],
                "scoring_engine": "legacy",
            })

        results.sort(key=lambda x: x['similarity'], reverse=True)
        results = results[:MAX_RESULTS]

        return {
            "query": search_request.name,
            "scoring_engine": "legacy",
            "total_results": len(results),
            "search_time_ms": round((time.time() - start_time) * 1000, 2),
            "results": results
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# Enhanced Search with Auto Class Suggestion
# ==========================================

# Pydantic models for enhanced search
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
    holder_tpe_client_id: Optional[str] = Field(None, description="Holder TPE Client ID")
    attorney: Optional[str] = Field(None, description="Patent attorney/representative name")
    attorney_no: Optional[str] = Field(None, description="Patent attorney number (unique ID)")
    registration_no: Optional[str] = Field(None, description="Registration number")

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
    Routes all scoring through score_pair() for consistent results.

    Supports text-only and text+image (via image_url) modes.
    """
    import time
    import psycopg2
    import psycopg2.extras
    from ai import get_text_embedding_cached

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
            suggestions = get_class_suggestions_internal(
                goods_description=search_request.goods_description,
                trademark_name=search_request.name,
                limit=5
            )
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

    # =========================================================
    # FEATURE FLAG: unified scoring vs legacy
    # =========================================================
    use_unified = settings.use_unified_scoring

    try:
        conn = psycopg2.connect(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        name_normalized = normalize_turkish(search_request.name)

        # SQL function to normalize Turkish characters in database names
        normalize_sql = """
            LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
            REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
            'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
            'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))
        """

        # =========================================================
        # CANDIDATE RETRIEVAL (3-stage funnel)
        # Fetch text_embedding, name_tr, logo_ocr_text + visual vectors
        # =========================================================
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
                t.attorney_name,
                t.attorney_no,
                t.registration_no,
                t.name_tr,
                t.logo_ocr_text,
                t.text_embedding,
                t.image_embedding,
                t.dinov2_embedding,
                t.color_histogram,
                GREATEST(
                    similarity(LOWER(t.name), LOWER(%s)),
                    similarity({normalize_sql}, LOWER(%s)),
                    CASE WHEN LOWER(t.name) LIKE LOWER(%s) THEN 0.9 ELSE 0 END,
                    CASE WHEN {normalize_sql} LIKE LOWER(%s) THEN 0.9 ELSE 0 END
                ) as score,
                (dmetaphone(t.name) = dmetaphone(%s)) as phonetic_match
            FROM trademarks t
        """

        where_clause = f"""
            WHERE (
                LOWER(t.name) LIKE LOWER(%s)
                OR {normalize_sql} LIKE LOWER(%s)
                OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                OR similarity({normalize_sql}, LOWER(%s)) > 0.2
            )
        """

        base_params = [
            search_request.name, name_normalized,
            f'%{search_request.name}%', f'%{name_normalized}%',
            search_request.name,
        ]
        where_params = [
            f'%{search_request.name}%', f'%{name_normalized}%',
            search_request.name, name_normalized,
        ]

        if search_classes:
            where_clause += " AND (t.nice_class_numbers && %s::integer[] OR 99 = ANY(t.nice_class_numbers))"
            where_params.append(search_classes)

        if search_request.status:
            where_clause += " AND t.current_status = %s"
            where_params.append(search_request.status)

        if search_request.attorney_no:
            where_clause += " AND t.attorney_no = %s"
            where_params.append(search_request.attorney_no)

        order_limit = " ORDER BY score DESC, t.name LIMIT 100"

        cur.execute(base_select + where_clause + order_limit, base_params + where_params)
        rows = cur.fetchall()

        # =========================================================
        # ENCODE QUERY VECTORS
        # =========================================================
        query_text_vec = get_text_embedding_cached(search_request.name)

        # Optional image encoding for combined search
        query_img_data = None
        if search_request.image_url and use_unified:
            try:
                # Download image from URL to temp file
                import urllib.request
                temp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                urllib.request.urlretrieve(search_request.image_url, temp_img.name)
                temp_img.close()
                query_img_data = encode_query_image(temp_img.name)
                os.unlink(temp_img.name)
            except Exception as e:
                logger.warning(f"Image URL processing failed: {e}")

        # =========================================================
        # SCORE EACH CANDIDATE
        # =========================================================
        searched_classes_set = set(search_classes) if search_classes else set()
        results = []

        for row in rows:
            result_classes = row['nice_class_numbers'] or []
            overlap_count = len(searched_classes_set.intersection(set(result_classes))) if searched_classes_set else 0
            target_name = row['name'] or ""

            if use_unified:
                # --- UNIFIED SCORING via score_pair() ---
                pg_text_sim = float(row['score']) if row['score'] else 0.0
                phon_match = bool(row.get('phonetic_match'))

                # Semantic similarity (text embedding cosine)
                semantic_sim = 0.0
                cand_text_emb = row.get('text_embedding')
                if query_text_vec and cand_text_emb:
                    try:
                        import numpy as np
                        q_arr = np.array(query_text_vec if isinstance(query_text_vec, list) else list(query_text_vec), dtype=np.float32)
                        # pgvector returns string like '[0.1,0.2,...]', parse it
                        if isinstance(cand_text_emb, str):
                            cand_arr = np.array([float(x) for x in cand_text_emb.strip('[]').split(',')], dtype=np.float32)
                        else:
                            cand_arr = np.array(list(cand_text_emb), dtype=np.float32)
                        dot = np.dot(q_arr, cand_arr)
                        norms = np.linalg.norm(q_arr) * np.linalg.norm(cand_arr)
                        semantic_sim = float(dot / norms) if norms > 0 else 0.0
                    except Exception:
                        semantic_sim = 0.0

                # Visual similarity (only if query has image)
                vis_sim = 0.0
                if query_img_data:
                    try:
                        import numpy as np

                        def _cosine(a, b_raw):
                            if a is None or b_raw is None:
                                return 0.0
                            a_arr = np.array(a, dtype=np.float32)
                            if isinstance(b_raw, str):
                                b_arr = np.array([float(x) for x in b_raw.strip('[]').split(',')], dtype=np.float32)
                            else:
                                b_arr = np.array(list(b_raw), dtype=np.float32)
                            dot = np.dot(a_arr, b_arr)
                            norms = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
                            return float(dot / norms) if norms > 0 else 0.0

                        clip_sim = _cosine(query_img_data['clip_vec'], row.get('image_embedding'))
                        dino_sim = _cosine(query_img_data['dino_vec'], row.get('dinov2_embedding'))
                        color_sim = _cosine(query_img_data['color_vec'], row.get('color_histogram'))
                        candidate_ocr = (row.get('logo_ocr_text') or "").strip()

                        vis_sim = calculate_visual_similarity(
                            clip_sim=clip_sim,
                            dinov2_sim=dino_sim,
                            color_sim=color_sim,
                            ocr_text_a=query_img_data.get('ocr_text', ''),
                            ocr_text_b=candidate_ocr,
                        )
                    except Exception:
                        vis_sim = 0.0

                score_breakdown = score_pair(
                    query_name=search_request.name,
                    candidate_name=target_name,
                    text_sim=pg_text_sim,
                    semantic_sim=semantic_sim,
                    visual_sim=vis_sim,
                    phonetic_sim=1.0 if phon_match else 0.0,
                    candidate_translations={'name_tr': row.get('name_tr') or ''}
                )

                score_val = score_breakdown['total']
                similarity_pct = round(score_val * 100, 1)

            else:
                # --- LEGACY SCORING via calculate_comprehensive_score() ---
                scoring = calculate_comprehensive_score(search_request.name, target_name)
                score_val = scoring['final_score']
                similarity_pct = round(score_val * 100, 1)

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
                holder_tpe_client_id=row.get('holder_tpe_client_id'),
                attorney=row.get('attorney_name'),
                attorney_no=row.get('attorney_no'),
                registration_no=row.get('registration_no'),
                bulletin_no=row.get('bulletin_no'),
                image_url=get_image_url(row.get('image_path'), row['application_no'], row.get('bulletin_no')),
                similarity=similarity_pct,
                name_similarity=similarity_pct,
                class_overlap_count=overlap_count
            ))

        cur.close()
        conn.close()

        # Re-sort by score and limit
        results.sort(key=lambda x: x.similarity, reverse=True)
        total_found = len(results)
        results = results[:MAX_RESULTS]

        search_time = (time.time() - start_time) * 1000

        search_context = SearchContext(
            searched_name=search_request.name,
            searched_classes=search_classes,
            goods_description=search_request.goods_description,
            total_results=len(results),
            search_time_ms=round(search_time, 2)
        )

        return EnhancedSearchResponse(
            results=results,
            search_context=search_context,
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
    lang: str = Field("tr", description="Language for class names: tr, en, ar")


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

# Turkish Nice Class names (proper Turkish characters)
NICE_CLASS_NAMES_TR = {
    1: "Kimyasallar",
    2: "Boyalar",
    3: "Kozmetikler",
    4: "Yağlar ve Yakıtlar",
    5: "Eczacılık Ürünleri",
    6: "Metaller",
    7: "Makineler",
    8: "El Aletleri",
    9: "Bilgisayar ve Elektronik",
    10: "Tıbbi Cihazlar",
    11: "Aydınlatma ve Isıtma",
    12: "Taşıtlar",
    13: "Ateşli Silahlar",
    14: "Mücevherat",
    15: "Müzik Aletleri",
    16: "Kağıt ve Ofis",
    17: "Kauçuk ve Plastik",
    18: "Deri Ürünleri",
    19: "Yapı Malzemeleri",
    20: "Mobilya",
    21: "Ev Eşyaları",
    22: "Halatlar ve Çadırlar",
    23: "İplikler",
    24: "Tekstil",
    25: "Giyim",
    26: "Aksesuarlar",
    27: "Halılar",
    28: "Oyunlar ve Oyuncaklar",
    29: "Et ve Süt Ürünleri",
    30: "Gıda Ürünleri",
    31: "Tarım Ürünleri",
    32: "İçecekler",
    33: "Alkollü İçecekler",
    34: "Tütün",
    35: "Reklamcılık",
    36: "Sigortacılık ve Finans",
    37: "İnşaat",
    38: "Telekomünikasyon",
    39: "Taşımacılık",
    40: "Üretim",
    41: "Eğitim ve Eğlence",
    42: "Bilimsel ve Teknolojik Hizmetler",
    43: "Yiyecek ve Konaklama",
    44: "Tıbbi Hizmetler",
    45: "Hukuki Hizmetler",
    99: "Global Marka (Tüm Sınıflar)"  # Özel: tüm 45 sınıfı kapsar
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
                class_name=(NICE_CLASS_NAMES_TR if request.lang == "tr" else NICE_CLASS_NAMES).get(row['class_number'], f"Class {row['class_number']}"),
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
