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
import sys
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from utils.deadline import calculate_appeal_deadline
from typing import List, Dict, Optional
from uuid import UUID

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv
from risk_engine import score_pair, get_risk_level, calculate_visual_similarity  # Centralized scoring

load_dotenv()

# ============================================
# CONFIGURATION
# ============================================

# Similarity thresholds — uses centralized RISK_THRESHOLDS from risk_engine
from risk_engine import RISK_THRESHOLDS
MIN_SIMILARITY_SCORE = RISK_THRESHOLDS["medium"]  # 0.50 — minimum to be a conflict

# Processing limits
BATCH_SIZE = 50                  # Trademarks to process per batch
MAX_CONFLICTS_PER_MARK = 20      # Max conflicts to store per new trademark
QUEUE_POLL_INTERVAL = 30         # Seconds between queue checks (daemon mode)

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
        port=int(os.getenv('DB_PORT', 5432)),
        database=os.getenv('DB_NAME', 'trademark_db'),
        user=os.getenv('DB_USER', 'turk_patent'),
        password=os.getenv('DB_PASSWORD', ''),
        connect_timeout=30
    )


def _cosine_sim(a, b):
    """Cosine similarity between two vectors."""
    v1, v2 = np.array(a), np.array(b)
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    return float(np.dot(v1, v2) / (n1 * n2)) if n1 > 0 and n2 > 0 else 0.0


# ============================================
# UNIVERSAL SCANNER CLASS
# ============================================

class UniversalScanner:
    """
    Scans new trademarks against the entire database to detect conflicts.
    """

    def __init__(self, conn=None, dry_run: bool = False):
        self.conn = conn or get_db_connection()
        self.dry_run = dry_run

    def scan_trademark(self, trademark_id: str, limit: int = MAX_CONFLICTS_PER_MARK) -> List[Dict]:
        """
        Scan a single new trademark against the entire database.

        Args:
            trademark_id: UUID of the new trademark to scan
            limit: Max conflicts to return

        Returns:
            List of detected conflicts
        """
        logger.info(f"Scanning trademark: {trademark_id}")

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get the new trademark details
            cur.execute("""
                SELECT
                    id, name, application_no, holder_id,
                    (SELECT name FROM holders WHERE id = t.holder_id) as holder_name,
                    nice_class_numbers, bulletin_no, bulletin_date,
                    text_embedding, image_embedding, logo_ocr_text,
                    dinov2_embedding, color_histogram
                FROM trademarks t
                WHERE id = %s::uuid
            """, (trademark_id,))

            new_mark = cur.fetchone()

            if not new_mark:
                logger.warning(f"Trademark not found: {trademark_id}")
                return []

            logger.info(f"  New mark: {new_mark['name']} ({new_mark['application_no']})")
            logger.info(f"  Classes: {new_mark['nice_class_numbers']}")

            # Calculate opposition deadline (2 calendar months from bulletin date)
            bulletin_date = new_mark['bulletin_date']
            opposition_deadline = calculate_appeal_deadline(bulletin_date)

            # Find similar existing trademarks
            conflicts = self._find_conflicts(cur, new_mark, limit)

            logger.info(f"  Found {len(conflicts)} potential conflicts")

            # Store conflicts if not dry run
            # Requires valid opposition_deadline (universal_conflicts.opposition_deadline is NOT NULL)
            if conflicts and not self.dry_run:
                if opposition_deadline is None:
                    logger.warning(f"  No bulletin_date for {trademark_id}, cannot store conflicts (deadline required)")
                else:
                    self._store_conflicts(cur, new_mark, conflicts, opposition_deadline)
                    self.conn.commit()
                    logger.info(f"  Stored {len(conflicts)} conflicts")

            return conflicts

    def _find_conflicts(self, cur, new_mark: Dict, limit: int) -> List[Dict]:
        """
        Find existing trademarks that conflict with the new mark.
        Uses hybrid search: text similarity + vector similarity + class overlap.
        """
        conflicts = []
        new_name = new_mark['name'] or ''
        new_classes = set(new_mark['nice_class_numbers'] or [])

        # Skip if no name
        if not new_name or len(new_name) < 2:
            return []

        # ============================================
        # STRATEGY 1: Text similarity search (trigram)
        # ============================================
        cur.execute("""
            SELECT
                t.id, t.name, t.application_no, t.registration_no,
                t.holder_id, h.name as holder_name,
                t.nice_class_numbers, t.current_status,
                t.application_date, t.registration_date,
                t.image_embedding, t.logo_ocr_text,
                similarity(t.name, %s) as text_sim,
                t.name_tr,
                t.dinov2_embedding, t.color_histogram
            FROM trademarks t
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE t.id != %s::uuid
              AND t.name IS NOT NULL
              AND t.name %% %s
              AND t.current_status IN ('Registered', 'Published', 'Renewed')
            ORDER BY text_sim DESC
            LIMIT 100
        """, (new_name, str(new_mark['id']), new_name))

        text_candidates = cur.fetchall()
        logger.info(f"  Text search: {len(text_candidates)} candidates")

        # ============================================
        # STRATEGY 2: Vector similarity search (if embeddings exist)
        # ============================================
        vector_candidates = []

        if new_mark.get('text_embedding'):
            cur.execute("""
                SELECT
                    t.id, t.name, t.application_no, t.registration_no,
                    t.holder_id, h.name as holder_name,
                    t.nice_class_numbers, t.current_status,
                    t.application_date, t.registration_date,
                    t.image_embedding, t.logo_ocr_text,
                    1 - (t.text_embedding <=> %s::halfvec) as semantic_sim,
                    t.name_tr,
                    t.dinov2_embedding, t.color_histogram
                FROM trademarks t
                LEFT JOIN holders h ON t.holder_id = h.id
                WHERE t.id != %s::uuid
                  AND t.text_embedding IS NOT NULL
                  AND t.current_status IN ('Registered', 'Published', 'Renewed')
                ORDER BY t.text_embedding <=> %s::halfvec
                LIMIT 100
            """, (new_mark['text_embedding'], str(new_mark['id']), new_mark['text_embedding']))

            vector_candidates = cur.fetchall()
            logger.info(f"  Vector search: {len(vector_candidates)} candidates")

        # ============================================
        # MERGE & SCORE CANDIDATES
        # ============================================
        seen_ids = set()
        all_candidates = []

        # Add text candidates
        for c in text_candidates:
            if c['id'] not in seen_ids:
                seen_ids.add(c['id'])
                c['source'] = 'text'
                all_candidates.append(c)

        # Add vector candidates
        for c in vector_candidates:
            if c['id'] not in seen_ids:
                seen_ids.add(c['id'])
                c['source'] = 'vector'
                all_candidates.append(c)

        logger.info(f"  Total unique candidates: {len(all_candidates)}")

        # ============================================
        # CALCULATE COMPREHENSIVE SCORES
        # ============================================
        for candidate in all_candidates:
            candidate_name = (candidate.get('name') or '').strip()
            if not candidate_name:
                continue

            candidate_classes = set(candidate['nice_class_numbers'] or [])

            text_sim = float(candidate.get('text_sim', 0) or 0)
            semantic_sim = float(candidate.get('semantic_sim', 0) or 0)

            # Visual similarity (full composite: CLIP + DINOv2 + color + OCR)
            clip_sim = 0.0
            if new_mark.get('image_embedding') and candidate.get('image_embedding'):
                clip_sim = _cosine_sim(new_mark['image_embedding'], candidate['image_embedding'])

            dino_sim = 0.0
            if new_mark.get('dinov2_embedding') and candidate.get('dinov2_embedding'):
                dino_sim = _cosine_sim(new_mark['dinov2_embedding'], candidate['dinov2_embedding'])

            color_sim = 0.0
            if new_mark.get('color_histogram') and candidate.get('color_histogram'):
                color_sim = _cosine_sim(new_mark['color_histogram'], candidate['color_histogram'])

            visual_sim = calculate_visual_similarity(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=color_sim,
                ocr_text_a=new_mark.get('logo_ocr_text') or '',
                ocr_text_b=candidate.get('logo_ocr_text') or '',
            )

            # Delegate to centralized scoring
            breakdown = score_pair(
                query_name=new_name,
                candidate_name=candidate_name,
                text_sim=text_sim,
                semantic_sim=semantic_sim,
                visual_sim=visual_sim,
                candidate_translations={
                    'name_tr': candidate.get('name_tr') or '',
                },
            )

            final_score = breakdown['total']

            # Class overlap (no additive boost — scoring handled by score_pair)
            overlapping = new_classes & candidate_classes

            # Check for Class 99 (global protection)
            if 99 in new_classes or 99 in candidate_classes:
                overlapping = new_classes | candidate_classes

            # Skip if below threshold
            if final_score < MIN_SIMILARITY_SCORE:
                continue

            # Determine conflict type
            if text_sim > 0.7 and semantic_sim > 0.7:
                conflict_type = 'HYBRID'
            elif text_sim > semantic_sim:
                conflict_type = 'TEXT'
            else:
                conflict_type = 'SEMANTIC'

            # Determine risk level using centralized function
            risk_level = get_risk_level(final_score).upper()

            # Build conflict reasons
            reasons = []
            if text_sim >= 0.8:
                reasons.append('Yuksek metin benzerligi')
            if semantic_sim >= 0.8:
                reasons.append('Yuksek anlamsal benzerlik')
            if overlapping:
                reasons.append(f'Ortak siniflar: {sorted(overlapping)}')
            if candidate['current_status'] == 'Registered':
                reasons.append('Tescilli marka')

            conflicts.append({
                'existing_mark_id': str(candidate['id']),
                'existing_mark_name': candidate_name,
                'existing_mark_app_no': candidate['application_no'],
                'existing_mark_holder_id': str(candidate['holder_id']) if candidate['holder_id'] else None,
                'existing_mark_holder_name': candidate['holder_name'],
                'existing_mark_nice_classes': candidate['nice_class_numbers'],
                'similarity_score': final_score,
                'text_similarity': breakdown.get('text_similarity', text_sim),
                'semantic_similarity': breakdown.get('semantic_similarity', semantic_sim),
                'visual_similarity': breakdown.get('visual_similarity', 0),
                'translation_similarity': breakdown.get('translation_similarity', 0),
                'conflict_type': conflict_type,
                'risk_level': risk_level,
                'overlapping_classes': sorted(overlapping) if overlapping else [],
                'conflict_reasons': reasons
            })

        # Sort by score and limit
        conflicts.sort(key=lambda x: x['similarity_score'], reverse=True)
        return conflicts[:limit]

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
                similarity_score = EXCLUDED.similarity_score,
                text_similarity = EXCLUDED.text_similarity,
                visual_similarity = EXCLUDED.visual_similarity,
                semantic_similarity = EXCLUDED.semantic_similarity,
                translation_similarity = EXCLUDED.translation_similarity,
                risk_level = EXCLUDED.risk_level,
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
                    conflicts = self.scan_trademark(str(item['trademark_id']))

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

    def scan_bulletin(self, bulletin_no: str, limit: int = None) -> Dict:
        """
        Scan all trademarks from a specific bulletin.

        Args:
            bulletin_no: Bulletin number (e.g., '2025/03')
            limit: Optional limit on trademarks to process

        Returns:
            Summary of processing
        """
        logger.info(f"Scanning bulletin: {bulletin_no}")

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get trademarks from bulletin
            query = """
                SELECT id, name, application_no
                FROM trademarks
                WHERE bulletin_no = %s
                ORDER BY application_no
            """
            params: list = [bulletin_no]

            if limit:
                query += " LIMIT %s"
                params.append(limit)

            cur.execute(query, params)
            trademarks = cur.fetchall()

            logger.info(f"  Found {len(trademarks)} trademarks in bulletin")

            total_conflicts = 0
            for i, tm in enumerate(trademarks, 1):
                logger.info(f"\n[{i}/{len(trademarks)}] {tm['name'][:50]}")
                conflicts = self.scan_trademark(str(tm['id']))
                total_conflicts += len(conflicts)

            return {
                'bulletin_no': bulletin_no,
                'trademarks_scanned': len(trademarks),
                'total_conflicts': total_conflicts
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
    parser.add_argument('--poll-interval', type=int, default=QUEUE_POLL_INTERVAL,
                        help=f'Queue poll interval in seconds (default: {QUEUE_POLL_INTERVAL})')

    args = parser.parse_args()

    scanner = UniversalScanner(dry_run=args.dry_run)

    try:
        if args.daemon:
            scanner.run_daemon(poll_interval=args.poll_interval)

        elif args.trademark_id:
            conflicts = scanner.scan_trademark(args.trademark_id)
            print(f"\nFound {len(conflicts)} conflicts:")
            for c in conflicts[:10]:
                print(f"  - {c['existing_mark_name']} ({c['similarity_score']:.1%}) [{c['risk_level']}]")

        elif args.bulletin:
            result = scanner.scan_bulletin(args.bulletin, limit=args.limit)
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
