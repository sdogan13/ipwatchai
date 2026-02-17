"""
Pipeline Scheduler
===================
Runs as a standalone daemon process. Schedules the full data pipeline:

- Weekly (Monday 3 AM):  Full pipeline (download + extract + metadata + embeddings + ingest)
- Daily (5 AM):          Processing pipeline (extract + metadata + embeddings + ingest)

The daily run catches manually downloaded archives and processes any
new bulletins without triggering a full download.

Usage:
    # Run as daemon
    python -m workers.pipeline_scheduler

    # Run as daemon with custom schedule
    python -m workers.pipeline_scheduler --full-day monday --full-hour 3 --daily-hour 5

    # Run once immediately (full pipeline)
    python -m workers.pipeline_scheduler --run-now

    # Run once immediately (skip download)
    python -m workers.pipeline_scheduler --run-now --skip-download
"""

import sys
import time
import logging
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import schedule

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [SCHEDULER] - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _run_full_pipeline():
    """Run the full 5-step pipeline (weekly)."""
    logger.info("Scheduled full pipeline starting...")
    try:
        from workers.pipeline_worker import PipelineWorker
        worker = PipelineWorker()
        result = asyncio.run(
            worker.run_full_pipeline(
                skip_download=False,
                triggered_by="schedule",
            )
        )
        logger.info(f"Full pipeline completed: {result.status}")
    except Exception as e:
        logger.error(f"Full pipeline failed: {e}")


def _run_daily_pipeline():
    """Run processing-only pipeline (daily, skip download)."""
    logger.info("Scheduled daily pipeline starting (skip download)...")
    try:
        from workers.pipeline_worker import PipelineWorker
        worker = PipelineWorker()
        result = asyncio.run(
            worker.run_full_pipeline(
                skip_download=True,
                triggered_by="schedule",
            )
        )
        logger.info(f"Daily pipeline completed: {result.status}")
    except Exception as e:
        logger.error(f"Daily pipeline failed: {e}")


def start_scheduler(
    full_day: str = "monday",
    full_hour: int = 3,
    daily_hour: int = 5,
):
    """
    Start the pipeline scheduler daemon.

    Args:
        full_day: Day of week for full pipeline (monday-sunday).
        full_hour: Hour (0-23) for full pipeline.
        daily_hour: Hour (0-23) for daily processing pipeline.
    """
    full_time = f"{full_hour:02d}:00"
    daily_time = f"{daily_hour:02d}:00"

    # Read schedule from config if available
    try:
        from config.settings import settings
        pipe = settings.pipeline
        full_day = pipe.collection_schedule_day
        full_time = f"{pipe.collection_schedule_hour:02d}:00"
        daily_time = f"{pipe.pipeline_schedule_hour:02d}:00"
    except ImportError:
        pass

    # Weekly full pipeline
    day_method = getattr(schedule.every(), full_day, schedule.every().monday)
    day_method.at(full_time).do(_run_full_pipeline)

    # Daily processing pipeline (skip download)
    schedule.every().day.at(daily_time).do(_run_daily_pipeline)

    logger.info("Pipeline scheduler started")
    logger.info(f"  Full pipeline: {full_day} at {full_time}")
    logger.info(f"  Daily pipeline: every day at {daily_time}")

    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            logger.info("\nShutting down scheduler...")
            break
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            time.sleep(300)


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline Scheduler - runs data pipeline on schedule",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run as daemon with default schedule
    python -m workers.pipeline_scheduler

    # Run once immediately
    python -m workers.pipeline_scheduler --run-now

    # Run once (skip download)
    python -m workers.pipeline_scheduler --run-now --skip-download
        """,
    )

    parser.add_argument(
        "--run-now", action="store_true",
        help="Run pipeline immediately instead of on schedule",
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip download step (only with --run-now)",
    )
    parser.add_argument(
        "--full-day", type=str, default="monday",
        help="Day for full pipeline (default: monday)",
    )
    parser.add_argument(
        "--full-hour", type=int, default=3,
        help="Hour for full pipeline (default: 3)",
    )
    parser.add_argument(
        "--daily-hour", type=int, default=5,
        help="Hour for daily pipeline (default: 5)",
    )

    args = parser.parse_args()

    if args.run_now:
        from workers.pipeline_worker import PipelineWorker
        worker = PipelineWorker()
        result = asyncio.run(
            worker.run_full_pipeline(
                skip_download=args.skip_download,
                triggered_by="manual",
            )
        )
        print(f"\nPipeline completed: {result.status}")
        print(f"Duration: {result.total_duration_seconds:.0f}s")
        for step in result.steps:
            icon = {"success": "+", "partial": "~", "failed": "X", "skipped": "-"}.get(step.status, "?")
            print(f"  [{icon}] {step.step_name}: {step.status} (processed={step.processed})")
        sys.exit(0 if result.status == "success" else 1)
    else:
        start_scheduler(
            full_day=args.full_day,
            full_hour=args.full_hour,
            daily_hour=args.daily_hour,
        )


if __name__ == "__main__":
    main()
