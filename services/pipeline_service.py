"""Service helpers for pipeline management routes."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import BackgroundTasks, HTTPException
from psycopg2.extras import RealDictCursor

from config.settings import settings
from database.crud import Database
from workers.pipeline_launcher import launch_pipeline_process

logger = logging.getLogger(__name__)

VALID_STEPS = (
    "download",
    "extract",
    "metadata",
    "embeddings",
    "ingest",
    "repair",
    "event_ingest",
    "final_status_repair",
)
PIPELINE_HEARTBEAT_GRACE_PERIOD = timedelta(minutes=20)
PIPELINE_LEGACY_RUNNING_GRACE_PERIOD = timedelta(hours=12)

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


def _normalize_pipeline_timestamp(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize DB timestamps so stale-run comparisons work with or without tzinfo."""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _is_running_row_stale(row, *, now: Optional[datetime] = None) -> bool:
    """Return True when a running row no longer has a credible liveness signal."""
    comparison_time = _normalize_pipeline_timestamp(now or datetime.now(timezone.utc))
    heartbeat_at = _normalize_pipeline_timestamp(row.get("heartbeat_at"))
    started_at = _normalize_pipeline_timestamp(row.get("started_at"))
    duration_seconds = row.get("duration_seconds")
    current_step = row.get("current_step")

    # Legacy rows can pick up a fresh heartbeat_at when the column is added later.
    # If a run is old and still never reported a step or elapsed time, treat it as stale.
    if (
        started_at is not None
        and current_step is None
        and duration_seconds is None
        and started_at <= comparison_time - PIPELINE_LEGACY_RUNNING_GRACE_PERIOD
    ):
        return True

    reference_time = heartbeat_at or started_at
    if reference_time is None:
        return True

    grace_period = (
        PIPELINE_HEARTBEAT_GRACE_PERIOD
        if heartbeat_at is not None
        else PIPELINE_LEGACY_RUNNING_GRACE_PERIOD
    )
    return reference_time <= comparison_time - grace_period


def _stale_run_error_message(row) -> str:
    """Build a consistent technical reason for stale run reconciliation."""
    if row.get("heartbeat_at"):
        return (
            "Run marked failed after pipeline heartbeat stopped. "
            "The worker likely exited or the app restarted before completion."
        )
    return (
        "Run marked failed after remaining in running state without a heartbeat. "
        "The worker likely exited before completion or before heartbeat tracking was available."
    )


def _mark_pipeline_run_failed(
    *,
    run_id: str,
    error_message: str,
    db_factory=Database,
    service_logger=None,
):
    """Persist a hard pipeline failure for an already-created run."""
    pipeline_logger = service_logger or logger

    try:
        with db_factory() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed',
                    current_step = NULL,
                    completed_at = NOW(),
                    heartbeat_at = NOW(),
                    duration_seconds = COALESCE(
                        duration_seconds,
                        EXTRACT(EPOCH FROM (NOW() - started_at))
                    ),
                    error_message = %s
                WHERE id = %s AND status = 'running'
            """,
                (error_message, run_id),
            )
            db.conn.commit()
    except Exception as exc:
        pipeline_logger.warning("Failed to mark pipeline run %s as failed: %s", run_id, exc)


def _reconcile_stale_running_runs(
    *,
    db_factory=Database,
    service_logger=None,
):
    """Mark stale running rows as failed so the dashboard reflects reality."""
    pipeline_logger = service_logger or logger

    try:
        with db_factory() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT id, started_at, heartbeat_at, current_step, duration_seconds, error_message
                FROM pipeline_runs
                WHERE status = 'running'
                ORDER BY started_at DESC
            """
            )
            rows = cur.fetchall()

            stale_rows = [row for row in rows if _is_running_row_stale(row)]
            for row in stale_rows:
                cur.execute(
                    """
                    UPDATE pipeline_runs
                    SET status = 'failed',
                        current_step = NULL,
                        completed_at = NOW(),
                        heartbeat_at = COALESCE(heartbeat_at, NOW()),
                        duration_seconds = COALESCE(
                            duration_seconds,
                            EXTRACT(EPOCH FROM (NOW() - started_at))
                        ),
                        error_message = COALESCE(NULLIF(error_message, ''), %s)
                    WHERE id = %s AND status = 'running'
                """,
                    (_stale_run_error_message(row), row["id"]),
                )

            if stale_rows:
                db.conn.commit()
                pipeline_logger.warning(
                    "Marked %s stale pipeline run(s) as failed: %s",
                    len(stale_rows),
                    ", ".join(str(row["id"]) for row in stale_rows),
                )
    except Exception as exc:
        pipeline_logger.warning("Failed to reconcile stale pipeline runs: %s", exc)


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
            "run_step_repair": "repair",
            "run_step_event_ingest": "event_ingest",
            "run_step_final_status_repair": "final_status_repair",
            "run_step_conflict_scan": "conflict_scan",
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
        _mark_pipeline_run_failed(
            run_id=run_id,
            error_message=f"Background task error: {exc}",
            db_factory=db_factory,
            service_logger=pipeline_logger,
        )
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
    process_launcher=launch_pipeline_process,
    service_logger=None,
):
    """Trigger a full pipeline run in a detached worker process."""
    pipeline_logger = service_logger or logger
    current_run_id, _ = state_getter()
    if current_run_id:
        raise HTTPException(
            status_code=409,
            detail={"message": "Pipeline zaten calisiyor", "run_id": current_run_id},
        )

    _reconcile_stale_running_runs(
        db_factory=db_factory,
        service_logger=pipeline_logger,
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
                INSERT INTO pipeline_runs (
                    id,
                    status,
                    triggered_by,
                    skip_download,
                    started_at,
                    heartbeat_at,
                    current_step
                )
                VALUES (%s, 'running', 'api', %s, NOW(), NOW(), 'starting')
            """,
                (run_id, skip_download),
            )
            db.conn.commit()
    except Exception as exc:
        pipeline_logger.warning("Failed to create pipeline_runs record: %s", exc)

    try:
        process_launcher(
            triggered_by="api",
            run_id=run_id,
            skip_download=skip_download,
            single_step=None,
            service_logger=pipeline_logger,
        )
    except Exception as exc:
        _mark_pipeline_run_failed(
            run_id=run_id,
            error_message=f"Failed to launch detached pipeline worker: {exc}",
            db_factory=db_factory,
            service_logger=pipeline_logger,
        )
        raise HTTPException(
            status_code=500,
            detail="Pipeline worker sureci baslatilamadi",
        )

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
    process_launcher=launch_pipeline_process,
    service_logger=None,
):
    """Trigger a single pipeline step in a detached worker process."""
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

    _reconcile_stale_running_runs(
        db_factory=db_factory,
        service_logger=pipeline_logger,
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
                INSERT INTO pipeline_runs (
                    id,
                    status,
                    triggered_by,
                    skip_download,
                    started_at,
                    heartbeat_at,
                    current_step
                )
                VALUES (%s, 'running', 'manual_step', %s, NOW(), NOW(), %s)
            """,
                (run_id, step == "download", step),
            )
            db.conn.commit()
    except Exception as exc:
        pipeline_logger.warning("Failed to create pipeline_runs record: %s", exc)

    skip_download = step != "download"
    try:
        process_launcher(
            triggered_by="api",
            run_id=run_id,
            skip_download=skip_download,
            single_step=step,
            service_logger=pipeline_logger,
        )
    except Exception as exc:
        _mark_pipeline_run_failed(
            run_id=run_id,
            error_message=f"Failed to launch detached pipeline worker: {exc}",
            db_factory=db_factory,
            service_logger=pipeline_logger,
        )
        raise HTTPException(
            status_code=500,
            detail="Pipeline worker sureci baslatilamadi",
        )

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

    _reconcile_stale_running_runs(
        db_factory=db_factory,
        service_logger=pipeline_logger,
    )

    try:
        with db_factory() as db:
            cur = db.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                """
                SELECT id, status, triggered_by, skip_download,
                       step_download, step_extract, step_metadata,
                       step_embeddings, step_ingest, step_repair, step_event_ingest, step_final_status_repair,
                       total_downloaded, total_extracted, total_parsed,
                       total_embedded, total_ingested, total_repaired, total_event_scopes_ingested, total_final_status_repaired,
                       started_at, completed_at, duration_seconds,
                       error_message, heartbeat_at, current_step
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
                        "step_repair": row["step_repair"],
                        "step_event_ingest": row["step_event_ingest"],
                        "step_final_status_repair": row["step_final_status_repair"],
                        "total_repaired": row["total_repaired"],
                        "total_event_scopes_ingested": row["total_event_scopes_ingested"],
                        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                        "completed_at": row["completed_at"].isoformat()
                        if row["completed_at"]
                        else None,
                        "duration_seconds": row["duration_seconds"],
                        "error_message": row["error_message"],
                        "current_step": row["current_step"],
                    }
                )
    except Exception as exc:
        pipeline_logger.warning("Failed to query pipeline_runs: %s", exc)

    is_running = current_run_id is not None
    if not is_running and recent_runs and recent_runs[0]["status"] == "running":
        is_running = True
        current_run_id = recent_runs[0]["id"]
        current_step = recent_runs[0].get("current_step")

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
                       step_embeddings, step_ingest, step_repair, step_event_ingest, step_final_status_repair,
                       total_downloaded, total_extracted, total_parsed,
                       total_embedded, total_ingested, total_repaired, total_event_scopes_ingested, total_final_status_repaired,
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
        "step_repair": row["step_repair"],
        "step_event_ingest": row["step_event_ingest"],
        "step_final_status_repair": row["step_final_status_repair"],
        "total_downloaded": row["total_downloaded"],
        "total_extracted": row["total_extracted"],
        "total_parsed": row["total_parsed"],
        "total_embedded": row["total_embedded"],
        "total_ingested": row["total_ingested"],
        "total_repaired": row["total_repaired"],
        "total_event_scopes_ingested": row["total_event_scopes_ingested"],
        "total_final_status_repaired": row["total_final_status_repaired"],
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
