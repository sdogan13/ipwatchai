"""
Agentic Trademark Search Pipeline
=================================
Orchestrates: scrapper → ai → ingest → risk_engine

Flow (auto_scrape=True, the agentic path):
1. Live scrape TurkPatent
2. Generate AI embeddings for scraped data
3. Ingest new data to database
4. Score against the database (now includes the freshly ingested rows)

If the scrape fails or returns no records, fall back to a single DB-only
risk assessment so the caller still gets results.

When auto_scrape=False (quick / public search), only the DB scoring runs.

Usage:
    python agentic_search.py "dogan patent"
    python agentic_search.py "nike" --classes 25,35
    python agentic_search.py "apple" --no-scrape
"""

import os
import sys
import io
import json
import logging
import time
import asyncio
import threading
import psycopg2
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from config.settings import settings

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_local_agentic_path(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


_PRE_DOTENV_PROJECT_ROOT = os.environ.get("PROJECT_ROOT")
_PRE_DOTENV_PIPELINE_BULLETINS_ROOT = os.environ.get("PIPELINE_BULLETINS_ROOT")
_PRE_DOTENV_DATA_ROOT = os.environ.get("DATA_ROOT")


# Fix console encoding for Turkish characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [AGENTIC] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project paths
PROJECT_ROOT = _resolve_local_agentic_path(
    _PRE_DOTENV_PROJECT_ROOT or os.environ.get("PROJECT_ROOT"),
    _LOCAL_PROJECT_ROOT,
)
DATA_ROOT = _resolve_local_agentic_path(
    _PRE_DOTENV_PIPELINE_BULLETINS_ROOT
    or _PRE_DOTENV_DATA_ROOT
    or os.environ.get("PIPELINE_BULLETINS_ROOT")
    or os.environ.get("DATA_ROOT")
    or settings.pipeline.bulletins_root,
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)

# ============================================
# REAL-TIME PROGRESS TRACKING (via Redis)
# ============================================
import redis as _redis_mod

_progress_redis = None
try:
    _progress_redis = _redis_mod.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        password=os.getenv("REDIS_PASSWORD") or None,
        decode_responses=True,
    )
    _progress_redis.ping()
except Exception:
    _progress_redis = None


def _update_progress(user_id: str, step: str, progress: int, detail: str = ""):
    """Append a step event to the user's progress list in Redis."""
    if not _progress_redis:
        return
    try:
        import json as _json
        key = f"search_progress:{user_id}"
        _progress_redis.rpush(key, _json.dumps({
            "step": step,
            "progress": progress,
            "detail": detail,
        }))
        _progress_redis.expire(key, 120)
    except Exception:
        pass


def _clear_progress(user_id: str):
    """Clear progress list for a new search."""
    if not _progress_redis:
        return
    try:
        _progress_redis.delete(f"search_progress:{user_id}")
    except Exception:
        pass


def _get_progress(user_id: str, after: int = 0) -> dict:
    """Read progress events after a given index. Returns new events + next index."""
    if not _progress_redis:
        return {"events": [], "next_index": 0}
    try:
        import json as _json
        key = f"search_progress:{user_id}"
        raw_list = _progress_redis.lrange(key, after, -1)
        events = [_json.loads(r) for r in raw_list]
        return {"events": events, "next_index": after + len(events)}
    except Exception:
        pass
    return {"events": [], "next_index": after}


# ============================================
# SCRAPE QUEUE — serialize TurkPatent requests
# ============================================
# Only one scrape can run at a time to avoid IP blocking.
# Waiting requests are served FIFO via the lock.
_scrape_lock = threading.Lock()
_scrape_queue_size = 0        # how many threads are waiting + running
_scrape_queue_counter = threading.Lock()  # protects the counter


def _scrape_queue_position() -> int:
    """Return current queue depth (0 = you're next)."""
    return _scrape_queue_size


class AgenticTrademarkSearch:
    """
    Trademark search.

    When auto_scrape=True (agentic path):
    1. Scrape TurkPatent live for the query
    2. Generate AI embeddings + translations for the new rows
    3. Ingest them into the database
    4. Score the query against the database (now including those rows)

    If the scrape fails or returns 0 rows, fall back to a DB-only assessment.

    When auto_scrape=False (quick / public path):
    Run a DB-only assessment and return.
    """

    def __init__(
        self,
        auto_scrape: bool = True,
        scrape_limit: int = 100,
        headless: bool = True
    ):
        """
        Args:
            auto_scrape: When True, scrape TurkPatent live. When False, DB-only.
            scrape_limit: Max records to scrape
            headless: Run browser in headless mode
        """
        self.auto_scrape = auto_scrape
        self.scrape_limit = scrape_limit
        self.headless = headless

        # Lazy-loaded components
        self._risk_engine = None
        self._scrapper = None
        self._conn = None

    @property
    def conn(self):
        """Lazy database connection."""
        if self._conn is None:
            self._conn = psycopg2.connect(
                host=os.getenv('DB_HOST', '127.0.0.1'),
                port=int(os.getenv('DB_PORT', 5432)),
                database=os.getenv('DB_NAME', 'trademark_db'),
                user=os.getenv('DB_USER', 'turk_patent'),
                password=settings.database.password,
                connect_timeout=10
            )
        return self._conn

    @property
    def risk_engine(self):
        """Lazy load risk engine."""
        if self._risk_engine is None:
            logger.info("   Loading Risk Engine & AI Models...")
            from risk_engine import RiskEngine
            self._risk_engine = RiskEngine(existing_conn=self.conn)
        return self._risk_engine

    @property
    def scrapper(self):
        """Lazy load scrapper."""
        if self._scrapper is None:
            logger.info("   Loading Scrapper...")
            from scrapper import TurkPatentScraper
            self._scrapper = TurkPatentScraper(headless=self.headless)
        return self._scrapper

    def search(
        self,
        query: str,
        nice_classes: List[int] = None,
        image_path: str = None,
        attorney_no: str = None,
        user_id: str = None
    ) -> Dict:
        """
        Trademark search.

        When auto_scrape=True: scrape TurkPatent live, ingest, then score.
            On scrape failure or empty scrape, fall back to a DB-only score.
        When auto_scrape=False: DB-only score.

        Args:
            query: Trademark name to search
            nice_classes: Optional Nice class filter
            image_path: Optional path to logo image for visual scoring
            attorney_no: Optional attorney filter
            user_id: If set, progress events are pushed to Redis under this key

        Returns:
            Search results with risk assessment
        """
        start_time = time.time()
        nice_classes = nice_classes or []
        image_used = image_path is not None

        logger.info("=" * 60)
        logger.info(f"AGENTIC SEARCH: '{query}'")
        logger.info("=" * 60)
        logger.info(f"   Nice Classes: {nice_classes or 'All'}")
        logger.info(f"   Auto-Scrape: {self.auto_scrape}")

        # Helper to push progress if user_id is set
        def _prog(step, pct, detail=""):
            if user_id:
                _update_progress(user_id, step, pct, detail)

        def _is_cancelled():
            if not user_id or not _progress_redis:
                return False
            try:
                return _progress_redis.get(f"search_cancel:{user_id}") == "1"
            except Exception:
                return False

        def _clear_cancel():
            if user_id and _progress_redis:
                try:
                    _progress_redis.delete(f"search_cancel:{user_id}")
                except Exception:
                    pass

        def _db_assess():
            """Run a DB-only risk assessment and return (max_score, candidates)."""
            db_result, _ = self.risk_engine.assess_brand_risk(
                name=query,
                image_path=image_path,
                target_classes=nice_classes if nice_classes else None,
                attorney_no=attorney_no
            )
            return (
                db_result.get("final_risk_score", 0),
                db_result.get("top_candidates", []),
            )

        # Clear stale cancel flag + progress list from previous search
        _clear_cancel()
        if user_id:
            _clear_progress(user_id)

        _prog("starting", 0, query)

        # ============================================
        # DB-ONLY PATH (quick / public search)
        # ============================================
        if not self.auto_scrape:
            logger.info("")
            logger.info("DB-only assessment (auto_scrape=False)")
            db_max_score, db_candidates = _db_assess()
            logger.info(f"   [OK] {len(db_candidates)} candidates, max score {db_max_score:.2%}")
            _prog("complete", 100, f"{len(db_candidates)}")
            return self._build_response(
                query=query,
                results=db_candidates,
                max_score=db_max_score,
                source="database",
                scrape_triggered=False,
                elapsed_time=time.time() - start_time,
                image_used=image_used,
            )

        # ============================================
        # AGENTIC PATH — scrape → embed → ingest → score
        # ============================================

        # ---- Cancel check before STEP 1 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before scraping.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=[], max_score=0,
                source="cancelled", scrape_triggered=False,
                elapsed_time=time.time() - start_time, image_used=image_used)

        # ============================================
        # STEP 1/4: Scrape TurkPatent Live
        # ============================================
        logger.info("")
        logger.info("STEP 1/4: Live scraping TurkPatent...")
        step1_start = time.time()
        _prog("scraping", 5, query)

        try:
            scraped_records = self._run_scrapper(query, _prog=_prog)
            scraped_count = len(scraped_records) if scraped_records else 0
            logger.info(f"   [OK] Scraped {scraped_count} records")
            logger.info(f"   [OK] Time: {time.time() - step1_start:.2f}s")
            _prog("scraping_done", 40, str(scraped_count))
        except Exception as e:
            logger.error(f"   [FAIL] Scraping failed: {e}")
            _prog("scraping_failed", 40, str(e))
            db_max_score, db_candidates = _db_assess()
            time.sleep(1.2)
            _prog("complete", 100, f"{len(db_candidates)}")
            response = self._build_response(
                query=query,
                results=db_candidates,
                max_score=db_max_score,
                source="database",
                scrape_triggered=True,
                elapsed_time=time.time() - start_time,
                image_used=image_used,
            )
            response["scrape_error"] = str(e)
            return response

        if not scraped_records:
            logger.info("   No records scraped. Falling back to database results.")
            _prog("scrape_no_results", 45, query)
            db_max_score, db_candidates = _db_assess()
            time.sleep(1.2)  # give frontend one poll cycle to display the event
            _prog("complete", 100, f"{len(db_candidates)}")
            return self._build_response(
                query=query,
                results=db_candidates,
                max_score=db_max_score,
                source="database",
                scrape_triggered=True,
                scraped_count=0,
                elapsed_time=time.time() - start_time,
                image_used=image_used,
            )

        # ---- Cancel check before STEP 2 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before embeddings.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=[], max_score=0,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                elapsed_time=time.time() - start_time, image_used=image_used)

        # ============================================
        # STEP 2/4: Generate AI Embeddings + Translations
        # ============================================
        logger.info("")
        logger.info("STEP 2/4: Generating AI embeddings...")
        step2_start = time.time()
        _prog("embeddings", 50, str(scraped_count))

        try:
            enriched_count = self._generate_embeddings(scraped_records)
            logger.info(f"   [OK] Generated embeddings for {enriched_count} records")
            logger.info(f"   [OK] Time: {time.time() - step2_start:.2f}s")
            _prog("embeddings_done", 60, str(enriched_count))
        except Exception as e:
            logger.warning(f"   [WARN] Embedding generation failed: {e}")
            enriched_count = 0

        # ---- Cancel check before STEP 3 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before ingestion.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=[], max_score=0,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                elapsed_time=time.time() - start_time, image_used=image_used)

        # ============================================
        # STEP 3/4: Ingest to Database
        # ============================================
        logger.info("")
        logger.info("STEP 3/4: Ingesting to database...")
        step3_start = time.time()
        _prog("ingesting", 65, str(scraped_count))

        try:
            ingested_count = self._ingest_to_database(scraped_records, query)
            logger.info(f"   [OK] Ingested {ingested_count} records")
            logger.info(f"   [OK] Time: {time.time() - step3_start:.2f}s")
            _prog("ingesting_done", 80, str(ingested_count))
        except Exception as e:
            logger.error(f"   [FAIL] Ingestion failed: {e}")
            ingested_count = 0

        # ---- Cancel check before STEP 4 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before scoring.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=[], max_score=0,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                ingested_count=ingested_count, elapsed_time=time.time() - start_time,
                image_used=image_used)

        # ============================================
        # STEP 4/4: Score against the database
        # ============================================
        logger.info("")
        logger.info("STEP 4/4: Scoring against database...")
        step4_start = time.time()
        _prog("scoring", 85)

        final_max_score, final_candidates = _db_assess()

        logger.info(f"   [OK] Max score: {final_max_score:.2%}")
        logger.info(f"   [OK] Total candidates: {len(final_candidates)}")
        logger.info(f"   [OK] Time: {time.time() - step4_start:.2f}s")
        _prog("complete", 100, f"{len(final_candidates)}")

        # ============================================
        # FINAL SUMMARY
        # ============================================
        total_time = time.time() - start_time

        logger.info("")
        logger.info("=" * 60)
        logger.info("AGENTIC SEARCH COMPLETE")
        logger.info("=" * 60)
        logger.info(f"   Query:            {query}")
        logger.info(f"   Final Score:      {final_max_score:.2%}")
        logger.info(f"   Records Scraped:  {scraped_count}")
        logger.info(f"   Records Ingested: {ingested_count}")
        logger.info(f"   Total Candidates: {len(final_candidates)}")
        logger.info(f"   Risk Level:       {self._get_risk_level(final_max_score)}")
        logger.info(f"   Total Time:       {total_time:.2f}s")
        logger.info("=" * 60)

        return self._build_response(
            query=query,
            results=final_candidates,
            max_score=final_max_score,
            source="combined",
            scrape_triggered=True,
            scraped_count=scraped_count,
            ingested_count=ingested_count,
            elapsed_time=total_time,
            image_used=image_used,
        )

    def _run_scrapper(self, query: str, _prog=None) -> List[Dict]:
        """Run the scrapper to get live data from TurkPatent.

        Uses a global lock to ensure only one scrape runs at a time,
        preventing concurrent requests that could get our IP blocked.
        """
        global _scrape_queue_size

        # Track queue position
        with _scrape_queue_counter:
            _scrape_queue_size += 1
            my_position = _scrape_queue_size

        if my_position > 1:
            logger.info(f"   [QUEUE] Waiting in scrape queue (position {my_position})...")
            if _prog:
                _prog("queued", 15, str(my_position))

        try:
            # Acquire lock — only one scrape at a time
            with _scrape_lock:
                logger.info(f"   [QUEUE] Lock acquired, starting scrape for '{query}'")
                if _prog and my_position > 1:
                    _prog("scraping", 20, query)

                # Scrapper returns list of row data and saves to JSON
                # Pass _prog so the surgical Turkish char fallback can emit a progress event
                results = self.scrapper.search_and_ingest(
                    trademark_name=query,
                    limit=self.scrape_limit,
                    progress_callback=_prog
                )

                if not results:
                    return []

                # Results are raw rows from scrapper, convert to dict format
                if isinstance(results[0], list):
                    formatted = self._format_scraped_rows(results)
                else:
                    formatted = results

                save_info = getattr(self.scrapper, "last_save_info", None) or {}
                if save_info.get("metadata_path"):
                    logger.info(f"   Saved to: {save_info['folder_name']}/metadata.json")

                # Brief cooldown after scrape to be gentle on TurkPatent
                time.sleep(2)

                return formatted
        finally:
            with _scrape_queue_counter:
                _scrape_queue_size -= 1

    def _format_scraped_rows(self, rows: List[List]) -> List[Dict]:
        """Convert raw scrapper rows to metadata.json format."""
        import re
        formatted = []

        for row in rows:
            if len(row) < 2:
                continue

            # Row format: [0]#, [1]AppNo, [2]Name, [3]Holder, [4]AppDate, [5]RegNo, [6]Status, [7]NICE
            app_no = row[1] if len(row) > 1 else ""
            name = row[2] if len(row) > 2 else ""
            holder = row[3] if len(row) > 3 else ""
            app_date = row[4] if len(row) > 4 else ""
            reg_no = row[5] if len(row) > 5 else ""
            status = row[6] if len(row) > 6 else ""
            nice_raw = row[7] if len(row) > 7 else ""

            # Parse NICE classes
            nice_list = re.findall(r'\d+', nice_raw)

            formatted.append({
                "APPLICATIONNO": app_no,
                "STATUS": status,
                "IMAGE": "",  # No images from live scrape (text-only)
                "TRADEMARK": {
                    "APPLICATIONDATE": app_date,
                    "REGISTERNO": reg_no,
                    "NAME": name,
                    "NICECLASSES_RAW": ", ".join(nice_list),
                    "NICECLASSES_LIST": nice_list,
                },
                "HOLDERS": [{
                    "TITLE": holder,
                    "COUNTRY": "TÜRKİYE"
                }],
                "ATTORNEYS": [],
                "GOODS": [],
                "EXTRACTEDGOODS": []
            })

        return formatted

    def _generate_embeddings(self, records: List[Dict]) -> int:
        """Generate translations for scraped records using pipeline.ai."""
        try:
            from pipeline import ai

            enriched_count = 0
            for record in records:
                name = record.get("TRADEMARK", {}).get("NAME", "")

                if name:
                    try:
                        # Generate Turkish translation + language detection
                        trans = ai.get_translations(name)
                        if trans.get("name_tr"):
                            record["name_tr"] = trans["name_tr"]
                        if trans.get("detected_lang"):
                            record["detected_lang"] = trans["detected_lang"]
                        enriched_count += 1
                    except Exception as e:
                        logger.debug(f"Failed to translate {name}: {e}")

            return enriched_count

        except ImportError:
            logger.warning("pipeline.ai not available, skipping translation generation")
            return 0

    def _ingest_to_database(self, records: List[Dict], query: str) -> int:
        """Ingest enriched records to database using pipeline.ingest."""
        if not records:
            return 0

        try:
            from pipeline.ingest import process_records_batch

            save_info = getattr(self.scrapper, "last_save_info", None) or {}
            metadata_path = save_info.get("metadata_path")
            if metadata_path:
                save_info = self.scrapper.save_to_json(records, target_file=metadata_path)

            folder_name = save_info.get("folder_name")
            if not folder_name:
                active_dir = getattr(self.scrapper, "active_data_dir", None)
                folder_name = active_dir.name if active_dir else "APP_1"

            process_records_batch(
                self.conn,
                records,
                folder_name=folder_name,
                filename="metadata.json",
                force=True,
            )
            self.conn.commit()
            return len(records)

        except Exception as e:
            logger.error(f"Ingest error: {e}")
            self.conn.rollback()
            return 0

    def _build_response(
        self,
        query: str,
        results: List[Dict],
        max_score: float,
        source: str,
        scrape_triggered: bool,
        scraped_count: int = 0,
        ingested_count: int = 0,
        elapsed_time: float = 0,
        image_used: bool = False,
    ) -> Dict:
        """Build standardized response object."""
        total = len(results)

        return {
            "query": query,
            "results": results,
            "total": total,
            "total_candidates": total,
            "max_score": max_score,
            "risk_level": self._get_risk_level(max_score),
            "source": source,
            "scrape_triggered": scrape_triggered,
            "scraped_count": scraped_count,
            "ingested_count": ingested_count,
            "image_used": image_used,
            "elapsed_seconds": round(elapsed_time, 2),
            "timestamp": datetime.now().isoformat()
        }

    def _get_risk_level(self, score: float) -> str:
        """Convert score to risk level using centralized thresholds."""
        from risk_engine import get_risk_level
        return get_risk_level(score).upper()

    def close(self):
        """Clean up resources."""
        if self._scrapper:
            self._scrapper.close()
            self._scrapper = None

        if self._risk_engine and hasattr(self._risk_engine, 'close'):
            self._risk_engine.close()
            self._risk_engine = None

        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ============================================
# FASTAPI ROUTER
# ============================================
import tempfile
from fastapi import APIRouter, Query, HTTPException, Depends, UploadFile, File, Form, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from models.schemas import SearchRiskReportClaimRequest, SearchRiskReportRequest, SearchRiskReportResponse
from services.search_risk_report_service import (
    RISK_REPORT_IMAGE_MAX_BYTES,
    _report_limit_detail,
    claim_pending_search_risk_report_data,
    generate_pending_search_risk_report_data,
    generate_search_risk_report_data,
)
from utils.settings_manager import get_rate_limit_value
from utils.feature_flags import is_feature_enabled
from utils.subscription import (
    check_live_search_eligibility,
    check_report_eligibility,
    increment_live_search_usage,
    get_user_plan,
    get_daily_live_search_usage,
)

_search_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/v1/search", tags=["Agentic Search"])


def _compact_translation_text(value) -> str:
    from utils.idf_scoring import normalize_turkish

    return normalize_turkish(value or "").replace(" ", "")


def _is_duplicate_name_translation(name, name_tr) -> bool:
    name_compact = _compact_translation_text(name)
    name_tr_compact = _compact_translation_text(name_tr)
    return bool(name_compact and name_tr_compact and name_compact == name_tr_compact)


def _display_translation_similarity(result, scores) -> float:
    if _is_duplicate_name_translation(
        result.get("trademark_name") or result.get("name"),
        result.get("name_tr"),
    ):
        return 0.0
    return scores.get("translation_similarity", 0)


def _normalize_search_results(result: dict) -> None:
    """
    Normalize raw search results in-place to match the public search format.
    Adds: image_url, trademark_name, nice_classes, flat scores (risk_score,
    text_similarity, visual_similarity, translation_similarity, phonetic_similarity).
    Keeps original fields for backward compatibility.
    """
    def _local_get_status_code(status_text: Optional[str]) -> str:
        if not status_text: return 'unknown'
        status_text = status_text.strip()
        mapping = {
            'Yayında': 'published',
            'Tescil Edildi': 'registered',
            'Başvuruldu': 'pending',
            'Reddedildi': 'rejected',
            'Geri Çekildi': 'withdrawn',
            'Devredildi': 'transferred',
            'Kısmi Red': 'partial_refusal',
            'Süresi Doldu': 'expired',
            'İtiraz Edildi': 'opposed',
            'Yenilendi': 'renewed',
            'İptal Edildi': 'cancelled',
            'Bilinmiyor': 'unknown',
        }
        return mapping.get(status_text, 'unknown')

    for r in result.get("results", []):
        scores = r.get("scores") or {}
        img_path = r.get("image_path")
        r["image_url"] = f"/api/trademark-image/{img_path}" if img_path else None
        r["trademark_name"] = r.get("trademark_name") or r.get("name", "")
        r["nice_classes"] = r.get("nice_classes") or r.get("classes") or []
        r["risk_score"] = scores.get("total") if scores.get("total") is not None else r.get("risk_score", 0)
        effective_text_score = scores.get("text_idf_score")
        if effective_text_score is None:
            effective_text_score = scores.get("text_similarity", 0)
        r["text_similarity"] = round(scores.get("text_similarity", 0), 3)
        r["text_idf_score"] = round(effective_text_score, 3)
        r["path_a_score"] = round(scores.get("path_a_score", scores.get("text_similarity", 0)), 3)
        r["path_b_score"] = round(scores.get("path_b_score", 0), 3)
        r["scoring_path_source"] = scores.get("scoring_path_source")
        r["visual_similarity"] = round(scores.get("visual_similarity", 0), 3)
        r["translation_similarity"] = round(_display_translation_similarity(r, scores), 3)
        r["phonetic_similarity"] = round(scores.get("phonetic_similarity", 0), 3)
        r["status_code"] = _local_get_status_code(r.get("status"))


def _build_report_request_from_search_results(
    *,
    query: str,
    nice_classes: List[int],
    language: str,
    image_used: bool,
    search_results: List[Dict],
) -> SearchRiskReportRequest:
    """Backend equivalent of the JS buildRiskReportCandidate — converts agentic
    search results into the SearchRiskReportRequest the LLM report service expects."""
    candidates: List[Dict[str, Any]] = []
    for r in (search_results or [])[:20]:
        name = r.get("trademark_name") or r.get("name") or ""
        if not name and r.get("application_no"):
            name = f"#{r['application_no']}"
        if not name:
            continue
        candidates.append({
            "name": name,
            "application_no": r.get("application_no") or None,
            "status": r.get("status") or None,
            "status_code": r.get("status_code") or None,
            "nice_classes": r.get("nice_classes") or r.get("classes") or [],
            "owner": r.get("owner") or r.get("holder_name") or None,
            "attorney": r.get("attorney") or r.get("attorney_name") or None,
            "image_url": r.get("image_url") or r.get("image_path") or None,
            "text_similarity": r.get("text_similarity"),
            "visual_similarity": r.get("visual_similarity"),
            "phonetic_similarity": r.get("phonetic_similarity"),
            "translation_similarity": r.get("translation_similarity"),
            "scores": r.get("scores"),
        })
    lang = language if language in ("tr", "en", "ar") else "tr"
    return SearchRiskReportRequest(
        query=(query or "").strip()[:300],
        selected_classes=nice_classes or [],
        language=lang,
        image_used=image_used,
        results=candidates,
    )


class SearchRequest(BaseModel):
    """Request model for intelligent search."""
    query: str
    nice_classes: Optional[list] = None
    auto_scrape: bool = True


@router.get("/status")
async def search_status():
    """Get agentic search service status."""
    return {
        "service": "agentic_search",
        "status": "operational",
        "version": "1.0.0",
        "features": {
            "database_search": True,
            "live_scraping": True,
            "ai_embeddings": True,
            "idf_scoring": True
        }
    }


def _run_search_sync(auto_scrape, query, nice_classes,
                      image_path=None, attorney_no=None, user_id=None):
    """Run AgenticTrademarkSearch synchronously (for use with asyncio.to_thread)."""
    with AgenticTrademarkSearch(auto_scrape=auto_scrape) as searcher:
        return searcher.search(
            query=query,
            nice_classes=nice_classes,
            image_path=image_path,
            attorney_no=attorney_no,
            user_id=user_id
        )


@router.get("/progress")
async def get_search_progress(
    after: int = 0,
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get real-time progress events after a given index."""
    return _get_progress(str(current_user.id), after=after)


@router.post("/cancel")
async def cancel_search(
    current_user: CurrentUser = Depends(get_current_user)
):
    """Cancel the current running Agentic Search."""
    user_id = str(current_user.id)
    if _progress_redis:
        try:
            _progress_redis.setex(f"search_cancel:{user_id}", 120, "1")
            _update_progress(user_id, "cancelled", 0)
        except Exception:
            pass
    return {"status": "cancelled"}


@router.get("/credits")
async def get_search_credits(
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get current user's Agentic Search credit status.
    Returns plan info and remaining credits.
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        current_usage = get_daily_live_search_usage(db, str(current_user.id))
        daily_limit = plan['daily_limit']

        return {
            "plan": plan['plan_name'],
            "display_name": plan['display_name'],
            "can_use_live_search": plan['can_use_live_search'],
            "daily_limit": daily_limit,
            "used_today": current_usage,
            "remaining": max(0, daily_limit - current_usage),
            "resets_on": (date.today() + timedelta(days=1)).isoformat(),
        }


async def _parse_risk_report_request(request: Request):
    content_type = request.headers.get("content-type", "")
    query_image_bytes = None
    query_image_mime = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        payload_raw = form.get("payload")
        if not payload_raw or not isinstance(payload_raw, str):
            raise HTTPException(status_code=422, detail="Missing risk report payload")
        try:
            report_request = SearchRiskReportRequest(**json.loads(payload_raw))
        except Exception as exc:
            detail = exc.errors() if hasattr(exc, "errors") else "Invalid risk report payload"
            raise HTTPException(status_code=422, detail=detail) from exc

        query_image = form.get("query_image")
        if query_image is not None and hasattr(query_image, "read"):
            query_image_mime = getattr(query_image, "content_type", None) or "application/octet-stream"
            if not query_image_mime.startswith("image/"):
                raise HTTPException(status_code=422, detail="Risk report query image must be an image file")
            query_image_bytes = await query_image.read()
            if query_image_bytes and len(query_image_bytes) > RISK_REPORT_IMAGE_MAX_BYTES:
                raise HTTPException(status_code=422, detail="Risk report query image is too large")
            if query_image_bytes:
                report_request.image_used = True
    else:
        try:
            report_request = SearchRiskReportRequest(**await request.json())
        except Exception as exc:
            detail = exc.errors() if hasattr(exc, "errors") else "Invalid risk report payload"
            raise HTTPException(status_code=422, detail=detail) from exc

    return report_request, query_image_bytes, query_image_mime


@router.post("/risk-report/public", response_model=SearchRiskReportResponse)
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.public_risk_report", "3/minute"))
async def public_search_risk_report(request: Request):
    """Generate a claimable landing-page risk report before login."""
    report_request, query_image_bytes, query_image_mime = await _parse_risk_report_request(request)
    return await generate_pending_search_risk_report_data(
        request=report_request,
        query_image_bytes=query_image_bytes,
        query_image_mime=query_image_mime,
    )


@router.post("/risk-report/claim")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "60/minute"))
async def claim_search_risk_report(
    request: Request,
    claim_request: SearchRiskReportClaimRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Attach a landing-page pending risk report to the logged-in account."""
    return await claim_pending_search_risk_report_data(
        claim_token=claim_request.claim_token,
        current_user=current_user,
    )


@router.post("/risk-report", response_model=SearchRiskReportResponse)
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "60/minute"))
async def search_risk_report(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate an advisory LLM risk report for the visible search results."""
    report_request, query_image_bytes, query_image_mime = await _parse_risk_report_request(request)
    return await generate_search_risk_report_data(
        request=report_request,
        current_user=current_user,
        query_image_bytes=query_image_bytes,
        query_image_mime=query_image_mime,
    )


async def _read_report_image_upload(image: Optional[UploadFile]):
    """Read an uploaded logo image from a multipart form, validating size + mime."""
    if image is None or not image.filename:
        return None, None, None
    mime = getattr(image, "content_type", None) or "application/octet-stream"
    if not mime.startswith("image/"):
        raise HTTPException(status_code=422, detail="Risk report query image must be an image file")
    image_bytes = await image.read()
    if image_bytes and len(image_bytes) > RISK_REPORT_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=422, detail="Risk report query image is too large")
    if not image_bytes:
        return None, None, None
    suffix = os.path.splitext(image.filename)[1] or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tempfile.gettempdir()) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    return image_bytes, mime, tmp_path


def _parse_classes_csv(classes: Optional[str]) -> List[int]:
    if not classes:
        return []
    return [int(c.strip()) for c in classes.split(",") if c.strip().isdigit()]


def _require_nice_classes(nice_classes: List[int]):
    """Reject a combined risk-report request that has no Nice classes selected."""
    if not nice_classes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "classes_required",
                "message": "Please select at least one Nice class to generate a risk report.",
                "message_en": "Please select at least one Nice class to generate a risk report.",
                "message_tr": "Risk raporu olusturmak icin en az bir Nice sinifi secmelisiniz.",
                "message_ar": "يرجى اختيار فئة Nice واحدة على الأقل لإنشاء تقرير المخاطر.",
            },
        )


@router.post("/intelligent-risk-report")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "10/minute"))
async def intelligent_risk_report(
    request: Request,
    query: str = Form(..., description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual scoring"),
    classes: Optional[str] = Form(None, description="Nice classes (comma-separated)"),
    attorney_no: Optional[str] = Form(None, description="Filter by attorney number"),
    language: Optional[str] = Form("tr", description="Report language (tr|en|ar)"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Run an agentic TurkPatent search and generate an advisory LLM risk report
    on the freshly scored results, in one call.

    Charges only the monthly_reports quota — the bundled agentic search does
    not consume a live-search credit. If the scrape fails or returns no
    records, the agentic pipeline falls back to a DB-only assessment and the
    report is still generated on those candidates.

    Returns:
    - 200: SearchRiskReportResponse with search context attached.
    - 401: missing/invalid auth.
    - 403: monthly_reports quota exhausted.
    - 503: live scraping disabled (kill switch).
    """
    # Feature flag kill switch
    if not is_feature_enabled("live_scraping_enabled"):
        raise HTTPException(status_code=503, detail="Live scraping is temporarily disabled")

    # The combined flow runs an LLM risk report — Nice classes are required input.
    nice_classes = _parse_classes_csv(classes)
    _require_nice_classes(nice_classes)

    # Pre-flight quota check so we don't waste a scrape on a quota-exhausted user.
    user_id = str(current_user.id)
    org_id = str(current_user.organization_id)
    with Database() as db:
        plan = get_user_plan(db, user_id)
        eligibility = check_report_eligibility(db, plan["plan_name"], org_id)
        if not eligibility["eligible"]:
            raise HTTPException(status_code=403, detail=_report_limit_detail(plan, eligibility))

    image_bytes, image_mime, image_path = await _read_report_image_upload(image)
    image_used = image_path is not None

    try:
        # 1) Run the agentic search (no live-search credit charged here).
        search_result = await asyncio.to_thread(
            _run_search_sync,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            image_path=image_path,
            attorney_no=attorney_no,
            user_id=user_id,
        )
        _normalize_search_results(search_result)

        # 2) Cancelled mid-search → return early with no quota consumed.
        if search_result.get("source") == "cancelled":
            return {
                "search": search_result,
                "report": None,
                "cancelled": True,
            }

        # 3) Build the report request from the agentic results and run the LLM.
        report_request = _build_report_request_from_search_results(
            query=query,
            nice_classes=nice_classes,
            language=language or "tr",
            image_used=image_used,
            search_results=search_result.get("results") or [],
        )
        report_response = await generate_search_risk_report_data(
            request=report_request,
            current_user=current_user,
            query_image_bytes=image_bytes,
            query_image_mime=image_mime,
        )

        # 4) Attach the search response so the dashboard can render results.
        report_dict = (
            report_response.dict() if hasattr(report_response, "dict") else dict(report_response)
        )
        report_dict["search"] = search_result
        return report_dict
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Intelligent risk report failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass


@router.post("/intelligent-risk-report/public")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.public_intelligent_risk_report", "1/minute"))
async def public_intelligent_risk_report(
    request: Request,
    query: str = Form(..., description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual scoring"),
    classes: Optional[str] = Form(None, description="Nice classes (comma-separated)"),
    language: Optional[str] = Form("tr", description="Report language (tr|en|ar)"),
):
    """
    Landing-page combined flow: agentic TurkPatent search + claimable pending
    risk report, no auth required.

    Tightly rate-limited per IP (default 1/min). The pending report carries a
    short-lived claim_token; charging the user's monthly_reports quota only
    happens when they log in and claim via /risk-report/claim.
    """
    # Feature flag kill switch
    if not is_feature_enabled("live_scraping_enabled"):
        raise HTTPException(status_code=503, detail="Live scraping is temporarily disabled")

    nice_classes = _parse_classes_csv(classes)
    _require_nice_classes(nice_classes)

    image_bytes, image_mime, image_path = await _read_report_image_upload(image)
    image_used = image_path is not None

    try:
        # Public flow has no user_id → no Redis progress events emitted.
        search_result = await asyncio.to_thread(
            _run_search_sync,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            image_path=image_path,
            attorney_no=None,
            user_id=None,
        )
        _normalize_search_results(search_result)

        report_request = _build_report_request_from_search_results(
            query=query,
            nice_classes=nice_classes,
            language=language or "tr",
            image_used=image_used,
            search_results=search_result.get("results") or [],
        )
        report_response = await generate_pending_search_risk_report_data(
            request=report_request,
            query_image_bytes=image_bytes,
            query_image_mime=image_mime,
        )

        report_dict = (
            report_response.dict() if hasattr(report_response, "dict") else dict(report_response)
        )
        report_dict["search"] = search_result
        return report_dict
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Public intelligent risk report failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass


@router.get("")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "60/minute"))
async def intelligent_search(
    request: Request,
    query: str = Query(..., description="Trademark name to search"),
    classes: Optional[str] = Query(None, description="Nice classes (comma-separated)"),
    attorney_no: Optional[str] = Query(None, description="Filter by attorney number"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Agentic search — always scrapes TurkPatent live.

    Pipeline: scrape TurkPatent → generate AI embeddings → ingest →
    score against the database. Falls back to a DB-only assessment if the
    scrape fails or returns no records.

    Requires: a plan with Agentic Search access.
    Deducts 1 credit per call.

    Returns:
    - 200: Success with results
    - 402: Monthly limit exceeded
    - 403: Plan doesn't include Agentic Search
    """
    # Feature flag kill switch
    if not is_feature_enabled("live_scraping_enabled"):
        raise HTTPException(status_code=503, detail="Live scraping is temporarily disabled")

    nice_classes = []
    if classes:
        nice_classes = [int(c.strip()) for c in classes.split(",") if c.strip().isdigit()]

    # Check plan eligibility before running
    with Database() as db:
        can_search, reason, details = check_live_search_eligibility(
            db, str(current_user.id)
        )

    if not can_search:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

    try:
        _uid = str(current_user.id)
        result = await asyncio.to_thread(
            _run_search_sync,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            attorney_no=attorney_no,
            user_id=_uid
        )

        # Always count intelligent search usage
        with Database() as db:
            increment_live_search_usage(
                db,
                str(current_user.id),
                str(current_user.organization_id)
            )
        result['credits_used'] = 1
        result['credits_remaining'] = details['daily_limit'] - (details['used_today'] + 1)

        # Normalize results to match public search format
        _normalize_search_results(result)

        # Free cached CUDA tensors after search
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Intelligent search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "60/minute"))
async def intelligent_search_with_image(
    request: Request,
    query: str = Form(..., description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual scoring"),
    classes: Optional[str] = Form(None, description="Nice classes (comma-separated)"),
    attorney_no: Optional[str] = Form(None, description="Filter by attorney number"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Agentic search with optional image upload for visual scoring.

    Always scrapes TurkPatent live. Text query is required; image is optional
    and enhances scoring with CLIP + DINOv2 + color histogram + OCR similarity
    against DB logos.

    Requires: a plan with Agentic Search access for live scraping.
    """
    # Feature flag kill switch
    if not is_feature_enabled("live_scraping_enabled"):
        raise HTTPException(status_code=503, detail="Live scraping is temporarily disabled")

    nice_classes = []
    if classes:
        nice_classes = [int(c.strip()) for c in classes.split(",") if c.strip().isdigit()]

    # Check plan eligibility
    with Database() as db:
        can_search, reason, details = check_live_search_eligibility(
            db, str(current_user.id)
        )

    if not can_search:
        status_code = 403 if reason == "upgrade_required" else 402
        raise HTTPException(status_code=status_code, detail=details)

    image_path = None
    try:
        # Save uploaded image to temp file if provided
        if image and image.filename:
            suffix = os.path.splitext(image.filename)[1] or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tempfile.gettempdir()) as tmp:
                content = await image.read()
                tmp.write(content)
                image_path = tmp.name
            logger.info(f"Image uploaded: {image.filename} ({len(content)} bytes) -> {image_path}")

        _uid = str(current_user.id)
        result = await asyncio.to_thread(
            _run_search_sync,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            image_path=image_path,
            attorney_no=attorney_no,
            user_id=_uid
        )

        # Always count intelligent search usage
        with Database() as db:
            increment_live_search_usage(
                db,
                str(current_user.id),
                str(current_user.organization_id)
            )
        result['credits_used'] = 1
        result['credits_remaining'] = details['daily_limit'] - (details['used_today'] + 1)

        # Normalize results to match public search format
        _normalize_search_results(result)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Intelligent search with image failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup temp file
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except OSError:
                pass
        # Free cached CUDA tensors after search
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


@router.post("/search")
async def post_search(
    request: SearchRequest,
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Full search via POST request.
    If auto_scrape is True, requires a plan with Agentic Search access.
    """
    # If auto_scrape requested, check plan eligibility
    if request.auto_scrape:
        with Database() as db:
            can_search, reason, details = check_live_search_eligibility(
                db, str(current_user.id)
            )

        if not can_search:
            status_code = 403 if reason == "upgrade_required" else 402
            raise HTTPException(status_code=status_code, detail=details)
    else:
        details = None

    try:
        _uid = str(current_user.id)
        # Pre-existing behavior: this endpoint always runs the agentic path,
        # even if request.auto_scrape=False. Plan eligibility is gated above.
        result = await asyncio.to_thread(
            _run_search_sync,
            auto_scrape=True,
            query=request.query,
            nice_classes=request.nice_classes or [],
            user_id=_uid
        )

        # Always count search usage when auto_scrape is enabled
        if details:
            with Database() as db:
                increment_live_search_usage(
                    db,
                    str(current_user.id),
                    str(current_user.organization_id)
                )
            result['credits_used'] = 1
            result['credits_remaining'] = details['daily_limit'] - (details['used_today'] + 1)
        else:
            result['credits_used'] = 0

        # Free cached CUDA tensors after search
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CLI INTERFACE
# ============================================
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Agentic Trademark Search - Live TurkPatent scrape + DB scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agentic_search.py "dogan patent"
  python agentic_search.py "nike" --classes 25,35
  python agentic_search.py "apple" --visible
  python agentic_search.py "coca cola" --no-scrape
        """
    )

    parser.add_argument("query", nargs="?", default="dogan patent",
                        help="Trademark to search")
    parser.add_argument("--classes", "-c", type=str,
                        help="Nice classes (comma-separated)")
    parser.add_argument("--no-scrape", action="store_true",
                        help="DB-only search (skip TurkPatent scrape)")
    parser.add_argument("--limit", "-l", type=int, default=100,
                        help="Max records to scrape (default: 100)")
    parser.add_argument("--visible", "-v", action="store_true",
                        help="Show browser window")

    args = parser.parse_args()

    # Parse classes
    nice_classes = []
    if args.classes:
        nice_classes = [int(c.strip()) for c in args.classes.split(",") if c.strip().isdigit()]

    # Run search
    with AgenticTrademarkSearch(
        auto_scrape=not args.no_scrape,
        scrape_limit=args.limit,
        headless=not args.visible
    ) as searcher:
        result = searcher.search(
            query=args.query,
            nice_classes=nice_classes,
        )

    # Print results
    print()
    print("=" * 70)
    print(f"{'AGENTIC SEARCH RESULTS':^70}")
    print("=" * 70)
    print(f"  Query:            {result['query']}")
    print(f"  Risk Level:       {result['risk_level']}")
    print(f"  Max Score:        {result['max_score']:.2%}")
    print(f"  Source:           {result['source']}")
    print(f"  Scrape Triggered: {result['scrape_triggered']}")

    if result.get('scraped_count', 0) > 0:
        print(f"  Scraped:          {result['scraped_count']} records")
        print(f"  Ingested:         {result.get('ingested_count', 0)} records")

    print(f"  Total Candidates: {result['total_candidates']}")
    print(f"  Time:             {result['elapsed_seconds']}s")
    print("=" * 70)

    # Top results table
    print()
    print("TOP 10 SIMILAR TRADEMARKS:")
    print("-" * 70)
    print(f"  {'#':>2} | {'Trademark':<30} | {'App No':<15} | {'Score':>6} | Status")
    print("-" * 70)

    for i, r in enumerate(result['results'][:10], 1):
        name = r.get('name', 'N/A')[:30]
        app_no = r.get('application_no', 'N/A')[:15]
        scores = r.get('scores', {})
        score = scores.get('total', 0) if isinstance(scores, dict) else 0
        status = r.get('status', 'N/A')[:12]

        # Flag high risk
        flag = ""
        if score >= 0.90:
            flag = " ⚠️"
        elif score >= 0.85:
            flag = " ⚡"

        print(f"  {i:>2} | {name:<30} | {app_no:<15} | {score:>5.1%} | {status}{flag}")

    print("-" * 70)

    # Return exit code based on risk level
    risk_codes = {"CRITICAL": 5, "VERY_HIGH": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return risk_codes.get(result['risk_level'], 0)


if __name__ == "__main__":
    sys.exit(main())
