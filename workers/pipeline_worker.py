"""
Trademark Data Pipeline Worker
================================
Orchestrates the full 7-step data pipeline:

1. data_collection.py - Download bulletin archives from TURKPATENT
2. zip.py            - Extract archives to structured folders
3. metadata.py       - Parse HSQLDB SQL files -> metadata.json
4. pipeline.ai       - Generate embeddings -> enrich metadata.json in-place
5. pipeline.ingest   - Load enriched metadata.json into PostgreSQL
6. ingest_events.py  - Reconcile event timelines + materialize event state
7. universal_scanner - Scan within-deadline bulletins for opposition conflicts

Usage:
    # Run full pipeline (all scheduled steps)
    python -m workers.pipeline_worker

    # Skip download step (process existing archives)
    python -m workers.pipeline_worker --skip-download

    # Run a single step
    python -m workers.pipeline_worker --step embeddings

    # Reuse an existing pipeline_runs row created by the API trigger
    python -m workers.pipeline_worker --triggered-by api --run-id <run-id>
"""

import os
import sys
import time
import json
import logging
import asyncio
import argparse
import threading
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from uuid import uuid4

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [PIPELINE] - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

PIPELINE_HEARTBEAT_INTERVAL_SECONDS = 60


# ===================== Data Models =====================

@dataclass
class StepResult:
    step_name: str              # download, extract, metadata, embeddings, ingest, event_ingest
    status: str = "pending"     # success, partial, failed, skipped
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineResult:
    status: str = "running"     # success, partial, failed
    steps: List[StepResult] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "total_duration_seconds": self.total_duration_seconds,
        }


# ===================== Database Helpers =====================

def _get_db_connection():
    """Get a direct database connection for pipeline tracking."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD", ""),
        connect_timeout=30,
    )


def _ensure_pipeline_runs_table(conn):
    """Create pipeline_runs table if it doesn't exist."""
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                triggered_by VARCHAR(50) DEFAULT 'schedule',
                skip_download BOOLEAN DEFAULT FALSE,
                step_download JSONB,
                step_extract JSONB,
                step_metadata JSONB,
                step_embeddings JSONB,
                step_ingest JSONB,
                step_event_ingest JSONB,
                step_final_status_repair JSONB,
                total_downloaded INTEGER DEFAULT 0,
                total_extracted INTEGER DEFAULT 0,
                total_parsed INTEGER DEFAULT 0,
                total_embedded INTEGER DEFAULT 0,
                total_ingested INTEGER DEFAULT 0,
                total_event_scopes_ingested INTEGER DEFAULT 0,
                total_final_status_repaired INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                heartbeat_at TIMESTAMP DEFAULT NOW(),
                current_step VARCHAR(50),
                duration_seconds FLOAT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ALTER COLUMN heartbeat_at SET DEFAULT NOW()
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS current_step VARCHAR(50)
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS step_event_ingest JSONB
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS total_event_scopes_ingested INTEGER DEFAULT 0
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS step_final_status_repair JSONB
        """
        )
        cur.execute(
            """
            ALTER TABLE pipeline_runs
            ADD COLUMN IF NOT EXISTS total_final_status_repaired INTEGER DEFAULT 0
        """
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _create_pipeline_run(conn, triggered_by: str, skip_download: bool) -> str:
    """Create a new pipeline_runs record. Returns the run_id."""
    run_id = str(uuid4())
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pipeline_runs (
                id,
                status,
                triggered_by,
                skip_download,
                started_at,
                heartbeat_at,
                current_step
            )
            VALUES (%s, 'running', %s, %s, NOW(), NOW(), 'starting')
        """, (run_id, triggered_by, skip_download))
        conn.commit()
        return run_id
    except Exception as e:
        conn.rollback()
        logger.warning(f"Failed to create pipeline run record: {e}")
        return run_id


def _touch_pipeline_run(conn, run_id: str, current_step: Optional[str]):
    """Refresh pipeline liveness for the active run."""
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pipeline_runs
            SET heartbeat_at = NOW(),
                current_step = %s,
                duration_seconds = EXTRACT(EPOCH FROM (NOW() - started_at))
            WHERE id = %s AND status = 'running'
        """,
            (current_step, run_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"Failed to refresh pipeline liveness: {e}")


def _heartbeat_pipeline_run(
    run_id: str,
    stop_event: threading.Event,
    current_step_ref: dict,
):
    """Keep the run row fresh while long-running pipeline work is executing."""
    while not stop_event.wait(PIPELINE_HEARTBEAT_INTERVAL_SECONDS):
        conn = None
        try:
            conn = _get_db_connection()
            _ensure_pipeline_runs_table(conn)
            _touch_pipeline_run(conn, run_id, current_step_ref.get("value"))
        except Exception as e:
            logger.warning(f"Pipeline heartbeat update failed: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


def _update_pipeline_run(conn, run_id: str, result: PipelineResult):
    """Update pipeline_runs record with final results."""
    try:
        cur = conn.cursor()

        # Collect per-step JSONB and aggregate counts
        step_data = {}
        totals = {
            "downloaded": 0, "extracted": 0,
            "parsed": 0, "embedded": 0, "ingested": 0,
            "event_scopes_ingested": 0,
            "final_status_repaired": 0,
        }
        step_column_map = {
            "download": ("step_download", "downloaded"),
            "extract": ("step_extract", "extracted"),
            "metadata": ("step_metadata", "parsed"),
            "embeddings": ("step_embeddings", "embedded"),
            "ingest": ("step_ingest", "ingested"),
            "event_ingest": ("step_event_ingest", "event_scopes_ingested"),
            "final_status_repair": ("step_final_status_repair", "final_status_repaired"),
        }

        for step in result.steps:
            col, total_key = step_column_map.get(step.step_name, (None, None))
            if col:
                step_data[col] = json.dumps(step.to_dict())
                totals[total_key] = step.processed

        # Build error message from failed steps
        error_parts = [
            f"{s.step_name}: {s.error}"
            for s in result.steps if s.status == "failed" and s.error
        ]
        error_message = "; ".join(error_parts) if error_parts else None

        cur.execute("""
            UPDATE pipeline_runs SET
                status = %s,
                step_download = %s,
                step_extract = %s,
                step_metadata = %s,
                step_embeddings = %s,
                step_ingest = %s,
                step_event_ingest = %s,
                step_final_status_repair = %s,
                total_downloaded = %s,
                total_extracted = %s,
                total_parsed = %s,
                total_embedded = %s,
                total_ingested = %s,
                total_event_scopes_ingested = %s,
                total_final_status_repaired = %s,
                completed_at = NOW(),
                heartbeat_at = NOW(),
                current_step = NULL,
                duration_seconds = %s,
                error_message = %s
            WHERE id = %s
        """, (
            result.status,
            step_data.get("step_download"),
            step_data.get("step_extract"),
            step_data.get("step_metadata"),
            step_data.get("step_embeddings"),
            step_data.get("step_ingest"),
            step_data.get("step_event_ingest"),
            step_data.get("step_final_status_repair"),
            totals["downloaded"],
            totals["extracted"],
            totals["parsed"],
            totals["embedded"],
            totals["ingested"],
            totals["event_scopes_ingested"],
            totals["final_status_repaired"],
            result.total_duration_seconds,
            error_message,
            run_id,
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"Failed to update pipeline run record: {e}")


# ===================== Notification Helper =====================

def _send_failure_notification(result: PipelineResult, run_id: str):
    """Send email notification on pipeline failure."""
    try:
        from notifications.service import EmailService
        email_svc = EmailService()

        failed_steps = [s for s in result.steps if s.status == "failed"]
        step_summary = "\n".join(
            f"  - {s.step_name}: {s.status} ({s.error or 'unknown error'})"
            for s in result.steps
        )

        subject = f"Pipeline Failed: {', '.join(s.step_name for s in failed_steps)}"
        text_body = (
            f"Pipeline run {run_id} completed with status: {result.status}\n"
            f"Duration: {result.total_duration_seconds:.0f}s\n\n"
            f"Step results:\n{step_summary}"
        )
        html_body = f"""
        <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
            <h2 style="color: #dc3545;">Pipeline {result.status.upper()}</h2>
            <p><strong>Run ID:</strong> {run_id}</p>
            <p><strong>Duration:</strong> {result.total_duration_seconds:.0f}s</p>
            <table style="width: 100%; border-collapse: collapse; background: white; margin-top: 15px;">
                <tr style="background: #f1f1f1;">
                    <th style="padding: 8px; text-align: left;">Step</th>
                    <th style="padding: 8px; text-align: left;">Status</th>
                    <th style="padding: 8px; text-align: left;">Processed</th>
                    <th style="padding: 8px; text-align: left;">Duration</th>
                </tr>
                {"".join(
                    f'<tr><td style="padding: 8px; border-top: 1px solid #eee;">{s.step_name}</td>'
                    f'<td style="padding: 8px; border-top: 1px solid #eee; color: {"#dc3545" if s.status == "failed" else "#28a745" if s.status == "success" else "#ffc107"};">{s.status}</td>'
                    f'<td style="padding: 8px; border-top: 1px solid #eee;">{s.processed}</td>'
                    f'<td style="padding: 8px; border-top: 1px solid #eee;">{s.duration_seconds:.0f}s</td></tr>'
                    for s in result.steps
                )}
            </table>
            {"".join(
                f'<div style="margin-top: 10px; padding: 10px; background: #fff3cd; border-radius: 4px;">'
                f'<strong>{s.step_name}:</strong> {s.error}</div>'
                for s in failed_steps if s.error
            )}
        </div>
        </body></html>
        """

        # Send to admin (from_email acts as admin inbox)
        from config.settings import settings
        admin_email = settings.email.smtp_user or settings.email.from_email
        if admin_email:
            email_svc.send_email(admin_email, subject, html_body, text_body)
            logger.info(f"Failure notification sent to {admin_email}")

    except Exception as e:
        logger.warning(f"Failed to send failure notification: {e}")


# ===================== Pipeline Worker =====================

class PipelineWorker:
    """
    Orchestrates the full 5-step trademark data pipeline.

    Each step is independent and callable individually.
    The full pipeline tracks progress in the pipeline_runs table.
    """

    def __init__(self):
        try:
            from config.settings import settings
            self.pipeline_settings = settings.pipeline
        except ImportError:
            self.pipeline_settings = None
            logger.warning("Config not available, using defaults")

    # ---- Step 1: Download ----

    async def run_step_download(self) -> StepResult:
        """Step 1: Download new bulletins via data_collection.py"""
        result = StepResult(step_name="download")
        t0 = time.time()

        try:
            logger.info("[Step 1/7] Starting bulletin download...")
            from data_collection import run_collection
            summary = await run_collection(settings=self.pipeline_settings)

            result.processed = summary.get("downloaded_raw", summary.get("downloaded", 0))
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("download_failed", 0) + summary.get("scrape_failed", 0)
            partial_issues = summary.get("partial_issues", 0)

            if result.failed > 0 and result.processed == 0 and summary.get("scraped", 0) == 0:
                result.status = "failed"
                result.error = "All collection sources failed"
            elif result.failed > 0 or partial_issues > 0:
                result.status = "partial"
                result.error = (
                    f"download_failed={summary.get('download_failed', 0)}, "
                    f"scrape_failed={summary.get('scrape_failed', 0)}, "
                    f"partial_issues={partial_issues}"
                )
            else:
                result.status = "success"
            logger.info(
                f"[Step 1/7] Download complete: "
                f"raw={summary.get('downloaded_raw', 0)}, scraped={summary.get('scraped', 0)}, "
                f"partial_issues={partial_issues}, skipped={result.skipped}"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 1/7] Download failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 2: Extract ----

    def run_step_extract(self) -> StepResult:
        """Step 2: Extract archives, parse PDFs, and write events.json."""
        result = StepResult(step_name="extract")
        t0 = time.time()

        try:
            # --- 2a: ZIP archive extraction (legacy) ---
            logger.info("[Step 2/7] Starting archive extraction...")
            from zip import run_extraction
            summary = run_extraction(settings=self.pipeline_settings)

            result.processed = summary.get("extracted", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)

            logger.info(
                f"[Step 2/7] ZIP extraction: "
                f"{result.processed} extracted, {result.skipped} skipped, "
                f"{result.failed} failed"
            )

            # --- 2b: PDF bulletin extraction (new format) ---
            logger.info("[Step 2/7] Starting PDF bulletin extraction...")
            root_dir = None
            if self.pipeline_settings:
                root_dir = Path(getattr(self.pipeline_settings, "bulletins_root", "bulletins/Marka"))
            try:
                from pdf_extract import run_pdf_extraction

                pdf_summary = run_pdf_extraction(root_dir=root_dir)
                pdf_processed = pdf_summary.get("processed", 0)
                pdf_failed = pdf_summary.get("failed", 0)
                pdf_records = pdf_summary.get("total_records", 0)

                result.processed += pdf_processed
                result.failed += pdf_failed

                if pdf_processed > 0:
                    logger.info(
                        f"[Step 2/7] PDF extraction: "
                        f"{pdf_processed} bulletin(s), {pdf_records} records"
                    )
                elif pdf_failed > 0:
                    logger.warning(f"[Step 2/7] PDF extraction: {pdf_failed} failed")
                else:
                    logger.info("[Step 2/7] No new PDF bulletins to extract")
            except ImportError:
                logger.warning("[Step 2/7] pdf_extract module not available (PyMuPDF not installed)")
            except Exception as e:
                logger.error(f"[Step 2/7] PDF extraction error: {e}")
                result.failed += 1

            # --- 2c: PDF event extraction (BLT/GZ supplementary sections) ---
            logger.info("[Step 2/7] Starting PDF event extraction...")
            try:
                from pdf_extract_events import run_event_extraction

                event_summary = run_event_extraction(root_dir=root_dir, settings=self.pipeline_settings)
                event_processed = event_summary.get("processed", 0)
                event_failed = event_summary.get("failed", 0)
                event_total = event_summary.get("total_events", 0)

                result.processed += event_processed
                result.failed += event_failed

                if event_processed > 0:
                    logger.info(
                        f"[Step 2/7] PDF event extraction: "
                        f"{event_processed} folder(s), {event_total} events"
                    )
                elif event_failed > 0:
                    logger.warning(f"[Step 2/7] PDF event extraction: {event_failed} failed")
                else:
                    logger.info("[Step 2/7] No new PDF events to extract")
            except ImportError:
                logger.warning("[Step 2/7] pdf_extract_events module not available")
            except Exception as e:
                logger.error(f"[Step 2/7] PDF event extraction error: {e}")
                result.failed += 1

            # --- Final status ---
            if result.failed > 0 and result.processed == 0:
                result.status = "failed"
                result.error = f"All extractions failed"
            elif result.failed > 0:
                result.status = "partial"
                result.error = f"{result.failed} extraction(s) failed"
            else:
                result.status = "success"

            logger.info(
                f"[Step 2/7] Extraction complete: "
                f"{result.processed} total, {result.skipped} skipped, "
                f"{result.failed} failed"
            )

        except RuntimeError as e:
            if "CRITICAL" in str(e):
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 2/7] Critical extraction failure: {e}")
            else:
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 2/7] Extraction failed: {e}")
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 2/7] Extraction failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 3: Metadata ----

    def run_step_metadata(self) -> StepResult:
        """Step 3: Parse SQL to metadata.json via metadata.py"""
        result = StepResult(step_name="metadata")
        t0 = time.time()

        try:
            logger.info("[Step 3/7] Starting metadata extraction...")
            from metadata import merge_scraped_sidecars, run_metadata

            root_dir = None
            if self.pipeline_settings:
                root_dir = Path(getattr(self.pipeline_settings, "bulletins_root", "bulletins/Marka"))

            summary = run_metadata(root_dir=root_dir, settings=self.pipeline_settings)
            merge_summary = merge_scraped_sidecars(root_dir=root_dir, verbose=True)

            result.processed = summary.get("processed", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)
            result.processed += merge_summary.get("folders_merged", 0)
            result.skipped += merge_summary.get("skipped", 0) + merge_summary.get("pending", 0)
            result.failed += merge_summary.get("failed", 0)

            if result.failed > 0 and result.processed == 0:
                result.status = "failed"
                result.error = f"All metadata extractions failed"
            elif result.failed > 0:
                result.status = "partial"
                result.error = f"{result.failed} metadata extraction(s) failed"
            else:
                result.status = "success"

            logger.info(
                f"[Step 3/7] Metadata complete: "
                f"parsed={summary.get('processed', 0)}, "
                f"merged={merge_summary.get('folders_merged', 0)}, "
                f"pending_scrape_only={merge_summary.get('pending', 0)}, "
                f"{result.failed} failed"
            )

        except RuntimeError as e:
            if "CANARY" in str(e).upper():
                result.status = "failed"
                result.error = f"Canary failure: {e}"
                logger.error(f"[Step 3/7] Canary failure (aborting): {e}")
            else:
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 3/7] Metadata failed: {e}")
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 3/7] Metadata failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 4: Embeddings ----

    def run_step_embeddings(self) -> StepResult:
        """
        Step 4: Generate embeddings and enrich metadata.json via pipeline.ai.

        Reads each metadata.json, generates:
          - CLIP embeddings (512-dim) for logo images
          - DINOv2 embeddings (768-dim) for logo images
          - MiniLM text embeddings (384-dim) for trademark names
          - Color histograms for logo images
          - OCR text extraction for logo images
        Then writes all embeddings BACK INTO the same metadata.json.

        This is the slowest step (GPU-bound).
        On failure: pipeline continues - ingest can load records without embeddings.
        """
        result = StepResult(step_name="embeddings")
        t0 = time.time()

        try:
            logger.info("[Step 4/7] Starting embedding generation (GPU)...")
            logger.info("[Step 4/7] Loading AI models (CLIP, DINOv2, MiniLM)...")

            # Deferred import - pipeline.ai loads CUDA models at import time
            from pipeline.ai import run_embedding_generation
            summary = run_embedding_generation(settings=self.pipeline_settings)

            result.processed = summary.get("processed", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)

            if result.failed > 0 and result.processed == 0:
                result.status = "failed"
                result.error = f"All embedding generations failed"
            elif result.failed > 0:
                result.status = "partial"
                result.error = f"{result.failed} folder(s) failed"
            else:
                result.status = "success"

            logger.info(
                f"[Step 4/7] Embeddings complete: "
                f"{result.processed} folders processed, {result.skipped} skipped, "
                f"{result.failed} failed | Duration: {time.time() - t0:.0f}s"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 4/7] Embedding generation failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 5: Ingest ----

    def run_step_ingest(self) -> StepResult:
        """
        Step 5: Load enriched metadata.json into PostgreSQL via pipeline.ingest.

        Reads metadata.json files that now contain embeddings from step 4.
        Inserts/updates trademarks with vector embeddings into PostgreSQL.
        """
        result = StepResult(step_name="ingest")
        t0 = time.time()

        try:
            logger.info("[Step 5/7] Starting database ingestion...")
            from pipeline.ingest import run_ingest
            summary = run_ingest(settings=self.pipeline_settings)

            result.processed = summary.get("inserted", 0) + summary.get("updated", 0)
            result.skipped = summary.get("skipped", 0)
            result.status = "success"

            logger.info(
                f"[Step 5/7] Ingestion complete: "
                f"{summary.get('inserted', 0)} inserted, "
                f"{summary.get('updated', 0)} updated, "
                f"{result.skipped} skipped"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 5/7] Ingestion failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    def run_step_event_ingest(self) -> StepResult:
        """
        Step 6: Reconcile events.json into trademark_events and event-derived trademark state.

        This preserves the full ingest_events.py behavior:
          - scope reconciliation into trademark_events
          - chronological materialization onto trademarks
          - final_status recomputation
          - event-based watchlist alerts
        """
        result = StepResult(step_name="event_ingest")
        t0 = time.time()

        root_dir = None
        if self.pipeline_settings:
            root_dir = Path(getattr(self.pipeline_settings, "bulletins_root", "bulletins/Marka"))

        from ingest_events import run_event_ingest

        summary = None
        for attempt in range(2):
            try:
                if attempt == 0:
                    logger.info("[Step 6/7] Starting event ingestion...")
                else:
                    logger.warning("[Step 6/7] Retrying event ingestion after hard failure...")

                summary = run_event_ingest(root_dir=root_dir)
                result.processed = summary.get("processed", 0)
                result.skipped = summary.get("skipped", 0)
                result.failed = summary.get("failed", 0)

                if summary.get("status") == "failed":
                    raise RuntimeError(summary.get("error") or "Event ingest failed")
                if summary.get("status") == "partial":
                    result.status = "partial"
                    result.error = summary.get("error") or "Event ingest completed with folder or alert failures"
                else:
                    result.status = "success"

                logger.info(
                    "[Step 6/7] Event ingestion complete: scopes=%s, skipped=%s, failed=%s, alerts=%s",
                    result.processed,
                    result.skipped,
                    result.failed,
                    summary.get("alerts_generated"),
                )
                break
            except Exception as e:
                if attempt == 0:
                    logger.error(f"[Step 6/7] Event ingestion hard-failed: {e}")
                    continue
                result.status = "failed"
                result.failed = max(result.failed, 1)
                result.error = str(e)
                logger.error(f"[Step 6/7] Event ingestion failed after retry: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    def run_step_final_status_repair(self) -> StepResult:
        """
        Maintenance step: reconcile final_status* across the full trademarks table.

        This is intentionally manual-only and does not run as part of normal full pipelines.
        """
        result = StepResult(step_name="final_status_repair")
        t0 = time.time()

        conn = None
        try:
            logger.info("[Maintenance] Starting final status repair...")
            conn = _get_db_connection()

            from utils.status_reconciler import repair_final_statuses

            summary = repair_final_statuses(conn)
            result.processed = summary.get("processed", 0)
            result.skipped = 0
            result.failed = 0
            result.status = "success"

            logger.info(
                "[Maintenance] Final status repair complete: %s processed across %s batches",
                summary.get("processed", 0),
                summary.get("batches", 0),
            )
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Maintenance] Final status repair failed: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 7: Conflict Scan (Opposition Radar) ----

    def run_step_conflict_scan(self) -> StepResult:
        """
        Step 7: Scan within-deadline bulletins for opposition conflicts.

        Uses the UniversalScanner to find conflicts between new trademark
        applications (within appeal deadline) and existing registered marks.
        Results populate the Opposition Radar lead feed.
        """
        result = StepResult(step_name="conflict_scan")
        t0 = time.time()

        try:
            logger.info("[Step 7/7] Starting conflict scan (Opposition Radar)...")

            conn = _get_db_connection()
            try:
                from workers.universal_scanner import UniversalScanner
                scanner = UniversalScanner(conn=conn)

                # Get distinct bulletins with active appeal deadlines
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT DISTINCT bulletin_no
                        FROM trademarks
                        WHERE appeal_deadline IS NOT NULL
                          AND appeal_deadline >= CURRENT_DATE
                          AND bulletin_no IS NOT NULL
                          AND name IS NOT NULL AND length(name) >= 2
                        ORDER BY bulletin_no DESC
                    """)
                    bulletins = [r['bulletin_no'] for r in cur.fetchall()]

                if not bulletins:
                    logger.info("[Step 7/7] No bulletins with active appeal deadlines")
                    result.status = "success"
                    result.duration_seconds = round(time.time() - t0, 1)
                    return result

                logger.info(f"[Step 7/7] Scanning {len(bulletins)} bulletin(s): {bulletins}")

                total_conflicts = 0
                total_scanned = 0
                errors = 0

                for bno in bulletins:
                    try:
                        summary = scanner.scan_bulletin(bno)
                        total_scanned += summary['trademarks_scanned']
                        total_conflicts += summary['total_conflicts']
                        errors += summary['errors']
                    except Exception as e:
                        logger.error(f"[Step 7/7] Bulletin {bno} scan failed: {e}")
                        errors += 1
                        try:
                            conn.rollback()
                        except Exception:
                            pass

                result.processed = total_conflicts
                result.skipped = total_scanned - total_conflicts
                result.failed = errors

                if errors > 0 and total_conflicts == 0:
                    result.status = "failed"
                    result.error = f"All bulletin scans failed ({errors} errors)"
                elif errors > 0:
                    result.status = "partial"
                    result.error = f"{errors} error(s) during scan"
                else:
                    result.status = "success"

                logger.info(
                    f"[Step 7/7] Conflict scan complete: "
                    f"{total_scanned} trademarks scanned, "
                    f"{total_conflicts} conflicts found, {errors} errors"
                )

                scanner.close()
            except Exception:
                conn.close()
                raise

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 7/7] Conflict scan failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Full Pipeline ----

    async def run_full_pipeline(
        self,
        skip_download: bool = False,
        triggered_by: str = "manual",
        single_step: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> PipelineResult:
        """
        Run the complete 7-step pipeline end-to-end.

        Args:
            skip_download: If True, skip step 1 (download).
            triggered_by: Who triggered this run (schedule, manual, api).
            single_step: If set, only run this specific step.
            run_id: If provided (e.g. from API), reuse this run ID instead
                    of creating a new pipeline_runs record.

        Returns:
            PipelineResult with per-step summaries and overall status.
        """
        pipeline_result = PipelineResult(started_at=datetime.utcnow())
        t0 = time.time()
        current_step_ref = {"value": single_step if single_step in {
            "download",
            "extract",
            "metadata",
            "embeddings",
            "ingest",
            "event_ingest",
            "conflict_scan",
            "final_status_repair",
        } else "starting"}
        heartbeat_stop = None
        heartbeat_thread = None

        def mark_active_step(step_name: str):
            current_step_ref["value"] = step_name
            if conn:
                _touch_pipeline_run(conn, run_id, step_name)

        # Track in database
        conn = None
        if run_id is None:
            run_id = str(uuid4())
        try:
            conn = _get_db_connection()
            _ensure_pipeline_runs_table(conn)
            if triggered_by != "api":
                # API caller already created the record; don't duplicate
                run_id = _create_pipeline_run(conn, triggered_by, skip_download)
            _touch_pipeline_run(conn, run_id, current_step_ref["value"])
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=_heartbeat_pipeline_run,
                args=(run_id, heartbeat_stop, current_step_ref),
                daemon=True,
                name=f"pipeline-heartbeat-{run_id[:8]}",
            )
            heartbeat_thread.start()
        except Exception as e:
            logger.warning(f"DB tracking unavailable: {e}")

        logger.info("=" * 60)
        logger.info("PIPELINE STARTED")
        logger.info(f"  Run ID: {run_id}")
        logger.info(f"  Triggered by: {triggered_by}")
        logger.info(f"  Skip download: {skip_download}")
        if single_step:
            logger.info(f"  Single step: {single_step}")
        logger.info("=" * 60)

        try:
            abort = False

            # --- Step 1: Download ---
            if single_step and single_step != "download":
                pass  # skip
            elif skip_download:
                step = StepResult(step_name="download", status="skipped")
                pipeline_result.steps.append(step)
                logger.info("[Step 1/7] Skipped (--skip-download)")
            else:
                mark_active_step("download")
                step = await self.run_step_download()
                pipeline_result.steps.append(step)
                if step.status == "failed":
                    logger.warning(
                        "[Step 1/7] Download failed but continuing "
                        "(archives may already exist)"
                    )

            if single_step == "download":
                abort = True  # Only wanted this step

            # --- Step 2: Extract ---
            if not abort:
                if single_step and single_step != "extract":
                    pass
                else:
                    mark_active_step("extract")
                    step = self.run_step_extract()
                    pipeline_result.steps.append(step)
                    if step.status == "failed":
                        logger.error("[Step 2/7] Extract failed critically, aborting pipeline")
                        abort = True

            if single_step == "extract":
                abort = True

            # --- Step 3: Metadata ---
            if not abort:
                if single_step and single_step != "metadata":
                    pass
                else:
                    mark_active_step("metadata")
                    step = self.run_step_metadata()
                    pipeline_result.steps.append(step)
                    if step.status == "failed" and step.error and "CANARY" in step.error.upper():
                        logger.error("[Step 3/7] Canary failure, aborting pipeline")
                        abort = True
                    elif step.status == "failed":
                        logger.error("[Step 3/7] Metadata failed critically, aborting pipeline")
                        abort = True

            if single_step == "metadata":
                abort = True

            # --- Step 4: Embeddings ---
            if not abort:
                if single_step and single_step != "embeddings":
                    pass
                else:
                    mark_active_step("embeddings")
                    step = self.run_step_embeddings()
                    pipeline_result.steps.append(step)
                    if step.status == "failed":
                        logger.warning(
                            "[Step 4/7] Embeddings failed but continuing "
                            "(ingest can load records without embeddings)"
                        )

            if single_step == "embeddings":
                abort = True

            # --- Step 5: Ingest ---
            if not abort:
                if single_step and single_step != "ingest":
                    pass
                else:
                    mark_active_step("ingest")
                    step = self.run_step_ingest()
                    pipeline_result.steps.append(step)

            if single_step == "ingest":
                abort = True

            # --- Step 6: Event Ingest ---
            if not abort:
                if single_step and single_step != "event_ingest":
                    pass
                else:
                    mark_active_step("event_ingest")
                    step = self.run_step_event_ingest()
                    pipeline_result.steps.append(step)
                    if step.status == "failed":
                        logger.warning(
                            "[Step 6/7] Event ingest failed after retry but pipeline continues"
                        )

            if single_step == "event_ingest":
                abort = True

            # --- Manual maintenance: Final Status Repair ---
            if not abort:
                if single_step and single_step != "final_status_repair":
                    pass
                elif single_step == "final_status_repair":
                    mark_active_step("final_status_repair")
                    step = self.run_step_final_status_repair()
                    pipeline_result.steps.append(step)
                    if step.status == "failed":
                        logger.error("[Maintenance] Final status repair failed")
                        abort = True

            if single_step == "final_status_repair":
                abort = True

            # --- Step 7: Conflict Scan (Opposition Radar) ---
            if not abort:
                if single_step and single_step != "conflict_scan":
                    pass
                else:
                    mark_active_step("conflict_scan")
                    step = self.run_step_conflict_scan()
                    pipeline_result.steps.append(step)
                    if step.status == "failed":
                        logger.warning(
                            "[Step 7/7] Conflict scan failed but pipeline continues"
                        )

            # --- Finalize ---
            pipeline_result.completed_at = datetime.utcnow()
            pipeline_result.total_duration_seconds = round(time.time() - t0, 1)

            # Determine overall status
            statuses = [s.status for s in pipeline_result.steps]
            if all(s in ("success", "skipped") for s in statuses):
                pipeline_result.status = "success"
            elif any(s == "failed" for s in statuses):
                if any(s == "success" for s in statuses):
                    pipeline_result.status = "partial"
                else:
                    pipeline_result.status = "failed"
            else:
                pipeline_result.status = "partial"

            if heartbeat_stop:
                heartbeat_stop.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=2)

            # Update DB record
            if conn:
                try:
                    _update_pipeline_run(conn, run_id, pipeline_result)
                except Exception as e:
                    logger.warning(f"Failed to update pipeline run: {e}")

            # Send notification on failure
            if pipeline_result.status in ("failed", "partial"):
                _send_failure_notification(pipeline_result, run_id)

            # Log summary
            logger.info("=" * 60)
            logger.info(f"PIPELINE {pipeline_result.status.upper()}")
            logger.info(f"  Duration: {pipeline_result.total_duration_seconds:.0f}s")
            for step in pipeline_result.steps:
                icon = {
                    "success": "+", "partial": "~",
                    "failed": "X", "skipped": "-",
                }.get(step.status, "?")
                logger.info(
                    f"  [{icon}] {step.step_name}: {step.status} "
                    f"(processed={step.processed}, duration={step.duration_seconds:.0f}s)"
                )
            logger.info("=" * 60)

            return pipeline_result
        finally:
            if heartbeat_stop:
                heartbeat_stop.set()
            if heartbeat_thread:
                heartbeat_thread.join(timeout=2)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


# ===================== CLI Entry Point =====================

def main():
    parser = argparse.ArgumentParser(
        description="Trademark Data Pipeline Worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps:
  1. download      - Download bulletin archives from TURKPATENT
  2. extract       - Extract archives to structured folders
  3. metadata      - Parse HSQLDB SQL files to metadata.json
  4. embeddings    - Generate CLIP/DINOv2/MiniLM embeddings (GPU)
  5. ingest        - Load enriched metadata.json into PostgreSQL
  6. event_ingest  - Reconcile events.json into trademark_events/state
  7. final_status_repair - Manual full-table final_status reconciliation
  8. conflict_scan - Scan within-deadline bulletins for opposition conflicts

Examples:
    # Run full pipeline
    python -m workers.pipeline_worker

    # Skip download (process existing archives)
    python -m workers.pipeline_worker --skip-download

    # Run only embedding generation
    python -m workers.pipeline_worker --step embeddings

    # Run only event ingestion
    python -m workers.pipeline_worker --step event_ingest

    # Run only final status repair
    python -m workers.pipeline_worker --step final_status_repair

    # Run only conflict scan (Opposition Radar)
    python -m workers.pipeline_worker --step conflict_scan
        """,
    )

    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip step 1 (download). Useful when archives already exist.",
    )
    parser.add_argument(
        "--step", type=str, default=None,
        choices=["download", "extract", "metadata", "embeddings", "ingest", "event_ingest", "final_status_repair", "conflict_scan"],
        help="Run only a single step.",
    )
    parser.add_argument(
        "--triggered-by", type=str, default="manual",
        help="Who triggered this run (manual, schedule, api).",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Reuse an existing pipeline_runs row instead of creating a new one.",
    )

    args = parser.parse_args()

    worker = PipelineWorker()
    result = asyncio.run(
        worker.run_full_pipeline(
            skip_download=args.skip_download,
            triggered_by=args.triggered_by,
            single_step=args.step,
            run_id=args.run_id,
        )
    )

    # Exit code based on result
    if result.status == "success":
        sys.exit(0)
    elif result.status == "partial":
        sys.exit(2)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
