"""
Universal Scanner Worker
========================
Scans new trademark applications against the entire database
to detect conflicts for the Opposition Radar feature.

Usage:
    # Process queue continuously
    python -m workers.universal_scanner --daemon

    # Process specific trademark
    python -m workers.universal_scanner --trademark-id <uuid>

    # Process latest bulletin
    python -m workers.universal_scanner --bulletin 2025/03

    # Dry run (no database writes)
    python -m workers.universal_scanner --dry-run --limit 10
"""

import os
import time
import logging
import argparse
from datetime import datetime, timedelta
from utils.deadline import calculate_appeal_deadline
from typing import List, Dict, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv
from risk_engine import get_risk_level, RISK_THRESHOLDS

load_dotenv()

# ============================================
# CONFIGURATION
# ============================================

MIN_SIMILARITY_SCORE = RISK_THRESHOLDS["medium"]  # 0.50 — minimum to be a conflict

# Processing limits
BATCH_SIZE = 50                  # Trademarks to process per batch
MAX_CONFLICTS_PER_MARK = 20      # Max conflicts to store per new trademark
QUEUE_POLL_INTERVAL = 30         # Seconds between queue checks (daemon mode)
DEFAULT_CANDIDATE_LIMIT = int(os.getenv('UNIVERSAL_SCANNER_CANDIDATE_LIMIT', '150'))

# Statuses considered "active" in the registered database
ACTIVE_STATUSES = {'Tescil Edildi', 'Yayında', 'Yenilendi'}

# Opposition deadline computed via utils.deadline.calculate_appeal_deadline()
# (Turkey: 2 calendar months from bulletin publication per KHK m.42)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [SCANNER] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================
# DATABASE CONNECTION
# ============================================

def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', 5433)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD'),
        connect_timeout=30
    )


# ============================================
# UNIVERSAL SCANNER CLASS
# ============================================

class UniversalScanner:
    """
    Scans within-appeal-deadline trademarks against the registered database.
    Treats each within-deadline trademark as a search query using the same
    pre_screen_candidates + calculate_hybrid_risk pipeline as the search engine.
    """

    def __init__(self, conn=None, dry_run: bool = False, candidate_limit: Optional[int] = None):
        self.conn = conn or get_db_connection()
        self.dry_run = dry_run
        self.candidate_limit = max(
            MAX_CONFLICTS_PER_MARK,
            candidate_limit or DEFAULT_CANDIDATE_LIMIT,
        )
        self._risk_engine = None
        self._suppress_risk_logs = (
            os.getenv('UNIVERSAL_SCANNER_VERBOSE_RISK_LOGS', '').lower()
            not in {'1', 'true', 'yes'}
        )
        self._apply_log_policy()

    def _apply_log_policy(self) -> None:
        if self._suppress_risk_logs:
            # Pair-level scoring logs are useful during search debugging, but make
            # bulletin-wide scans dramatically slower and noisy.
            logging.getLogger('risk_engine').disabled = True

    @property
    def risk_engine(self):
        if self._risk_engine is None:
            from risk_engine import RiskEngine
            self._risk_engine = RiskEngine(existing_conn=self.conn)
            self._apply_log_policy()
        return self._risk_engine

    def get_within_deadline_trademarks(self) -> List[Dict]:
        """Return all trademarks whose appeal deadline has not yet passed."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.id, t.name, t.application_no, t.nice_class_numbers,
                       t.bulletin_no, t.bulletin_date, t.image_path, t.appeal_deadline,
                       h.name as holder_name
                FROM trademarks t
                LEFT JOIN holders h ON t.holder_id = h.id
                WHERE t.appeal_deadline IS NOT NULL
                  AND t.appeal_deadline >= CURRENT_DATE
                  AND t.name IS NOT NULL AND length(t.name) >= 2
                ORDER BY t.bulletin_date DESC, t.application_no
            """)
            results = [dict(r) for r in cur.fetchall()]
            logger.info(f"Within-deadline pool: {len(results)} trademarks")
            return results

    def _scan_queue_exists(self) -> bool:
        """Return whether the optional resumable scan queue table exists."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.universal_scan_queue') IS NOT NULL")
                return bool(cur.fetchone()[0])
        except Exception:
            self.conn.rollback()
            return False

    def _mark_queue_item(self, trademark_id: str, status: str, error_message: Optional[str] = None) -> None:
        """Mark a queued item so bulletin scans can resume without rescanning."""
        try:
            with self.conn.cursor() as cur:
                if status == 'completed':
                    cur.execute("""
                        UPDATE universal_scan_queue
                        SET status = 'completed', completed_at = NOW(), error_message = NULL
                        WHERE trademark_id = %s::uuid
                    """, (trademark_id,))
                else:
                    cur.execute("""
                        UPDATE universal_scan_queue
                        SET status = %s, error_message = %s, last_attempt_at = NOW(), attempts = attempts + 1
                        WHERE trademark_id = %s::uuid
                    """, (status, error_message, trademark_id))
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def scan_trademark(
        self,
        trademark_id: str,
        limit: int = MAX_CONFLICTS_PER_MARK,
        candidate_limit: Optional[int] = None,
    ) -> List[Dict]:
        """
        Scan a single within-deadline trademark against the registered database
        using the same pipeline as the search engine.
        """
        logger.info(f"Scanning trademark: {trademark_id}")

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT t.id, t.name, t.application_no, t.nice_class_numbers,
                       t.bulletin_no, t.bulletin_date, t.image_path, t.appeal_deadline,
                       t.holder_id, t.holder_tpe_client_id, h.name as holder_name
                FROM trademarks t
                LEFT JOIN holders h ON t.holder_id = h.id
                WHERE t.id = %s::uuid
            """, (trademark_id,))
            mark = cur.fetchone()

        if not mark:
            logger.warning(f"Trademark not found: {trademark_id}")
            return []

        name = mark['name'] or ''
        target_classes = list(mark['nice_class_numbers'] or [])
        image_path = mark.get('image_path')
        mark_holder_tpe_id = mark.get('holder_tpe_client_id')
        logger.info(f"  Mark: {name} ({mark['application_no']}) classes={target_classes}")

        # Encode query vectors (same as search engine)
        q_text_vec, q_img_vec, q_dino_vec, q_color_vec, q_ocr_text =             self.risk_engine.get_query_vectors(name, image_path)

        # Pre-screen candidates using same 6-stage pipeline as search engine
        raw_candidates = self.risk_engine.pre_screen_candidates(
            name, target_classes, limit=candidate_limit or self.candidate_limit,
            q_img_vec=q_img_vec, q_dino_vec=q_dino_vec, q_ocr_text=q_ocr_text
        )
        logger.info(f"  Pre-screened: {len(raw_candidates)} candidates")

        # Score all candidates
        scored = self.risk_engine.calculate_hybrid_risk(
            raw_candidates, name, q_text_vec, q_img_vec, q_dino_vec, q_color_vec,
            query_ocr_text=q_ocr_text or ''
        )

        # Filter: active statuses only, exclude same holder, apply threshold
        new_classes = set(target_classes)
        conflicts = []
        today = datetime.now().date()
        grace_cutoff = today - timedelta(days=183)  # ~6 months grace period for renewal
        for r in scored:
            # Skip candidates without a trademark ID (can't store)
            if not r.get('trademark_id'):
                continue
            # Exclude trademarks belonging to the same holder
            if mark_holder_tpe_id and r.get('holder_tpe_client_id') == mark_holder_tpe_id:
                continue
            if r['status'] not in ACTIVE_STATUSES:
                continue
            # Skip expired marks (Turkey: 10-year registration + 6-month renewal grace)
            expiry = r.get('expiry_date')
            if expiry:
                from datetime import date as date_type
                if isinstance(expiry, str):
                    try:
                        expiry = date_type.fromisoformat(expiry)
                    except ValueError:
                        expiry = None
                if expiry and expiry < grace_cutoff:
                    continue
            scores = r['scores']
            score = scores['total']
            if score < MIN_SIMILARITY_SCORE:
                continue

            candidate_classes = set(r.get('classes') or [])
            overlapping = new_classes & candidate_classes
            if 99 in new_classes or 99 in candidate_classes:
                overlapping = new_classes | candidate_classes

            text_sim = scores.get('text_similarity', 0)
            semantic_sim = scores.get('semantic_similarity', 0)
            if text_sim > 0.7 and semantic_sim > 0.7:
                conflict_type = 'HYBRID'
            elif text_sim >= semantic_sim:
                conflict_type = 'TEXT'
            else:
                conflict_type = 'SEMANTIC'

            reasons = []
            if text_sim >= 0.8:
                reasons.append('Yuksek metin benzerligi')
            if semantic_sim >= 0.8:
                reasons.append('Yuksek anlamsal benzerlik')
            if overlapping:
                reasons.append(f'Ortak siniflar: {sorted(overlapping)}')
            if r['status'] == 'Tescil Edildi':
                reasons.append('Tescilli marka')

            conflicts.append({
                'existing_mark_id': r.get('trademark_id'),
                'existing_mark_name': r['name'],
                'existing_mark_app_no': r['application_no'],
                'existing_mark_holder_id': r.get('holder_id'),
                'existing_mark_holder_name': r.get('holder_name'),
                'existing_mark_nice_classes': r.get('classes'),
                'similarity_score': score,
                'text_similarity': text_sim,
                'semantic_similarity': semantic_sim,
                'visual_similarity': scores.get('visual_similarity', 0),
                'translation_similarity': scores.get('translation_similarity', 0),
                'conflict_type': conflict_type,
                'risk_level': get_risk_level(score).upper(),
                'overlapping_classes': sorted(overlapping) if overlapping else [],
                'conflict_reasons': reasons
            })

        conflicts.sort(key=lambda x: x['similarity_score'], reverse=True)
        conflicts = conflicts[:limit]
        logger.info(f"  Found {len(conflicts)} conflicts")

        opposition_deadline = calculate_appeal_deadline(mark.get('bulletin_date'))
        if conflicts and not self.dry_run:
            if opposition_deadline is None:
                logger.warning(f"  No bulletin_date for {trademark_id}, cannot store conflicts")
            else:
                with self.conn.cursor() as cur:
                    self._store_conflicts(cur, mark, conflicts, opposition_deadline)
                self.conn.commit()
                logger.info(f"  Stored {len(conflicts)} conflicts")

        return conflicts

    def _store_conflicts(self, cur, new_mark: Dict, conflicts: List[Dict], opposition_deadline):
        """Store detected conflicts in the database."""

        if not conflicts:
            return

        values = []
        for c in conflicts:
            values.append((
                str(new_mark['id']),
                new_mark['name'],
                new_mark['application_no'],
                new_mark.get('holder_name'),
                new_mark['nice_class_numbers'],
                c['existing_mark_id'],
                c['existing_mark_name'],
                c['existing_mark_app_no'],
                c['existing_mark_holder_id'],
                c['existing_mark_holder_name'],
                c['existing_mark_nice_classes'],
                c['similarity_score'],
                c['text_similarity'],
                c.get('visual_similarity', 0),
                c['semantic_similarity'],
                c.get('translation_similarity', 0),
                c['conflict_type'],
                c['overlapping_classes'],
                c['risk_level'],
                c['conflict_reasons'],
                new_mark.get('bulletin_no'),
                new_mark.get('bulletin_date'),
                opposition_deadline
            ))

        execute_values(cur, """
            INSERT INTO universal_conflicts (
                new_mark_id, new_mark_name, new_mark_app_no, new_mark_holder_name, new_mark_nice_classes,
                existing_mark_id, existing_mark_name, existing_mark_app_no,
                existing_mark_holder_id, existing_mark_holder_name, existing_mark_nice_classes,
                similarity_score, text_similarity, visual_similarity, semantic_similarity,
                translation_similarity,
                conflict_type, overlapping_classes, risk_level, conflict_reasons,
                bulletin_no, bulletin_date, opposition_deadline
            ) VALUES %s
            ON CONFLICT (new_mark_id, existing_mark_id)
            DO UPDATE SET
                new_mark_name = EXCLUDED.new_mark_name,
                new_mark_app_no = EXCLUDED.new_mark_app_no,
                new_mark_holder_name = EXCLUDED.new_mark_holder_name,
                new_mark_nice_classes = EXCLUDED.new_mark_nice_classes,
                existing_mark_name = EXCLUDED.existing_mark_name,
                existing_mark_app_no = EXCLUDED.existing_mark_app_no,
                existing_mark_holder_id = EXCLUDED.existing_mark_holder_id,
                existing_mark_holder_name = EXCLUDED.existing_mark_holder_name,
                existing_mark_nice_classes = EXCLUDED.existing_mark_nice_classes,
                similarity_score = EXCLUDED.similarity_score,
                text_similarity = EXCLUDED.text_similarity,
                visual_similarity = EXCLUDED.visual_similarity,
                semantic_similarity = EXCLUDED.semantic_similarity,
                translation_similarity = EXCLUDED.translation_similarity,
                conflict_type = EXCLUDED.conflict_type,
                overlapping_classes = EXCLUDED.overlapping_classes,
                risk_level = EXCLUDED.risk_level,
                conflict_reasons = EXCLUDED.conflict_reasons,
                bulletin_no = EXCLUDED.bulletin_no,
                bulletin_date = EXCLUDED.bulletin_date,
                opposition_deadline = EXCLUDED.opposition_deadline,
                updated_at = NOW()
        """, values)

    def process_queue(self, limit: int = BATCH_SIZE) -> int:
        """
        Process pending items from the scan queue.

        Returns:
            Number of items processed
        """
        logger.info(f"Processing queue (limit: {limit})...")

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get pending items (atomic claim with FOR UPDATE SKIP LOCKED)
            cur.execute("""
                UPDATE universal_scan_queue
                SET status = 'processing', last_attempt_at = NOW(), attempts = attempts + 1
                WHERE id IN (
                    SELECT id FROM universal_scan_queue
                    WHERE status = 'pending'
                    ORDER BY priority DESC, created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING trademark_id, bulletin_no
            """, (limit,))

            items = cur.fetchall()
            self.conn.commit()

            if not items:
                logger.info("  No pending items in queue")
                return 0

            logger.info(f"  Found {len(items)} items to process")

            processed = 0
            for item in items:
                try:
                    self.scan_trademark(str(item['trademark_id']))

                    # Mark as completed
                    cur.execute("""
                        UPDATE universal_scan_queue
                        SET status = 'completed', completed_at = NOW()
                        WHERE trademark_id = %s::uuid
                    """, (str(item['trademark_id']),))
                    self.conn.commit()

                    processed += 1

                except Exception as e:
                    logger.error(f"  Error processing {item['trademark_id']}: {e}")
                    cur.execute("""
                        UPDATE universal_scan_queue
                        SET status = 'failed', error_message = %s
                        WHERE trademark_id = %s::uuid
                    """, (str(e), str(item['trademark_id'])))
                    self.conn.commit()

            return processed

    def run_daemon(self, poll_interval: int = QUEUE_POLL_INTERVAL):
        """Run as a daemon, continuously processing the queue."""
        logger.info("Starting Universal Scanner daemon...")
        logger.info(f"  Poll interval: {poll_interval}s")
        logger.info(f"  Batch size: {BATCH_SIZE}")
        logger.info(f"  Min similarity: {MIN_SIMILARITY_SCORE}")

        while True:
            try:
                processed = self.process_queue()

                if processed == 0:
                    logger.info(f"  Queue empty, sleeping {poll_interval}s...")
                    time.sleep(poll_interval)
                else:
                    logger.info(f"  Processed {processed} items")
                    time.sleep(1)  # Brief pause between batches

            except KeyboardInterrupt:
                logger.info("\nShutting down daemon...")
                break
            except Exception as e:
                logger.error(f"Daemon error: {e}")
                time.sleep(poll_interval)

    def scan_bulletin(self, bulletin_no: str, limit: int = None, skip_completed: bool = True) -> Dict:
        """
        Scan all trademarks from a specific bulletin.

        Args:
            bulletin_no: Bulletin number (e.g., '2025/03')
            limit: Optional limit on trademarks to process

        Returns:
            Summary of processing
        """
        logger.info(f"Scanning bulletin: {bulletin_no}")
        has_queue = self._scan_queue_exists()

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get trademarks from bulletin (skip NULL names — can't produce conflicts)
            query = """
                SELECT t.id, t.name, t.application_no
                FROM trademarks t
                WHERE t.bulletin_no = %s
                  AND t.name IS NOT NULL AND length(t.name) >= 2
                  AND t.status_source = 'BLT'
                  AND t.current_status::text = 'Yayında'
            """
            params: list = [bulletin_no]

            if skip_completed:
                query += """
                  AND NOT EXISTS (
                      SELECT 1
                      FROM universal_conflicts uc
                      WHERE uc.new_mark_id = t.id
                  )
                """
                if has_queue:
                    query += """
                  AND NOT EXISTS (
                      SELECT 1
                      FROM universal_scan_queue q
                      WHERE q.trademark_id = t.id
                        AND q.status = 'completed'
                  )
                """

            query += " ORDER BY t.application_no"

            if limit:
                query += " LIMIT %s"
                params.append(limit)

            cur.execute(query, params)
            trademarks = cur.fetchall()

            logger.info(f"  Found {len(trademarks)} trademarks in bulletin")

            total_conflicts = 0
            errors = 0
            for i, tm in enumerate(trademarks, 1):
                mark_name = tm['name'] or '(no name)'
                logger.info(f"\n[{i}/{len(trademarks)}] {mark_name[:50]}")
                try:
                    conflicts = self.scan_trademark(str(tm['id']))
                    total_conflicts += len(conflicts)
                    if has_queue and not self.dry_run:
                        self._mark_queue_item(str(tm['id']), 'completed')
                except Exception as e:
                    logger.error(f"  Error scanning {tm['id']}: {e}")
                    errors += 1
                    try:
                        self.conn.rollback()
                    except Exception:
                        pass
                    if has_queue and not self.dry_run:
                        self._mark_queue_item(str(tm['id']), 'failed', str(e))

            return {
                'bulletin_no': bulletin_no,
                'trademarks_scanned': len(trademarks),
                'total_conflicts': total_conflicts,
                'errors': errors
            }

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()


# ============================================
# CLI INTERFACE
# ============================================

def main():
    parser = argparse.ArgumentParser(
        description="Universal Scanner - Opposition Radar Worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run as daemon (continuous queue processing)
    python -m workers.universal_scanner --daemon

    # Process specific trademark
    python -m workers.universal_scanner --trademark-id 123e4567-e89b-12d3-a456-426614174000

    # Process bulletin
    python -m workers.universal_scanner --bulletin 2025/03

    # Dry run (no database writes)
    python -m workers.universal_scanner --bulletin 2025/03 --dry-run --limit 5
        """
    )

    parser.add_argument('--daemon', '-d', action='store_true',
                        help='Run as daemon (continuous queue processing)')
    parser.add_argument('--trademark-id', '-t', type=str,
                        help='Scan specific trademark by UUID')
    parser.add_argument('--bulletin', '-b', type=str,
                        help='Scan all trademarks from bulletin (e.g., 2025/03)')
    parser.add_argument('--limit', '-l', type=int,
                        help='Limit number of trademarks to process')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run (no database writes)')
    parser.add_argument('--candidate-limit', type=int, default=None,
                        help=f'Candidate pre-screen cap per trademark (default: {DEFAULT_CANDIDATE_LIMIT})')
    parser.add_argument('--rescan-completed', action='store_true',
                        help='Rescan trademarks that already have conflicts or completed queue entries')
    parser.add_argument('--poll-interval', type=int, default=QUEUE_POLL_INTERVAL,
                        help=f'Queue poll interval in seconds (default: {QUEUE_POLL_INTERVAL})')

    args = parser.parse_args()

    scanner = UniversalScanner(dry_run=args.dry_run, candidate_limit=args.candidate_limit)

    try:
        if args.daemon:
            scanner.run_daemon(poll_interval=args.poll_interval)

        elif args.trademark_id:
            conflicts = scanner.scan_trademark(args.trademark_id)
            print(f"\nFound {len(conflicts)} conflicts:")
            for c in conflicts[:10]:
                print(f"  - {c['existing_mark_name']} ({c['similarity_score']:.1%}) [{c['risk_level']}]")

        elif args.bulletin:
            result = scanner.scan_bulletin(
                args.bulletin,
                limit=args.limit,
                skip_completed=not args.rescan_completed,
            )
            print(f"\n{'='*50}")
            print(f"Bulletin: {result['bulletin_no']}")
            print(f"Scanned: {result['trademarks_scanned']} trademarks")
            print(f"Conflicts: {result['total_conflicts']}")
            print(f"{'='*50}")

        else:
            # Default: process queue once
            processed = scanner.process_queue(limit=args.limit or BATCH_SIZE)
            print(f"Processed {processed} queue items")

    finally:
        scanner.close()


if __name__ == "__main__":
    main()
