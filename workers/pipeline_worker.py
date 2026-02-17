"""
Trademark Data Pipeline Worker
================================
Orchestrates the full 5-step data pipeline:

1. data_collection.py - Download bulletin archives from TURKPATENT
2. zip.py            - Extract archives to structured folders
3. metadata.py       - Parse HSQLDB SQL files -> metadata.json
4. ai.py             - Generate embeddings -> enrich metadata.json in-place
5. ingest.py         - Load enriched metadata.json into PostgreSQL

Usage:
    # Run full pipeline (all 5 steps)
    python -m workers.pipeline_worker

    # Skip download step (process existing archives)
    python -m workers.pipeline_worker --skip-download

    # Run a single step
    python -m workers.pipeline_worker --step embeddings

    # Dry run (show what would happen)
    python -m workers.pipeline_worker --dry-run
"""

import os
import sys
import time
import json
import logging
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List
from uuid import uuid4

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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


# ===================== Data Models =====================

@dataclass
class StepResult:
    step_name: str              # download, extract, metadata, embeddings, ingest
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
                total_downloaded INTEGER DEFAULT 0,
                total_extracted INTEGER DEFAULT 0,
                total_parsed INTEGER DEFAULT 0,
                total_embedded INTEGER DEFAULT 0,
                total_ingested INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                duration_seconds FLOAT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
    except Exception:
        conn.rollback()


def _create_pipeline_run(conn, triggered_by: str, skip_download: bool) -> str:
    """Create a new pipeline_runs record. Returns the run_id."""
    run_id = str(uuid4())
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pipeline_runs (id, status, triggered_by, skip_download, started_at)
            VALUES (%s, 'running', %s, %s, NOW())
        """, (run_id, triggered_by, skip_download))
        conn.commit()
        return run_id
    except Exception as e:
        conn.rollback()
        logger.warning(f"Failed to create pipeline run record: {e}")
        return run_id


def _update_pipeline_run(conn, run_id: str, result: PipelineResult):
    """Update pipeline_runs record with final results."""
    try:
        cur = conn.cursor()

        # Collect per-step JSONB and aggregate counts
        step_data = {}
        totals = {
            "downloaded": 0, "extracted": 0,
            "parsed": 0, "embedded": 0, "ingested": 0,
        }
        step_column_map = {
            "download": ("step_download", "downloaded"),
            "extract": ("step_extract", "extracted"),
            "metadata": ("step_metadata", "parsed"),
            "embeddings": ("step_embeddings", "embedded"),
            "ingest": ("step_ingest", "ingested"),
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
                total_downloaded = %s,
                total_extracted = %s,
                total_parsed = %s,
                total_embedded = %s,
                total_ingested = %s,
                completed_at = NOW(),
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
            totals["downloaded"],
            totals["extracted"],
            totals["parsed"],
            totals["embedded"],
            totals["ingested"],
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
            logger.info("[Step 1/5] Starting bulletin download...")
            from data_collection import run_collection
            summary = await run_collection(settings=self.pipeline_settings)

            result.processed = summary.get("downloaded", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)
            result.status = "success"
            logger.info(
                f"[Step 1/5] Download complete: "
                f"{result.processed} downloaded, {result.skipped} skipped"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 1/5] Download failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 2: Extract ----

    def run_step_extract(self) -> StepResult:
        """Step 2: Extract archives via zip.py"""
        result = StepResult(step_name="extract")
        t0 = time.time()

        try:
            logger.info("[Step 2/5] Starting archive extraction...")
            from zip import run_extraction
            summary = run_extraction(settings=self.pipeline_settings)

            result.processed = summary.get("extracted", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)

            if result.failed > 0 and result.processed == 0:
                result.status = "failed"
                result.error = f"All {result.failed} extractions failed"
            elif result.failed > 0:
                result.status = "partial"
                result.error = f"{result.failed} extraction(s) failed"
            else:
                result.status = "success"

            logger.info(
                f"[Step 2/5] Extraction complete: "
                f"{result.processed} extracted, {result.skipped} skipped, "
                f"{result.failed} failed"
            )

        except RuntimeError as e:
            if "CRITICAL" in str(e):
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 2/5] Critical extraction failure: {e}")
            else:
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 2/5] Extraction failed: {e}")
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 2/5] Extraction failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 3: Metadata ----

    def run_step_metadata(self) -> StepResult:
        """Step 3: Parse SQL to metadata.json via metadata.py"""
        result = StepResult(step_name="metadata")
        t0 = time.time()

        try:
            logger.info("[Step 3/5] Starting metadata extraction...")
            from metadata import run_metadata
            summary = run_metadata(settings=self.pipeline_settings)

            result.processed = summary.get("processed", 0)
            result.skipped = summary.get("skipped", 0)
            result.failed = summary.get("failed", 0)

            if result.failed > 0 and result.processed == 0:
                result.status = "failed"
                result.error = f"All metadata extractions failed"
            elif result.failed > 0:
                result.status = "partial"
                result.error = f"{result.failed} metadata extraction(s) failed"
            else:
                result.status = "success"

            logger.info(
                f"[Step 3/5] Metadata complete: "
                f"{result.processed} parsed, {result.skipped} skipped, "
                f"{result.failed} failed"
            )

        except RuntimeError as e:
            if "CANARY" in str(e).upper():
                result.status = "failed"
                result.error = f"Canary failure: {e}"
                logger.error(f"[Step 3/5] Canary failure (aborting): {e}")
            else:
                result.status = "failed"
                result.error = str(e)
                logger.error(f"[Step 3/5] Metadata failed: {e}")
        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 3/5] Metadata failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 4: Embeddings ----

    def run_step_embeddings(self) -> StepResult:
        """
        Step 4: Generate embeddings and enrich metadata.json via ai.py.

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
            logger.info("[Step 4/5] Starting embedding generation (GPU)...")
            logger.info("[Step 4/5] Loading AI models (CLIP, DINOv2, MiniLM)...")

            # Deferred import - ai.py loads CUDA models at import time
            from ai import run_embedding_generation
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
                f"[Step 4/5] Embeddings complete: "
                f"{result.processed} folders processed, {result.skipped} skipped, "
                f"{result.failed} failed | Duration: {time.time() - t0:.0f}s"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 4/5] Embedding generation failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Step 5: Ingest ----

    def run_step_ingest(self) -> StepResult:
        """
        Step 5: Load enriched metadata.json into PostgreSQL via ingest.py.

        Reads metadata.json files that now contain embeddings from step 4.
        Inserts/updates trademarks with vector embeddings into PostgreSQL.
        """
        result = StepResult(step_name="ingest")
        t0 = time.time()

        try:
            logger.info("[Step 5/5] Starting database ingestion...")
            from ingest import run_ingest
            summary = run_ingest(settings=self.pipeline_settings)

            result.processed = summary.get("inserted", 0) + summary.get("updated", 0)
            result.skipped = summary.get("skipped", 0)
            result.status = "success"

            logger.info(
                f"[Step 5/5] Ingestion complete: "
                f"{summary.get('inserted', 0)} inserted, "
                f"{summary.get('updated', 0)} updated, "
                f"{result.skipped} skipped"
            )

        except Exception as e:
            result.status = "failed"
            result.error = str(e)
            logger.error(f"[Step 5/5] Ingestion failed: {e}")

        result.duration_seconds = round(time.time() - t0, 1)
        return result

    # ---- Full Pipeline ----

    async def run_full_pipeline(
        self,
        skip_download: bool = False,
        triggered_by: str = "manual",
        single_step: Optional[str] = None,
    ) -> PipelineResult:
        """
        Run the complete 5-step pipeline end-to-end.

        Args:
            skip_download: If True, skip step 1 (download).
            triggered_by: Who triggered this run (schedule, manual, api).
            single_step: If set, only run this specific step.

        Returns:
            PipelineResult with per-step summaries and overall status.
        """
        pipeline_result = PipelineResult(started_at=datetime.utcnow())
        t0 = time.time()

        # Track in database
        conn = None
        run_id = str(uuid4())
        try:
            conn = _get_db_connection()
            _ensure_pipeline_runs_table(conn)
            run_id = _create_pipeline_run(conn, triggered_by, skip_download)
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

        abort = False

        # --- Step 1: Download ---
        if single_step and single_step != "download":
            pass  # skip
        elif skip_download:
            step = StepResult(step_name="download", status="skipped")
            pipeline_result.steps.append(step)
            logger.info("[Step 1/5] Skipped (--skip-download)")
        else:
            step = await self.run_step_download()
            pipeline_result.steps.append(step)
            if step.status == "failed":
                logger.warning(
                    "[Step 1/5] Download failed but continuing "
                    "(archives may already exist)"
                )

        if single_step == "download":
            abort = True  # Only wanted this step

        # --- Step 2: Extract ---
        if not abort:
            if single_step and single_step != "extract":
                pass
            else:
                step = self.run_step_extract()
                pipeline_result.steps.append(step)
                if step.status == "failed":
                    logger.error("[Step 2/5] Extract failed critically, aborting pipeline")
                    abort = True

        if single_step == "extract":
            abort = True

        # --- Step 3: Metadata ---
        if not abort:
            if single_step and single_step != "metadata":
                pass
            else:
                step = self.run_step_metadata()
                pipeline_result.steps.append(step)
                if step.status == "failed" and step.error and "CANARY" in step.error.upper():
                    logger.error("[Step 3/5] Canary failure, aborting pipeline")
                    abort = True
                elif step.status == "failed":
                    logger.error("[Step 3/5] Metadata failed critically, aborting pipeline")
                    abort = True

        if single_step == "metadata":
            abort = True

        # --- Step 4: Embeddings ---
        if not abort:
            if single_step and single_step != "embeddings":
                pass
            else:
                step = self.run_step_embeddings()
                pipeline_result.steps.append(step)
                if step.status == "failed":
                    logger.warning(
                        "[Step 4/5] Embeddings failed but continuing "
                        "(ingest can load records without embeddings)"
                    )

        if single_step == "embeddings":
            abort = True

        # --- Step 5: Ingest ---
        if not abort:
            if single_step and single_step != "ingest":
                pass
            else:
                step = self.run_step_ingest()
                pipeline_result.steps.append(step)

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

        # Update DB record
        if conn:
            try:
                _update_pipeline_run(conn, run_id, pipeline_result)
            except Exception as e:
                logger.warning(f"Failed to update pipeline run: {e}")
            finally:
                conn.close()

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


# ===================== CLI Entry Point =====================

def main():
    parser = argparse.ArgumentParser(
        description="Trademark Data Pipeline Worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps:
  1. download   - Download bulletin archives from TURKPATENT
  2. extract    - Extract archives to structured folders
  3. metadata   - Parse HSQLDB SQL files to metadata.json
  4. embeddings - Generate CLIP/DINOv2/MiniLM embeddings (GPU)
  5. ingest     - Load enriched metadata.json into PostgreSQL

Examples:
    # Run full pipeline
    python -m workers.pipeline_worker

    # Skip download (process existing archives)
    python -m workers.pipeline_worker --skip-download

    # Run only embedding generation
    python -m workers.pipeline_worker --step embeddings

    # Run only ingestion
    python -m workers.pipeline_worker --step ingest
        """,
    )

    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip step 1 (download). Useful when archives already exist.",
    )
    parser.add_argument(
        "--step", type=str, default=None,
        choices=["download", "extract", "metadata", "embeddings", "ingest"],
        help="Run only a single step.",
    )
    parser.add_argument(
        "--triggered-by", type=str, default="manual",
        help="Who triggered this run (manual, schedule, api).",
    )

    args = parser.parse_args()

    worker = PipelineWorker()
    result = asyncio.run(
        worker.run_full_pipeline(
            skip_download=args.skip_download,
            triggered_by=args.triggered_by,
            single_step=args.step,
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
