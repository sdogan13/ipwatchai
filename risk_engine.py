import os
import time

# ===================== CRITICAL STABILITY FIX =====================
os.environ["XFORMERS_DISABLED"] = "1"
_PRE_DOTENV_PIPELINE_BULLETINS_ROOT = os.environ.get("PIPELINE_BULLETINS_ROOT")
_PRE_DOTENV_DATA_ROOT = os.environ.get("DATA_ROOT")

import torch
import numpy as np
import cv2
from pathlib import Path
from PIL import Image
# CrossEncoder removed - was unused, wasted ~120MB VRAM
from dotenv import load_dotenv

# Import Pipeline Components
import scrapper
from pipeline import ingest
from pipeline import ai  # Optimization: Reuse models loaded here
from pipeline.ingest_rules import _repair_mojibake
from services.scoring_service import (
    RISK_THRESHOLDS,  # noqa: F401  re-exported
    _calculate_visual_breakdown,
    _dynamic_combine,  # noqa: F401  re-exported
    build_logo_image_profile,
    calculate_name_similarity,  # noqa: F401  re-exported
    calculate_token_overlap,  # noqa: F401  re-exported
    calculate_visual_similarity,  # noqa: F401  re-exported
    check_substring_containment,  # noqa: F401  re-exported
    extract_ocr_text,
    get_risk_level,  # noqa: F401  re-exported
    resolve_logo_image_path,
    score_pair,
)
from utils.idf_scoring import (
    normalize_turkish,
    turkish_lower,  # noqa: F401  re-exported
    
)
# Translation similarity for cross-language conflict detection
# Translation similarity no longer used directly - handled by dual-path scoring in score_pair()
# Graduated phonetic scoring (replaces binary dMetaphone match)
from utils.phonetic import calculate_phonetic_similarity
# ===================== DATABASE CONNECTION POOL =====================
from db.pool import (
    get_connection,
    release_connection
)

# ===================== STRUCTURED LOGGING =====================
from logging_config import get_logger, setup_logging

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _sql_turkish_fold_expr(column: str) -> str:
    """Return SQL that folds Turkish characters without embedding non-ASCII literals."""
    expr = f"COALESCE({column}, '')"
    replacements = [
        ("CHR(287)", "'g'"),  # ğ
        ("CHR(286)", "'g'"),  # Ğ
        ("CHR(305)", "'i'"),  # ı
        ("CHR(304)", "'i'"),  # İ
        ("CHR(246)", "'o'"),  # ö
        ("CHR(214)", "'o'"),  # Ö
        ("CHR(252)", "'u'"),  # ü
        ("CHR(220)", "'u'"),  # Ü
        ("CHR(351)", "'s'"),  # ş
        ("CHR(350)", "'s'"),  # Ş
        ("CHR(231)", "'c'"),  # ç
        ("CHR(199)", "'c'"),  # Ç
    ]
    for source, target in replacements:
        expr = f"REPLACE({expr}, {source}, {target})"
    return f"LOWER({expr})"


def _sql_turkish_normalized_expr(column: str) -> str:
    folded = _sql_turkish_fold_expr(column)
    non_alnum_folded = f"REGEXP_REPLACE({folded}, '[^a-z0-9]+', ' ', 'g')"
    return f"TRIM(REGEXP_REPLACE({non_alnum_folded}, '[[:space:]]+', ' ', 'g'))"


def _sql_turkish_compact_expr(column: str) -> str:
    normalized = _sql_turkish_normalized_expr(column)
    return f"REPLACE({normalized}, ' ', '')"


def _sql_like_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _resolve_local_risk_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


# Load environment vars
load_dotenv()

# Setup Logging
setup_logging()
logger = get_logger(__name__)

# ===================== CONFIG =====================
DATA_ROOT = _resolve_local_risk_root(
    _PRE_DOTENV_PIPELINE_BULLETINS_ROOT
    or _PRE_DOTENV_DATA_ROOT
    or os.environ.get("PIPELINE_BULLETINS_ROOT")
    or os.environ.get("DATA_ROOT"),
    _LOCAL_DEFAULT_BULLETINS_ROOT,
)

# ===================== RISK THRESHOLDS - Single source of truth =====================
# Used by: risk_engine, watchlist/scanner, workers/universal_scanner, database/crud, agentic_search, frontend
def get_status_category(status):
    """
    Categorizes trademark status into risk levels and returns user guidance messages.
    """
    categories = {
        # HIGH RISK - Blocks your application
        'Tescil Edildi': {'level': 'RISK', 'multiplier': 1.0,
                       'message': 'Active trademark - blocks registration'},
        'Yayında': {'level': 'RISK', 'multiplier': 1.0,
                      'message': 'Pending registration - likely to block'},
        'Yenilendi': {'level': 'RISK', 'multiplier': 1.0,
                    'message': 'Recently renewed - actively protected'},
        'İtiraz Edildi': {'level': 'RISK', 'multiplier': 0.9,
                    'message': 'Under opposition but still active'},
        'Başvuruldu': {'level': 'RISK', 'multiplier': 0.85,
                    'message': 'Pending application - may be registered soon'},

        # WARNING - Indicates protection exists / legal risk
        'Reddedildi': {'level': 'WARNING', 'multiplier': 0.8,
                    'message': 'Previous application rejected - office protects this name'},
        'İptal Edildi': {'level': 'WARNING', 'multiplier': 0.75,
                      'message': 'Cancelled by court/appeal - name is legally defended'},
        'Kısmi Red': {'level': 'WARNING', 'multiplier': 0.6,
                            'message': 'Partially rejected - some classes blocked'},

        # OPPORTUNITY - Name may be available
        'Süresi Doldu': {'level': 'OPPORTUNITY', 'multiplier': 0.3,
                    'message': 'Expired - name may be available (owner has 6-month grace period to renew)'},
        'Geri Çekildi': {'level': 'OPPORTUNITY', 'multiplier': 0.3,
                      'message': 'Withdrawn - owner abandoned, name may be available'},

        'Bilinmiyor': {'level': 'UNKNOWN', 'multiplier': 0.5,
                    'message': 'Status unknown - verify manually'}
    }
    status_aliases = {
        'Registered': 'Tescil Edildi',
        'Published': 'Yayında',
        'Renewed': 'Yenilendi',
        'Opposed': 'İtiraz Edildi',
        'Applied': 'Başvuruldu',
        'Refused': 'Reddedildi',
        'Cancelled': 'İptal Edildi',
        'Partial Refusal': 'Kısmi Red',
        'Expired': 'Süresi Doldu',
        'Withdrawn': 'Geri Çekildi',
        'Unknown': 'Bilinmiyor',
    }
    status = _repair_mojibake(status)
    canonical_status = status_aliases.get(status, status)
    return categories.get(canonical_status, categories['Bilinmiyor'])


# ===================== TURKISH TEXT NORMALIZATION =====================
# normalize_turkish and turkish_lower imported from utils.idf_scoring (canonical)


class RiskEngine:
    def __init__(self, existing_conn=None):
        init_start = time.perf_counter()
        logger.info("Initializing Risk Engine", extra={"reusing_models": True})

        # --- OPTIMIZATION: Reuse models from pipeline.ai to save VRAM ---
        self.device = ai.device
        self.text_model = ai.text_model
        self.clip_model = ai.clip_model
        self.clip_preprocess = ai.clip_preprocess
        self.dino_model = ai.dinov2_model
        self.dino_preprocess = ai.dinov2_preprocess

        # Pre-warm the configured live translation backend so first search isn't slow
        try:
            from utils.translation import (
                get_default_translation_backend,
                initialize as init_translation,
            )

            live_backend = get_default_translation_backend("live")
            init_translation(str(self.device), backend=live_backend)
        except Exception:
            pass

        # Track if we own the connection (for cleanup)
        self._owns_connection = False

        if existing_conn:
            self.conn = existing_conn
        else:
            try:
                # Get connection from pool
                self.conn = get_connection()
                self._owns_connection = True
                logger.info("Database connected", extra={"source": "pool"})
            except Exception as e:
                logger.error("Database connection failed", extra={"error": str(e)})
                raise e

        self._ensure_phonetic_capabilities()

        init_duration = (time.perf_counter() - init_start) * 1000
        logger.info(
            "Risk Engine initialized",
            extra={
                "duration_ms": round(init_duration, 2),
                "device": str(self.device),
            },
        )

    def close(self):
        """Release database connection back to the pool."""
        if self._owns_connection and self.conn:
            try:
                release_connection(self.conn)
                logger.info("Database connection released")
            except Exception as e:
                logger.error("Error releasing connection", extra={"error": str(e)})
            finally:
                self.conn = None
                self._owns_connection = False

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release connection."""
        self.close()
        return False

    def __del__(self):
        """Destructor - ensure connection is released."""
        if hasattr(self, '_owns_connection') and self._owns_connection:
            self.close()

    def _ensure_phonetic_capabilities(self):
        try:
            cur = self.conn.cursor()
            cur.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;")
            # Safe split string
            check_query = (
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = 'idx_tm_phonetic'"
            )
            cur.execute(check_query)
            if not cur.fetchone():
                cur.execute("CREATE INDEX idx_tm_phonetic ON trademarks (dmetaphone(name));")
                self.conn.commit()
        except Exception:
            self.conn.rollback()

    def _encode_single_image(self, pil_img):
        """Extract all visual vectors + OCR text from a PIL image.

        Returns:
            tuple: (clip_vec, dino_vec, color_vec, ocr_text)
        """
        try:
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            hsv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
            cv2.normalize(hist, hist)
            color_vec = hist.flatten().tolist()
        except Exception:
            color_vec = None

        clip_input = self.clip_preprocess(pil_img).unsqueeze(0).to(self.device)
        if next(self.clip_model.parameters()).dtype == torch.float16:
            clip_input = clip_input.half()
        with torch.no_grad():
            clip_feat = self.clip_model.encode_image(clip_input)
            clip_feat /= clip_feat.norm(dim=-1, keepdim=True)
            clip_vec = clip_feat.squeeze().tolist()
        del clip_input, clip_feat

        dino_input = self.dino_preprocess(pil_img).unsqueeze(0).to(self.device)
        if next(self.dino_model.parameters()).dtype == torch.float16:
            dino_input = dino_input.half()
        with torch.no_grad():
            dino_vec = self.dino_model(dino_input).flatten().tolist()
        del dino_input

        # Extract OCR text from the image
        ocr_text = ""
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                pil_img.save(f, format='PNG')
                tmp_path = f.name
            ocr_text = extract_ocr_text(tmp_path) or ""
            os.unlink(tmp_path)
        except Exception:
            ocr_text = ""

        return clip_vec, dino_vec, color_vec, ocr_text

    def get_query_vectors(self, name, image_path=None):
        """Encode query name and optional image into vectors.

        Returns:
            tuple: (text_vec, img_vec, dino_vec, color_vec, ocr_text)
        """
        # Trademark name retrieval is lexical/phonetic/translation based. Text
        # embeddings are intentionally not generated for trademark risk scoring.
        text_vec = None
        img_vec, dino_vec, color_vec, ocr_text = None, None, None, ""
        self._last_query_logo_profile = None

        if image_path and os.path.exists(image_path):
            try:
                pil_img = Image.open(image_path).convert('RGB')
                img_vec, dino_vec, color_vec, ocr_text = self._encode_single_image(pil_img)
                self._last_query_logo_profile = build_logo_image_profile(
                    image_path,
                    ocr_text,
                )
            except Exception as e:
                logger.error("Image process failed", extra={"error": str(e)})

        return text_vec, img_vec, dino_vec, color_vec, ocr_text

    def suggest_classes(self, description, limit=3):
        if not description or not str(description).strip():
            return []

        desc_vec = self.text_model.encode(description).tolist()
        cur = self.conn.cursor()
        sql = """
            SELECT class_number, description,
                   (1 - (description_embedding <=> %s::halfvec)) as similarity
            FROM nice_classes_lookup
            ORDER BY similarity DESC
            LIMIT %s
        """
        try:
            cur.execute(sql, (str(desc_vec), limit))
            results = cur.fetchall()
            return [{"class_number": r[0], "description": r[1], "confidence": float(r[2])} for r in results]
        except Exception:
            self.conn.rollback()
            return []

    def _record_candidate_retrieval(self, candidate_id, stage, fields, variant=None):
        if not hasattr(self, "_candidate_retrieval_metadata"):
            self._candidate_retrieval_metadata = {}

        key = str(candidate_id)
        metadata = self._candidate_retrieval_metadata.setdefault(
            key,
            {
                "retrieval_sources": [],
                "retrieval_matched_fields": [],
                "retrieval_matched_stages": [],
                "retrieval_query_variants": [],
            },
        )

        clean_fields = sorted({field for field in fields if field})
        source = {"stage": stage, "fields": clean_fields}
        if variant:
            source["variant"] = variant

        if source not in metadata["retrieval_sources"]:
            metadata["retrieval_sources"].append(source)

        for field in clean_fields:
            if field not in metadata["retrieval_matched_fields"]:
                metadata["retrieval_matched_fields"].append(field)
        if stage not in metadata["retrieval_matched_stages"]:
            metadata["retrieval_matched_stages"].append(stage)
        if variant and variant not in metadata["retrieval_query_variants"]:
            metadata["retrieval_query_variants"].append(variant)

    @staticmethod
    def _row_text_fields(row, fallback_fields=("name", "name_tr"), offset=6):
        fields = []
        if len(row) > offset and row[offset]:
            fields.append("name")
        if len(row) > offset + 1 and row[offset + 1]:
            fields.append("name_tr")
        return fields or list(fallback_fields)

    def pre_screen_candidates(self, name_input, target_classes=None, limit=500, attorney_no=None, q_img_vec=None, q_dino_vec=None, q_ocr_text=None):
        cur = self.conn.cursor()
        name_input = name_input or ""
        name_normalized = normalize_turkish(name_input)
        seen_ids = set()
        all_candidates = []
        self._candidate_retrieval_metadata = {}

        name_norm_expr = _sql_turkish_normalized_expr("name")
        name_tr_norm_expr = _sql_turkish_normalized_expr("name_tr")
        name_compact_expr = _sql_turkish_compact_expr("name")
        name_tr_compact_expr = _sql_turkish_compact_expr("name_tr")
        ocr_norm_expr = _sql_turkish_normalized_expr("logo_ocr_text")

        def apply_common_filters(sql, params):
            sql += " AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)"
            if attorney_no:
                sql += " AND attorney_no = %s"
                params.append(attorney_no)
            if target_classes and len(target_classes) > 0:
                sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
                params.append(target_classes)
            return sql, params

        def add_matches(matches, stage, variant, fields_getter=None, fallback_fields=None):
            for match in matches:
                if len(all_candidates) >= limit and match[0] not in seen_ids:
                    continue
                fields = fields_getter(match) if fields_getter else list(fallback_fields or [])
                self._record_candidate_retrieval(match[0], stage, fields, variant)
                if match[0] not in seen_ids:
                    seen_ids.add(match[0])
                    all_candidates.append(match[:6])

        def run_stage(sql, params, stage, variant, fields_getter=None, fallback_fields=None):
            if len(all_candidates) >= limit:
                return
            cur.execute(sql, params)
            add_matches(cur.fetchall(), stage, variant, fields_getter, fallback_fields)

        translated_name = None
        translated_normalized = None
        detected_lang = "unknown"
        try:
            from utils.translation import auto_translate_to_turkish
            tr_result, detected_lang = auto_translate_to_turkish(name_input)
            if tr_result and normalize_turkish(tr_result) != name_normalized:
                translated_name = tr_result
                translated_normalized = normalize_turkish(tr_result)
                logger.debug(
                    "Pre-screen translation",
                    extra={
                        "query": name_input,
                        "detected_lang": detected_lang,
                        "translated": translated_name,
                    },
                )
        except Exception as e:
            logger.warning(
                "Pre-screen translation failed, continuing without",
                extra={"error": str(e)},
            )

        query_variants = []
        seen_variants = set()

        def add_query_variant(kind, value):
            normalized = normalize_turkish(value or "")
            if len(normalized) < 2 or normalized in seen_variants:
                return
            seen_variants.add(normalized)
            query_variants.append({"kind": kind, "value": normalized})

        add_query_variant("normalized", name_normalized)
        if translated_normalized:
            add_query_variant("translated", translated_normalized)

        for variant in query_variants:
            value = variant["value"]
            label = f"{variant['kind']}:{value}"
            exact_sql = f"""
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       1.0 as lexical_score,
                       ({name_norm_expr} = %s) as retrieval_name_match,
                       ({name_tr_norm_expr} = %s) as retrieval_name_tr_match
                FROM trademarks
                WHERE ({name_norm_expr} = %s OR {name_tr_norm_expr} = %s)
            """
            exact_params = [value, value, value, value]
            exact_sql, exact_params = apply_common_filters(exact_sql, exact_params)
            exact_sql += " ORDER BY length(name) ASC LIMIT 25;"
            run_stage(
                exact_sql,
                exact_params,
                "exact",
                label,
                fields_getter=self._row_text_fields,
            )

        from services.scoring_service import _GENERIC_SUFFIXES, tokenize as _tok
        from idf_lookup import IDFLookup as _IDF

        true_generic_terms = set(_GENERIC_SUFFIXES)
        try:
            true_generic_terms.update(_IDF.get_descriptor_suffixes())
            true_generic_terms.update(
                _IDF.get_descriptor_suffixes(use_translated_idf=True)
            )
        except Exception as exc:
            logger.debug("Descriptor suffix lookup failed during retrieval: %s", exc)

        def is_true_generic(token, use_translated_idf=False):
            token = normalize_turkish(token or "")
            return token in true_generic_terms or _IDF.is_descriptor_like(
                token,
                use_translated_idf=use_translated_idf,
            )

        def is_anchor_token(token, use_translated_idf=False):
            token = normalize_turkish(token or "")
            if len(token) < 2 or is_true_generic(token, use_translated_idf):
                return False
            get_class = _IDF.get_word_class_tr if use_translated_idf else _IDF.get_word_class
            get_idf = _IDF.get_idf_tr if use_translated_idf else _IDF.get_idf
            word_class = get_class(token)
            return word_class in {"distinctive", "semi_generic"} or (
                word_class == "generic"
                and get_idf(token) >= 6.5
                and not _IDF.is_descriptor_like(
                    token,
                    use_translated_idf=use_translated_idf,
                )
            )

        ordered_tokens = name_normalized.split()
        translated_ordered_tokens = translated_normalized.split() if translated_normalized else []
        original_token_set = set(_tok(name_input))
        translated_token_set = set(_tok(translated_name)) if translated_name else set()

        compact_variants = []
        seen_compact_variants = set()

        def add_compact_variant(kind, value):
            compact_value = normalize_turkish(value or "").replace(" ", "")
            if len(compact_value) < 4 or compact_value in seen_compact_variants:
                return
            seen_compact_variants.add(compact_value)
            compact_variants.append({"kind": kind, "value": compact_value})

        for variant in query_variants:
            add_compact_variant(f"{variant['kind']}_compact", variant["value"])

        for tokens, kind, use_tr in (
            (ordered_tokens, "core_compact", False),
            (translated_ordered_tokens, "translated_core_compact", True),
        ):
            for index, token in enumerate(tokens[:-1]):
                next_token = tokens[index + 1]
                if is_anchor_token(token, use_translated_idf=use_tr) and is_true_generic(
                    next_token,
                    use_translated_idf=use_tr,
                ):
                    add_compact_variant(kind, token + next_token)
            for token in tokens:
                if is_true_generic(token, use_translated_idf=use_tr):
                    continue
                for suffix in true_generic_terms:
                    if len(suffix) >= 3 and token.endswith(suffix):
                        root = token[: -len(suffix)]
                        if len(root) >= 4 and is_anchor_token(
                            root,
                            use_translated_idf=use_tr,
                        ):
                            add_compact_variant(kind, token)
                            break

        for variant in compact_variants:
            compact_value = variant["value"]
            pattern = f"%{_sql_like_escape(compact_value)}%"
            label = f"{variant['kind']}:{compact_value}"
            compact_sql = f"""
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.88 as lexical_score,
                       ({name_compact_expr} LIKE %s ESCAPE '\\') as retrieval_name_match,
                       ({name_tr_compact_expr} LIKE %s ESCAPE '\\') as retrieval_name_tr_match
                FROM trademarks
                WHERE ({name_compact_expr} LIKE %s ESCAPE '\\'
                    OR {name_tr_compact_expr} LIKE %s ESCAPE '\\')
            """
            compact_params = [pattern, pattern, pattern, pattern]
            compact_sql, compact_params = apply_common_filters(compact_sql, compact_params)
            compact_sql += f"""
                ORDER BY CASE
                    WHEN {name_compact_expr} = %s OR {name_tr_compact_expr} = %s THEN 0
                    ELSE 1
                END,
                length(name) ASC
                LIMIT 80;
            """
            compact_params.extend([compact_value, compact_value])
            run_stage(
                compact_sql,
                compact_params,
                "compact",
                label,
                fields_getter=self._row_text_fields,
            )

        containment_variants = [
            variant for variant in query_variants
            if len(variant["value"]) >= 4 and (" " in variant["value"] or variant["kind"] == "translated")
        ]
        for variant in containment_variants:
            value = variant["value"]
            pattern = f"%{_sql_like_escape(value)}%"
            label = f"{variant['kind']}_containment:{value}"
            contain_sql = f"""
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       0.86 as lexical_score,
                       ({name_norm_expr} LIKE %s ESCAPE '\\') as retrieval_name_match,
                       ({name_tr_norm_expr} LIKE %s ESCAPE '\\') as retrieval_name_tr_match
                FROM trademarks
                WHERE ({name_norm_expr} LIKE %s ESCAPE '\\'
                    OR {name_tr_norm_expr} LIKE %s ESCAPE '\\')
            """
            contain_params = [pattern, pattern, pattern, pattern]
            contain_sql, contain_params = apply_common_filters(contain_sql, contain_params)
            contain_sql += " ORDER BY length(name) ASC LIMIT 60;"
            run_stage(
                contain_sql,
                contain_params,
                "containment",
                label,
                fields_getter=self._row_text_fields,
            )

        anchor_candidates = {}
        for token in original_token_set:
            if is_anchor_token(token):
                anchor_candidates[token] = max(anchor_candidates.get(token, 0.0), _IDF.get_idf(token))
        for token in translated_token_set:
            if is_anchor_token(token, use_translated_idf=True):
                anchor_candidates[token] = max(
                    anchor_candidates.get(token, 0.0),
                    _IDF.get_idf_tr(token),
                )

        anchor_tokens = sorted(
            anchor_candidates,
            key=lambda token: anchor_candidates[token],
            reverse=True,
        )[:8]
        short_anchor_tokens = [
            token for token in anchor_tokens if 2 <= len(token) <= 3
        ]
        broad_anchor_tokens = [
            token for token in anchor_tokens if len(token) > 3
        ]

        def short_token_boundary_clause(norm_expr, tokens, joiner):
            clauses = []
            params = []
            padded_expr = f"(' ' || {norm_expr} || ' ')"
            for token in tokens:
                token_pattern = f"% {_sql_like_escape(token)} %"
                clauses.append(f"{padded_expr} LIKE %s ESCAPE '\\'")
                params.append(token_pattern)
            return f" {joiner} ".join(clauses), params

        def run_short_token_stage(stage, tokens, joiner, score, stage_limit):
            if not tokens:
                return
            name_clause, name_params = short_token_boundary_clause(
                name_norm_expr,
                tokens,
                joiner,
            )
            name_tr_clause, name_tr_params = short_token_boundary_clause(
                name_tr_norm_expr,
                tokens,
                joiner,
            )
            token_sql = f"""
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       {score} as lexical_score,
                       ({name_clause}) as retrieval_name_match,
                       ({name_tr_clause}) as retrieval_name_tr_match
                FROM trademarks
                WHERE (({name_clause}) OR ({name_tr_clause}))
            """
            token_params = name_params + name_tr_params + name_params + name_tr_params
            token_sql, token_params = apply_common_filters(token_sql, token_params)
            token_sql += f" ORDER BY length(name) ASC LIMIT {stage_limit};"
            run_stage(
                token_sql,
                token_params,
                stage,
                "short_tokens:" + ",".join(tokens),
                fields_getter=self._row_text_fields,
            )

        def token_clause(norm_expr, compact_expr, tokens, joiner):
            clauses = []
            params = []
            for token in tokens:
                token_pattern = f"%{_sql_like_escape(token)}%"
                compact_pattern = f"%{_sql_like_escape(token.replace(' ', ''))}%"
                clauses.append(
                    f"({norm_expr} LIKE %s ESCAPE '\\' OR {compact_expr} LIKE %s ESCAPE '\\')"
                )
                params.extend([token_pattern, compact_pattern])
            return f" {joiner} ".join(clauses), params

        def run_token_stage(stage, tokens, joiner, score, stage_limit):
            if not tokens:
                return
            name_clause, name_params = token_clause(name_norm_expr, name_compact_expr, tokens, joiner)
            name_tr_clause, name_tr_params = token_clause(name_tr_norm_expr, name_tr_compact_expr, tokens, joiner)
            token_sql = f"""
                SELECT id, application_no, name, nice_class_numbers, image_path,
                       {score} as lexical_score,
                       ({name_clause}) as retrieval_name_match,
                       ({name_tr_clause}) as retrieval_name_tr_match
                FROM trademarks
                WHERE (({name_clause}) OR ({name_tr_clause}))
            """
            token_params = name_params + name_tr_params + name_params + name_tr_params
            token_sql, token_params = apply_common_filters(token_sql, token_params)
            token_sql += f" ORDER BY length(name) ASC LIMIT {stage_limit};"
            run_stage(
                token_sql,
                token_params,
                stage,
                "tokens:" + ",".join(tokens),
                fields_getter=self._row_text_fields,
            )

        if len(short_anchor_tokens) > 1:
            run_short_token_stage(
                "short-all-token",
                short_anchor_tokens,
                "AND",
                "0.82",
                80,
            )
        if short_anchor_tokens:
            run_short_token_stage(
                "short-token",
                short_anchor_tokens,
                "OR",
                "0.78",
                120,
            )
        if len(broad_anchor_tokens) > 1:
            run_token_stage("all-token", broad_anchor_tokens, "AND", "0.85", 100)
        if broad_anchor_tokens:
            run_token_stage("any-token", broad_anchor_tokens, "OR", "0.80", 80)

        remaining_limit = max(limit - len(all_candidates), 0)
        if remaining_limit > 0:
            for variant in query_variants:
                value = variant["value"]
                label = f"{variant['kind']}_fuzzy:{value}"
                fuzzy_sql = f"""
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           GREATEST(
                               similarity({name_norm_expr}, %s),
                               COALESCE(similarity({name_tr_norm_expr}, %s), 0)
                           ) as lexical_score,
                           (similarity({name_norm_expr}, %s) >= 0.3) as retrieval_name_match,
                           (COALESCE(similarity({name_tr_norm_expr}, %s), 0) >= 0.3) as retrieval_name_tr_match
                    FROM trademarks
                    WHERE LOWER(COALESCE(name, '')) != LOWER(%s)
                      AND GREATEST(
                          similarity({name_norm_expr}, %s),
                          COALESCE(similarity({name_tr_norm_expr}, %s), 0)
                      ) >= 0.3
                """
                fuzzy_params = [value, value, value, value, name_input, value, value]
                fuzzy_sql, fuzzy_params = apply_common_filters(fuzzy_sql, fuzzy_params)
                fuzzy_sql += f"""
                    ORDER BY GREATEST(
                        similarity({name_norm_expr}, %s),
                        COALESCE(similarity({name_tr_norm_expr}, %s), 0)
                    ) DESC
                    LIMIT %s;
                """
                fuzzy_params.extend([value, value, min(remaining_limit, 120)])
                run_stage(
                    fuzzy_sql,
                    fuzzy_params,
                    "fuzzy",
                    label,
                    fields_getter=self._row_text_fields,
                )
                remaining_limit = max(limit - len(all_candidates), 0)
                if remaining_limit <= 0:
                    break

        if q_img_vec:
            try:
                img_sql = """
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           (1 - (image_embedding <=> %s::halfvec)) as lexical_score
                    FROM trademarks
                    WHERE image_embedding IS NOT NULL
                """
                img_params = [str(q_img_vec)]
                img_sql, img_params = apply_common_filters(img_sql, img_params)
                img_sql += " ORDER BY image_embedding <=> %s::halfvec LIMIT 50;"
                img_params.append(str(q_img_vec))
                run_stage(img_sql, img_params, "visual", "image_embedding", fallback_fields=["visual"])
            except Exception as e:
                logger.warning(
                    "Image vector search stage failed, continuing without",
                    extra={"error": str(e)},
                )

        ocr_queries = set()
        if name_input.strip():
            ocr_queries.add(name_normalized)
        if translated_normalized:
            ocr_queries.add(translated_normalized)
        if q_ocr_text and q_ocr_text.strip():
            ocr_queries.add(normalize_turkish(q_ocr_text.strip()))

        for ocr_q in ocr_queries:
            if len(ocr_q) < 2:
                continue
            try:
                escaped_ocr = f"%{_sql_like_escape(ocr_q)}%"
                ocr_sql = f"""
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           0.75 as lexical_score
                    FROM trademarks
                    WHERE logo_ocr_text IS NOT NULL AND logo_ocr_text != ''
                      AND {ocr_norm_expr} LIKE %s ESCAPE '\\'
                """
                ocr_params = [escaped_ocr]
                ocr_sql, ocr_params = apply_common_filters(ocr_sql, ocr_params)
                ocr_sql += " ORDER BY length(name) ASC LIMIT 20;"
                run_stage(
                    ocr_sql,
                    ocr_params,
                    "OCR",
                    f"ocr:{ocr_q}",
                    fallback_fields=["logo_ocr_text"],
                )
            except Exception as e:
                logger.warning(
                    "OCR text search failed for query",
                    extra={"ocr_query": ocr_q, "error": str(e)},
                )

        if name_normalized and len(name_normalized) >= 2:
            try:
                qlen = len(name_normalized)
                name_phonetic = f"dmetaphone({name_norm_expr}) = dmetaphone(%s)"
                name_tr_phonetic = f"dmetaphone({name_tr_norm_expr}) = dmetaphone(%s)"
                phon_sql = f"""
                    SELECT id, application_no, name, nice_class_numbers, image_path,
                           0.70 as lexical_score,
                           ({name_phonetic}) as retrieval_name_match,
                           ({name_tr_phonetic}) as retrieval_name_tr_match
                    FROM trademarks
                    WHERE (
                        length({name_norm_expr}) BETWEEN GREATEST(2, %s - 2) AND %s + 4
                        OR length({name_tr_norm_expr}) BETWEEN GREATEST(2, %s - 2) AND %s + 4
                    )
                      AND ({name_phonetic} OR {name_tr_phonetic})
                """
                phon_params = [
                    name_normalized,
                    name_normalized,
                    qlen,
                    qlen,
                    qlen,
                    qlen,
                    name_normalized,
                    name_normalized,
                ]
                phon_sql, phon_params = apply_common_filters(phon_sql, phon_params)
                phon_sql += f"""
                    ORDER BY LEAST(
                        levenshtein({name_norm_expr}, %s),
                        levenshtein({name_tr_norm_expr}, %s)
                    ) ASC,
                    length(name) ASC
                    LIMIT 100;
                """
                phon_params.extend([name_normalized, name_normalized])
                run_stage(
                    phon_sql,
                    phon_params,
                    "phonetic",
                    f"phonetic:{name_normalized}",
                    fields_getter=self._row_text_fields,
                )
            except Exception as e:
                logger.warning(
                    "Phonetic pre-screen failed, continuing without",
                    extra={"error": str(e)},
                )

        return all_candidates

    def pre_screen_by_image(self, q_img_vec, q_dino_vec=None, target_classes=None, limit=20):
        """Pre-screen candidates by visual similarity when text query is empty."""
        cur = self.conn.cursor()
        all_candidates = []

        # Use CLIP embedding as primary, DINOv2 as secondary
        visual_cols = []
        params = []
        if q_img_vec:
            visual_cols.append("(1 - (image_embedding <=> %s::halfvec))")
            params.append(str(q_img_vec))
        if q_dino_vec:
            visual_cols.append("(1 - (dinov2_embedding <=> %s::halfvec))")
            params.append(str(q_dino_vec))

        if not visual_cols:
            return []

        # Combine visual scores
        if len(visual_cols) == 2:
            score_expr = f"GREATEST({visual_cols[0]}, {visual_cols[1]})"
        else:
            score_expr = visual_cols[0]

        sql = f"""
            SELECT id, application_no, name, nice_class_numbers, image_path,
                   {score_expr} as visual_score
            FROM trademarks
            WHERE image_embedding IS NOT NULL
              AND (application_date >= NOW() - INTERVAL '11 years' OR application_date IS NULL)
        """

        if target_classes and len(target_classes) > 0:
            sql += " AND (nice_class_numbers && %s::integer[] OR 99 = ANY(nice_class_numbers))"
            params.append(target_classes)

        order_params = []
        if q_img_vec:
            order_params.append(str(q_img_vec))
        if q_dino_vec:
            order_params.append(str(q_dino_vec))
        params.extend(order_params)

        sql += f" ORDER BY {score_expr} DESC LIMIT %s;"
        params.append(limit)

        cur.execute(sql, params)
        all_candidates = cur.fetchall()
        return all_candidates

    def calculate_hybrid_risk(self, candidates, name_input, query_text_vec,
                                 query_img_vec, query_dino_vec=None, query_color_vec=None,
                                 query_ocr_text=""):
        if not candidates: return []

        candidate_ids = [str(c[0]) for c in candidates]

        clip_col = f"(1 - (t.image_embedding <=> %s::halfvec))" if query_img_vec else "0.0"
        dino_col = f"(1 - (t.dinov2_embedding <=> %s::halfvec))" if query_dino_vec else "0.0"
        color_col = f"(1 - (t.color_histogram <=> %s::halfvec))" if query_color_vec else "0.0"

        sql = f"""
            SELECT
                t.application_no, t.name, t.final_status, t.nice_class_numbers, t.image_path,
                t.name_tr, t.application_date, t.expiry_date,
                t.holder_name, t.holder_tpe_client_id,
                t.attorney_name, t.attorney_no, t.registration_no,
                similarity(t.name, %s) as score_lexical,
                {clip_col} as score_clip,
                {dino_col} as score_dinov2,
                {color_col} as score_color,
                t.logo_ocr_text,
                t.bulletin_no,
                (dmetaphone(t.name) = dmetaphone(%s)) as phonetic_match,
                (t.extracted_goods IS NOT NULL
                    AND t.extracted_goods != '[]'::jsonb
                    AND t.extracted_goods != 'null'::jsonb) AS has_extracted_goods,
                t.extracted_goods,
                t.id as trademark_id,
                t.holder_id
            FROM trademarks t
            WHERE t.id = ANY(%s::uuid[])
        """

        params = [name_input]
        if query_img_vec:
            params.append(str(query_img_vec))
        if query_dino_vec:
            params.append(str(query_dino_vec))
        if query_color_vec:
            params.append(str(query_color_vec))
        params.extend([name_input, candidate_ids])

        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        results = []

        for r in rows:
            candidate_name = r[1] or ""
            candidate_image_path = r[4]
            candidate_name_tr = r[5] or ""
            candidate_app_date = r[6]
            candidate_expiry_date = r[7]
            candidate_holder_name = r[8]
            candidate_holder_tpe_id = r[9]
            candidate_attorney_name = r[10]
            candidate_attorney_no = r[11]
            candidate_registration_no = r[12]
            lex_postgres = float(r[13]) if r[13] is not None else 0.0
            clip_sim = float(r[14]) if r[14] is not None else 0.0
            dino_sim = float(r[15]) if r[15] is not None else 0.0
            color_sim = float(r[16]) if r[16] is not None else 0.0
            candidate_ocr = (r[17] or "").strip()
            bool(r[19]) if len(r) > 19 and r[19] is not None else False
            has_eg = bool(r[20]) if len(r) > 20 and r[20] is not None else False
            raw_extracted_goods = r[21] if len(r) > 21 else None
            query_logo_profile = getattr(self, "_last_query_logo_profile", None)
            candidate_logo_profile = None
            if (query_img_vec or query_dino_vec) and candidate_image_path:
                candidate_profile_path = resolve_logo_image_path(
                    candidate_image_path,
                    roots=[str(DATA_ROOT)],
                )
                if candidate_profile_path:
                    candidate_logo_profile = build_logo_image_profile(
                        candidate_profile_path,
                        candidate_ocr,
                    )

            vis, visual_breakdown = _calculate_visual_breakdown(
                clip_sim=clip_sim,
                dinov2_sim=dino_sim,
                color_sim=color_sim,
                ocr_text_a=query_ocr_text,
                ocr_text_b=candidate_ocr,
                logo_profile_a=query_logo_profile,
                logo_profile_b=candidate_logo_profile,
            )

            # Centralized scoring via score_pair()
            score_breakdown = score_pair(
                query_name=name_input,
                candidate_name=candidate_name,
                text_sim=lex_postgres,
                semantic_sim=0.0,
                visual_sim=vis,
                phonetic_sim=calculate_phonetic_similarity(name_input, candidate_name),
                candidate_translations={
                    'name_tr': candidate_name_tr,
                },
                visual_breakdown=visual_breakdown,
            )
            candidate_trademark_id = str(r[22]) if len(r) > 22 and r[22] else None
            retrieval_metadata = getattr(self, "_candidate_retrieval_metadata", {}).get(
                candidate_trademark_id,
                {},
            )
            if retrieval_metadata:
                score_breakdown["retrieval_sources"] = retrieval_metadata.get("retrieval_sources", [])
                score_breakdown["retrieval_matched_fields"] = retrieval_metadata.get(
                    "retrieval_matched_fields",
                    [],
                )
                score_breakdown["retrieval_matched_stages"] = retrieval_metadata.get(
                    "retrieval_matched_stages",
                    [],
                )
                score_breakdown["retrieval_query_variants"] = retrieval_metadata.get(
                    "retrieval_query_variants",
                    [],
                )
            results.append({
                "application_no": r[0],
                "name": candidate_name,
                "name_tr": candidate_name_tr or None,
                "status": r[2],
                "classes": r[3],
                "image_path": candidate_image_path,
                "application_date": str(candidate_app_date) if candidate_app_date else None,
                "expiry_date": str(candidate_expiry_date) if candidate_expiry_date else None,
                "holder_name": candidate_holder_name,
                "holder_tpe_client_id": candidate_holder_tpe_id,
                "attorney_name": candidate_attorney_name,
                "attorney_no": candidate_attorney_no,
                "registration_no": candidate_registration_no,
                "bulletin_no": r[18] if len(r) > 18 else None,
                "exact_match": score_breakdown.get("exact_match", False),
                "scores": score_breakdown,
                "has_extracted_goods": has_eg,
                "extracted_goods": raw_extracted_goods if has_eg else None,
                "trademark_id": candidate_trademark_id,
                "holder_id": str(r[23]) if len(r) > 23 and r[23] else None,
            })

        # Sort: exact matches first, then by total score
        results.sort(key=lambda x: (x.get('exact_match', False), x['scores']['total']), reverse=True)
        return results

    def collect_risk_candidates(
        self,
        name,
        image_path=None,
        target_classes=None,
        attorney_no=None,
        limit=10,
        precomputed_features=None,
    ):
        """Return ranked trademark candidates from the canonical RiskEngine path."""
        name = name or ""
        if precomputed_features:
            q_text_vec = None
            q_img_vec = precomputed_features.get("clip_embedding")
            q_dino_vec = precomputed_features.get("dino_embedding")
            q_color_vec = precomputed_features.get("color_histogram")
            q_ocr_text = precomputed_features.get("ocr_text") or ""
            self._last_query_logo_profile = (
                build_logo_image_profile(image_path, q_ocr_text)
                if image_path and os.path.exists(image_path)
                else None
            )
        else:
            q_text_vec, q_img_vec, q_dino_vec, q_color_vec, q_ocr_text = self.get_query_vectors(
                name,
                image_path,
            )

        # For image-only/logo flows, OCR is retrieval-only against
        # logo_ocr_text. Do not promote OCR into the trademark-name path.
        screen_name = name if name.strip() else ""
        raw_candidates = self.pre_screen_candidates(
            screen_name,
            target_classes,
            limit=max(500, int(limit or 10)),
            attorney_no=attorney_no,
            q_img_vec=q_img_vec,
            q_dino_vec=q_dino_vec,
            q_ocr_text=q_ocr_text,
        )
        final_results = self.calculate_hybrid_risk(
            raw_candidates,
            name,
            q_text_vec,
            q_img_vec,
            q_dino_vec,
            q_color_vec,
            query_ocr_text=q_ocr_text,
        )
        return final_results[: int(limit or 10)]

    def assess_brand_risk(self, name, image_path=None, target_classes=None, description=None, attorney_no=None):
        """
        Fast path only - returns result from local DB without live investigation.
        Returns tuple: (result_dict, needs_live_investigation: bool)
        """
        name = name or ""
        query_start = time.perf_counter()
        logger.info(
            "Assessing brand risk",
            extra={
                "trademark_name": name,
                "has_image": image_path is not None,
                "mode": "fast_path",
            },
        )

        suggested_classes = []
        if (not target_classes or len(target_classes) == 0) and description:
            suggestions = self.suggest_classes(description)
            target_classes = [s['class_number'] for s in suggestions]
            suggested_classes = suggestions
            logger.info(
                "Auto-mapped classes",
                extra={
                    "classes": target_classes,
                    "suggestion_count": len(suggestions),
                },
            )

        risk_start = time.perf_counter()
        final_results = self.collect_risk_candidates(
            name=name,
            image_path=image_path,
            target_classes=target_classes,
            attorney_no=attorney_no,
            limit=500,
        )
        risk_duration = (time.perf_counter() - risk_start) * 1000
        logger.debug(
            "Risk candidates collected",
            extra={
                "results": len(final_results),
                "duration_ms": round(risk_duration, 2),
            },
        )

        top_score = final_results[0]['scores']['total'] if final_results else 0.0
        needs_live = top_score < 0.75

        total_duration = (time.perf_counter() - query_start) * 1000
        logger.info(
            "Brand risk assessment complete",
            extra={
                "trademark_name": name,
                "risk_score": round(top_score, 4),
                "candidates": len(final_results),
                "needs_live_investigation": needs_live,
                "duration_ms": round(total_duration, 2),
            },
        )

        result = {
            "query": {
                "name": name,
                "classes": target_classes,
                "has_logo": image_path is not None,
                "effective_name": name,
                "text_source": "USER_TEXT" if name.strip() else "IMAGE_ONLY",
                "ocr_text_used": False,
            },
            "auto_suggested_classes": suggested_classes,
            "final_risk_score": top_score,
            "top_candidates": final_results[:100],
            "source": "local_db"
        }

        return result, needs_live

    def run_live_investigation(self, name, target_classes=None, progress_callback=None, attorney_no=None):
        """
        Run live investigation: Scrape -> AI Enrich -> Ingest -> Recalculate.
        progress_callback(percent, message) is called to report progress.
        Returns updated result dict.
        """
        investigation_start = time.perf_counter()

        def report(pct, msg):
            logger.info(
                "Live investigation progress",
                extra={"percent": pct, "message": msg, "trademark_name": name},
            )
            if progress_callback:
                progress_callback(pct, msg)

        report(10, "Starting live investigation")

        try:
            from agentic_search import _scrape_lock

            report(15, "Waiting for scrape queue...")
            with _scrape_lock:
                report(18, "Launching scraper")
                bot = scrapper.TurkPatentScraper(headless=True)
                scraped_data = bot.search_and_ingest(name)

                # Brief cooldown between scrapes
                time.sleep(2)

            if scraped_data and bot.active_data_dir:
                active_dir = bot.active_data_dir
                meta_file = bot.active_metadata_file
                bot.close()

                report(40, "Generating AI embeddings")
                ai.process_folder(active_dir)

                report(70, "Ingesting to database")
                ingest.process_file_batch(self.conn, meta_file, force=True)

                report(90, "Recalculating risk scores")
                logger.info(
                    "Live scraping complete",
                    extra={"trademark_name": name, "data_dir": str(active_dir)},
                )
            else:
                logger.info(
                    "No new data found from scraper",
                    extra={"trademark_name": name},
                )
                bot.close()
                report(90, "No new data found, finalizing")

        except Exception as e:
            logger.error(
                "Live investigation failed",
                extra={"trademark_name": name, "error": str(e)},
            )
            self.conn.rollback()
            raise

        # Recalculate with new data (text-only, no image)
        final_results = self.collect_risk_candidates(
            name=name,
            target_classes=target_classes,
            attorney_no=attorney_no,
            limit=500,
        )

        report(100, "Complete")

        investigation_duration = (time.perf_counter() - investigation_start) * 1000
        top_score = final_results[0]['scores']['total'] if final_results else 0.0

        logger.info(
            "Live investigation complete",
            extra={
                "name": name,
                "risk_score": round(top_score, 4),
                "candidates": len(final_results),
                "duration_ms": round(investigation_duration, 2),
            },
        )

        return {
            "query": {"name": name, "classes": target_classes, "has_logo": False},
            "auto_suggested_classes": [],
            "final_risk_score": top_score,
            "top_candidates": final_results[:100],
            "source": "live_investigation"
        }

    def assess_brand_risk_full(self, name, image_path=None, target_classes=None, description=None):
        """
        Full synchronous assessment (legacy behavior) - includes live investigation if needed.
        """
        result, needs_live = self.assess_brand_risk(name, image_path, target_classes, description)

        if needs_live:
            logging.info("Risk < 75%. Triggering Live Investigation...")
            result = self.run_live_investigation(name, target_classes)

        return result

if __name__ == "__main__":
    engine = RiskEngine()
    report = engine.assess_brand_risk_full("Nike", target_classes=[25])
    print(f"\nRisk: {report['final_risk_score'] * 100:.2f}%")
    for m in report['top_candidates']:
        print(f" - {m['name']} ({m['status']}): {m['scores']['total']*100:.1f}%")
