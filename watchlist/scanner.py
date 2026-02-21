"""
Watchlist Monitoring Scanner
Scans new trademarks against all active watchlist items
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any
from uuid import UUID
import numpy as np

from config.settings import settings
from database.crud import (
    Database, WatchlistCRUD, AlertCRUD, ScanLogCRUD, get_db_connection
)
from utils.idf_scoring import (
    calculate_comprehensive_score,
    normalize_turkish,
    MAX_ALERTS_PER_ITEM
)
from utils.class_utils import (
    GLOBAL_CLASS,
    get_overlapping_classes,
)
import ai  # Shared AI models (loaded once at app startup)
from risk_engine import score_pair, calculate_visual_similarity  # Centralized scoring
from utils.phonetic import calculate_phonetic_similarity  # Graduated phonetic scoring

logger = logging.getLogger(__name__)


def generate_logo_embeddings(logo_path: str) -> Optional[Dict]:
    """
    Generate all visual embeddings for a logo image file.
    Returns dict with clip_embedding, dino_embedding, color_histogram, ocr_text
    or None if the file cannot be processed.
    """
    import os
    if not logo_path or not os.path.isfile(logo_path):
        logger.warning(f"Logo file not found: {logo_path}")
        return None

    try:
        from PIL import Image
        import torch
        import cv2

        img = Image.open(logo_path).convert('RGB')
        result: Dict[str, Any] = {}

        # CLIP embedding (512-dim)
        try:
            clip_input = ai.clip_preprocess(img).unsqueeze(0).to(ai.device)
            with torch.no_grad():
                clip_emb = ai.clip_model.encode_image(clip_input)
                clip_emb = clip_emb / clip_emb.norm(dim=-1, keepdim=True)
            result['clip_embedding'] = clip_emb.cpu().squeeze().tolist()
        except Exception as e:
            logger.warning(f"CLIP embedding failed: {e}")

        # DINOv2 embedding (768-dim)
        try:
            dino_input = ai.dinov2_transform(img).unsqueeze(0).to(ai.device)
            with torch.no_grad():
                dino_emb = ai.dinov2_model(dino_input)
                dino_emb = dino_emb / dino_emb.norm(dim=-1, keepdim=True)
            result['dino_embedding'] = dino_emb.cpu().squeeze().tolist()
        except Exception as e:
            logger.warning(f"DINOv2 embedding failed: {e}")

        # Color histogram (32-dim: 8 bins per H/S/V + 8 grayscale)
        try:
            img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
            h_hist = cv2.calcHist([hsv], [0], None, [8], [0, 180]).flatten()
            s_hist = cv2.calcHist([hsv], [1], None, [8], [0, 256]).flatten()
            v_hist = cv2.calcHist([hsv], [2], None, [8], [0, 256]).flatten()
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            g_hist = cv2.calcHist([gray], [0], None, [8], [0, 256]).flatten()
            combined = np.concatenate([h_hist, s_hist, v_hist, g_hist])
            norm = np.linalg.norm(combined)
            if norm > 0:
                combined = combined / norm
            result['color_histogram'] = combined.tolist()
        except Exception as e:
            logger.warning(f"Color histogram failed: {e}")

        # OCR text
        try:
            from utils.idf_scoring import extract_ocr_text
            result['ocr_text'] = extract_ocr_text(logo_path)
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")

        return result if result else None

    except Exception as e:
        logger.error(f"Logo embedding generation failed for {logo_path}: {e}")
        return None


class WatchlistScanner:
    """
    Scans trademarks against watchlist items to detect conflicts.

    Usage:
        scanner = get_scanner()  # Use singleton for model caching

        # After ingesting new bulletin
        alerts = scanner.scan_new_trademarks(trademark_ids, "bulletin", "BLT_500")

        # Full rescan
        alerts = scanner.full_rescan()
    """

    # Configuration constants
    MIN_THRESHOLD = 0.70  # Minimum 70% similarity required
    # MAX_ALERTS_PER_ITEM imported from utils.idf_scoring (global constant = 10)

    def __init__(self, db_conn=None):
        self.conn = db_conn or get_db_connection()
        self.db = Database(self.conn)

    def scan_new_trademarks(
        self,
        trademark_ids: List[UUID],
        source_type: str,
        source_reference: str,
        progress_callback=None
    ) -> int:
        """
        Scan specific trademarks against all active watchlists.
        Called after ingest.py processes new data.

        Args:
            trademark_ids: List of newly ingested trademark IDs
            source_type: 'bulletin', 'gazette', 'live_search'
            source_reference: e.g., 'BLT_500', 'GZ_123', 'APP_5'
            progress_callback: Optional callback(percent, message)

        Returns:
            Number of alerts generated
        """
        logger.info(f"Starting scan of {len(trademark_ids)} trademarks from {source_reference}")

        # Create scan log
        scan_id = ScanLogCRUD.create(self.db, source_type, source_reference)

        try:
            # Get all active watchlist items
            watchlist_items = WatchlistCRUD.get_all_active(self.db)
            logger.info(f"   Checking against {len(watchlist_items)} watchlist items")

            if not watchlist_items:
                ScanLogCRUD.complete(self.db, scan_id, len(trademark_ids), 0, 0)
                return 0

            # Get trademark details
            trademarks = self._get_trademarks_by_ids(trademark_ids)

            alerts_generated = 0
            total_checks = len(trademarks) * len(watchlist_items)
            checks_done = 0

            # Collect conflicts per watchlist item (for limiting)
            conflicts_by_watchlist: Dict[str, List[Tuple[Dict, Dict]]] = {}

            for tm in trademarks:
                for wl_item in watchlist_items:
                    wl_id = wl_item['id']

                    # Check for conflict
                    conflict = self._check_conflict(tm, wl_item)

                    # Enforce minimum threshold (at least 70%)
                    threshold = max(wl_item.get('alert_threshold', 0.75), self.MIN_THRESHOLD)

                    if conflict and conflict['total'] >= threshold:
                        # Collect conflict for later sorting/limiting
                        if wl_id not in conflicts_by_watchlist:
                            conflicts_by_watchlist[wl_id] = []
                        conflicts_by_watchlist[wl_id].append((tm, conflict, wl_item))

                    checks_done += 1
                    if progress_callback and checks_done % 100 == 0:
                        progress_callback(
                            int(checks_done / total_checks * 100),
                            f"Checked {checks_done}/{total_checks}"
                        )

            # Now create alerts with per-item limit
            for wl_id, conflict_list in conflicts_by_watchlist.items():
                # Sort by score DESC
                conflict_list.sort(key=lambda x: x[1]['total'], reverse=True)

                # Limit to MAX_ALERTS_PER_ITEM
                limited_conflicts = conflict_list[:MAX_ALERTS_PER_ITEM]

                if len(conflict_list) > MAX_ALERTS_PER_ITEM:
                    wl_name = conflict_list[0][2]['brand_name']
                    logger.info(
                        f"   Limiting '{wl_name}': {len(conflict_list)} conflicts -> "
                        f"top {MAX_ALERTS_PER_ITEM}"
                    )

                for tm, conflict, wl_item in limited_conflicts:
                    # Check if alert already exists
                    if not AlertCRUD.check_duplicate(
                        self.db,
                        UUID(wl_item['id']),
                        tm['application_no']
                    ):
                        # Get overlapping classes from conflict scores
                        overlap = conflict.get('overlapping_classes', [])

                        # Create alert with overlapping classes
                        AlertCRUD.create(
                            self.db,
                            org_id=UUID(wl_item['organization_id']),
                            watchlist_id=UUID(wl_item['id']),
                            conflicting_trademark={
                                'id': tm.get('id'),
                                'name': tm.get('name'),
                                'application_no': tm.get('application_no'),
                                'status': tm.get('current_status'),
                                'classes': tm.get('nice_class_numbers', []),
                                'holder': tm.get('holder_name'),
                                'image_path': tm.get('image_path')
                            },
                            scores=conflict,
                            source_info={
                                'type': source_type,
                                'reference': source_reference,
                                'date': datetime.utcnow().date()
                            },
                            overlapping_classes=overlap
                        )
                        alerts_generated += 1
                        logger.info(
                            f"   Alert: '{wl_item['brand_name']}' vs '{tm['name']}' "
                            f"(score: {conflict['total']:.2%}, overlap: {overlap})"
                        )

            # Update watchlist last_scanned_at
            for wl_item in watchlist_items:
                WatchlistCRUD.update_scanned(self.db, UUID(wl_item['id']))

            # Complete scan log
            ScanLogCRUD.complete(
                self.db, scan_id,
                trademarks_scanned=len(trademarks),
                watchlist_checked=len(watchlist_items),
                alerts_generated=alerts_generated
            )

            logger.info(f"Scan complete: {alerts_generated} alerts generated")
            return alerts_generated

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            ScanLogCRUD.fail(self.db, scan_id, str(e))
            raise

    def scan_single_watchlist(
        self,
        watchlist_id: UUID,
        limit: int = 500
    ) -> int:
        """
        Scan all trademarks against a single watchlist item.
        Useful when user adds new watchlist item.

        Args:
            watchlist_id: The watchlist item to scan for
            limit: Max trademarks to check (default 500 candidates)

        Returns:
            Number of alerts generated (max MAX_ALERTS_PER_ITEM)
        """
        logger.info(f"Scanning for watchlist item {watchlist_id}")

        # Get watchlist item (internal backend call, no tenant filter needed)
        wl_item = WatchlistCRUD.get_by_id_internal(self.db, watchlist_id)
        if not wl_item:
            raise ValueError(f"Watchlist item {watchlist_id} not found")

        # Generate embedding if not exists
        if not wl_item.get('text_embedding'):
            self._update_watchlist_embedding(wl_item)
            wl_item = WatchlistCRUD.get_by_id_internal(self.db, watchlist_id)

        # Find similar trademarks
        candidates = self._find_similar_trademarks(wl_item, limit)

        # Enforce minimum threshold (at least 70%)
        threshold = max(wl_item.get('alert_threshold', 0.75), self.MIN_THRESHOLD)

        # Collect all conflicts first
        conflicts: List[Tuple[Dict, Dict]] = []
        for tm in candidates:
            conflict = self._check_conflict(tm, wl_item)

            if conflict and conflict['total'] >= threshold:
                conflicts.append((tm, conflict))

        # Sort by score DESC and limit
        conflicts.sort(key=lambda x: x[1]['total'], reverse=True)
        limited_conflicts = conflicts[:MAX_ALERTS_PER_ITEM]

        if len(conflicts) > MAX_ALERTS_PER_ITEM:
            logger.info(
                f"   Limiting '{wl_item['brand_name']}': {len(conflicts)} conflicts -> "
                f"top {MAX_ALERTS_PER_ITEM}"
            )

        alerts_generated = 0
        for tm, conflict in limited_conflicts:
            if not AlertCRUD.check_duplicate(
                self.db, watchlist_id, tm['application_no']
            ):
                # Get overlapping classes from conflict scores
                overlap = conflict.get('overlapping_classes', [])

                AlertCRUD.create(
                    self.db,
                    org_id=UUID(wl_item['organization_id']),
                    watchlist_id=watchlist_id,
                    conflicting_trademark={
                        'id': tm.get('id'),
                        'name': tm.get('name'),
                        'application_no': tm.get('application_no'),
                        'status': tm.get('current_status'),
                        'classes': tm.get('nice_class_numbers', []),
                        'holder': tm.get('holder_name'),
                        'image_path': tm.get('image_path')
                    },
                    scores=conflict,
                    source_info={
                        'type': 'initial_scan',
                        'reference': str(watchlist_id),
                        'date': datetime.utcnow().date()
                    },
                    overlapping_classes=overlap
                )
                alerts_generated += 1
                logger.info(f"   Alert created: overlap classes {overlap}")

        WatchlistCRUD.update_scanned(self.db, watchlist_id)

        logger.info(f"Initial scan complete: {alerts_generated} alerts")
        return alerts_generated

    def _check_conflict(self, trademark: Dict, watchlist_item: Dict) -> Optional[Dict]:
        """
        Check if trademark conflicts with watchlist item.
        Delegates scoring to risk_engine.score_pair() for consistency.
        """
        # 0. Skip if this is the user's own trademark
        own_app_no = watchlist_item.get('customer_application_no')
        tm_app_no = trademark.get('application_no')
        if own_app_no and tm_app_no and own_app_no == tm_app_no:
            return None

        # 0b. CRITICAL: Skip EXACT name matches (self-conflict prevention)
        tm_name_raw = trademark.get('name') or ''
        wl_name_raw = watchlist_item.get('brand_name') or ''
        tm_name_normalized = normalize_turkish(tm_name_raw.lower().strip())
        wl_name_normalized = normalize_turkish(wl_name_raw.lower().strip())

        if tm_name_normalized == wl_name_normalized:
            return None

        # 0c. Skip very high similarity names (>98%) to catch minor variations
        from difflib import SequenceMatcher
        name_ratio = SequenceMatcher(None, tm_name_normalized, wl_name_normalized).ratio()
        if name_ratio > 0.98:
            return None

        # 1. Check Nice class overlap
        tm_classes = trademark.get('nice_class_numbers', []) or []
        wl_classes = watchlist_item.get('nice_class_numbers', []) or []

        overlapping_classes = get_overlapping_classes(tm_classes, wl_classes)

        if not overlapping_classes:
            return None

        has_global_class = GLOBAL_CLASS in tm_classes or GLOBAL_CLASS in wl_classes

        # 2. Compute raw similarity values
        tm_name = (trademark.get('name') or '').lower().strip()
        wl_name = (watchlist_item.get('brand_name') or '').lower().strip()

        if not tm_name or not wl_name:
            return None

        # 2a. Text similarity via comprehensive scoring
        scoring_result = calculate_comprehensive_score(wl_name, tm_name)
        text_sim = scoring_result['final_score']

        # 2b. Semantic similarity (cosine of pre-computed text embeddings)
        semantic_sim = 0.0
        tm_emb = trademark.get('text_embedding')
        wl_emb = watchlist_item.get('text_embedding')
        if tm_emb and wl_emb:
            semantic_sim = self._cosine_sim(tm_emb, wl_emb)

        # 2c. Visual similarity (combined CLIP/DINOv2/Color/OCR — logo text vs logo text)
        visual_sim = self._compute_visual_sim(trademark, watchlist_item)

        # 2d. Phonetic similarity (graduated 0.0-1.0)
        phonetic_sim = self._phonetic_sim(tm_name, wl_name)

        # 3. DELEGATE TO CENTRALIZED SCORING
        score_breakdown = score_pair(
            query_name=wl_name_raw,
            candidate_name=tm_name_raw,
            text_sim=text_sim,
            semantic_sim=semantic_sim,
            visual_sim=visual_sim,
            phonetic_sim=phonetic_sim,
            candidate_translations={
                'name_tr': trademark.get('name_tr'),
            }
        )

        # 4. Map to watchlist output format (compatible with AlertCRUD.create)
        #    Class overlap is already enforced as a gate at step 1 above.
        #    No external class factor — scoring is handled entirely by score_pair().
        return {
            'total': score_breakdown['total'],
            'text_similarity': score_breakdown.get('text_similarity', 0),
            'semantic_similarity': score_breakdown.get('semantic_similarity', 0),
            'visual_similarity': score_breakdown.get('visual_similarity', 0),
            'translation_similarity': score_breakdown.get('translation_similarity', 0),
            'phonetic_match': phonetic_sim >= 0.5,
            'text_idf_score': score_breakdown.get('text_idf_score', 0),
            'scoring_path': score_breakdown.get('scoring_path', ''),
            'overlapping_classes': list(overlapping_classes),
            'has_global_class': has_global_class,
        }

    @staticmethod
    def _cosine_sim(vec1, vec2) -> float:
        """Cosine similarity between two vectors (pure numpy math)."""
        if isinstance(vec1, str):
            import json
            vec1 = json.loads(vec1)
        if isinstance(vec2, str):
            import json
            vec2 = json.loads(vec2)

        v1 = np.array(vec1)
        v2 = np.array(vec2)

        dot = np.dot(v1, v2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(dot / (norm1 * norm2))

    @staticmethod
    def _compute_visual_sim(trademark, watchlist_item) -> float:
        """Combine visual sub-components into single similarity value.
        Delegates to risk_engine.calculate_visual_similarity().
        OCR compares logo text vs logo text ONLY — never brand name vs OCR."""
        if not watchlist_item.get('monitor_similar_logos'):
            return 0.0

        cos = WatchlistScanner._cosine_sim

        clip_sim = 0.0
        if trademark.get('image_embedding') and watchlist_item.get('logo_embedding'):
            clip_sim = cos(trademark['image_embedding'], watchlist_item['logo_embedding'])

        dino_sim = 0.0
        wl_dino = watchlist_item.get('logo_dinov2_embedding') or watchlist_item.get('dino_embedding')
        if trademark.get('dinov2_embedding') and wl_dino:
            dino_sim = cos(trademark['dinov2_embedding'], wl_dino)

        color_sim = 0.0
        wl_color = watchlist_item.get('logo_color_histogram') or watchlist_item.get('color_embedding')
        if trademark.get('color_histogram') and wl_color:
            color_sim = cos(trademark['color_histogram'], wl_color)

        # OCR text from BOTH logos — never use brand name here
        tm_ocr = trademark.get('logo_ocr_text') or ''
        wl_ocr = watchlist_item.get('logo_ocr_text') or ''

        return calculate_visual_similarity(
            clip_sim=clip_sim,
            dinov2_sim=dino_sim,
            color_sim=color_sim,
            ocr_text_a=wl_ocr,
            ocr_text_b=tm_ocr,
        )

    @staticmethod
    def _phonetic_sim(s1: str, s2: str) -> float:
        """Return graduated phonetic similarity (0.0-1.0)."""
        return calculate_phonetic_similarity(s1, s2)

    def _get_trademarks_by_ids(self, trademark_ids: List[UUID]) -> List[Dict]:
        """Get trademark details by IDs"""
        if not trademark_ids:
            return []

        cur = self.db.cursor()

        # Convert UUIDs to strings
        id_strings = [str(id) for id in trademark_ids]

        cur.execute("""
            SELECT t.*
            FROM trademarks t
            WHERE t.id = ANY(%s::uuid[])
        """, (id_strings,))

        return [dict(row) for row in cur.fetchall()]

    def _find_similar_trademarks(self, watchlist_item: Dict, limit: int) -> List[Dict]:
        """Find trademarks similar to watchlist item using text similarity."""
        cur = self.db.cursor()

        wl_name = watchlist_item.get('brand_name', '')
        wl_classes = watchlist_item.get('nice_class_numbers', []) or []
        own_app_no = watchlist_item.get('customer_application_no')

        logger.debug(f"  Finding similar TMs for '{wl_name}', classes={wl_classes}")

        # Enforce minimum threshold in SQL query for efficiency
        min_similarity = max(
            watchlist_item.get('alert_threshold', 0.75),
            self.MIN_THRESHOLD
        ) * 0.5  # Use half threshold in SQL to catch near-misses for IDF scoring

        # Normalize watchlist name for exact-match exclusion
        wl_name_normalized = normalize_turkish(wl_name.lower().strip())

        # Build class filter - if no classes specified, search ALL classes
        if wl_classes:
            class_filter = "(t.nice_class_numbers && %s::integer[] OR 99 = ANY(t.nice_class_numbers))"
            class_params = [wl_classes]
        else:
            class_filter = "TRUE"
            class_params = []
            logger.warning(f"  No Nice classes specified for '{wl_name}' - searching all classes")

        # Build query params
        app_no_filter = ""
        app_no_params = []
        if own_app_no:
            app_no_filter = "AND (t.application_no IS NULL OR t.application_no != %s)"
            app_no_params = [own_app_no]

        # CRITICAL: Exclude EXACT name matches (self-conflict prevention)
        exact_name_filter = """
            AND LOWER(TRIM(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
                'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c')
            )) != %s
        """

        # Turkish normalization SQL fragment (reused in SELECT and WHERE)
        turkish_norm = """LOWER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(t.name,
                'ğ','g'),'Ğ','g'),'ı','i'),'İ','i'),'ö','o'),'Ö','o'),
                'ü','u'),'Ü','u'),'ş','s'),'Ş','s'),'ç','c'),'Ç','c'))"""

        # Match search engine pre-screening: trigram on original name,
        # Turkish-normalized name, AND name_tr (translation) for cross-language conflicts
        query = f"""
            SELECT t.*,
                   GREATEST(
                       similarity(t.name, %s),
                       similarity({turkish_norm}, %s),
                       COALESCE(similarity(t.name_tr, %s), 0)
                   ) as text_score
            FROM trademarks t
            WHERE {class_filter}
              AND t.current_status NOT IN ('Refused', 'Withdrawn', 'Expired')
              AND (t.appeal_deadline IS NOT NULL AND t.appeal_deadline >= CURRENT_DATE)
              AND GREATEST(
                  similarity(t.name, %s),
                  similarity({turkish_norm}, %s),
                  COALESCE(similarity(t.name_tr, %s), 0)
              ) >= %s
              {app_no_filter}
              {exact_name_filter}
            ORDER BY text_score DESC
            LIMIT %s
        """

        # Params: SELECT(3) + class_params + WHERE(6+threshold) + app_no + exact_name + limit
        base_params = (
            [wl_name, wl_name_normalized, wl_name] +
            class_params +
            [wl_name, wl_name_normalized, wl_name, min_similarity] +
            app_no_params +
            [wl_name_normalized, limit]
        )

        cur.execute(query, base_params)
        results = [dict(row) for row in cur.fetchall()]

        logger.debug(f"  Found {len(results)} similar trademarks for '{wl_name}'")
        return results

    def _update_watchlist_embedding(self, watchlist_item: Dict):
        """Generate and store embedding for watchlist item"""
        brand_name = watchlist_item.get('brand_name', '')

        # Generate text embedding using shared ai module
        text_emb = ai.text_model.encode(brand_name).tolist()

        # Generate logo embeddings if logo_path exists
        logo_path = watchlist_item.get('logo_path')
        logo_emb = None
        logo_ocr = None

        if logo_path:
            logo_result = generate_logo_embeddings(logo_path)
            if logo_result:
                logo_emb = logo_result.get('clip_embedding')
                logo_ocr = logo_result.get('ocr_text')
                # Store full visual embeddings
                WatchlistCRUD.update_logo(
                    self.db,
                    UUID(watchlist_item['id']),
                    logo_path=logo_path,
                    logo_embedding=logo_emb,
                    dino_embedding=logo_result.get('dino_embedding'),
                    color_histogram=logo_result.get('color_histogram'),
                    logo_ocr_text=logo_ocr,
                )

        WatchlistCRUD.update_embedding(
            self.db,
            UUID(watchlist_item['id']),
            text_emb,
            logo_emb,
            logo_ocr_text=logo_ocr,
        )


# ==========================================
# Singleton Scanner Factory
# ==========================================

_scanner_instance = None

def get_scanner() -> WatchlistScanner:
    """
    Get singleton scanner instance.

    IMPORTANT: Use this instead of WatchlistScanner() directly
    to avoid creating multiple DB connections.

    Usage:
        from watchlist.scanner import get_scanner
        scanner = get_scanner()
        scanner.scan_single_watchlist(item_id)
    """
    global _scanner_instance
    if _scanner_instance is None:
        logger.info("Creating singleton WatchlistScanner instance...")
        _scanner_instance = WatchlistScanner()
        logger.info("Singleton scanner ready")
    return _scanner_instance


def reset_scanner():
    """Reset singleton scanner (useful for testing or reconnection)"""
    global _scanner_instance
    _scanner_instance = None


# ==========================================
# Integration with ingest.py
# ==========================================

def trigger_watchlist_scan(
    trademark_ids: List[UUID],
    source_type: str,
    source_reference: str
):
    """
    Called by ingest.py after processing new data.

    Add this to the end of process_file_batch() in ingest.py:

        from watchlist.scanner import trigger_watchlist_scan
        trigger_watchlist_scan(new_trademark_ids, source_type, source_reference)
    """
    try:
        scanner = get_scanner()  # Use singleton!
        alerts_count = scanner.scan_new_trademarks(
            trademark_ids, source_type, source_reference
        )
        logger.info(f"Watchlist scan triggered: {alerts_count} alerts generated")
    except Exception as e:
        logger.error(f"Watchlist scan failed: {e}")
        # Don't raise - ingestion should continue even if scan fails


# ==========================================
# CLI Entry Point
# ==========================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run watchlist scanner")
    parser.add_argument("--full-rescan", action="store_true", help="Rescan all trademarks")
    parser.add_argument("--watchlist-id", type=str, help="Scan for specific watchlist item")
    parser.add_argument("--source", type=str, help="Source reference (e.g., BLT_500)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Use singleton for CLI too
    scanner = get_scanner()

    if args.watchlist_id:
        scanner.scan_single_watchlist(UUID(args.watchlist_id))
    elif args.full_rescan:
        # Get all trademark IDs
        cur = scanner.db.cursor()
        cur.execute("SELECT id FROM trademarks WHERE current_status NOT IN ('Refused', 'Withdrawn', 'Expired')")
        ids = [UUID(row['id']) for row in cur.fetchall()]
        scanner.scan_new_trademarks(ids, "full_rescan", f"manual_{datetime.utcnow().date()}")
    else:
        print("Use --full-rescan or --watchlist-id")
