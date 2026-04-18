"""Service helpers for pipeline management routes."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import BackgroundTasks, HTTPException
from psycopg2.extras import RealDictCursor

from config.settings import settings
from database.crud import Database

logger = logging.getLogger(__name__)

VALID_STEPS = ("download", "extract", "metadata", "embeddings", "ingest")

_running_run_id: Optional[str] = None
_running_step: Optional[str] = None


def get_running_state() -> tuple[Optional[str], Optional[str]]:
    """Return the in-process pipeline run and current step."""
    return _running_run_id, _running_step


def set_running_state(run_id: Optional[str], step: Optional[str] = None):
    """Update the in-process pipeline run and current step."""
    global _running_run_id, _running_step
    _running_run_id = run_id
    _running_step = step


def compute_next_scheduled(settings_obj=settings) -> Optional[str]:
    """Compute the next scheduled full pipeline run from config."""
    try:
        pipe = settings_obj.pipeline
        day_name = getattr(pipe, "collection_schedule_day", "monday")
        hour = getattr(pipe, "collection_schedule_hour", 3)

        day_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target_weekday = day_map.get(day_name.lower(), 0)

        now = datetime.utcnow()
        days_ahead = target_weekday - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and now.hour >= hour):
            days_ahead += 7

        next_run = now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(
            days=days_ahead
        )
        return next_run.isoformat()
    except Exception:
        return None


async def run_pipeline_background(
    run_id: str,
    skip_download: bool,
    single_step: Optional[str] = None,
    *,
    worker_factory=None,
    db_factory=Database,
    state_setter=set_running_state,
    service_logger=None,
):
    """Run the pipeline in the background and keep the in-process state updated."""
    pipeline_logger = service_logger or logger

    try:
        state_setter(run_id, single_step or "starting")

        factory = worker_factory
        if factory is None:
            from workers.pipeline_worker import PipelineWorker

            factory = PipelineWorker

        worker = factory()
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

            def make_wrapper(orig, current_step_name):
                if asyncio.iscoroutinefunction(orig):
                    async def wrapper(*args, **kwargs):
                        state_setter(run_id, current_step_name)
                        return await orig(*args, **kwargs)
                else:
                    def wrapper(*args, **kwargs):
                        state_setter(run_id, current_step_name)
                        return orig(*args, **kwargs)

                return wrapper

            setattr(worker, method_name, make_wrapper(original, step_name))

        await worker.run_full_pipeline(
            skip_download=skip_download,
            triggered_by="api",
            single_step=single_step,
            run_id=run_id,
        )
    except Exception as exc:
        pipeline_logger.error("Background pipeline failed: %s", exc)
        try:
            with db_factory() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE pipeline_runs
                    SET status = 'failed',
                        completed_at = NOW(),
                        error_message = %s
                    WHERE id = %s AND status = 'running'
                """,
                    (f"Background task error: {exc}", run_id),
                )
                db.conn.commit()
        except Exception as db_err:
            pipeline_logger.warning("Failed to update pipeline_runs on error: %s", db_err)
    finally:
        state_setter(None, None)


async def trigger_pipeline_run_data(
    *,
    skip_download: bool,
    background_tasks: BackgroundTasks | None,
    current_user,
    state_getter=get_running_state,
    db_factory=Database,
    run_id_factory=None,
    background_runner=run_pipeline_background,
    service_logger=None,
):
    """Trigger a full pipeline run in the background."""
    pipeline_logger = service_logger or logger
    current_run_id, _ = state_getter()
    if current_run_id:
        raise HTTPException(
            status_code=409,
            detail={"message": "Pipeline zaten calisiyor", "run_id": current_run_id},
        )

    try:
        with db_factory() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT id FROM pipeline_runs
                WHERE status = 'running'
                ORDER BY started_at DESC LIMIT 1
            """
            )
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
        pass

    make_run_id = run_id_factory or (lambda: str(uuid.uuid4()))
    run_id = make_run_id()

    try:
        with db_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO pipeline_runs (id, status, triggered_by, skip_download, started_at)
                VALUES (%s, 'running', 'api', %s, NOW())
            """,
                (run_id, skip_download),
            )
            db.conn.commit()
    except Exception as exc:
        pipeline_logger.warning("Failed to create pipeline_runs record: %s", exc)

    background_queue = background_tasks or BackgroundTasks()
    background_queue.add_task(background_runner, run_id, skip_download)

    pipeline_logger.info(
        "Pipeline triggered by %s (run_id=%s, skip_download=%s)",
        getattr(current_user, "email", "unknown"),
        run_id,
        skip_download,
    )
    return {
        "run_id": run_id,
        "status": "started",
        "skip_download": skip_download,
    }


async def trigger_pipeline_step_data(
    *,
    step: str,
    background_tasks: BackgroundTasks | None,
    current_user,
    state_getter=get_running_state,
    db_factory=Database,
    run_id_factory=None,
    background_runner=run_pipeline_background,
    service_logger=None,
):
    """Trigger a single pipeline step in the background."""
    pipeline_logger = service_logger or logger
    if step not in VALID_STEPS:
        raise HTTPException(
            status_code=400,
            detail=f"Gecersiz adim: '{step}'. Gecerli adimlar: {', '.join(VALID_STEPS)}",
        )

    current_run_id, _ = state_getter()
    if current_run_id:
        raise HTTPException(
            status_code=409,
            detail={"message": "Pipeline zaten calisiyor", "run_id": current_run_id},
        )

    make_run_id = run_id_factory or (lambda: str(uuid.uuid4()))
    run_id = make_run_id()
    try:
        with db_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                INSERT INTO pipeline_runs (id, status, triggered_by, skip_download, started_at)
                VALUES (%s, 'running', 'manual_step', %s, NOW())
            """,
                (run_id, step == "download"),
            )
            db.conn.commit()
    except Exception as exc:
        pipeline_logger.warning("Failed to create pipeline_runs record: %s", exc)

    skip_download = step != "download"
    background_queue = background_tasks or BackgroundTasks()
    background_queue.add_task(background_runner, run_id, skip_download, step)

    pipeline_logger.info(
        "Pipeline step '%s' triggered by %s (run_id=%s)",
        step,
        getattr(current_user, "email", "unknown"),
        run_id,
    )
    return {
        "run_id": run_id,
        "status": "started",
        "step": step,
    }


async def get_pipeline_status_data(
    *,
    limit: int,
    current_user,
    state_getter=get_running_state,
    db_factory=Database,
    next_scheduled_getter=compute_next_scheduled,
    service_logger=None,
):
    """Return the current pipeline status and recent run history."""
    pipeline_logger = service_logger or logger
    current_run_id, current_step = state_getter()
    recent_runs = []

    try:
        with db_factory() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
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
            """,
                (limit,),
            )
            rows = cur.fetchall()

            for row in rows:
                recent_runs.append(
                    {
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
                        "completed_at": row["completed_at"].isoformat()
                        if row["completed_at"]
                        else None,
                        "duration_seconds": row["duration_seconds"],
                        "error_message": row["error_message"],
                    }
                )
    except Exception as exc:
        pipeline_logger.warning("Failed to query pipeline_runs: %s", exc)

    is_running = current_run_id is not None
    if not is_running and recent_runs and recent_runs[0]["status"] == "running":
        is_running = True
        current_step = None

    return {
        "is_running": is_running,
        "current_run_id": current_run_id,
        "current_step": current_step,
        "next_scheduled": next_scheduled_getter(),
        "recent_runs": recent_runs,
    }


async def get_pipeline_run_detail_data(
    *,
    run_id: str,
    current_user,
    db_factory=Database,
):
    """Return full detail for a specific pipeline run."""
    try:
        with db_factory() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT id, status, triggered_by, skip_download,
                       step_download, step_extract, step_metadata,
                       step_embeddings, step_ingest,
                       total_downloaded, total_extracted, total_parsed,
                       total_embedded, total_ingested,
                       started_at, completed_at, duration_seconds,
                       error_message, created_at
                FROM pipeline_runs
                WHERE id = %s
            """,
                (run_id,),
            )
            row = cur.fetchone()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Veritabani hatasi: {exc}")

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


_get_running_state = get_running_state
_set_running_state = set_running_state
_compute_next_scheduled = compute_next_scheduled
_run_pipeline_background = run_pipeline_background
