"""
Pipeline Management API — Admin-only endpoints
=================================================
POST /api/v1/pipeline/trigger       — Trigger full pipeline run
POST /api/v1/pipeline/trigger-step  — Trigger a single step
GET  /api/v1/pipeline/status        — Current status + recent history
GET  /api/v1/pipeline/runs/{run_id} — Detailed run results

Usage:
    from api.pipeline import router as pipeline_router
    app.include_router(pipeline_router)
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from auth.authentication import CurrentUser, require_role, require_superadmin
from config.settings import settings
from database.crud import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["Pipeline"])

# Valid pipeline step names
VALID_STEPS = ("download", "extract", "metadata", "embeddings", "ingest")

# Track currently running pipeline in-process (simple flag)
_running_run_id: Optional[str] = None
_running_step: Optional[str] = None


def _get_running_state() -> tuple:
    """Return (run_id, current_step) of in-process pipeline."""
    return _running_run_id, _running_step


def _set_running_state(run_id: Optional[str], step: Optional[str] = None):
    global _running_run_id, _running_step
    _running_run_id = run_id
    _running_step = step


def _compute_next_scheduled() -> Optional[str]:
    """Compute next scheduled full pipeline run based on config."""
    try:
        pipe = settings.pipeline
        day_name = getattr(pipe, "collection_schedule_day", "monday")
        hour = getattr(pipe, "collection_schedule_hour", 3)

        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        target_weekday = day_map.get(day_name.lower(), 0)

        now = datetime.utcnow()
        days_ahead = target_weekday - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and now.hour >= hour):
            days_ahead += 7

        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
        return next_run.isoformat()
    except Exception:
        return None


async def _run_pipeline_background(run_id: str, skip_download: bool, single_step: Optional[str] = None):
    """Background task that runs the pipeline and updates state."""
    try:
        _set_running_state(run_id, single_step or "starting")

        from workers.pipeline_worker import PipelineWorker

        worker = PipelineWorker()

        # Monkey-patch step methods to update current step indicator
        original_methods = {}
        step_names = {
            "run_step_download": "download",
            "run_step_extract": "extract",
            "run_step_metadata": "metadata",
            "run_step_embeddings": "embeddings",
            "run_step_ingest": "ingest",
        }
        for method_name, step_name in step_names.items():
            original = getattr(worker, method_name)
            original_methods[method_name] = original

            def make_wrapper(orig, sname):
                if asyncio.iscoroutinefunction(orig):
                    async def wrapper(*args, **kwargs):
                        _set_running_state(run_id, sname)
                        return await orig(*args, **kwargs)
                else:
                    def wrapper(*args, **kwargs):
                        _set_running_state(run_id, sname)
                        return orig(*args, **kwargs)
                return wrapper

            setattr(worker, method_name, make_wrapper(original, step_name))

        await worker.run_full_pipeline(
            skip_download=skip_download,
            triggered_by="api",
            single_step=single_step,
            run_id=run_id,
        )

    except Exception as e:
        logger.error(f"Background pipeline failed: {e}")
        # Update the API-created DB record so it doesn't stay stuck as 'running'
        try:
            with Database() as db:
                cur = db.cursor()
                cur.execute("""
                    UPDATE pipeline_runs
                    SET status = 'failed',
                        completed_at = NOW(),
                        error_message = %s
                    WHERE id = %s AND status = 'running'
                """, (f"Background task error: {e}", run_id))
                db.conn.commit()
        except Exception as db_err:
            logger.warning(f"Failed to update pipeline_runs on error: {db_err}")
    finally:
        _set_running_state(None, None)


# ==========================================
# POST /api/v1/pipeline/trigger
# ==========================================
@router.post("/trigger")
async def trigger_pipeline(
    skip_download: bool = Query(False, description="Skip download step"),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Trigger a full pipeline run in the background.
    Returns immediately with the run ID.
    Admin/owner only.
    """
    # Check if already running (in-process flag)
    current_run_id, _ = _get_running_state()
    if current_run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Pipeline zaten calisiyor",
                "run_id": current_run_id,
            },
        )

    # Also check DB for stuck 'running' records
    try:
        with Database() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id FROM pipeline_runs
                WHERE status = 'running'
                ORDER BY started_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            if row:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Pipeline zaten calisiyor (veritabaninda)",
                        "run_id": str(row["id"]),
                    },
                )
    except HTTPException:
        raise
    except Exception:
        pass  # DB not available, proceed anyway

    # Create pipeline_runs record
    import uuid
    run_id = str(uuid.uuid4())
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO pipeline_runs (id, status, triggered_by, skip_download, started_at)
                VALUES (%s, 'running', 'api', %s, NOW())
            """, (run_id, skip_download))
            db.conn.commit()
    except Exception as e:
        logger.warning(f"Failed to create pipeline_runs record: {e}")

    # Launch in background
    background_tasks.add_task(_run_pipeline_background, run_id, skip_download)

    logger.info(
        f"Pipeline triggered by {current_user.email} "
        f"(run_id={run_id}, skip_download={skip_download})"
    )

    return {
        "run_id": run_id,
        "status": "started",
        "skip_download": skip_download,
    }


# ==========================================
# POST /api/v1/pipeline/trigger-step
# ==========================================
@router.post("/trigger-step")
async def trigger_pipeline_step(
    step: str = Query(..., description="Step to run: download, extract, metadata, embeddings, ingest"),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Trigger a single pipeline step in the background.
    Admin/owner only. Useful for retrying a failed step.
    """
    if step not in VALID_STEPS:
        raise HTTPException(
            status_code=400,
            detail=f"Gecersiz adim: '{step}'. Gecerli adimlar: {', '.join(VALID_STEPS)}",
        )

    # Check if already running
    current_run_id, _ = _get_running_state()
    if current_run_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Pipeline zaten calisiyor",
                "run_id": current_run_id,
            },
        )

    # Create pipeline_runs record
    import uuid
    run_id = str(uuid.uuid4())
    try:
        with Database() as db:
            cur = db.cursor()
            cur.execute("""
                INSERT INTO pipeline_runs (id, status, triggered_by, skip_download, started_at)
                VALUES (%s, 'running', 'manual_step', %s, NOW())
            """, (run_id, step == "download"))
            db.conn.commit()
    except Exception as e:
        logger.warning(f"Failed to create pipeline_runs record: {e}")

    # Launch single step in background
    skip_download = step != "download"
    background_tasks.add_task(_run_pipeline_background, run_id, skip_download, step)

    logger.info(
        f"Pipeline step '{step}' triggered by {current_user.email} (run_id={run_id})"
    )

    return {
        "run_id": run_id,
        "status": "started",
        "step": step,
    }


# ==========================================
# GET /api/v1/pipeline/status
# ==========================================
@router.get("/status")
async def pipeline_status(
    limit: int = Query(10, ge=1, le=50, description="Number of recent runs"),
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """
    Returns recent pipeline runs and current status.
    Admin/owner only.
    """
    current_run_id, current_step = _get_running_state()

    recent_runs = []
    try:
        with Database() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id, status, triggered_by, skip_download,
                       step_download, step_extract, step_metadata,
                       step_embeddings, step_ingest,
                       total_downloaded, total_extracted, total_parsed,
                       total_embedded, total_ingested,
                       started_at, completed_at, duration_seconds,
                       error_message
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()

            for row in rows:
                run = {
                    "id": str(row["id"]),
                    "status": row["status"],
                    "triggered_by": row["triggered_by"],
                    "skip_download": row["skip_download"],
                    "step_download": row["step_download"],
                    "step_extract": row["step_extract"],
                    "step_metadata": row["step_metadata"],
                    "step_embeddings": row["step_embeddings"],
                    "step_ingest": row["step_ingest"],
                    "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                    "duration_seconds": row["duration_seconds"],
                    "error_message": row["error_message"],
                }
                recent_runs.append(run)
    except Exception as e:
        logger.warning(f"Failed to query pipeline_runs: {e}")

    # If DB says running but in-process flag is clear, it may be a stale record
    is_running = current_run_id is not None
    if not is_running and recent_runs and recent_runs[0]["status"] == "running":
        is_running = True
        current_step = None  # We don't know which step

    return {
        "is_running": is_running,
        "current_run_id": current_run_id,
        "current_step": current_step,
        "next_scheduled": _compute_next_scheduled(),
        "recent_runs": recent_runs,
    }


# ==========================================
# GET /api/v1/pipeline/runs/{run_id}
# ==========================================
@router.get("/runs/{run_id}")
async def pipeline_run_detail(
    run_id: str,
    current_user: CurrentUser = Depends(require_superadmin()),
):
    """Full detail for a specific pipeline run including per-step JSONB data."""
    try:
        with Database() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute("""
                SELECT id, status, triggered_by, skip_download,
                       step_download, step_extract, step_metadata,
                       step_embeddings, step_ingest,
                       total_downloaded, total_extracted, total_parsed,
                       total_embedded, total_ingested,
                       started_at, completed_at, duration_seconds,
                       error_message, created_at
                FROM pipeline_runs
                WHERE id = %s
            """, (run_id,))
            row = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Veritabani hatasi: {e}")

    if not row:
        raise HTTPException(status_code=404, detail="Pipeline calistirmasi bulunamadi")

    return {
        "id": str(row["id"]),
        "status": row["status"],
        "triggered_by": row["triggered_by"],
        "skip_download": row["skip_download"],
        "step_download": row["step_download"],
        "step_extract": row["step_extract"],
        "step_metadata": row["step_metadata"],
        "step_embeddings": row["step_embeddings"],
        "step_ingest": row["step_ingest"],
        "total_downloaded": row["total_downloaded"],
        "total_extracted": row["total_extracted"],
        "total_parsed": row["total_parsed"],
        "total_embedded": row["total_embedded"],
        "total_ingested": row["total_ingested"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "duration_seconds": row["duration_seconds"],
        "error_message": row["error_message"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
