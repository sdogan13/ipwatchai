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
from services.scoring_service import (
    _calculate_visual_breakdown,
    build_logo_image_profile,
    calculate_comprehensive_score,
    extract_ocr_text,
    resolve_logo_image_path,
)
from utils.event_severity import EVENT_SEVERITY_MAP
from utils.idf_scoring import (
    MAX_ALERTS_PER_ITEM
)
from utils.class_utils import (
    GLOBAL_CLASS,
    get_overlapping_classes,
)
from utils.deadline import active_appeal_deadline_sql
from utils.watchlist_filters import is_same_holder_conflict
from pipeline import ai  # Shared AI models (loaded once at app startup)
from risk_engine import score_pair  # Centralized scoring
from utils.phonetic import calculate_phonetic_similarity  # Graduated phonetic scoring

logger = logging.getLogger(__name__)

# Store conflicts down to this baseline so the watchlist tab can reveal lower
# risk matches when the user changes the display filter.
CONFLICT_GENERATION_FLOOR = 0.50


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
            if ai.USE_FP16:
                clip_input = clip_input.half()
            with torch.no_grad():
                clip_emb = ai.clip_model.encode_image(clip_input)
                clip_emb = clip_emb / clip_emb.norm(dim=-1, keepdim=True)
            result['clip_embedding'] = clip_emb.float().cpu().squeeze().tolist()
        except Exception as e:
            logger.warning(f"CLIP embedding failed: {e}")

        # DINOv2 embedding (768-dim)
        try:
            dino_input = ai.dinov2_preprocess(img).unsqueeze(0).to(ai.device)
            if ai.USE_FP16:
                dino_input = dino_input.half()
            with torch.no_grad():
                dino_emb = ai.dinov2_model(dino_input)
                dino_emb = dino_emb / dino_emb.norm(dim=-1, keepdim=True)
            result['dino_embedding'] = dino_emb.float().cpu().squeeze().tolist()
        except Exception as e:
            logger.warning(f"DINOv2 embedding failed: {e}")

        # Color histogram (512-dim: HSV 8x8x8 = 512 bins, matching trademarks table)
        try:
            img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            hsv = cv2.cvtColor(img_cv, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256]).flatten()
            norm = np.linalg.norm(hist)
            if norm > 0:
                hist = hist / norm
            result['color_histogram'] = hist.tolist()
        except Exception as e:
            logger.warning(f"Color histogram failed: {e}")

        # OCR text
        try:
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
        Called after pipeline.ingest processes new data.

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

            # Purge only alerts below the storage floor. The user-selected
            # risk threshold is a display/notification preference; alerts at
            # or above the floor stay available for later filtering.
            stale_resolved = 0
            for wl_item in watchlist_items:
                stale_resolved += AlertCRUD.resolve_below_threshold(
                    self.db, UUID(wl_item['id']), CONFLICT_GENERATION_FLOOR
                )
            if stale_resolved:
                logger.info(
                    f"   Pre-scan cleanup: resolved {stale_resolved} stale alert(s) "
                    f"below storage floor {CONFLICT_GENERATION_FLOOR:.0%}"
                )

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

                    if conflict and conflict['total'] >= CONFLICT_GENERATION_FLOOR:
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
                                'status': tm.get('final_status'),
                                'classes': tm.get('nice_class_numbers', []),
                                'holder': tm.get('holder_name'),
                                'holder_tpe_client_id': tm.get('holder_tpe_client_id'),
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
    ) -> int:
        """
        Scan all within-appeal-deadline trademarks against a single watchlist item.
        Useful when user adds new watchlist item.

        Args:
            watchlist_id: The watchlist item to scan for

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

        # All trademarks within appeal deadline
        candidates = self._get_trademarks_within_deadline()

        # Purge only alerts below the storage floor; display filtering happens
        # in the watchlist tab.
        resolved = AlertCRUD.resolve_below_threshold(
            self.db, watchlist_id, CONFLICT_GENERATION_FLOOR
        )
        if resolved:
            logger.info(
                f"   Resolved {resolved} stale alert(s) below storage floor "
                f"{CONFLICT_GENERATION_FLOOR:.0%}"
            )

        # Collect all conflicts first
        conflicts: List[Tuple[Dict, Dict]] = []
        for tm in candidates:
            conflict = self._check_conflict(tm, wl_item)

            if conflict and conflict['total'] >= CONFLICT_GENERATION_FLOOR:
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
                        'status': tm.get('final_status'),
                        'classes': tm.get('nice_class_numbers', []),
                        'holder': tm.get('holder_name'),
                        'holder_tpe_client_id': tm.get('holder_tpe_client_id'),
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
        # 0. Skip if this is the user's own trademark (application number match only)
        own_app_no = watchlist_item.get('customer_application_no')
        tm_app_no = trademark.get('application_no')
        if own_app_no and tm_app_no and own_app_no == tm_app_no:
            return None
        if is_same_holder_conflict(trademark, watchlist_item):
            logger.debug(
                "Skipping same-holder watchlist conflict: watchlist=%s candidate=%s holder=%s",
                watchlist_item.get('id'),
                tm_app_no,
                trademark.get('holder_tpe_client_id') or trademark.get('holder_id'),
            )
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

        # 2c. Visual similarity (combined CLIP/DINOv2/OCR — logo text vs logo text)
        visual_sim, visual_breakdown = self._compute_visual_breakdown(
            trademark,
            watchlist_item,
        )

        # 2d. Phonetic similarity (graduated 0.0-1.0)
        phonetic_sim = self._phonetic_sim(tm_name, wl_name)

        # 3. DELEGATE TO CENTRALIZED SCORING
        score_breakdown = score_pair(
            query_name=watchlist_item.get('brand_name') or '',
            candidate_name=trademark.get('name') or '',
            text_sim=text_sim,
            semantic_sim=semantic_sim,
            visual_sim=visual_sim,
            phonetic_sim=phonetic_sim,
            candidate_translations={
                'name_tr': trademark.get('name_tr'),
            },
            visual_breakdown=visual_breakdown,
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
            'path_a_score': score_breakdown.get('path_a_score', 0),
            'path_b_score': score_breakdown.get('path_b_score', 0),
            'scoring_path': score_breakdown.get('scoring_path', ''),
            'scoring_path_source': score_breakdown.get('scoring_path_source', ''),
            'score_details': score_breakdown,
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
        score, _ = WatchlistScanner._compute_visual_breakdown(
            trademark,
            watchlist_item,
        )
        return score

    @staticmethod
    def _compute_visual_breakdown(trademark, watchlist_item) -> Tuple[float, Dict]:
        """Combine visual sub-components into single similarity value.
        Delegates to services.scoring_service._calculate_visual_breakdown().
        OCR compares logo text vs logo text ONLY — never brand name vs OCR.

        Note: visual scoring runs whenever embeddings exist on both sides.
        The monitor_similar_logos flag is stored for UX purposes but does NOT
        suppress scoring — logos are always compared when data is available.
        """
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

        # If no embedding overlap exists on either side, skip — nothing to compare
        if clip_sim == 0.0 and dino_sim == 0.0 and color_sim == 0.0:
            return 0.0, {
                "total": 0.0,
                "active_components": [],
                "components": {"clip": 0.0, "dinov2": 0.0, "ocr": 0.0},
                "source": "watchlist_visual_components",
            }

        # OCR text from BOTH logos — never use brand name here
        tm_ocr = trademark.get('logo_ocr_text') or ''
        wl_ocr = watchlist_item.get('logo_ocr_text') or ''
        tm_profile = None
        tm_profile_path = resolve_logo_image_path(
            trademark.get('image_path') or '',
            roots=[settings.paths.data_root, settings.pipeline.bulletins_root],
        )
        if tm_profile_path:
            tm_profile = build_logo_image_profile(tm_profile_path, tm_ocr)
        wl_profile = None
        wl_profile_path = resolve_logo_image_path(watchlist_item.get('logo_path') or '')
        if wl_profile_path:
            wl_profile = build_logo_image_profile(wl_profile_path, wl_ocr)

        score, breakdown = _calculate_visual_breakdown(
            clip_sim=clip_sim,
            dinov2_sim=dino_sim,
            color_sim=color_sim,
            ocr_text_a=wl_ocr,
            ocr_text_b=tm_ocr,
            logo_profile_a=wl_profile,
            logo_profile_b=tm_profile,
        )
        breakdown["source"] = "watchlist_visual_components"
        return score, breakdown

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

        cur.execute(f"""
            SELECT t.*
            FROM trademarks t
            WHERE t.id = ANY(%s::uuid[])
              AND {active_appeal_deadline_sql("t")}
        """, (id_strings,))

        return [dict(row) for row in cur.fetchall()]

    def _get_trademarks_within_deadline(self) -> List[Dict]:
        """
        Return all trademarks whose appeal deadline has not yet passed.
        This is the full candidate pool for watchlist conflict detection —
        no pre-screening by name similarity, class, or score.
        """
        cur = self.db.cursor()
        cur.execute(f"""
            SELECT t.*
            FROM trademarks t
            WHERE {active_appeal_deadline_sql("t")}
        """)
        results = [dict(row) for row in cur.fetchall()]
        logger.info(f"  Within-deadline pool: {len(results)} trademarks")
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
# Event-Based Alert Scanning
# ==========================================

# Business-context messages for event types (English keys, i18n done on frontend)
EVENT_ALERT_MESSAGES = {
    "transfer": "Trademark has been transferred to a new holder",
    "merger": "Trademark holder has undergone a merger",
    "partial_transfer": "Partial transfer of trademark rights",
    "cancellation": "Trademark has been cancelled",
    "withdrawal": "Trademark application has been withdrawn",
    "renewal": "Trademark has been renewed",
    "seizure": "Seizure order placed on trademark",
    "precautionary_seizure": "Precautionary seizure placed on trademark",
    "injunction": "Court injunction issued on trademark",
    "precautionary_injunction": "Precautionary injunction issued on trademark",
    "seizure_lift": "Seizure on trademark has been lifted",
    "injunction_lift": "Injunction on trademark has been lifted",
    "restriction_lift": "Restriction on trademark has been lifted",
    "license": "License agreement registered for trademark",
    "bankruptcy": "Trademark holder declared bankrupt",
    "correction": "Official correction to trademark record",
    "address_change": "Trademark holder address changed",
    "name_change": "Trademark holder name changed",
}



def scan_events_for_watchlist(conn=None) -> int:
    """
    Check for new trademark events that affect watched trademarks.

    Joins trademark_events with watchlist_mt to find events on trademarks
    that users are actively monitoring, then creates event-type alerts.

    Returns number of alerts generated.
    """
    from database.crud import Database, get_db_connection
    from uuid import uuid4

    if conn is None:
        conn = get_db_connection()
    db = Database(conn)
    cur = db.cursor()

    # Find events on watched trademarks that haven't been alerted yet.
    # Match by application_no (watchlist stores the watched trademark's app_no).
    cur.execute("""
        SELECT te.id AS event_id, te.application_no, te.event_type, te.event_subtype,
               te.source_type, te.bulletin_no, te.bulletin_date,
               te.old_value, te.new_value, te.details,
               w.id AS watchlist_id, w.user_id, w.organization_id, w.brand_name,
               t.id AS trademark_id, t.name AS trademark_name,
               t.nice_class_numbers, t.image_path, t.final_status
        FROM trademark_events te
        JOIN watchlist_mt w ON w.customer_application_no = te.application_no
        JOIN trademarks t ON t.application_no = te.application_no
        WHERE w.is_active = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM alerts_mt a
              WHERE a.watchlist_item_id = w.id
                AND a.alert_type = 'event'
                AND a.source_type = te.event_type
                AND a.source_bulletin = te.bulletin_no
          )
        ORDER BY te.bulletin_date DESC NULLS LAST
    """)

    rows = cur.fetchall()
    if not rows:
        logger.info("Event alert scan: no new events for watched trademarks")
        return 0

    logger.info(f"Event alert scan: found {len(rows)} event(s) on watched trademarks")

    alerts_generated = 0
    for row in rows:
        event_type = row["event_type"]
        severity = EVENT_SEVERITY_MAP.get(event_type, "medium")
        message = EVENT_ALERT_MESSAGES.get(event_type, f"Event: {event_type}")

        # Build details string for resolution_notes (used for display context)
        detail_parts = []
        if row["old_value"]:
            detail_parts.append(f"Old: {row['old_value'][:200]}")
        if row["new_value"]:
            detail_parts.append(f"New: {row['new_value'][:200]}")
        detail_text = " | ".join(detail_parts) if detail_parts else None

        alert_id = uuid4()
        try:
            cur.execute("""
                INSERT INTO alerts_mt (
                    id, user_id, organization_id, watchlist_item_id,
                    conflicting_trademark_id, conflicting_name,
                    conflicting_application_no, conflicting_classes,
                    conflicting_holder_name, conflicting_image_path,
                    overall_risk_score, severity, source_type,
                    source_bulletin, alert_type, status, resolution_notes
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                str(alert_id),
                str(row["user_id"]),
                str(row["organization_id"]),
                str(row["watchlist_id"]),
                str(row["trademark_id"]),
                row["trademark_name"],
                row["application_no"],
                row["nice_class_numbers"] or [],
                row.get("new_value", "")[:500] if event_type in ("transfer", "merger", "partial_transfer") else None,
                row["image_path"],
                1.0,  # Event alerts are always 100% relevance (exact trademark match)
                severity,
                event_type,  # source_type stores the event type
                row["bulletin_no"],
                "event",
                "new",
                message + (f" — {detail_text}" if detail_text else ""),
            ))
            alerts_generated += 1
            logger.info(
                f"  Event alert: {row['brand_name']} — {event_type} "
                f"(bulletin {row['bulletin_no']}, severity={severity})"
            )
        except Exception as e:
            logger.warning(f"  Failed to create event alert: {e}")
            conn.rollback()

    conn.commit()
    logger.info(f"Event alert scan complete: {alerts_generated} alerts generated")
    return alerts_generated


# ==========================================
# Integration with pipeline.ingest
# ==========================================

def trigger_watchlist_scan(
    trademark_ids: List[UUID],
    source_type: str,
    source_reference: str
):
    """
    Called by pipeline.ingest after processing new data.

    Add this to the end of process_file_batch() in pipeline/ingest.py:

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
    parser.add_argument("--events", action="store_true", help="Scan for event-based alerts on watched trademarks")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Use singleton for CLI too
    scanner = get_scanner()

    if args.events:
        count = scan_events_for_watchlist()
        print(f"Event alerts generated: {count}")
    elif args.watchlist_id:
        scanner.scan_single_watchlist(UUID(args.watchlist_id))
    elif args.full_rescan:
        # Get all trademark IDs
        cur = scanner.db.cursor()
        cur.execute(
            f"SELECT id FROM trademarks t WHERE {active_appeal_deadline_sql('t')}"
        )
        ids = [UUID(row['id']) for row in cur.fetchall()]
        scanner.scan_new_trademarks(ids, "full_rescan", f"manual_{datetime.utcnow().date()}")
    else:
        print("Use --full-rescan or --watchlist-id")
