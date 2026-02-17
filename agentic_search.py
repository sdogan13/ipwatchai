"""
Agentic Trademark Search Pipeline
=================================
Orchestrates: risk_engine → scrapper → ai → ingest → risk_engine

Flow:
1. Search local database (2.3M records)
2. If confidence < 75%, trigger live scrape
3. Generate AI embeddings for scraped data
4. Ingest new data to database
5. Recalculate risk with complete data

Usage:
    python agentic_search.py "dogan patent"
    python agentic_search.py "nike" --classes 25,35
    python agentic_search.py "apple" --force-scrape --visible
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
from typing import Dict, List, Optional
from datetime import datetime
from dotenv import load_dotenv
from config.settings import settings

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
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", r"C:\Users\701693\turk_patent"))
DATA_ROOT = Path(os.getenv("DATA_ROOT", r"C:\Users\701693\turk_patent\bulletins\Marka"))

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
    Intelligent trademark search with automatic live investigation.

    When database confidence is low, automatically:
    1. Scrapes TurkPatent for live data
    2. Generates AI embeddings
    3. Ingests to database
    4. Recalculates risk score
    """

    def __init__(
        self,
        confidence_threshold: float = 0.75,
        auto_scrape: bool = True,
        scrape_limit: int = 100,
        headless: bool = True
    ):
        """
        Args:
            confidence_threshold: Trigger live search if max score below this
            auto_scrape: Enable automatic scraping
            scrape_limit: Max records to scrape
            headless: Run browser in headless mode
        """
        self.confidence_threshold = confidence_threshold
        self.auto_scrape = auto_scrape
        self.scrape_limit = scrape_limit
        self.headless = headless

        # Lazy-loaded components
        self._risk_engine = None
        self._scrapper = None
        self._conn = None

        # Track scraped data location
        self.scraped_data_dir = DATA_ROOT / "APP_LIVE"
        try:
            self.scraped_data_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # Read-only filesystem (e.g. Docker container)

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
        force_scrape: bool = False,
        image_path: str = None,
        status_filter: str = None,
        attorney_no: str = None,
        user_id: str = None
    ) -> Dict:
        """
        Intelligent search with automatic live investigation.

        Args:
            query: Trademark name to search
            nice_classes: Optional Nice class filter
            force_scrape: Force live scrape regardless of DB results
            image_path: Optional path to logo image for visual scoring

        Returns:
            Complete search results with risk assessment
        """
        start_time = time.time()
        nice_classes = nice_classes or []

        logger.info("=" * 60)
        logger.info(f"AGENTIC SEARCH: '{query}'")
        logger.info("=" * 60)
        logger.info(f"   Nice Classes: {nice_classes or 'All'}")
        logger.info(f"   Confidence Threshold: {self.confidence_threshold:.0%}")
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

        # Clear stale cancel flag + progress list from previous search
        _clear_cancel()
        if user_id:
            _clear_progress(user_id)

        _prog("starting", 0, query)

        # ============================================
        # STEP 1: Search Local Database
        # ============================================
        logger.info("")
        logger.info("STEP 1/5: Searching local database (2.3M records)...")
        step1_start = time.time()
        _prog("db_search", 5, query)

        db_result, needs_live = self.risk_engine.assess_brand_risk(
            name=query,
            image_path=image_path,
            target_classes=nice_classes if nice_classes else None,
            status_filter=status_filter,
            attorney_no=attorney_no
        )

        db_max_score = db_result.get("final_risk_score", 0)
        db_candidates = db_result.get("top_candidates", [])
        image_used = image_path is not None

        logger.info(f"   [OK] Found {len(db_candidates)} candidates")
        logger.info(f"   [OK] Max score: {db_max_score:.2%}")
        logger.info(f"   [OK] Time: {time.time() - step1_start:.2f}s")
        _prog("db_search_done", 15, f"{len(db_candidates)}")

        # Show top 3 from database
        if db_candidates:
            logger.info("   Top 3 from database:")
            for i, c in enumerate(db_candidates[:3]):
                name = c.get('name', 'N/A')
                score = c.get('scores', {}).get('total', 0)
                logger.info(f"      {i+1}. {name[:30]} (score: {score:.3f})")

        # ============================================
        # STEP 2: Decide if Live Scrape Needed
        # ============================================
        needs_live_search = (
            force_scrape or
            db_max_score < self.confidence_threshold or
            len(db_candidates) == 0
        )

        if not needs_live_search:
            logger.info("")
            logger.info(f"HIGH CONFIDENCE ({db_max_score:.2%} >= {self.confidence_threshold:.0%})")
            logger.info("No live scrape needed.")

            return self._build_response(
                query=query,
                results=db_candidates,
                max_score=db_max_score,
                source="database",
                scrape_triggered=False,
                elapsed_time=time.time() - start_time,
                image_used=image_used,
            )

        if not self.auto_scrape:
            logger.info("")
            logger.info(f"LOW CONFIDENCE ({db_max_score:.2%} < {self.confidence_threshold:.0%})")
            logger.info("Auto-scrape disabled. Returning database results.")

            response = self._build_response(
                query=query,
                results=db_candidates,
                max_score=db_max_score,
                source="database",
                scrape_triggered=False,
                elapsed_time=time.time() - start_time,
                image_used=image_used,
            )
            response["needs_live_investigation"] = True
            return response

        # ---- Cancel check before STEP 2 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before scraping.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=db_candidates, max_score=db_max_score,
                source="cancelled", scrape_triggered=False, elapsed_time=time.time() - start_time,
                image_used=image_used)

        # ============================================
        # STEP 2: Scrape TurkPatent Live
        # ============================================
        logger.info("")
        logger.info("STEP 2/5: Live scraping TurkPatent...")
        if force_scrape:
            logger.info(f"   Reason: force_scrape=True (score was {db_max_score:.2%})")
        else:
            logger.info(f"   Reason: Score {db_max_score:.2%} < Threshold {self.confidence_threshold:.0%}")
        step2_start = time.time()
        _prog("scraping", 20, query)

        try:
            scraped_records = self._run_scrapper(query, _prog=_prog)
            scraped_count = len(scraped_records) if scraped_records else 0
            logger.info(f"   [OK] Scraped {scraped_count} records")
            logger.info(f"   [OK] Time: {time.time() - step2_start:.2f}s")
            _prog("scraping_done", 45, str(scraped_count))
        except Exception as e:
            logger.error(f"   [FAIL] Scraping failed: {e}")
            _prog("scraping_failed", 45, str(e))
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
            logger.info("   No new records scraped. Returning database results.")
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

        # ---- Cancel check before STEP 3 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before embeddings.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=db_candidates, max_score=db_max_score,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                elapsed_time=time.time() - start_time, image_used=image_used)

        # ============================================
        # STEP 3: Generate AI Embeddings + Translations
        # ============================================
        logger.info("")
        logger.info("STEP 3/5: Generating AI embeddings...")
        step3_start = time.time()
        _prog("embeddings", 50, str(scraped_count))

        try:
            enriched_count = self._generate_embeddings(scraped_records)
            logger.info(f"   [OK] Generated embeddings for {enriched_count} records")
            logger.info(f"   [OK] Time: {time.time() - step3_start:.2f}s")
            _prog("embeddings_done", 60, str(enriched_count))
        except Exception as e:
            logger.warning(f"   [WARN] Embedding generation failed: {e}")
            enriched_count = 0

        # ---- Cancel check before STEP 4 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before ingestion.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=db_candidates, max_score=db_max_score,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                elapsed_time=time.time() - start_time, image_used=image_used)

        # ============================================
        # STEP 4: Ingest to Database
        # ============================================
        logger.info("")
        logger.info("STEP 4/5: Ingesting to database...")
        step4_start = time.time()
        _prog("ingesting", 65, str(scraped_count))

        try:
            ingested_count = self._ingest_to_database(scraped_records, query)
            logger.info(f"   [OK] Ingested {ingested_count} records")
            logger.info(f"   [OK] Time: {time.time() - step4_start:.2f}s")
            _prog("ingesting_done", 80, str(ingested_count))
        except Exception as e:
            logger.error(f"   [FAIL] Ingestion failed: {e}")
            ingested_count = 0

        # ---- Cancel check before STEP 5 ----
        if _is_cancelled():
            logger.info("   [CANCELLED] Search cancelled by user before scoring.")
            _prog("cancelled", 0)
            return self._build_response(query=query, results=db_candidates, max_score=db_max_score,
                source="cancelled", scrape_triggered=True, scraped_count=scraped_count,
                ingested_count=ingested_count, elapsed_time=time.time() - start_time,
                image_used=image_used)

        # ============================================
        # STEP 5: Recalculate Risk Score
        # ============================================
        logger.info("")
        logger.info("STEP 5/5: Recalculating risk score...")
        step5_start = time.time()
        _prog("scoring", 85)

        final_result, _ = self.risk_engine.assess_brand_risk(
            name=query,
            image_path=image_path,
            target_classes=nice_classes if nice_classes else None,
            status_filter=status_filter,
            attorney_no=attorney_no
        )

        final_max_score = final_result.get("final_risk_score", 0)
        final_candidates = final_result.get("top_candidates", [])

        logger.info(f"   [OK] New max score: {final_max_score:.2%}")
        logger.info(f"   [OK] Total candidates: {len(final_candidates)}")
        logger.info(f"   [OK] Time: {time.time() - step5_start:.2f}s")
        _prog("complete", 100, f"{len(final_candidates)}")

        # Calculate improvement
        score_improvement = final_max_score - db_max_score

        # ============================================
        # FINAL SUMMARY
        # ============================================
        total_time = time.time() - start_time

        logger.info("")
        logger.info("=" * 60)
        logger.info("AGENTIC SEARCH COMPLETE")
        logger.info("=" * 60)
        logger.info(f"   Query:            {query}")
        logger.info(f"   Initial Score:    {db_max_score:.2%}")
        logger.info(f"   Final Score:      {final_max_score:.2%}")
        sign = '+' if score_improvement >= 0 else ''
        logger.info(f"   Improvement:      {sign}{score_improvement:.2%}")
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
            score_before=db_max_score,
            score_improvement=score_improvement,
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
                results = self.scrapper.search_and_ingest(
                    trademark_name=query,
                    limit=self.scrape_limit
                )

                if not results:
                    return []

                # Results are raw rows from scrapper, convert to dict format
                if isinstance(results[0], list):
                    formatted = self._format_scraped_rows(results)
                else:
                    formatted = results

                # Save to live scrape directory
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_query = "".join(c if c.isalnum() else "_" for c in query)
                output_file = self.scraped_data_dir / f"live_{safe_query}_{timestamp}.json"

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(formatted, f, ensure_ascii=False, indent=2)

                logger.info(f"   Saved to: {output_file.name}")

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
        """Generate AI embeddings + translations for scraped records using ai.py."""
        try:
            import ai

            enriched_count = 0
            for record in records:
                name = record.get("TRADEMARK", {}).get("NAME", "")

                if name:
                    try:
                        # Generate text embedding
                        text_emb = ai.get_text_embedding_cached(name)
                        if text_emb:
                            record["text_embedding"] = text_emb
                            enriched_count += 1
                    except Exception as e:
                        logger.debug(f"Failed to generate embedding for {name}: {e}")

                    try:
                        # Generate Turkish translation + language detection
                        trans = ai.get_translations(name)
                        if trans.get("name_tr"):
                            record["name_tr"] = trans["name_tr"]
                        if trans.get("detected_lang"):
                            record["detected_lang"] = trans["detected_lang"]
                    except Exception as e:
                        logger.debug(f"Failed to translate {name}: {e}")

            return enriched_count

        except ImportError:
            logger.warning("ai.py not available, skipping embedding generation")
            return 0

    def _ingest_to_database(self, records: List[Dict], query: str) -> int:
        """Ingest enriched records to database using ingest.py."""
        if not records:
            return 0

        # Create folder structure expected by ingest.py
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_query = "".join(c if c.isalnum() else "_" for c in query)

        live_folder = self.scraped_data_dir / f"LIVE_{safe_query}_{timestamp}"
        live_folder.mkdir(parents=True, exist_ok=True)

        metadata_file = live_folder / "metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        # Call ingest.py to process
        try:
            from ingest import process_file_batch

            process_file_batch(self.conn, metadata_file, force=True)
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
        score_before: float = None,
        score_improvement: float = None,
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
            "score_before": score_before,
            "score_improvement": score_improvement,
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
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database
from utils.settings_manager import get_rate_limit_value
from utils.feature_flags import is_feature_enabled
from utils.subscription import (
    check_live_search_eligibility,
    check_quick_search_eligibility,
    increment_live_search_usage,
    increment_quick_search_usage,
    get_user_plan,
    get_live_search_usage,
)

_search_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/v1/search", tags=["Agentic Search"])


def _normalize_search_results(result: dict) -> None:
    """
    Normalize raw search results in-place to match the public search format.
    Adds: image_url, trademark_name, nice_classes, flat scores (risk_score,
    text_similarity, visual_similarity, translation_similarity, phonetic_similarity).
    Keeps original fields for backward compatibility.
    """
    for r in result.get("results", []):
        scores = r.get("scores") or {}
        img_path = r.get("image_path")
        r["image_url"] = f"/api/trademark-image/{img_path}" if img_path else None
        r["trademark_name"] = r.get("trademark_name") or r.get("name", "")
        r["nice_classes"] = r.get("nice_classes") or r.get("classes") or []
        r["risk_score"] = scores.get("total") if scores.get("total") is not None else r.get("risk_score", 0)
        r["text_similarity"] = round(scores.get("text_similarity", 0), 3)
        r["visual_similarity"] = round(scores.get("visual_similarity", 0), 3)
        r["translation_similarity"] = round(scores.get("translation_similarity", 0), 3)
        r["phonetic_similarity"] = round(scores.get("phonetic_similarity", 0), 3)


class SearchRequest(BaseModel):
    """Request model for intelligent search."""
    query: str
    nice_classes: Optional[list] = None
    force_scrape: bool = False
    confidence_threshold: float = 0.75
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


def _run_search_sync(confidence_threshold, auto_scrape, query, nice_classes,
                      force_scrape=False, image_path=None,
                      status_filter=None, attorney_no=None, user_id=None):
    """Run AgenticTrademarkSearch synchronously (for use with asyncio.to_thread)."""
    with AgenticTrademarkSearch(
        confidence_threshold=confidence_threshold,
        auto_scrape=auto_scrape
    ) as searcher:
        return searcher.search(
            query=query,
            nice_classes=nice_classes,
            force_scrape=force_scrape,
            image_path=image_path,
            status_filter=status_filter,
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
    """Cancel the current running live search."""
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
    Get current user's live search credit status.
    Returns plan info and remaining credits.
    """
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        current_usage = get_live_search_usage(db, str(current_user.id))
        monthly_limit = plan['monthly_limit']

        return {
            "plan": plan['plan_name'],
            "display_name": plan['display_name'],
            "can_use_live_search": plan['can_use_live_search'],
            "monthly_limit": monthly_limit,
            "used_this_month": current_usage,
            "remaining": max(0, monthly_limit - current_usage),
            "resets_on": datetime.now().strftime('%Y-%m') + "-01",
        }


@router.get("/quick")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.quick_search", "60/minute"))
async def quick_search(
    request: Request,
    query: str = Query(..., description="Trademark name to search"),
    classes: Optional[str] = Query(None, description="Nice classes (comma-separated)"),
    status: Optional[str] = Query(None, description="Filter by trademark status"),
    attorney_no: Optional[str] = Query(None, description="Filter by attorney number"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Quick database-only search (no live scraping).
    Returns results from local database only.
    Subject to daily search cap per plan.
    """
    # Daily search cap check
    with Database() as db:
        can_search, reason, details = check_quick_search_eligibility(
            db, str(current_user.id)
        )
        if not can_search:
            raise HTTPException(status_code=429, detail=details)

    nice_classes = []
    if classes:
        nice_classes = [int(c.strip()) for c in classes.split(",") if c.strip().isdigit()]

    try:
        with AgenticTrademarkSearch(
            confidence_threshold=0.75,
            auto_scrape=False  # Quick search = no scraping
        ) as searcher:
            result = searcher.search(
                query=query,
                nice_classes=nice_classes,
                force_scrape=False,
                status_filter=status,
                attorney_no=attorney_no
            )

        # Normalize results to match public search format
        _normalize_search_results(result)

        # Increment daily counter after successful search
        with Database() as db:
            increment_quick_search_usage(
                db, str(current_user.id), str(current_user.organization_id)
            )

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
        logger.error(f"Quick search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/quick")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.quick_search", "60/minute"))
async def quick_search_with_image(
    request: Request,
    query: Optional[str] = Form(None, description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual search"),
    classes: Optional[str] = Form(None, description="Nice classes (comma-separated)"),
    status: Optional[str] = Form(None, description="Filter by trademark status"),
    attorney_no: Optional[str] = Form(None, description="Filter by attorney number"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Quick database-only search with optional image upload for visual scoring.
    No live scraping. Subject to daily search cap per plan.
    """
    has_image = image is not None and image.filename
    has_query = query and len(query.strip()) >= 2
    if not has_query and not has_image:
        raise HTTPException(status_code=422, detail="Provide a brand name (min 2 chars) or upload a logo image")
    query = query.strip() if query else ""

    # Daily search cap check
    with Database() as db:
        can_search, reason, details = check_quick_search_eligibility(
            db, str(current_user.id)
        )
        if not can_search:
            raise HTTPException(status_code=429, detail=details)

    nice_classes = []
    if classes:
        nice_classes = [int(c.strip()) for c in classes.split(",") if c.strip().isdigit()]

    image_path = None
    try:
        # Save uploaded image to temp file if provided
        if has_image:
            suffix = os.path.splitext(image.filename)[1] or ".png"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tempfile.gettempdir()) as tmp:
                content = await image.read()
                tmp.write(content)
                image_path = tmp.name
            logger.info(f"Quick search image uploaded: {image.filename} ({len(content)} bytes)")

        with AgenticTrademarkSearch(
            confidence_threshold=0.75,
            auto_scrape=False
        ) as searcher:
            result = searcher.search(
                query=query,
                nice_classes=nice_classes,
                force_scrape=False,
                image_path=image_path,
                status_filter=status,
                attorney_no=attorney_no
            )

        # Normalize results to match public search format
        _normalize_search_results(result)

        # Increment daily counter after successful search
        with Database() as db:
            increment_quick_search_usage(
                db, str(current_user.id), str(current_user.organization_id)
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Quick search with image failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
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


@router.get("/intelligent")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "10/minute"))
async def intelligent_search(
    request: Request,
    query: str = Query(..., description="Trademark name to search"),
    classes: Optional[str] = Query(None, description="Nice classes (comma-separated)"),
    status: Optional[str] = Query(None, description="Filter by trademark status"),
    attorney_no: Optional[str] = Query(None, description="Filter by attorney number"),
    threshold: float = Query(0.75, description="Confidence threshold for live scraping"),
    force_scrape: bool = Query(False, description="Force live scrape regardless of DB results"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Intelligent search with automatic live investigation.

    Requires: Professional or Enterprise plan.
    Deducts 1 credit per live scrape triggered.

    If database confidence is below threshold, automatically:
    1. Scrapes TurkPatent for live data
    2. Generates AI embeddings
    3. Ingests to database
    4. Recalculates risk score

    Returns:
    - 200: Success with results
    - 402: Monthly limit exceeded
    - 403: Plan doesn't include live search
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
        # Canlı Arama always scrapes TurkPatent for live data
        result = await asyncio.to_thread(
            _run_search_sync,
            confidence_threshold=threshold,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            force_scrape=True,
            status_filter=status,
            attorney_no=attorney_no,
            user_id=_uid
        )

        # Always count intelligent search usage
        with Database() as db:
            new_count = increment_live_search_usage(
                db,
                str(current_user.id),
                str(current_user.organization_id)
            )
        result['credits_used'] = 1
        result['credits_remaining'] = details['monthly_limit'] - (details['current_usage'] + 1)

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


@router.post("/intelligent")
@_search_limiter.limit(lambda: get_rate_limit_value("rate_limit.intelligent_search", "10/minute"))
async def intelligent_search_with_image(
    request: Request,
    query: str = Form(..., description="Trademark name to search"),
    image: Optional[UploadFile] = File(None, description="Optional logo image for visual scoring"),
    classes: Optional[str] = Form(None, description="Nice classes (comma-separated)"),
    status: Optional[str] = Form(None, description="Filter by trademark status"),
    attorney_no: Optional[str] = Form(None, description="Filter by attorney number"),
    threshold: float = Form(0.75, description="Confidence threshold for live scraping"),
    force_scrape: bool = Form(False, description="Force live scrape regardless of DB results"),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Intelligent search with optional image upload for visual scoring.

    Text query is always required. Image is optional and enhances scoring
    with CLIP + DINOv2 + color histogram + OCR similarity against DB logos.

    Requires: Professional or Enterprise plan for live scraping.
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
        # Canlı Arama always scrapes TurkPatent for live data
        result = await asyncio.to_thread(
            _run_search_sync,
            confidence_threshold=threshold,
            auto_scrape=True,
            query=query,
            nice_classes=nice_classes,
            force_scrape=True,
            image_path=image_path,
            status_filter=status,
            attorney_no=attorney_no,
            user_id=_uid
        )

        # Always count intelligent search usage
        with Database() as db:
            new_count = increment_live_search_usage(
                db,
                str(current_user.id),
                str(current_user.organization_id)
            )
        result['credits_used'] = 1
        result['credits_remaining'] = details['monthly_limit'] - (details['current_usage'] + 1)

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
    If auto_scrape is True, requires Professional or Enterprise plan.
    """
    # If auto_scrape requested, check plan eligibility
    if request.auto_scrape or request.force_scrape:
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
        # Canlı Arama always scrapes TurkPatent for live data
        result = await asyncio.to_thread(
            _run_search_sync,
            confidence_threshold=request.confidence_threshold,
            auto_scrape=True,
            query=request.query,
            nice_classes=request.nice_classes or [],
            force_scrape=True,
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
            result['credits_remaining'] = details['monthly_limit'] - (details['current_usage'] + 1)
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
        description="Agentic Trademark Search - Intelligent search with auto-scraping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python agentic_search.py "dogan patent"
  python agentic_search.py "nike" --classes 25,35
  python agentic_search.py "apple" --force-scrape --visible
  python agentic_search.py "coca cola" --no-scrape --threshold 0.80
        """
    )

    parser.add_argument("query", nargs="?", default="dogan patent",
                        help="Trademark to search")
    parser.add_argument("--classes", "-c", type=str,
                        help="Nice classes (comma-separated)")
    parser.add_argument("--threshold", "-t", type=float, default=0.75,
                        help="Confidence threshold (default: 0.75)")
    parser.add_argument("--no-scrape", action="store_true",
                        help="Disable auto-scraping")
    parser.add_argument("--force-scrape", "-f", action="store_true",
                        help="Force live scrape")
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
        confidence_threshold=args.threshold,
        auto_scrape=not args.no_scrape,
        scrape_limit=args.limit,
        headless=not args.visible
    ) as searcher:
        result = searcher.search(
            query=args.query,
            nice_classes=nice_classes,
            force_scrape=args.force_scrape
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

    if result.get('score_improvement') is not None:
        print(f"  Score Before:     {result['score_before']:.2%}")
        sign = '+' if result['score_improvement'] >= 0 else ''
        print(f"  Improvement:      {sign}{result['score_improvement']:.2%}")

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
