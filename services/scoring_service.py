"""Canonical scoring helpers shared across search, watchlist, and admin flows."""

import logging
import math
import re
import warnings
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from foreign_generics import FOREIGN_GENERICS_OVERRIDE
from idf_lookup import IDFLookup
from utils.idf_scoring import normalize_turkish, tokenize, turkish_lower
from utils.phonetic import calculate_phonetic_similarity

logger = logging.getLogger("risk_engine")


RISK_THRESHOLDS = {
    "critical": 0.90,
    "very_high": 0.80,
    "high": 0.70,
    "medium": 0.50,
    "low": 0.0,
}

SCORE_VERSION = "v2_text_visual"

_TEXT_TOKEN_WEIGHTS = {
    "distinctive": 1.0,
    "common_anchor": 0.65,
    "semi_generic": 0.45,
    "low_protectability_anchor": 0.45,
    "generic": 0.10,
}

_ANCHOR_TOKEN_ROLES = frozenset(
    {"distinctive", "common_anchor", "semi_generic", "low_protectability_anchor"}
)
_COMMON_ANCHOR_MIN_IDF = 6.5
_TRUE_GENERIC_ONLY_CAP = 0.18
_MISSING_COMMON_ANCHOR_CAP = 0.18
_LOW_PROTECTABILITY_MIN_DOC_FREQ = 50
_LOW_PROTECTABILITY_MIN_LAST_RATE = 0.30
_LOW_PROTECTABILITY_MAX_FIRST_RATE = 0.30
_LOW_PROTECTABILITY_MAX_SINGLE_RATE = 0.08
_LOW_PROTECTABILITY_EXACT_CAP = 0.65
_LOW_PROTECTABILITY_NON_EXACT_CAP = 0.58
_LOW_PROTECTABILITY_SIGNALS = frozenset(
    {
        "mostly_suffix",
        "low_initial_use",
        "low_single_use",
        "moderate_suffix_with_dispersion",
        "compound_suffix",
    }
)
_COMPOUND_PREFIX_MIN_LENGTH = 4
_COMPOUND_SUFFIX_MIN_LENGTH = 3
_ADDED_MATTER_CHANGED_CORE_CAP = 0.78
_ADDED_MATTER_SINGLE_ANCHOR_EXTRA_CAP = 0.78
_ADDED_MATTER_DISTINCTIVE_EXTRA_CAP = 0.84
_ADDED_MATTER_SEMI_GENERIC_EXTRA_CAP = 0.88
_PARTIAL_MULTI_ANCHOR_CHANGED_MATTER_CAP = 0.58
_SINGLE_ANCHOR_ASYMMETRIC_ADDED_MATTER_CAP = 0.68
_WEAK_FUZZY_ANCHOR_FRAGMENT_CAP = 0.62
_WEAK_FUZZY_ANCHOR_QUALITY_CAP = 0.68
_WEAK_PHONETIC_ANCHOR_FRAGMENT_CAP = 0.58
_WEAK_PHONETIC_ANCHOR_QUALITY_CAP = 0.68
_FUZZY_ANCHOR_FRAGMENT_LENGTH_RATIO = 0.78
_FUZZY_ANCHOR_STRONG_LENGTH_RATIO = 0.80
_FUZZY_ANCHOR_STRONG_RAW_MIN = 0.78
_MISSING_DOMINANT_ANCHOR_CAP = 0.62
_SHORT_ANCHOR_NON_EXACT_MAX_LENGTH = 2
_SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_MAX_LENGTH = 4
_SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP = 0.58
_EXACT_SHORT_ANCHOR_ADDED_MATTER_CAP = 0.84
_SHORT_ACRONYM_SUBSET_FLOOR = 0.68
_SHORT_ACRONYM_SUBSET_CAP = 0.82
_SHORT_NON_EXACT_VISUAL_MAX_LENGTH = 6
_SHORT_ANCHOR_NON_EXACT_CAP = 0.45
_WEAK_TEXT_VISUAL_LOW_MAX = 0.80
_WEAK_TEXT_VISUAL_MID_MAX = 0.90
_WEAK_TEXT_VISUAL_LOW_CAP = 0.45
_WEAK_TEXT_VISUAL_MID_CAP = 0.69
_STRONG_VISUAL_INDEPENDENCE_MIN = 0.80
_OCR_DISAGREEMENT_MAX = 0.70
_OCR_STRONG_MATCH_MIN = 0.78
_VERY_STRONG_VISUAL_COMPONENT_MIN = 0.90
_PLAIN_TEXT_WORDMARK_STRONG_TEXT_AGREEMENT = 0.78
_PLAIN_TEXT_WORDMARK_BOTH_CAP = 0.45
_PLAIN_TEXT_WORDMARK_ONE_SIDE_CAP = 0.55
_OCR_SHORT_NON_EXACT_MAX_LEN = 5
_OCR_SHORT_NON_EXACT_CAP = 0.68
_OCR_FRAGMENT_LENGTH_RATIO = 0.60
_OCR_FRAGMENT_CAP = 0.62
_IMAGE_ONLY_VISUAL_GUARD_FLOOR = 0.50
_IMAGE_ONLY_VISUAL_GUARD_CAP = 0.68
_IMAGE_ONLY_NEAR_DUPLICATE_COMPONENT_MIN = 0.88
_IMAGE_ONLY_NEAR_DUPLICATE_TOTAL_MIN = 0.88
_IMAGE_ONLY_LAYOUT_VARIANT_STRONG_COMPONENT_MIN = 0.84
_IMAGE_ONLY_LAYOUT_VARIANT_SECONDARY_COMPONENT_MIN = 0.72
_IMAGE_ONLY_LAYOUT_VARIANT_NEURAL_MIN = 0.78
_IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_STRONG_MIN = 0.81
_IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SECONDARY_MIN = 0.74
_IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_NEURAL_MIN = 0.765
_IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SPREAD_MAX = 0.10
_IMAGE_ONLY_LAYOUT_VARIANT_CAP = 0.84
_LOGO_PROFILE_CACHE_LIMIT = 4096
_WEAK_TEXT_CAP_MARKERS = frozenset(
    {
        "generic_only_cap",
        "missing_anchor_generic_only_cap",
        "missing_anchor_containment_only_cap",
        "missing_common_anchor_cap",
        "missing_distinctive_anchor_cap",
        "missing_dominant_anchor_cap",
        "short_anchor_non_exact_anchor_cap",
        "semantic_or_phonetic_without_lexical_anchor_cap",
    }
)
_LIMITED_TEXT_CAP_MARKERS = frozenset(
    {
        "added_matter_partial_multi_anchor_changed_matter_cap",
        "added_matter_single_anchor_asymmetric_added_matter_cap",
        "added_matter_single_anchor_low_protectability_extra_cap",
        "weak_fuzzy_anchor_fragment_cap",
        "weak_fuzzy_anchor_quality_cap",
        "weak_phonetic_anchor_fragment_cap",
        "weak_phonetic_anchor_quality_cap",
        "weak_shared_low_protectability_exact_anchor_cap",
        "weak_shared_low_protectability_non_exact_anchor_cap",
        "short_non_exact_anchor_added_matter_cap",
        "short_acronym_subset_missing_matter_cap",
    }
)

_VISUAL_COMPONENT_WEIGHTS = {
    "clip": 0.45,
    "dinov2": 0.35,
    "ocr": 0.15,
}


def _clamp_score(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        return lower
    if not math.isfinite(score):
        return lower
    return max(lower, min(upper, score))


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGO_PROFILE_CACHE: Dict[Tuple[str, int, int, str], Dict] = {}


def resolve_logo_image_path(image_path: str, roots: Optional[List[str]] = None) -> Optional[str]:
    """Resolve a stored trademark image path without importing route modules."""
    if not image_path:
        return None

    raw_path = str(image_path).strip()
    if not raw_path:
        return None

    normalized = raw_path.replace("\\", "/")
    path = Path(normalized)
    if ".." in path.parts:
        return None

    candidates = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append(_PROJECT_ROOT / normalized)
        if "/images/" in normalized:
            candidates.append(_PROJECT_ROOT / normalized.replace("/images/", "/"))

        for root in roots or []:
            if not root:
                continue
            root_path = Path(str(root)).expanduser()
            if not root_path.is_absolute():
                root_path = _PROJECT_ROOT / root_path

            candidates.append(root_path / normalized)
            marker = "bulletins/Marka/"
            if normalized.startswith(marker):
                candidates.append(root_path / normalized[len(marker):])
            if "/images/" in normalized:
                candidates.append(root_path / normalized.replace("/images/", "/"))

    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return str(resolved)
    return None


def _empty_logo_profile(kind: str = "unknown", reason: str = "") -> Dict:
    return {
        "kind": kind,
        "is_plain_text_wordmark": False,
        "confidence": 0.0,
        "reason": reason,
        "metrics": {},
    }


def _count_activity_bands(row_activity) -> int:
    bands = 0
    in_band = False
    gap = 0
    for active in row_activity:
        if active:
            if not in_band:
                bands += 1
                in_band = True
            gap = 0
        elif in_band:
            gap += 1
            if gap > 2:
                in_band = False
                gap = 0
    return bands


def _safe_logo_profile(profile: Optional[Dict]) -> Optional[Dict]:
    if not isinstance(profile, dict):
        return None
    metrics = profile.get("metrics") if isinstance(profile.get("metrics"), dict) else {}
    return {
        "kind": profile.get("kind", "unknown"),
        "is_plain_text_wordmark": bool(profile.get("is_plain_text_wordmark")),
        "confidence": round(_clamp_score(profile.get("confidence", 0.0)), 4),
        "reason": profile.get("reason", ""),
        "metrics": {
            key: round(value, 4) if isinstance(value, float) else value
            for key, value in metrics.items()
            if key
        },
    }


def _classify_logo_profile_from_metrics(metrics: Dict) -> Dict:
    uniform_blank_background = (
        metrics.get("uniform_background_ratio", 0.0) >= 0.78
        and metrics.get("border_std", 999.0) <= 38.0
    )
    text_band_geometry = (
        0.006 <= metrics.get("foreground_ratio", 0.0) <= 0.16
        and metrics.get("bbox_width_ratio", 0.0) >= 0.34
        and metrics.get("bbox_height_ratio", 1.0) <= 0.34
        and metrics.get("bbox_area_ratio", 1.0) <= 0.24
        and metrics.get("occupied_row_ratio", 1.0) <= 0.30
        and metrics.get("horizontal_band_count", 99) <= 4
    )
    has_ocr_text = bool(metrics.get("has_ocr_text"))
    plain_score = (
        (0.30 if uniform_blank_background else 0.0)
        + (0.30 if text_band_geometry else 0.0)
        + (0.20 if has_ocr_text else 0.0)
        + (0.10 if metrics.get("occupied_col_ratio", 0.0) >= 0.30 else 0.0)
        + (0.10 if metrics.get("foreground_ratio", 1.0) <= 0.10 else 0.0)
    )
    is_plain_text = bool(
        uniform_blank_background
        and text_band_geometry
        and has_ocr_text
        and plain_score >= 0.70
    )
    return {
        "kind": "plain_text_wordmark" if is_plain_text else "graphic_or_mixed",
        "is_plain_text_wordmark": is_plain_text,
        "confidence": round(_clamp_score(plain_score), 4),
        "reason": (
            "blank_background_text_geometry"
            if is_plain_text
            else "visual_geometry_not_plain_text"
        ),
        "metrics": metrics,
    }


def build_logo_image_profile(image_path: str, ocr_text: str = "") -> Dict:
    """Classify whether an image is mostly plain OCR text on a blank background."""
    resolved_path = resolve_logo_image_path(image_path)
    if not resolved_path:
        return _empty_logo_profile(reason="image_not_found")

    ocr_norm = normalize_turkish(ocr_text or "")
    try:
        stat = Path(resolved_path).stat()
    except OSError:
        return _empty_logo_profile(reason="image_not_found")

    cache_key = (
        resolved_path,
        int(stat.st_mtime),
        int(stat.st_size),
        ocr_norm[:80],
    )
    cached = _LOGO_PROFILE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    try:
        from PIL import Image
        import numpy as np

        with Image.open(resolved_path) as image:
            image = image.convert("RGB")
            image.thumbnail((512, 512))
            arr = np.asarray(image, dtype=np.float32)
    except Exception as exc:
        profile = _empty_logo_profile(reason=f"profile_failed:{type(exc).__name__}")
        _LOGO_PROFILE_CACHE[cache_key] = profile
        return dict(profile)

    if arr.ndim < 2:
        profile = _empty_logo_profile(reason="profile_failed:invalid_image_shape")
        _LOGO_PROFILE_CACHE[cache_key] = profile
        return dict(profile)

    height, width = arr.shape[:2]
    if width <= 0 or height <= 0:
        return _empty_logo_profile(reason="empty_image")

    border_size = max(1, min(width, height) // 20)
    border_pixels = np.concatenate(
        [
            arr[:border_size, :, :].reshape(-1, 3),
            arr[-border_size:, :, :].reshape(-1, 3),
            arr[:, :border_size, :].reshape(-1, 3),
            arr[:, -border_size:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    background = np.median(border_pixels, axis=0)
    distances = np.linalg.norm(arr - background, axis=2)
    border_distances = np.linalg.norm(border_pixels - background, axis=1)
    uniform_background_ratio = float(np.mean(border_distances <= 32.0))
    border_std = float(np.mean(np.std(border_pixels, axis=0)))
    threshold = max(28.0, float(np.percentile(border_distances, 95)) + 14.0)
    foreground = distances > threshold
    foreground_ratio = float(np.mean(foreground))

    if not foreground.any():
        profile = _empty_logo_profile(kind="blank", reason="no_foreground")
        profile["metrics"] = {
            "uniform_background_ratio": uniform_background_ratio,
            "border_std": border_std,
            "foreground_ratio": foreground_ratio,
        }
        _LOGO_PROFILE_CACHE[cache_key] = profile
        return dict(profile)

    ys, xs = np.where(foreground)
    bbox_width = int(xs.max() - xs.min() + 1)
    bbox_height = int(ys.max() - ys.min() + 1)
    bbox_width_ratio = bbox_width / max(width, 1)
    bbox_height_ratio = bbox_height / max(height, 1)
    bbox_area_ratio = (bbox_width * bbox_height) / max(width * height, 1)
    row_counts = foreground.sum(axis=1)
    col_counts = foreground.sum(axis=0)
    row_activity = row_counts > max(2, width * 0.01)
    col_activity = col_counts > max(1, height * 0.004)
    occupied_row_ratio = float(np.mean(row_activity))
    occupied_col_ratio = float(np.mean(col_activity))
    band_count = _count_activity_bands(row_activity.tolist())
    has_ocr_text = len(ocr_norm.replace(" ", "")) >= 3

    profile = _classify_logo_profile_from_metrics(
        {
            "width": width,
            "height": height,
            "uniform_background_ratio": uniform_background_ratio,
            "border_std": border_std,
            "foreground_ratio": foreground_ratio,
            "bbox_width_ratio": bbox_width_ratio,
            "bbox_height_ratio": bbox_height_ratio,
            "bbox_area_ratio": bbox_area_ratio,
            "occupied_row_ratio": occupied_row_ratio,
            "occupied_col_ratio": occupied_col_ratio,
            "horizontal_band_count": band_count,
            "has_ocr_text": has_ocr_text,
        }
    )

    if len(_LOGO_PROFILE_CACHE) >= _LOGO_PROFILE_CACHE_LIMIT:
        _LOGO_PROFILE_CACHE.clear()
    _LOGO_PROFILE_CACHE[cache_key] = profile
    return dict(profile)


def get_risk_level(score: float) -> str:
    """Single source of truth for risk level classification."""
    if score >= RISK_THRESHOLDS["critical"]:
        return "critical"
    if score >= RISK_THRESHOLDS["very_high"]:
        return "very_high"
    if score >= RISK_THRESHOLDS["high"]:
        return "high"
    if score >= RISK_THRESHOLDS["medium"]:
        return "medium"
    return "low"


def calculate_visual_similarity(
    clip_sim: float = 0.0,
    dinov2_sim: float = 0.0,
    color_sim: float = 0.0,
    ocr_text_a: str = "",
    ocr_text_b: str = "",
) -> float:
    """Combine CLIP, DINOv2, and OCR with active-component normalization.

    color_sim remains in the signature for compatibility with existing callers,
    but it does not contribute to the V2 visual risk score.
    """
    score, _ = _calculate_visual_breakdown(
        clip_sim=clip_sim,
        dinov2_sim=dinov2_sim,
        color_sim=color_sim,
        ocr_text_a=ocr_text_a,
        ocr_text_b=ocr_text_b,
    )
    return score


def _calculate_ocr_visual_similarity(ocr_text_a: str, ocr_text_b: str) -> float:
    """Score logo OCR against logo OCR without promoting OCR into trademark-name text.

    OCR is intentionally conservative: exact/compact-exact text can be strong, but
    noisy partial token overlap is not treated as proof of visual similarity.
    """
    a_norm = normalize_turkish(ocr_text_a or "")
    b_norm = normalize_turkish(ocr_text_b or "")
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    a_tokens = [token for token in a_norm.split() if token]
    b_tokens = [token for token in b_norm.split() if token]
    a_compact = "".join(a_tokens)
    b_compact = "".join(b_tokens)
    if a_compact and b_compact and a_compact == b_compact:
        return 1.0

    raw_char_score = SequenceMatcher(None, a_norm, b_norm).ratio()
    compact_char_score = (
        SequenceMatcher(None, a_compact, b_compact).ratio()
        if a_compact and b_compact
        else 0.0
    )
    char_score = max(raw_char_score, compact_char_score)
    if not a_compact or not b_compact:
        return round(_clamp_score(char_score), 4)

    shorter_len = min(len(a_compact), len(b_compact))
    longer_len = max(len(a_compact), len(b_compact), 1)
    length_ratio = shorter_len / longer_len
    if shorter_len <= _OCR_SHORT_NON_EXACT_MAX_LEN:
        char_score = min(char_score, _OCR_SHORT_NON_EXACT_CAP)
    if length_ratio < _OCR_FRAGMENT_LENGTH_RATIO:
        char_score = min(char_score, _OCR_FRAGMENT_CAP)

    return round(_clamp_score(char_score), 4)


def _calculate_visual_breakdown(
    clip_sim: float = 0.0,
    dinov2_sim: float = 0.0,
    color_sim: float = 0.0,
    ocr_text_a: str = "",
    ocr_text_b: str = "",
    logo_profile_a: Optional[Dict] = None,
    logo_profile_b: Optional[Dict] = None,
) -> Tuple[float, Dict]:
    del color_sim  # Compatibility input; color is not a V2 visual risk signal.

    ocr_sim = _calculate_ocr_visual_similarity(ocr_text_a, ocr_text_b)

    components = {}
    if clip_sim:
        components["clip"] = _clamp_score(clip_sim)
    if dinov2_sim:
        components["dinov2"] = _clamp_score(dinov2_sim)
    if ocr_text_a and ocr_text_b:
        components["ocr"] = ocr_sim

    active_weight = sum(_VISUAL_COMPONENT_WEIGHTS[name] for name in components)
    if active_weight <= 0:
        return 0.0, {
            "total": 0.0,
            "active_components": [],
            "components": {
                "clip": 0.0,
                "dinov2": 0.0,
                "ocr": 0.0,
            },
            "normalization": "active_components",
            "cap_applied": None,
            "logo_profile": {
                "query": _safe_logo_profile(logo_profile_a),
                "candidate": _safe_logo_profile(logo_profile_b),
            },
        }

    weighted_score = sum(
        components[name] * _VISUAL_COMPONENT_WEIGHTS[name]
        for name in components
    ) / active_weight
    neural_components = {
        name: components[name]
        for name in ("clip", "dinov2")
        if name in components
    }
    neural_weight = sum(_VISUAL_COMPONENT_WEIGHTS[name] for name in neural_components)
    neural_score = (
        sum(
            neural_components[name] * _VISUAL_COMPONENT_WEIGHTS[name]
            for name in neural_components
        ) / neural_weight
        if neural_weight > 0
        else 0.0
    )
    score = max(weighted_score, neural_score) if neural_components else weighted_score
    query_plain = _logo_profile_is_plain_text(logo_profile_a)
    candidate_plain = _logo_profile_is_plain_text(logo_profile_b)
    wordmark_ocr_driver_active = bool(
        query_plain
        and candidate_plain
        and ocr_sim >= _OCR_STRONG_MATCH_MIN
    )
    if wordmark_ocr_driver_active:
        score = max(score, ocr_sim)

    active = frozenset(components)
    ocr_available = bool(ocr_text_a and ocr_text_b)
    ocr_disagreement = bool(ocr_available and ocr_sim < _OCR_DISAGREEMENT_MAX)
    ocr_strong_match = bool(ocr_available and ocr_sim >= _OCR_STRONG_MATCH_MIN)
    very_strong_visual_components = bool(
        components.get("clip", 0.0) >= _VERY_STRONG_VISUAL_COMPONENT_MIN
        and components.get("dinov2", 0.0) >= _VERY_STRONG_VISUAL_COMPONENT_MIN
    )

    score = round(_clamp_score(score), 4)
    return score, {
        "total": score,
        "active_components": sorted(active),
        "components": {
            "clip": round(components.get("clip", 0.0), 4),
            "dinov2": round(components.get("dinov2", 0.0), 4),
            "ocr": round(ocr_sim, 4),
        },
        "weights": {
            name: round(_VISUAL_COMPONENT_WEIGHTS[name] / active_weight, 4)
            for name in sorted(active)
        },
        "normalization": "active_components",
        "ocr_policy": (
            "wordmark_visual_driver"
            if wordmark_ocr_driver_active
            else "support_only"
        ),
        "ocr_text_normalized": {
            "query": normalize_turkish(ocr_text_a)[:120] if ocr_text_a else "",
            "candidate": normalize_turkish(ocr_text_b)[:120] if ocr_text_b else "",
        },
        "weighted_total_with_ocr": round(_clamp_score(weighted_score), 4),
        "neural_total_without_ocr": round(_clamp_score(neural_score), 4),
        "cap_applied": None,
        "cap_reason": None,
        "caps_applied": [],
        "logo_profile": {
            "query": _safe_logo_profile(logo_profile_a),
            "candidate": _safe_logo_profile(logo_profile_b),
        },
        "ocr_disagreement": ocr_disagreement,
        "ocr_strong_match": ocr_strong_match,
        "very_strong_visual_components": very_strong_visual_components,
        "wordmark_ocr_visual_driver": {
            "applies": wordmark_ocr_driver_active,
            "reason": (
                "plain_text_wordmark_ocr_match"
                if wordmark_ocr_driver_active
                else "not_applicable"
            ),
            "score": round(ocr_sim, 4) if wordmark_ocr_driver_active else 0.0,
            "query_plain_text_wordmark": query_plain,
            "candidate_plain_text_wordmark": candidate_plain,
        },
        "ocr_guardrails": "disabled",
        "ocr_similarity_policy": "exact_or_conservative_character_only",
        "ocr_thresholds": {
            "disagreement_below": _OCR_DISAGREEMENT_MAX,
            "strong_match_min": _OCR_STRONG_MATCH_MIN,
            "short_non_exact_max_len": _OCR_SHORT_NON_EXACT_MAX_LEN,
            "short_non_exact_cap": _OCR_SHORT_NON_EXACT_CAP,
            "fragment_length_ratio": _OCR_FRAGMENT_LENGTH_RATIO,
            "fragment_cap": _OCR_FRAGMENT_CAP,
        },
    }


def check_substring_containment(query: str, target: str) -> float:
    """Return 1.0 when either normalized string contains the other."""
    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    if not q_norm or not t_norm:
        return 0.0
    if q_norm in t_norm or t_norm in q_norm:
        return 1.0
    return 0.0


def calculate_token_overlap(query: str, target: str) -> float:
    """Return the share of query tokens present in the target."""
    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    q_tokens = set(q_norm.split())
    t_tokens = set(t_norm.split())

    if not q_tokens:
        return 0.0

    matches = q_tokens.intersection(t_tokens)
    return len(matches) / len(q_tokens)


def calculate_multilevel_similarity(query: str, target: str) -> float:
    """Compute the combined token, word, and structural similarity score."""
    if not query or not target:
        return 0.0

    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    if not q_norm or not t_norm:
        return 0.0
    if q_norm == t_norm:
        return 1.0

    token_sim = SequenceMatcher(None, q_norm, t_norm).ratio()
    q_words = set(q_norm.split())
    t_words = set(t_norm.split())

    if not q_words or not t_words:
        return min(token_sim, 0.99)

    idf_weight = {"distinctive": 1.0, "semi_generic": 0.5, "generic": 0.1}

    def _word_weight(word: str) -> float:
        from idf_lookup import IDFLookup

        word_class = IDFLookup.get_word_class(word)
        return idf_weight.get(word_class, 1.0)

    exact_matched = q_words.intersection(t_words)
    query_only = list(q_words - t_words)
    target_only = list(t_words - q_words)

    matched_weight = sum(_word_weight(word) for word in exact_matched)
    fuzzy_threshold = 0.75
    fuzzy_weight = 0.0
    remaining_q = []
    remaining_t = list(target_only)

    for query_word in query_only:
        if not remaining_t:
            remaining_q.append(query_word)
            continue

        best_ratio = 0.0
        best_idx = -1
        for idx, target_word in enumerate(remaining_t):
            ratio = SequenceMatcher(None, query_word, target_word).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = idx

        if best_ratio >= fuzzy_threshold:
            fuzzy_weight += _word_weight(query_word) * best_ratio
            remaining_t.pop(best_idx)
        else:
            remaining_q.append(query_word)

    total_matched_weight = matched_weight + fuzzy_weight
    unmatched_q_weight = sum(_word_weight(word) for word in remaining_q)
    unmatched_t_weight = sum(_word_weight(word) for word in remaining_t)

    denominator = (
        total_matched_weight
        + 0.3 * unmatched_q_weight
        + 0.2 * unmatched_t_weight
    )
    word_sim = total_matched_weight / denominator if denominator > 0 else 0.0

    length_ratio = min(len(q_norm), len(t_norm)) / max(len(q_norm), len(t_norm))
    word_count_ratio = min(len(q_words), len(t_words)) / max(len(q_words), len(t_words))
    containment = 1.0 if (q_norm in t_norm or t_norm in q_norm) else 0.0
    sentence_sim = 0.25 * length_ratio + 0.25 * word_count_ratio + 0.50 * containment

    combined = 0.25 * token_sim + 0.65 * word_sim + 0.10 * sentence_sim
    return min(combined, 0.99)


def calculate_name_similarity(query: str, target: str) -> float:
    """Calculate text similarity with Turkish normalization."""
    return calculate_multilevel_similarity(query, target)


calculate_turkish_similarity = calculate_name_similarity


_GENERIC_SUFFIXES: frozenset = frozenset(
    {
        "patent",
        "marka",
        "grup",
        "group",
        "holding",
        "sanayi",
        "san",
        "ticaret",
        "tic",
        "limited",
        "ltd",
        "sti",
        "pty",
        "inc",
        "corp",
        "company",
        "co",
        "office",
        "ve",
        "dis",
        "ic",
        "ithalat",
        "ihracat",
        "uretim",
        "dagitim",
        "yonetim",
        "hizmet",
        "hizmetleri",
        "endustri",
        "endustriyel",
        "teknoloji",
        "tech",
        "digital",
        "global",
        "inter",
        "international",
        "trademark",
        "market",
        "store",
    }
)


def _compound_suffixes(use_translated_idf: bool = False) -> Tuple[str, ...]:
    suffixes = {
        suffix
        for suffix in _GENERIC_SUFFIXES
        if len(suffix) >= _COMPOUND_SUFFIX_MIN_LENGTH
    }
    try:
        suffixes.update(
            IDFLookup.get_descriptor_suffixes(
                use_translated_idf=use_translated_idf,
                min_length=_COMPOUND_SUFFIX_MIN_LENGTH,
            )
        )
    except Exception as exc:
        logger.debug("Descriptor suffix lookup failed: %s", exc)
    return tuple(sorted(suffixes, key=len, reverse=True))


def _shared_true_generic_suffix_parts(
    w1: str,
    w2: str,
    use_translated_idf: bool = False,
) -> Optional[Tuple[str, str, str]]:
    """Return roots and suffix when both tokens share a true-generic suffix."""
    for suffix in _compound_suffixes(use_translated_idf):
        if w1.endswith(suffix) and w2.endswith(suffix):
            return w1[: -len(suffix)], w2[: -len(suffix)], suffix
    return None


def _strip_shared_generic_suffix(
    w1: str,
    w2: str,
    use_translated_idf: bool = False,
) -> tuple:
    """Strip the same generic suffix from both words before fuzzy matching."""
    shared_suffix = _shared_true_generic_suffix_parts(w1, w2, use_translated_idf)
    if shared_suffix is not None:
        root1, root2, _ = shared_suffix
        if (
            len(root1) >= _COMPOUND_PREFIX_MIN_LENGTH
            and len(root2) >= _COMPOUND_PREFIX_MIN_LENGTH
        ):
            return root1, root2

    min_len = min(len(w1), len(w2))
    common_len = 0
    for idx in range(1, min_len + 1):
        if w1[-idx] == w2[-idx]:
            common_len = idx
        else:
            break

    if common_len >= 4:
        suffix = w1[-common_len:]
        get_class = IDFLookup.get_word_class_tr if use_translated_idf else IDFLookup.get_word_class
        if get_class(suffix) == "generic":
            root1 = w1[:-common_len]
            root2 = w2[:-common_len]
            if len(root1) >= 2 and len(root2) >= 2:
                return root1, root2

    return w1, w2


def fuzzy_match(w1: str, w2: str, use_translated_idf: bool = False) -> float:
    """Run fuzzy matching after removing a shared generic suffix."""
    if not w1 or not w2:
        return 0.0

    shared_suffix = _shared_true_generic_suffix_parts(w1, w2, use_translated_idf)
    if shared_suffix is not None:
        root1, root2, _ = shared_suffix
        if (
            len(root1) < _COMPOUND_PREFIX_MIN_LENGTH
            or len(root2) < _COMPOUND_PREFIX_MIN_LENGTH
        ):
            return 0.0
        root_similarity = SequenceMatcher(None, root1, root2).ratio()
        if root_similarity < 0.72:
            return min(0.45, root_similarity * 0.60)
        return root_similarity

    stripped_w1, stripped_w2 = _strip_shared_generic_suffix(
        w1,
        w2,
        use_translated_idf,
    )
    return SequenceMatcher(None, stripped_w1, stripped_w2).ratio()


def _compact_text(value: str) -> str:
    return normalize_turkish(value or "").replace(" ", "")


def _is_embedded_token_extension(w1: str, w2: str) -> bool:
    if not w1 or not w2 or w1 == w2:
        return False
    shorter, longer = (w1, w2) if len(w1) <= len(w2) else (w2, w1)
    return (
        len(shorter) >= _COMPOUND_PREFIX_MIN_LENGTH
        and len(longer) - len(shorter) >= 2
        and (longer.startswith(shorter) or longer.endswith(shorter))
    )


def _is_descriptor_like_term(word: str, use_translated_idf: bool = False) -> bool:
    return IDFLookup.is_descriptor_like(
        normalize_turkish(word or ""),
        use_translated_idf=use_translated_idf,
    )


def _descriptor_stat_number(stats: Dict, key: str) -> float:
    try:
        return float(stats.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_low_protectability_anchor(word: str, use_translated_idf: bool = False) -> bool:
    word_norm = normalize_turkish(word or "")
    if not word_norm or _is_true_generic(word_norm, use_translated_idf):
        return False

    get_class = IDFLookup.get_word_class_tr if use_translated_idf else IDFLookup.get_word_class
    if get_class(word_norm) not in {"distinctive", "semi_generic"}:
        return False

    stats = _descriptor_stats_for_word(word_norm, use_translated_idf)
    if not stats:
        return False

    doc_frequency = _descriptor_stat_number(stats, "doc_frequency")
    last_rate = _descriptor_stat_number(stats, "last_rate")
    first_rate = _descriptor_stat_number(stats, "first_rate")
    single_rate = _descriptor_stat_number(stats, "single_rate")
    reason_flags = set(stats.get("reason_flags") or [])
    signal_count = len(reason_flags.intersection(_LOW_PROTECTABILITY_SIGNALS))

    return (
        doc_frequency >= _LOW_PROTECTABILITY_MIN_DOC_FREQ
        and last_rate >= _LOW_PROTECTABILITY_MIN_LAST_RATE
        and first_rate <= _LOW_PROTECTABILITY_MAX_FIRST_RATE
        and single_rate <= _LOW_PROTECTABILITY_MAX_SINGLE_RATE
        and signal_count >= 2
    )


def _is_true_generic(word: str, use_translated_idf: bool = False) -> bool:
    word_norm = normalize_turkish(word or "")
    return (
        word_norm in _GENERIC_SUFFIXES
        or word_norm in FOREIGN_GENERICS_OVERRIDE
        or _is_descriptor_like_term(word_norm, use_translated_idf)
    )


def _is_legacy_override_material_added_matter(
    word: str,
    use_translated_idf: bool = False,
) -> bool:
    word_norm = normalize_turkish(word or "")
    if (
        word_norm not in FOREIGN_GENERICS_OVERRIDE
        or _is_descriptor_like_term(word_norm, use_translated_idf)
    ):
        return False

    stats = _descriptor_stats_for_word(word_norm, use_translated_idf)
    if not stats:
        return False

    original_class = (
        stats.get("original_word_class")
        or stats.get("final_word_class")
        or ""
    )
    descriptor_score = IDFLookup.get_descriptor_score(
        word_norm,
        use_translated_idf=use_translated_idf,
    )
    return (
        original_class in {"distinctive", "semi_generic"}
        and descriptor_score < 0.72
    )


def _word_class(word: str, use_translated_idf: bool = False) -> str:
    get_class = IDFLookup.get_word_class_tr if use_translated_idf else IDFLookup.get_word_class
    word_norm = normalize_turkish(word or "")
    if _is_true_generic(word_norm, use_translated_idf):
        return "generic"
    return get_class(word_norm)


def _token_role(word: str, use_translated_idf: bool = False) -> str:
    word_norm = normalize_turkish(word or "")
    if _is_true_generic(word_norm, use_translated_idf):
        return "generic"

    get_idf = IDFLookup.get_idf_tr if use_translated_idf else IDFLookup.get_idf
    word_class = _word_class(word_norm, use_translated_idf)
    if (
        word_class in {"distinctive", "semi_generic"}
        and _is_low_protectability_anchor(word_norm, use_translated_idf)
    ):
        return "low_protectability_anchor"
    if (
        word_class == "generic"
        and get_idf(word_norm) >= _COMMON_ANCHOR_MIN_IDF
        and not _is_descriptor_like_term(word_norm, use_translated_idf)
    ):
        return "common_anchor"
    return word_class


def _word_weight(word: str, use_translated_idf: bool = False) -> float:
    return _TEXT_TOKEN_WEIGHTS.get(_token_role(word, use_translated_idf), 1.0)


def _split_compound_token(word: str, use_translated_idf: bool = False) -> Optional[Dict]:
    word_norm = normalize_turkish(word or "")
    if (
        not word_norm
        or not word_norm.isalnum()
        or _is_true_generic(word_norm, use_translated_idf)
    ):
        return None

    for suffix in _compound_suffixes(use_translated_idf):
        if not word_norm.endswith(suffix):
            continue
        root = word_norm[: -len(suffix)]
        if (
            len(root) < _COMPOUND_PREFIX_MIN_LENGTH
            or not root.isalnum()
            or _is_true_generic(root, use_translated_idf)
            or _token_role(root, use_translated_idf) not in _ANCHOR_TOKEN_ROLES
        ):
            continue
        return {"token": word_norm, "root": root, "suffix": suffix}

    return None


def _expand_compound_token(
    word: str,
    use_translated_idf: bool = False,
    depth: int = 0,
) -> Tuple[List[str], List[Dict]]:
    split = _split_compound_token(word, use_translated_idf)
    if split is None or depth >= 2:
        return [normalize_turkish(word or "")], []

    root_parts, root_expansions = _expand_compound_token(
        split["root"],
        use_translated_idf,
        depth + 1,
    )
    return root_parts + [split["suffix"]], root_expansions + [split]


def _expand_compound_tokens(
    tokens: Set[str],
    use_translated_idf: bool = False,
) -> Tuple[Set[str], List[Dict]]:
    expanded: Set[str] = set()
    expansions: List[Dict] = []
    for token in sorted(tokens):
        parts, token_expansions = _expand_compound_token(token, use_translated_idf)
        expanded.update(part for part in parts if part)
        expansions.extend(token_expansions)
    return expanded, expansions


def _ordered_expanded_tokens(
    value: str,
    use_translated_idf: bool = False,
) -> List[str]:
    ordered_tokens: List[str] = []
    for raw_token in re.findall(r"[a-z0-9]+", normalize_turkish(value or "")):
        parts, _ = _expand_compound_token(raw_token, use_translated_idf)
        for part in parts:
            if part and part not in ordered_tokens:
                ordered_tokens.append(part)
    return ordered_tokens


def _empty_role_sets() -> Dict[str, Set[str]]:
    return {
        "distinctive": set(),
        "common_anchor": set(),
        "semi_generic": set(),
        "low_protectability_anchor": set(),
        "generic": set(),
    }


def _text_breakdown_base(
    query: str,
    target: str,
    text_sim: float,
    semantic_sim: float,
    phonetic_sim: float,
    visual_sim: float,
    use_translated_idf: bool,
) -> Dict:
    return {
        "query": query,
        "target": target,
        "score_version": SCORE_VERSION,
        "text_similarity": round(_clamp_score(text_sim), 4),
        "semantic_similarity": round(_clamp_score(semantic_sim), 4),
        "phonetic_similarity": round(_clamp_score(phonetic_sim), 4),
        "visual_similarity": round(_clamp_score(visual_sim), 4),
        "translation_similarity": 0.0,
        "scoring_path": "",
        "total": 0.0,
        "exact_match": False,
        "containment": 0.0,
        "token_overlap": 0.0,
        "weighted_overlap": 0.0,
        "distinctive_match": 0.0,
        "common_anchor_match": 0.0,
        "semi_generic_match": 0.0,
        "generic_match": 0.0,
        "matched_words": [],
        "token_classes": {},
        "token_score": 0.0,
        "containment_score": 0.0,
        "compound_containment_score": 0.0,
        "char_support_score": 0.0,
        "semantic_support_score": 0.0,
        "phonetic_support_score": 0.0,
        "dominant_core_score": 0.0,
        "calibration_breakdown": {},
        "caps_applied": [],
        "compound_expansions": {"query": [], "target": []},
        "added_matter_breakdown": {},
        "weak_shared_anchor_guard": {},
        "short_acronym_subset_guard": {},
        "short_non_exact_anchor_added_matter_guard": {},
        "low_protectability_terms": {"query": [], "target": []},
        "low_protectability_stats": {},
        "short_anchor_guard": [],
        "use_translated_idf": use_translated_idf,
    }


def _descriptor_stats_for_word(
    word: str,
    use_translated_idf: bool = False,
) -> Dict:
    return IDFLookup.get_descriptor_stats(
        normalize_turkish(word or ""),
        use_translated_idf=use_translated_idf,
    )


def _matched_word_record(
    *,
    query_word: str,
    target_word: str,
    match_type: str,
    score: float,
    word_class: str,
    token_role: str,
    use_translated_idf: bool = False,
) -> Dict:
    record = {
        "query_word": query_word,
        "target_word": target_word,
        "match_type": match_type,
        "score": round(score, 4),
        "word_class": word_class,
        "token_role": token_role,
    }
    descriptor_stats = _descriptor_stats_for_word(
        query_word,
        use_translated_idf,
    )
    if descriptor_stats:
        record["descriptor_stats"] = descriptor_stats
    return record


def _token_pair_similarity(
    query_word: str,
    target_word: str,
    use_translated_idf: bool = False,
) -> Tuple[float, str]:
    if query_word == target_word:
        return 1.0, "exact"

    query_role = _token_role(query_word, use_translated_idf)
    target_role = _token_role(target_word, use_translated_idf)
    if "generic" in {query_role, target_role} and query_role != target_role:
        return 0.0, ""

    fuzzy = fuzzy_match(query_word, target_word, use_translated_idf)
    if _is_embedded_token_extension(query_word, target_word):
        fuzzy = min(fuzzy, 0.69)
    if fuzzy >= 0.84:
        return fuzzy, "fuzzy"
    if fuzzy >= 0.74 and min(len(query_word), len(target_word)) >= 4:
        return fuzzy * 0.95, "fuzzy"

    phonetic = calculate_phonetic_similarity(query_word, target_word)
    if phonetic >= 0.90 and fuzzy >= 0.55:
        return min(0.78, phonetic * 0.82), "phonetic"
    if phonetic >= 0.84 and fuzzy >= 0.60:
        return min(0.72, phonetic * 0.78), "phonetic"

    return 0.0, ""


def _short_anchor_guard_record(
    query_word: str,
    target_word: str,
    use_translated_idf: bool = False,
) -> Optional[Dict]:
    query_norm = normalize_turkish(query_word or "")
    target_norm = normalize_turkish(target_word or "")
    if not query_norm or not target_norm or query_norm == target_norm:
        return None

    query_role = _token_role(query_norm, use_translated_idf)
    target_role = _token_role(target_norm, use_translated_idf)
    guarded_tokens = []
    if (
        query_role in _ANCHOR_TOKEN_ROLES
        and len(query_norm) <= _SHORT_ANCHOR_NON_EXACT_MAX_LENGTH
    ):
        guarded_tokens.append(
            {"side": "query", "token": query_norm, "token_role": query_role}
        )
    if (
        target_role in _ANCHOR_TOKEN_ROLES
        and len(target_norm) <= _SHORT_ANCHOR_NON_EXACT_MAX_LENGTH
    ):
        guarded_tokens.append(
            {"side": "target", "token": target_norm, "token_role": target_role}
        )

    if not guarded_tokens:
        return None

    return {
        "query_word": query_norm,
        "target_word": target_norm,
        "guarded_tokens": guarded_tokens,
        "reason": "short_anchor_requires_exact_match",
    }


def _align_tokens_v2(
    q_tokens: Set[str],
    t_tokens: Set[str],
    use_translated_idf: bool = False,
) -> Dict:
    query_total = sum(_word_weight(word, use_translated_idf) for word in q_tokens)
    target_total = sum(_word_weight(word, use_translated_idf) for word in t_tokens)
    remaining_targets = set(t_tokens)
    matches = []
    short_anchor_guard = []

    for query_word in sorted(
        q_tokens,
        key=lambda word: (-_word_weight(word, use_translated_idf), word),
    ):
        best_target = ""
        best_score = 0.0
        best_type = ""
        if query_word in remaining_targets:
            best_target = query_word
            best_score = 1.0
            best_type = "exact"
        else:
            for target_word in remaining_targets:
                guard_record = _short_anchor_guard_record(
                    query_word,
                    target_word,
                    use_translated_idf,
                )
                if guard_record is not None:
                    short_anchor_guard.append(guard_record)
                    continue
                score, match_type = _token_pair_similarity(
                    query_word,
                    target_word,
                    use_translated_idf,
                )
                if score > best_score:
                    best_score = score
                    best_target = target_word
                    best_type = match_type

        if best_score >= 0.70 and best_target:
            remaining_targets.remove(best_target)
            query_weight = _word_weight(query_word, use_translated_idf)
            target_weight = _word_weight(best_target, use_translated_idf)
            word_class = _word_class(query_word, use_translated_idf)
            token_role = _token_role(query_word, use_translated_idf)
            match = {
                "query_word": query_word,
                "target_word": best_target,
                "match_type": best_type,
                "score": round(best_score, 4),
                "word_class": word_class,
                "token_role": token_role,
                "query_weight": query_weight,
                "target_weight": target_weight,
            }
            descriptor_stats = _descriptor_stats_for_word(
                query_word,
                use_translated_idf,
            )
            if descriptor_stats:
                match["descriptor_stats"] = descriptor_stats
            matches.append(match)

    matched_query_weight = sum(match["query_weight"] * match["score"] for match in matches)
    matched_target_weight = sum(match["target_weight"] * match["score"] for match in matches)
    recall = matched_query_weight / query_total if query_total > 0 else 0.0
    precision = matched_target_weight / target_total if target_total > 0 else 0.0
    if recall + precision > 0:
        f1 = 2 * recall * precision / (recall + precision)
    else:
        f1 = 0.0

    matched_weight = sum(match["query_weight"] for match in matches)
    quality = matched_query_weight / matched_weight if matched_weight > 0 else 0.0

    return {
        "matches": matches,
        "query_total": query_total,
        "target_total": target_total,
        "recall": _clamp_score(recall),
        "precision": _clamp_score(precision),
        "f1": _clamp_score(f1),
        "quality": _clamp_score(quality),
        "short_anchor_guard": short_anchor_guard,
    }


def _phrase_containment_score(
    q_norm: str,
    t_norm: str,
    q_tokens: Set[str],
    t_tokens: Set[str],
) -> Tuple[float, float]:
    q_compact = q_norm.replace(" ", "")
    t_compact = t_norm.replace(" ", "")
    if not q_compact or not t_compact:
        return 0.0, 0.0

    query_in_target = q_norm in t_norm or (len(q_compact) >= 4 and q_compact in t_compact)
    target_in_query = t_norm in q_norm or (len(t_compact) >= 4 and t_compact in q_compact)

    if query_in_target:
        coverage = len(q_compact) / max(len(t_compact), 1)
        extra_words = max(len(t_tokens - q_tokens), 0)
        score = 0.84 + (0.12 * coverage) - min(0.18, extra_words * 0.035)
        return _clamp_score(max(0.65, score)), 1.0

    if target_in_query:
        coverage = len(t_compact) / max(len(q_compact), 1)
        extra_words = max(len(q_tokens - t_tokens), 0)
        score = 0.84 + (0.12 * coverage) - min(0.10, extra_words * 0.02)
        return _clamp_score(max(0.60, score)), 0.5

    return 0.0, 0.0


def _class_token_sets(tokens: Set[str], use_translated_idf: bool = False) -> Dict[str, Set[str]]:
    classes = _empty_role_sets()
    for token in tokens:
        classes.setdefault(_token_role(token, use_translated_idf), set()).add(token)
    return classes


def _is_ignored_added_matter_token(token: str) -> bool:
    token_norm = normalize_turkish(token or "")
    return len(token_norm) == 1 and token_norm.isalnum()


def _group_added_matter_tokens(
    tokens: Set[str],
    use_translated_idf: bool = False,
) -> Dict[str, List[str]]:
    groups = {
        "true_generic": [],
        "semi_generic": [],
        "common_anchor": [],
        "low_protectability_anchor": [],
        "distinctive": [],
        "ignored_short_initial": [],
    }
    for token in sorted(tokens):
        if _is_ignored_added_matter_token(token):
            groups["ignored_short_initial"].append(token)
            continue

        role = _token_role(token, use_translated_idf)
        if role == "generic":
            groups["true_generic"].append(token)
        elif role in groups:
            groups[role].append(token)
        else:
            groups["distinctive"].append(token)
    return groups


def _legacy_override_material_terms(
    groups: Dict[str, List[str]],
    use_translated_idf: bool = False,
) -> List[str]:
    return [
        token
        for token in groups.get("true_generic", [])
        if _is_legacy_override_material_added_matter(token, use_translated_idf)
    ]


def _material_added_matter_terms(
    groups: Dict[str, List[str]],
    use_translated_idf: bool = False,
) -> List[str]:
    terms = []
    for role in ("semi_generic", "common_anchor", "low_protectability_anchor", "distinctive"):
        terms.extend(groups.get(role, []))
    terms.extend(_legacy_override_material_terms(groups, use_translated_idf))
    return sorted(set(terms))


def _weighted_match_coverage(
    tokens: Set[str],
    match_scores: Dict[str, float],
    use_translated_idf: bool = False,
) -> float:
    total = sum(_word_weight(token, use_translated_idf) for token in tokens)
    if total <= 0:
        return 0.0
    matched = sum(
        _word_weight(token, use_translated_idf) * _clamp_score(match_scores.get(token, 0.0))
        for token in tokens
    )
    return _clamp_score(matched / total)


def _bounded_score(floor: float, ceiling: float, evidence: float) -> float:
    floor = _clamp_score(floor)
    ceiling = _clamp_score(ceiling)
    if ceiling < floor:
        floor, ceiling = ceiling, floor
    evidence = _clamp_score(evidence)
    return _clamp_score(floor + ((ceiling - floor) * evidence))


def _average_anchor_match_quality(
    matches: List[Dict],
    query_anchor_tokens: Set[str],
    target_anchor_tokens: Set[str],
) -> float:
    scores = [
        _clamp_score(match.get("score", 0.0))
        for match in matches
        if (
            match.get("score", 0.0) >= 0.70
            and (
                match.get("query_word") in query_anchor_tokens
                or match.get("target_word") in target_anchor_tokens
            )
        )
    ]
    if not scores:
        return 0.0
    return _clamp_score(sum(scores) / len(scores))


def _calibrate_added_matter_score(
    *,
    floor: float,
    ceiling: float,
    query_anchor_coverage: float,
    target_anchor_coverage: float,
    full_query_token_coverage: float,
    full_target_token_coverage: float,
    match_quality: float,
    query_material_extra_count: int,
    target_material_extra_count: int,
    brandlike_extra_count: int,
    require_balanced_coverage: bool = False,
) -> Tuple[float, Dict]:
    if require_balanced_coverage:
        anchor_evidence = min(query_anchor_coverage, target_anchor_coverage)
        full_evidence = min(full_query_token_coverage, full_target_token_coverage)
    else:
        anchor_evidence = (
            (query_anchor_coverage * 0.65)
            + (target_anchor_coverage * 0.35)
        )
        full_evidence = (
            (full_query_token_coverage * 0.65)
            + (full_target_token_coverage * 0.35)
        )

    material_extra_count = max(
        0,
        int(query_material_extra_count or 0) + int(target_material_extra_count or 0),
    )
    extra_penalty = min(0.24, max(0, material_extra_count - 1) * 0.04)
    brandlike_penalty = min(0.12, max(0, int(brandlike_extra_count or 0)) * 0.02)
    raw_evidence = (
        (anchor_evidence * 0.45)
        + (full_evidence * 0.35)
        + (_clamp_score(match_quality) * 0.20)
    )
    evidence = _clamp_score(raw_evidence - extra_penalty - brandlike_penalty)
    score = _bounded_score(floor, ceiling, evidence)
    return score, {
        "floor": round(_clamp_score(floor), 4),
        "ceiling": round(_clamp_score(ceiling), 4),
        "evidence_score": round(evidence, 4),
        "raw_evidence_score": round(_clamp_score(raw_evidence), 4),
        "calibrated_score": round(score, 4),
        "factors": {
            "query_anchor_coverage": round(query_anchor_coverage, 4),
            "target_anchor_coverage": round(target_anchor_coverage, 4),
            "full_query_token_coverage": round(full_query_token_coverage, 4),
            "full_target_token_coverage": round(full_target_token_coverage, 4),
            "match_quality": round(_clamp_score(match_quality), 4),
            "query_material_extra_count": query_material_extra_count,
            "target_material_extra_count": target_material_extra_count,
            "brandlike_extra_count": brandlike_extra_count,
            "extra_penalty": round(extra_penalty, 4),
            "brandlike_penalty": round(brandlike_penalty, 4),
            "require_balanced_coverage": require_balanced_coverage,
        },
    }


def _calibration_record(
    *,
    floor: float,
    ceiling: float,
    evidence: float,
    calibrated_score: float,
    factors: Optional[Dict] = None,
) -> Dict:
    return {
        "floor": round(_clamp_score(floor), 4),
        "ceiling": round(_clamp_score(ceiling), 4),
        "evidence_score": round(_clamp_score(evidence), 4),
        "calibrated_score": round(_clamp_score(calibrated_score), 4),
        "factors": factors or {},
    }


def _low_protectability_stats_for_tokens(
    tokens: Set[str],
    use_translated_idf: bool = False,
) -> Dict[str, Dict]:
    stats = {}
    for token in sorted(tokens):
        if _is_low_protectability_anchor(token, use_translated_idf):
            token_stats = _descriptor_stats_for_word(token, use_translated_idf)
            if token_stats:
                stats[token] = token_stats
    return stats


def _weak_shared_low_protectability_anchor_guard(
    *,
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    added_matter_breakdown: Dict,
    use_translated_idf: bool = False,
) -> Dict:
    base_record = {
        "applies": False,
        "reason": "",
        "cap_reason": "",
        "score_cap": None,
        "calibrated_score_cap": None,
        "calibration_breakdown": {},
        "matched_anchor": None,
        "low_protectability_tokens": [],
    }
    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_tokens = set().union(*(t_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    anchor_matches = [
        match
        for match in matches
        if (
            match.get("score", 0.0) >= 0.70
            and (
                match.get("query_word") in query_anchor_tokens
                or match.get("target_word") in target_anchor_tokens
            )
        )
    ]
    if len(anchor_matches) != 1:
        return base_record

    match = anchor_matches[0]
    low_tokens = sorted(
        token
        for token in {match.get("query_word", ""), match.get("target_word", "")}
        if token and _is_low_protectability_anchor(token, use_translated_idf)
    )
    if not low_tokens:
        return base_record

    non_weak_query_anchors = query_anchor_tokens - q_classes["low_protectability_anchor"]
    query_extra_tokens = set(added_matter_breakdown.get("query_extra_tokens") or [])
    if not query_extra_tokens and non_weak_query_anchors:
        return base_record

    match_score = _clamp_score(match.get("score", 0.0))
    match_type = match.get("match_type") or ""
    exact_match = match_type == "exact" and match_score >= 0.98
    floor = 0.50 if exact_match else 0.42
    ceiling = (
        _LOW_PROTECTABILITY_EXACT_CAP
        if exact_match
        else _LOW_PROTECTABILITY_NON_EXACT_CAP
    )
    cap_reason = (
        "weak_shared_low_protectability_exact_anchor_cap"
        if exact_match
        else "weak_shared_low_protectability_non_exact_anchor_cap"
    )
    query_anchor_coverage = _clamp_score(
        added_matter_breakdown.get("query_anchor_coverage", 0.0)
    )
    target_anchor_coverage = _clamp_score(
        added_matter_breakdown.get("target_anchor_coverage", 0.0)
    )
    full_query_token_coverage = _clamp_score(
        added_matter_breakdown.get("full_query_token_coverage", 0.0)
    )
    full_target_token_coverage = _clamp_score(
        added_matter_breakdown.get("full_target_token_coverage", 0.0)
    )
    material_extra_count = int(
        added_matter_breakdown.get("query_material_extra_count", 0) or 0
    ) + int(added_matter_breakdown.get("target_material_extra_count", 0) or 0)
    extra_penalty = min(0.12, material_extra_count * 0.04)
    raw_evidence = (
        (match_score * 0.45)
        + (min(query_anchor_coverage, target_anchor_coverage) * 0.25)
        + (min(full_query_token_coverage, full_target_token_coverage) * 0.20)
        + ((1.0 if exact_match else 0.0) * 0.10)
        - extra_penalty
    )
    evidence = _clamp_score(raw_evidence)
    calibrated_score = _bounded_score(floor, ceiling, evidence)

    return {
        "applies": True,
        "reason": (
            "only shared anchor has low protectability; full query core is not copied"
        ),
        "cap_reason": cap_reason,
        "score_cap": ceiling,
        "calibrated_score_cap": calibrated_score,
        "calibration_breakdown": _calibration_record(
            floor=floor,
            ceiling=ceiling,
            evidence=evidence,
            calibrated_score=calibrated_score,
            factors={
                "match_score": round(match_score, 4),
                "match_type": match_type,
                "query_anchor_coverage": round(query_anchor_coverage, 4),
                "target_anchor_coverage": round(target_anchor_coverage, 4),
                "full_query_token_coverage": round(full_query_token_coverage, 4),
                "full_target_token_coverage": round(full_target_token_coverage, 4),
                "material_extra_count": material_extra_count,
                "extra_penalty": round(extra_penalty, 4),
            },
        ),
        "matched_anchor": {
            "query_word": match.get("query_word", ""),
            "target_word": match.get("target_word", ""),
            "match_type": match_type,
            "score": round(match_score, 4),
        },
        "low_protectability_tokens": low_tokens,
    }


def _short_acronym_subset_guard(
    *,
    q_tokens: Set[str],
    t_tokens: Set[str],
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    added_matter_breakdown: Dict,
    use_translated_idf: bool = False,
) -> Dict:
    base_record = {
        "applies": False,
        "reason": "",
        "cap_reason": "",
        "score_cap": None,
        "calibrated_score_cap": None,
        "calibration_breakdown": {},
        "matched_anchor": None,
    }
    meaningful_q_tokens = {
        token for token in q_tokens if not _is_ignored_added_matter_token(token)
    }
    meaningful_t_tokens = {
        token for token in t_tokens if not _is_ignored_added_matter_token(token)
    }
    query_longer = len(meaningful_q_tokens) >= 2 and len(meaningful_t_tokens) == 1
    target_longer = len(meaningful_q_tokens) == 1 and len(meaningful_t_tokens) >= 2
    if not (query_longer or target_longer):
        return base_record

    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_tokens = set().union(*(t_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    exact_short_anchor_matches = [
        match
        for match in matches
        if (
            match.get("match_type") == "exact"
            and match.get("score", 0.0) >= 0.98
            and match.get("query_word") in query_anchor_tokens
            and match.get("target_word") in target_anchor_tokens
            and len(match.get("query_word", "")) <= _SHORT_ANCHOR_NON_EXACT_MAX_LENGTH
            and len(match.get("target_word", "")) <= _SHORT_ANCHOR_NON_EXACT_MAX_LENGTH
        )
    ]
    if len(exact_short_anchor_matches) != 1:
        return base_record

    match = exact_short_anchor_matches[0]
    if query_longer:
        if meaningful_t_tokens != {match.get("target_word", "")}:
            return base_record
        if _clamp_score(added_matter_breakdown.get("full_query_token_coverage", 0.0)) >= 0.98:
            return base_record
        missing_side = "query"
        missing_material_extra_count = int(
            added_matter_breakdown.get("query_material_extra_count", 0) or 0
        )
        missing_extra_tokens = set(added_matter_breakdown.get("query_extra_tokens") or [])
    else:
        if meaningful_q_tokens != {match.get("query_word", "")}:
            return base_record
        if _clamp_score(added_matter_breakdown.get("full_target_token_coverage", 0.0)) >= 0.98:
            return base_record
        missing_side = "target"
        missing_material_extra_count = int(
            added_matter_breakdown.get("target_material_extra_count", 0) or 0
        )
        missing_extra_tokens = set(added_matter_breakdown.get("target_extra_tokens") or [])

    if missing_material_extra_count <= 0 and not missing_extra_tokens:
        return base_record

    match_score = _clamp_score(match.get("score", 0.0))
    query_anchor_coverage = _clamp_score(
        added_matter_breakdown.get("query_anchor_coverage", 0.0)
    )
    target_anchor_coverage = _clamp_score(
        added_matter_breakdown.get("target_anchor_coverage", 0.0)
    )
    full_query_token_coverage = _clamp_score(
        added_matter_breakdown.get("full_query_token_coverage", 0.0)
    )
    full_target_token_coverage = _clamp_score(
        added_matter_breakdown.get("full_target_token_coverage", 0.0)
    )
    missing_matter_count = max(missing_material_extra_count, len(missing_extra_tokens))
    missing_matter_penalty = min(0.18, missing_matter_count * 0.06)
    raw_evidence = (
        (min(full_query_token_coverage, full_target_token_coverage) * 0.45)
        + (query_anchor_coverage * 0.20)
        + (target_anchor_coverage * 0.15)
        + (match_score * 0.20)
        - missing_matter_penalty
    )
    evidence = _clamp_score(raw_evidence)
    calibrated_score = _bounded_score(
        _SHORT_ACRONYM_SUBSET_FLOOR,
        _SHORT_ACRONYM_SUBSET_CAP,
        evidence,
    )

    return {
        "applies": True,
        "reason": f"exact short anchor copied but material {missing_side} matter is missing",
        "cap_reason": "short_acronym_subset_missing_matter_cap",
        "score_cap": _SHORT_ACRONYM_SUBSET_CAP,
        "calibrated_score_cap": calibrated_score,
        "calibration_breakdown": _calibration_record(
            floor=_SHORT_ACRONYM_SUBSET_FLOOR,
            ceiling=_SHORT_ACRONYM_SUBSET_CAP,
            evidence=evidence,
            calibrated_score=calibrated_score,
            factors={
                "match_score": round(match_score, 4),
                "query_anchor_coverage": round(query_anchor_coverage, 4),
                "target_anchor_coverage": round(target_anchor_coverage, 4),
                "full_query_token_coverage": round(full_query_token_coverage, 4),
                "full_target_token_coverage": round(full_target_token_coverage, 4),
                "missing_side": missing_side,
                "missing_material_extra_count": missing_material_extra_count,
                "missing_extra_token_count": len(missing_extra_tokens),
                "missing_matter_penalty": round(missing_matter_penalty, 4),
            },
        ),
        "matched_anchor": {
            "query_word": match.get("query_word", ""),
            "target_word": match.get("target_word", ""),
            "match_type": match.get("match_type", ""),
            "score": round(match_score, 4),
        },
    }


def _short_non_exact_anchor_added_matter_guard(
    *,
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    added_matter_breakdown: Dict,
) -> Dict:
    base_record = {
        "applies": False,
        "reason": "",
        "cap_reason": "",
        "score_cap": None,
        "matched_anchor": None,
        "query_extra_tokens": [],
        "target_extra_tokens": [],
    }
    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_tokens = set().union(*(t_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    anchor_matches = [
        match
        for match in matches
        if (
            match.get("score", 0.0) >= 0.70
            and (
                match.get("query_word") in query_anchor_tokens
                or match.get("target_word") in target_anchor_tokens
            )
        )
    ]
    if len(anchor_matches) != 1:
        return base_record

    match = anchor_matches[0]
    match_type = match.get("match_type") or ""
    if match_type not in {"fuzzy", "phonetic"}:
        return base_record

    query_word = normalize_turkish(match.get("query_word", ""))
    target_word = normalize_turkish(match.get("target_word", ""))
    if not query_word or not target_word or query_word == target_word:
        return base_record

    shorter_anchor_length = min(len(query_word), len(target_word))
    if shorter_anchor_length > _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_MAX_LENGTH:
        return base_record

    query_extra_tokens = sorted(added_matter_breakdown.get("query_extra_tokens") or [])
    target_extra_tokens = sorted(added_matter_breakdown.get("target_extra_tokens") or [])
    query_material_extra_count = int(
        added_matter_breakdown.get("query_material_extra_count", 0) or 0
    )
    target_material_extra_count = int(
        added_matter_breakdown.get("target_material_extra_count", 0) or 0
    )
    has_extra_matter = bool(
        query_extra_tokens
        or target_extra_tokens
        or query_material_extra_count > 0
        or target_material_extra_count > 0
    )
    if not has_extra_matter:
        return base_record

    return {
        "applies": True,
        "reason": "short non-exact anchor match with added matter",
        "cap_reason": "short_non_exact_anchor_added_matter_cap",
        "score_cap": _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP,
        "matched_anchor": {
            "query_word": query_word,
            "target_word": target_word,
            "match_type": match_type,
            "score": round(_clamp_score(match.get("score", 0.0)), 4),
            "shorter_anchor_length": shorter_anchor_length,
        },
        "query_extra_tokens": query_extra_tokens,
        "target_extra_tokens": target_extra_tokens,
        "query_material_extra_count": query_material_extra_count,
        "target_material_extra_count": target_material_extra_count,
    }


def _edit_distance(left: str, right: str) -> int:
    left = left or ""
    right = right or ""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


def _common_prefix_length(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left or "", right or ""):
        if left_char != right_char:
            break
        count += 1
    return count


def _fuzzy_anchor_metrics(
    query_word: str,
    target_word: str,
    use_translated_idf: bool = False,
) -> Dict:
    query_norm = normalize_turkish(query_word or "")
    target_norm = normalize_turkish(target_word or "")
    max_length = max(len(query_norm), len(target_norm), 1)
    min_length = min(len(query_norm), len(target_norm))
    edit_distance = _edit_distance(query_norm, target_norm)
    length_ratio = min_length / max_length
    edit_similarity = 1.0 - (edit_distance / max_length)
    common_prefix_length = _common_prefix_length(query_norm, target_norm)
    raw_fuzzy_ratio = fuzzy_match(query_norm, target_norm, use_translated_idf)
    allowed_strong_edits = max(1, math.ceil(max_length * 0.20))
    strong_near_miss = bool(
        length_ratio >= _FUZZY_ANCHOR_STRONG_LENGTH_RATIO
        and raw_fuzzy_ratio >= _FUZZY_ANCHOR_STRONG_RAW_MIN
        and edit_distance <= allowed_strong_edits
    )

    return {
        "query_word": query_norm,
        "target_word": target_norm,
        "raw_fuzzy_ratio": round(_clamp_score(raw_fuzzy_ratio), 4),
        "length_ratio": round(_clamp_score(length_ratio), 4),
        "edit_distance": edit_distance,
        "edit_similarity": round(_clamp_score(edit_similarity), 4),
        "common_prefix_length": common_prefix_length,
        "common_prefix_ratio": round(common_prefix_length / max_length, 4),
        "max_length": max_length,
        "allowed_strong_edits": allowed_strong_edits,
        "strong_near_miss": strong_near_miss,
    }


def _anchor_quality_guard(
    *,
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    use_translated_idf: bool = False,
) -> Dict:
    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_tokens = set().union(*(t_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    anchor_matches = [
        match
        for match in matches
        if (
            match.get("query_word") in query_anchor_tokens
            or match.get("target_word") in target_anchor_tokens
        )
    ]
    base_record = {
        "applies": False,
        "reason": "",
        "cap_reason": "",
        "score_cap": None,
        "calibrated_score_cap": None,
        "calibration_breakdown": {},
        "metrics": {},
        "match_type": "",
    }

    if not query_anchor_tokens or len(anchor_matches) != 1:
        return base_record

    match = anchor_matches[0]
    match_type = match.get("match_type")
    if match_type not in {"fuzzy", "phonetic"}:
        return base_record

    metrics = _fuzzy_anchor_metrics(
        match.get("query_word", ""),
        match.get("target_word", ""),
        use_translated_idf,
    )
    query_match_scores = {
        match.get("query_word"): _clamp_score(match.get("score", 0.0))
    }
    target_match_scores = {
        match.get("target_word"): _clamp_score(match.get("score", 0.0))
    }
    query_anchor_coverage = _weighted_match_coverage(
        query_anchor_tokens,
        query_match_scores,
        use_translated_idf,
    )
    target_anchor_coverage = _weighted_match_coverage(
        target_anchor_tokens,
        target_match_scores,
        use_translated_idf,
    )
    anchor_coverage = min(query_anchor_coverage, target_anchor_coverage)
    metrics.update(
        {
            "match_score": round(_clamp_score(match.get("score", 0.0)), 4),
            "query_anchor_coverage": round(query_anchor_coverage, 4),
            "target_anchor_coverage": round(target_anchor_coverage, 4),
        }
    )

    if metrics["strong_near_miss"]:
        record = dict(base_record)
        record.update(
            {
                "reason": "strong_near_miss",
                "metrics": metrics,
                "match_type": match_type,
            }
        )
        return record

    fragment_like = metrics["length_ratio"] < _FUZZY_ANCHOR_FRAGMENT_LENGTH_RATIO
    if match_type == "phonetic":
        floor = 0.42 if fragment_like else 0.54
        ceiling = (
            _WEAK_PHONETIC_ANCHOR_FRAGMENT_CAP
            if fragment_like
            else _WEAK_PHONETIC_ANCHOR_QUALITY_CAP
        )
        cap_reason = (
            "weak_phonetic_anchor_fragment_cap"
            if fragment_like
            else "weak_phonetic_anchor_quality_cap"
        )
    else:
        floor = 0.46 if fragment_like else 0.56
        ceiling = (
            _WEAK_FUZZY_ANCHOR_FRAGMENT_CAP
            if fragment_like
            else _WEAK_FUZZY_ANCHOR_QUALITY_CAP
        )
        cap_reason = (
            "weak_fuzzy_anchor_fragment_cap"
            if fragment_like
            else "weak_fuzzy_anchor_quality_cap"
        )
    raw_evidence = (
        (metrics["raw_fuzzy_ratio"] * 0.35)
        + (metrics["length_ratio"] * 0.25)
        + (metrics["edit_similarity"] * 0.25)
        + (anchor_coverage * 0.15)
    )
    evidence = _clamp_score(raw_evidence)
    calibrated_score = _bounded_score(floor, ceiling, evidence)

    return {
        "applies": True,
        "reason": (
            f"fragment_like_{match_type}_anchor"
            if fragment_like
            else f"weak_{match_type}_anchor_quality"
        ),
        "cap_reason": cap_reason,
        "score_cap": ceiling,
        "calibrated_score_cap": calibrated_score,
        "match_type": match_type,
        "calibration_breakdown": _calibration_record(
            floor=floor,
            ceiling=ceiling,
            evidence=evidence,
            calibrated_score=calibrated_score,
            factors={
                "raw_fuzzy_ratio": metrics["raw_fuzzy_ratio"],
                "length_ratio": metrics["length_ratio"],
                "edit_similarity": metrics["edit_similarity"],
                "query_anchor_coverage": round(query_anchor_coverage, 4),
                "target_anchor_coverage": round(target_anchor_coverage, 4),
                "fragment_like": fragment_like,
            },
        ),
        "metrics": metrics,
    }


def _weak_fuzzy_anchor_guard(
    *,
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    use_translated_idf: bool = False,
) -> Dict:
    """Compatibility wrapper for callers that still use the old guard name."""
    return _anchor_quality_guard(
        q_classes=q_classes,
        t_classes=t_classes,
        matches=matches,
        use_translated_idf=use_translated_idf,
    )


def _count_grouped_tokens(groups: Dict[str, List[str]], *keys: str) -> int:
    return sum(len(groups.get(key, [])) for key in keys)


def _analyze_added_matter_v2(
    query: str,
    target: str,
    q_tokens: Set[str],
    t_tokens: Set[str],
    q_classes: Dict[str, Set[str]],
    t_classes: Dict[str, Set[str]],
    matches: List[Dict],
    use_translated_idf: bool = False,
) -> Dict:
    meaningful_q_tokens = {
        token for token in q_tokens if not _is_ignored_added_matter_token(token)
    }
    meaningful_t_tokens = {
        token for token in t_tokens if not _is_ignored_added_matter_token(token)
    }
    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_tokens = set().union(*(t_classes[role] for role in _ANCHOR_TOKEN_ROLES))

    query_match_scores = {
        match["query_word"]: match["score"]
        for match in matches
        if match["score"] >= 0.70
    }
    target_match_scores = {
        match["target_word"]: match["score"]
        for match in matches
        if match["score"] >= 0.70
    }
    exact_query_tokens = {
        match["query_word"]
        for match in matches
        if match["match_type"] == "exact" and match["score"] >= 0.98
    }
    exact_target_tokens = {
        match["target_word"]
        for match in matches
        if match["match_type"] == "exact" and match["score"] >= 0.98
    }
    matched_query_anchor_tokens = query_anchor_tokens.intersection(query_match_scores)
    matched_target_anchor_tokens = target_anchor_tokens.intersection(target_match_scores)

    query_extra_tokens = meaningful_q_tokens - set(query_match_scores)
    target_extra_tokens = meaningful_t_tokens - set(target_match_scores)
    query_exact_extra_tokens = meaningful_q_tokens - exact_query_tokens
    target_exact_extra_tokens = meaningful_t_tokens - exact_target_tokens
    ignored_query_tokens = q_tokens - meaningful_q_tokens
    ignored_target_tokens = t_tokens - meaningful_t_tokens

    query_extra_roles = _group_added_matter_tokens(
        query_extra_tokens | ignored_query_tokens,
        use_translated_idf,
    )
    target_extra_roles = _group_added_matter_tokens(
        target_extra_tokens | ignored_target_tokens,
        use_translated_idf,
    )
    all_extra_roles = {
        role: query_extra_roles.get(role, []) + target_extra_roles.get(role, [])
        for role in query_extra_roles
    }

    query_anchor_coverage = _weighted_match_coverage(
        query_anchor_tokens,
        query_match_scores,
        use_translated_idf,
    )
    target_anchor_coverage = _weighted_match_coverage(
        target_anchor_tokens,
        target_match_scores,
        use_translated_idf,
    )
    full_query_token_coverage = _weighted_match_coverage(
        meaningful_q_tokens,
        query_match_scores,
        use_translated_idf,
    )
    full_target_token_coverage = _weighted_match_coverage(
        meaningful_t_tokens,
        target_match_scores,
        use_translated_idf,
    )

    penalized_extra_count = _count_grouped_tokens(
        all_extra_roles,
        "true_generic",
        "semi_generic",
        "common_anchor",
        "low_protectability_anchor",
        "distinctive",
    )
    query_strict_material_extra_count = _count_grouped_tokens(
        query_extra_roles,
        "semi_generic",
        "common_anchor",
        "low_protectability_anchor",
        "distinctive",
    )
    target_strict_material_extra_count = _count_grouped_tokens(
        target_extra_roles,
        "semi_generic",
        "common_anchor",
        "low_protectability_anchor",
        "distinctive",
    )
    query_legacy_override_material_terms = _legacy_override_material_terms(
        query_extra_roles,
        use_translated_idf,
    )
    target_legacy_override_material_terms = _legacy_override_material_terms(
        target_extra_roles,
        use_translated_idf,
    )
    query_material_extra_terms = _material_added_matter_terms(
        query_extra_roles,
        use_translated_idf,
    )
    target_material_extra_terms = _material_added_matter_terms(
        target_extra_roles,
        use_translated_idf,
    )
    brandlike_extra_count = _count_grouped_tokens(
        all_extra_roles,
        "common_anchor",
        "distinctive",
    ) + len(query_legacy_override_material_terms) + len(target_legacy_override_material_terms)
    low_protectability_extra_count = _count_grouped_tokens(
        all_extra_roles,
        "low_protectability_anchor",
    )
    query_material_extra_count = len(query_material_extra_terms)
    target_material_extra_count = len(target_material_extra_terms)
    semi_generic_extra_count = _count_grouped_tokens(all_extra_roles, "semi_generic")
    true_generic_extra_count = _count_grouped_tokens(all_extra_roles, "true_generic")

    ordered_target_tokens = _ordered_expanded_tokens(target, use_translated_idf)
    leading_target_material_extra_token = ""
    for token in ordered_target_tokens:
        if token not in meaningful_t_tokens:
            continue
        if token in target_match_scores:
            break
        if token in target_material_extra_terms:
            leading_target_material_extra_token = token
        break

    partial_multi_anchor_changed_matter = bool(
        len(matched_query_anchor_tokens) == 1
        and len(matched_target_anchor_tokens) == 1
        and (len(query_anchor_tokens) >= 2 or len(target_anchor_tokens) >= 2)
        and query_strict_material_extra_count > 0
        and target_strict_material_extra_count > 0
    )
    single_anchor_asymmetric_added_matter = bool(
        len(matched_query_anchor_tokens) == 1
        and len(matched_target_anchor_tokens) == 1
        and len(query_anchor_tokens) == 1
        and not partial_multi_anchor_changed_matter
        and _count_grouped_tokens(query_extra_roles, "true_generic") > 0
        and query_strict_material_extra_count == 0
        and target_material_extra_count > 0
        and (
            target_material_extra_count >= 2
            or bool(leading_target_material_extra_token)
            or len(target_extra_tokens) >= 2
        )
    )

    full_query_exact = bool(meaningful_q_tokens) and not query_exact_extra_tokens
    full_target_exact = bool(meaningful_t_tokens) and not target_exact_extra_tokens
    exact_query_anchor_tokens = query_anchor_tokens.intersection(exact_query_tokens)
    has_exact_query_anchor = bool(exact_query_anchor_tokens)
    single_exact_short_query_anchor = bool(
        full_query_exact
        and len(meaningful_q_tokens) == 1
        and len(exact_query_anchor_tokens) == 1
        and len(next(iter(exact_query_anchor_tokens))) <= (
            _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_MAX_LENGTH
        )
    )
    anchor_match_quality = _average_anchor_match_quality(
        matches,
        query_anchor_tokens,
        target_anchor_tokens,
    )
    copied_core_size = 0
    if full_query_exact:
        copied_core_size = len(meaningful_q_tokens)
    elif full_target_exact:
        copied_core_size = len(meaningful_t_tokens)

    dominant_core_score = 0.0
    score_cap = None
    cap_reason = ""
    reason = "no_shared_anchor"
    calibration_breakdown = {}

    if query_anchor_tokens and query_anchor_coverage > 0 and has_exact_query_anchor:
        if (full_query_exact or full_target_exact) and penalized_extra_count:
            if brandlike_extra_count:
                if copied_core_size <= 1:
                    floor = 0.80 if single_exact_short_query_anchor else 0.66
                    ceiling = (
                        _EXACT_SHORT_ANCHOR_ADDED_MATTER_CAP
                        if single_exact_short_query_anchor
                        else _ADDED_MATTER_SINGLE_ANCHOR_EXTRA_CAP
                    )
                    dominant_core_score, calibration_breakdown = (
                        _calibrate_added_matter_score(
                            floor=floor,
                            ceiling=ceiling,
                            query_anchor_coverage=query_anchor_coverage,
                            target_anchor_coverage=target_anchor_coverage,
                            full_query_token_coverage=full_query_token_coverage,
                            full_target_token_coverage=full_target_token_coverage,
                            match_quality=anchor_match_quality,
                            query_material_extra_count=query_material_extra_count,
                            target_material_extra_count=target_material_extra_count,
                            brandlike_extra_count=brandlike_extra_count,
                        )
                    )
                    score_cap = ceiling
                    cap_reason = (
                        "single_short_anchor_distinctive_extra"
                        if single_exact_short_query_anchor
                        else "single_anchor_distinctive_extra"
                    )
                    reason = "single copied anchor plus distinctive added matter"
                else:
                    dominant_core_score, calibration_breakdown = (
                        _calibrate_added_matter_score(
                            floor=0.80,
                            ceiling=_ADDED_MATTER_DISTINCTIVE_EXTRA_CAP,
                            query_anchor_coverage=query_anchor_coverage,
                            target_anchor_coverage=target_anchor_coverage,
                            full_query_token_coverage=full_query_token_coverage,
                            full_target_token_coverage=full_target_token_coverage,
                            match_quality=anchor_match_quality,
                            query_material_extra_count=query_material_extra_count,
                            target_material_extra_count=target_material_extra_count,
                            brandlike_extra_count=brandlike_extra_count,
                        )
                    )
                    score_cap = _ADDED_MATTER_DISTINCTIVE_EXTRA_CAP
                    cap_reason = "distinctive_extra"
                    reason = "copied core plus distinctive added matter"
            elif low_protectability_extra_count and copied_core_size <= 1:
                dominant_core_score, calibration_breakdown = (
                    _calibrate_added_matter_score(
                        floor=0.66,
                        ceiling=_ADDED_MATTER_SINGLE_ANCHOR_EXTRA_CAP,
                        query_anchor_coverage=query_anchor_coverage,
                        target_anchor_coverage=target_anchor_coverage,
                        full_query_token_coverage=full_query_token_coverage,
                        full_target_token_coverage=full_target_token_coverage,
                        match_quality=anchor_match_quality,
                        query_material_extra_count=query_material_extra_count,
                        target_material_extra_count=target_material_extra_count,
                        brandlike_extra_count=low_protectability_extra_count,
                    )
                )
                score_cap = _ADDED_MATTER_SINGLE_ANCHOR_EXTRA_CAP
                cap_reason = "single_anchor_low_protectability_extra"
                reason = "single copied anchor plus low-protectability added matter"
            elif semi_generic_extra_count:
                dominant_core_score = max(
                    0.80,
                    _ADDED_MATTER_SEMI_GENERIC_EXTRA_CAP
                    - min(0.06, semi_generic_extra_count * 0.03),
                )
                score_cap = _ADDED_MATTER_SEMI_GENERIC_EXTRA_CAP
                cap_reason = "semi_generic_extra"
                reason = "copied core plus semi-generic added matter"
            else:
                dominant_core_score = max(
                    0.88,
                    0.96 - min(0.08, true_generic_extra_count * 0.02),
                )
                reason = "copied core plus true-generic added matter"
        elif partial_multi_anchor_changed_matter:
            dominant_core_score, calibration_breakdown = (
                _calibrate_added_matter_score(
                    floor=0.44,
                    ceiling=_PARTIAL_MULTI_ANCHOR_CHANGED_MATTER_CAP,
                    query_anchor_coverage=query_anchor_coverage,
                    target_anchor_coverage=target_anchor_coverage,
                    full_query_token_coverage=full_query_token_coverage,
                    full_target_token_coverage=full_target_token_coverage,
                    match_quality=anchor_match_quality,
                    query_material_extra_count=query_material_extra_count,
                    target_material_extra_count=target_material_extra_count,
                    brandlike_extra_count=brandlike_extra_count,
                    require_balanced_coverage=True,
                )
            )
            score_cap = _PARTIAL_MULTI_ANCHOR_CHANGED_MATTER_CAP
            cap_reason = "partial_multi_anchor_changed_matter"
            reason = "single shared anchor with changed matter on both sides"
        elif single_anchor_asymmetric_added_matter:
            dominant_core_score, calibration_breakdown = (
                _calibrate_added_matter_score(
                    floor=0.58,
                    ceiling=_SINGLE_ANCHOR_ASYMMETRIC_ADDED_MATTER_CAP,
                    query_anchor_coverage=query_anchor_coverage,
                    target_anchor_coverage=target_anchor_coverage,
                    full_query_token_coverage=full_query_token_coverage,
                    full_target_token_coverage=full_target_token_coverage,
                    match_quality=anchor_match_quality,
                    query_material_extra_count=query_material_extra_count,
                    target_material_extra_count=target_material_extra_count,
                    brandlike_extra_count=brandlike_extra_count,
                    require_balanced_coverage=True,
                )
            )
            score_cap = _SINGLE_ANCHOR_ASYMMETRIC_ADDED_MATTER_CAP
            cap_reason = "single_anchor_asymmetric_added_matter"
            reason = "single shared anchor with asymmetric material added matter"
        elif penalized_extra_count and query_anchor_coverage >= 0.98:
            dominant_core_score, calibration_breakdown = (
                _calibrate_added_matter_score(
                    floor=0.65,
                    ceiling=_ADDED_MATTER_CHANGED_CORE_CAP,
                    query_anchor_coverage=query_anchor_coverage,
                    target_anchor_coverage=target_anchor_coverage,
                    full_query_token_coverage=full_query_token_coverage,
                    full_target_token_coverage=full_target_token_coverage,
                    match_quality=anchor_match_quality,
                    query_material_extra_count=query_material_extra_count,
                    target_material_extra_count=target_material_extra_count,
                    brandlike_extra_count=brandlike_extra_count,
                )
            )
            score_cap = _ADDED_MATTER_CHANGED_CORE_CAP
            cap_reason = "changed_remaining_matter"
            reason = "shared dominant anchor with changed remaining matter"
        elif penalized_extra_count:
            dominant_core_score, calibration_breakdown = (
                _calibrate_added_matter_score(
                    floor=0.50,
                    ceiling=_ADDED_MATTER_CHANGED_CORE_CAP,
                    query_anchor_coverage=query_anchor_coverage,
                    target_anchor_coverage=target_anchor_coverage,
                    full_query_token_coverage=full_query_token_coverage,
                    full_target_token_coverage=full_target_token_coverage,
                    match_quality=anchor_match_quality,
                    query_material_extra_count=query_material_extra_count,
                    target_material_extra_count=target_material_extra_count,
                    brandlike_extra_count=brandlike_extra_count,
                )
            )
            score_cap = _ADDED_MATTER_CHANGED_CORE_CAP
            cap_reason = "partial_core_changed_matter"
            reason = "partial dominant core with changed matter"

    return {
        "query_anchor_coverage": round(query_anchor_coverage, 4),
        "target_anchor_coverage": round(target_anchor_coverage, 4),
        "full_query_token_coverage": round(full_query_token_coverage, 4),
        "full_target_token_coverage": round(full_target_token_coverage, 4),
        "query_extra_tokens": sorted(query_extra_tokens),
        "target_extra_tokens": sorted(target_extra_tokens),
        "query_extra_roles": query_extra_roles,
        "target_extra_roles": target_extra_roles,
        "ignored_short_initials": sorted(ignored_query_tokens | ignored_target_tokens),
        "matched_query_anchor_tokens": sorted(matched_query_anchor_tokens),
        "matched_target_anchor_tokens": sorted(matched_target_anchor_tokens),
        "query_material_extra_terms": sorted(query_material_extra_terms),
        "target_material_extra_terms": sorted(target_material_extra_terms),
        "query_legacy_override_material_terms": sorted(query_legacy_override_material_terms),
        "target_legacy_override_material_terms": sorted(target_legacy_override_material_terms),
        "leading_target_material_extra_token": leading_target_material_extra_token,
        "query_strict_material_extra_count": query_strict_material_extra_count,
        "target_strict_material_extra_count": target_strict_material_extra_count,
        "query_material_extra_count": query_material_extra_count,
        "target_material_extra_count": target_material_extra_count,
        "low_protectability_extra_count": low_protectability_extra_count,
        "partial_multi_anchor_changed_matter": partial_multi_anchor_changed_matter,
        "single_anchor_asymmetric_added_matter": single_anchor_asymmetric_added_matter,
        "anchor_match_quality": round(anchor_match_quality, 4),
        "calibration_breakdown": calibration_breakdown,
        "dominant_core_score": round(_clamp_score(dominant_core_score), 4),
        "score_cap": round(score_cap, 4) if score_cap is not None else None,
        "calibrated_score_cap": (
            round(_clamp_score(dominant_core_score), 4)
            if score_cap is not None and calibration_breakdown
            else round(score_cap, 4) if score_cap is not None else None
        ),
        "cap_reason": cap_reason,
        "reason": reason,
    }


def _dominant_anchor_missing_guard(
    query: str,
    q_classes: Dict[str, Set[str]],
    matches: List[Dict],
    use_translated_idf: bool = False,
) -> Dict:
    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    ordered_query_anchors = [
        token
        for token in _ordered_expanded_tokens(query, use_translated_idf)
        if token in query_anchor_tokens
    ]
    matched_anchor_scores = {
        match["query_word"]: match["score"]
        for match in matches
        if match["token_role"] in _ANCHOR_TOKEN_ROLES and match["score"] >= 0.70
    }

    dominant_anchor = ordered_query_anchors[0] if ordered_query_anchors else ""
    dominant_matched = bool(
        dominant_anchor and matched_anchor_scores.get(dominant_anchor, 0.0) >= 0.70
    )
    applies = bool(
        dominant_anchor
        and len(ordered_query_anchors) >= 2
        and not dominant_matched
        and matched_anchor_scores
    )

    return {
        "dominant_anchor": dominant_anchor,
        "ordered_query_anchors": ordered_query_anchors,
        "matched_anchor_tokens": sorted(matched_anchor_scores),
        "dominant_anchor_matched": dominant_matched,
        "applies": applies,
        "cap": _MISSING_DOMINANT_ANCHOR_CAP if applies else None,
    }


def _score_textual_path_v2(
    query: str,
    target: str,
    text_sim: float = 0.0,
    semantic_sim: float = 0.0,
    phonetic_sim: float = 0.0,
    visual_sim: float = 0.0,
    use_translated_idf: bool = False,
) -> Tuple[float, Dict]:
    text_sim = _clamp_score(text_sim)
    # Trademark text-semantic embeddings were removed from the risk engine. Keep
    # the field/argument for API compatibility, but do not let it affect scores.
    semantic_sim = 0.0
    phonetic_sim = _clamp_score(phonetic_sim)
    visual_sim = _clamp_score(visual_sim)

    q_norm = normalize_turkish(query or "")
    t_norm = normalize_turkish(target or "")
    q_raw_tokens = tokenize(query or "")
    t_raw_tokens = tokenize(target or "")
    q_tokens, q_compound_expansions = _expand_compound_tokens(
        q_raw_tokens,
        use_translated_idf,
    )
    t_tokens, t_compound_expansions = _expand_compound_tokens(
        t_raw_tokens,
        use_translated_idf,
    )

    breakdown = _text_breakdown_base(
        query=query,
        target=target,
        text_sim=text_sim,
        semantic_sim=semantic_sim,
        phonetic_sim=phonetic_sim,
        visual_sim=visual_sim,
        use_translated_idf=use_translated_idf,
    )
    breakdown["compound_expansions"] = {
        "query": q_compound_expansions,
        "target": t_compound_expansions,
    }
    q_compact = _compact_text(query)
    t_compact = _compact_text(target)

    if (
        (not q_tokens or not t_tokens)
        and q_compact == t_compact
        and len(q_compact) >= 2
    ):
        breakdown["exact_match"] = True
        breakdown["containment"] = 1.0
        breakdown["weighted_overlap"] = 1.0
        breakdown["scoring_path"] = "TEXT_COMPACT_EXACT"
        breakdown["total"] = 0.96
        return 0.96, breakdown

    if not q_tokens or not t_tokens:
        score = min(max(text_sim, semantic_sim, phonetic_sim) * 0.50, 0.45)
        breakdown["scoring_path"] = "TEXT_EMPTY"
        breakdown["caps_applied"] = [
            "semantic_or_phonetic_without_lexical_anchor_cap:0.45"
        ]
        breakdown["anchor_quality_guard"] = {
            "applies": False,
            "reason": "no_lexical_anchor",
            "cap_reason": "",
            "score_cap": None,
            "calibrated_score_cap": None,
            "calibration_breakdown": {},
            "metrics": {},
            "match_type": "",
        }
        breakdown["fuzzy_anchor_guard"] = breakdown["anchor_quality_guard"]
        breakdown["total"] = round(score, 4)
        return breakdown["total"], breakdown

    q_classes = _class_token_sets(q_tokens, use_translated_idf)
    t_classes = _class_token_sets(t_tokens, use_translated_idf)
    breakdown["token_classes"] = {
        "query": {key: sorted(value) for key, value in q_classes.items() if value},
        "target": {key: sorted(value) for key, value in t_classes.items() if value},
    }
    descriptor_terms = {
        "query": sorted(
            token
            for token in q_tokens
            if _is_descriptor_like_term(token, use_translated_idf)
        ),
        "target": sorted(
            token
            for token in t_tokens
            if _is_descriptor_like_term(token, use_translated_idf)
        ),
    }
    breakdown["descriptor_terms"] = descriptor_terms
    breakdown["non_protectable_terms"] = descriptor_terms
    descriptor_stats = {
        token: _descriptor_stats_for_word(token, use_translated_idf)
        for token in sorted(set(descriptor_terms["query"] + descriptor_terms["target"]))
    }
    descriptor_stats = {token: stats for token, stats in descriptor_stats.items() if stats}
    if descriptor_stats:
        breakdown["descriptor_stats"] = descriptor_stats
    low_protectability_terms = {
        "query": sorted(
            token
            for token in q_tokens
            if _is_low_protectability_anchor(token, use_translated_idf)
        ),
        "target": sorted(
            token
            for token in t_tokens
            if _is_low_protectability_anchor(token, use_translated_idf)
        ),
    }
    breakdown["low_protectability_terms"] = low_protectability_terms
    low_protectability_stats = _low_protectability_stats_for_tokens(
        set(low_protectability_terms["query"] + low_protectability_terms["target"]),
        use_translated_idf,
    )
    if low_protectability_stats:
        breakdown["low_protectability_stats"] = low_protectability_stats

    exact_overlap = q_tokens.intersection(t_tokens)
    breakdown["token_overlap"] = round(len(exact_overlap) / len(q_tokens), 4)

    if q_norm == t_norm:
        breakdown["exact_match"] = True
        breakdown["containment"] = 1.0
        breakdown["weighted_overlap"] = 1.0
        breakdown["distinctive_match"] = 1.0 if q_classes["distinctive"] else 0.0
        breakdown["common_anchor_match"] = 1.0 if q_classes["common_anchor"] else 0.0
        breakdown["semi_generic_match"] = 1.0 if q_classes["semi_generic"] else 0.0
        breakdown["low_protectability_match"] = (
            1.0 if q_classes["low_protectability_anchor"] else 0.0
        )
        breakdown["generic_match"] = 1.0 if q_classes["generic"] else 0.0
        breakdown["matched_words"] = [
            _matched_word_record(
                query_word=word,
                target_word=word,
                match_type="exact",
                score=1.0,
                word_class=_word_class(word, use_translated_idf),
                token_role=_token_role(word, use_translated_idf),
                use_translated_idf=use_translated_idf,
            )
            for word in sorted(q_tokens)
        ]
        breakdown["scoring_path"] = "TEXT_EXACT"
        breakdown["total"] = 1.0
        return 1.0, breakdown

    if q_compact == t_compact and len(q_compact) >= 4:
        breakdown["exact_match"] = True
        breakdown["containment"] = 1.0
        breakdown["weighted_overlap"] = 1.0
        breakdown["scoring_path"] = "TEXT_COMPACT_EXACT"
        breakdown["total"] = 0.96
        return 0.96, breakdown

    alignment = _align_tokens_v2(q_tokens, t_tokens, use_translated_idf)
    matches = alignment["matches"]
    breakdown["short_anchor_guard"] = alignment.get("short_anchor_guard", [])
    breakdown["matched_words"] = []
    for match in matches:
        public_match = {
            "query_word": match["query_word"],
            "target_word": match["target_word"],
            "match_type": match["match_type"],
            "score": match["score"],
            "word_class": match["word_class"],
            "token_role": match["token_role"],
        }
        if "descriptor_stats" in match:
            public_match["descriptor_stats"] = match["descriptor_stats"]
        breakdown["matched_words"].append(public_match)
    breakdown["weighted_overlap"] = round(alignment["recall"], 4)

    matched_by_class = {
        "distinctive": {
            match["query_word"]
            for match in matches
            if match["token_role"] == "distinctive" and match["score"] >= 0.70
        },
        "common_anchor": {
            match["query_word"]
            for match in matches
            if match["token_role"] == "common_anchor" and match["score"] >= 0.70
        },
        "semi_generic": {
            match["query_word"]
            for match in matches
            if match["token_role"] == "semi_generic" and match["score"] >= 0.70
        },
        "low_protectability_anchor": {
            match["query_word"]
            for match in matches
            if (
                match["token_role"] == "low_protectability_anchor"
                and match["score"] >= 0.70
            )
        },
        "generic": {
            match["query_word"]
            for match in matches
            if match["token_role"] == "generic" and match["score"] >= 0.70
        },
    }

    if q_classes["distinctive"]:
        breakdown["distinctive_match"] = round(
            len(matched_by_class["distinctive"]) / len(q_classes["distinctive"]),
            4,
        )
    if q_classes["common_anchor"]:
        breakdown["common_anchor_match"] = round(
            len(matched_by_class["common_anchor"]) / len(q_classes["common_anchor"]),
            4,
        )
    if q_classes["semi_generic"]:
        breakdown["semi_generic_match"] = round(
            len(matched_by_class["semi_generic"]) / len(q_classes["semi_generic"]),
            4,
        )
    if q_classes["low_protectability_anchor"]:
        breakdown["low_protectability_match"] = round(
            len(matched_by_class["low_protectability_anchor"])
            / len(q_classes["low_protectability_anchor"]),
            4,
        )
    if q_classes["generic"]:
        breakdown["generic_match"] = round(
            len(matched_by_class["generic"]) / len(q_classes["generic"]),
            4,
        )

    token_score = alignment["f1"] * (0.80 + 0.20 * alignment["quality"])
    token_score = _clamp_score(token_score)

    containment_score, containment_flag = _phrase_containment_score(
        q_norm,
        t_norm,
        q_tokens,
        t_tokens,
    )
    exact_anchor_match = any(
        match["token_role"] in _ANCHOR_TOKEN_ROLES and match["match_type"] == "exact"
        for match in matches
    )
    compound_containment_score = 0.0
    q_compact_for_containment = q_norm.replace(" ", "")
    t_compact_for_containment = t_norm.replace(" ", "")
    if (
        q_compound_expansions
        and exact_anchor_match
        and len(q_compact_for_containment) >= 4
        and q_compact_for_containment in t_compact_for_containment
    ):
        coverage = len(q_compact_for_containment) / max(len(t_compact_for_containment), 1)
        compound_containment_score = _clamp_score(
            min(0.96, 0.88 + (0.08 * coverage))
        )
        containment_score = max(containment_score, compound_containment_score)
        containment_flag = 1.0

    query_anchor_tokens = set().union(*(q_classes[role] for role in _ANCHOR_TOKEN_ROLES))
    matched_anchor_tokens = set().union(*(matched_by_class[role] for role in _ANCHOR_TOKEN_ROLES))
    target_anchor_containment_tokens = {
        token
        for token in t_tokens
        if _token_role(token, use_translated_idf) in _ANCHOR_TOKEN_ROLES
    }
    containment_anchor = bool(
        containment_flag
        and any(
            anchor_token in target_anchor_containment_tokens
            or any(
                target_token.startswith(anchor_token)
                and len(target_token) - len(anchor_token) >= 3
                for target_token in target_anchor_containment_tokens
            )
            for anchor_token in query_anchor_tokens
        )
    )
    lexical_anchor = bool(
        matched_anchor_tokens
        or containment_anchor
    )
    common_anchor_containment = bool(
        containment_anchor
        and any(
            anchor_token in target_anchor_containment_tokens
            or any(
                target_token.startswith(anchor_token)
                and len(target_token) - len(anchor_token) >= 3
                for target_token in target_anchor_containment_tokens
            )
            for anchor_token in q_classes["common_anchor"]
        )
    )
    added_matter_breakdown = _analyze_added_matter_v2(
        query=query,
        target=target,
        q_tokens=q_tokens,
        t_tokens=t_tokens,
        q_classes=q_classes,
        t_classes=t_classes,
        matches=matches,
        use_translated_idf=use_translated_idf,
    )
    weak_shared_anchor_guard = _weak_shared_low_protectability_anchor_guard(
        q_classes=q_classes,
        t_classes=t_classes,
        matches=matches,
        added_matter_breakdown=added_matter_breakdown,
        use_translated_idf=use_translated_idf,
    )
    short_acronym_subset_guard = _short_acronym_subset_guard(
        q_tokens=q_tokens,
        t_tokens=t_tokens,
        q_classes=q_classes,
        t_classes=t_classes,
        matches=matches,
        added_matter_breakdown=added_matter_breakdown,
        use_translated_idf=use_translated_idf,
    )
    short_non_exact_anchor_added_matter_guard = (
        _short_non_exact_anchor_added_matter_guard(
            q_classes=q_classes,
            t_classes=t_classes,
            matches=matches,
            added_matter_breakdown=added_matter_breakdown,
        )
    )
    anchor_quality_guard = _anchor_quality_guard(
        q_classes=q_classes,
        t_classes=t_classes,
        matches=matches,
        use_translated_idf=use_translated_idf,
    )
    dominant_core_score = added_matter_breakdown["dominant_core_score"]
    dominant_anchor_guard = _dominant_anchor_missing_guard(
        query=query,
        q_classes=q_classes,
        matches=matches,
        use_translated_idf=use_translated_idf,
    )

    char_similarity = max(text_sim, calculate_multilevel_similarity(query or "", target or ""))
    if lexical_anchor:
        char_support_score = min(0.75, (char_similarity * 0.80) + (alignment["recall"] * 0.20))
    else:
        char_support_score = min(0.45, char_similarity * 0.55)

    semantic_support_score = 0.0

    name_phonetic = max(phonetic_sim, calculate_phonetic_similarity(q_norm, t_norm))
    phonetic_support_score = 0.0
    if name_phonetic >= 0.82:
        if lexical_anchor or char_similarity >= 0.55:
            phonetic_support_score = min(0.72, 0.42 + (name_phonetic * 0.30))
        else:
            phonetic_support_score = min(0.45, name_phonetic * 0.40)

    component_scores = {
        "TEXT_TOKEN_ALIGNMENT": token_score,
        "TEXT_CONTAINMENT": containment_score,
        "TEXT_CHAR_SUPPORT": char_support_score,
        "TEXT_SEMANTIC_SUPPORT": semantic_support_score,
        "TEXT_PHONETIC_SUPPORT": phonetic_support_score,
        "TEXT_DOMINANT_CORE": dominant_core_score,
    }
    if short_non_exact_anchor_added_matter_guard.get("applies"):
        component_scores = {
            path: min(score_value, _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP)
            for path, score_value in component_scores.items()
        }
    scoring_path, score = max(component_scores.items(), key=lambda item: item[1])

    if scoring_path == "TEXT_TOKEN_ALIGNMENT":
        if matches and all(match["match_type"] == "exact" for match in matches):
            scoring_path = "TEXT_TOKEN_EXACT"
        elif any(match["match_type"] == "fuzzy" for match in matches):
            scoring_path = "TEXT_FUZZY_ALIGNMENT"
        elif any(match["match_type"] == "phonetic" for match in matches):
            scoring_path = "TEXT_PHONETIC_ALIGNMENT"

    cap_notes = []
    if not query_anchor_tokens:
        score = min(score, _TRUE_GENERIC_ONLY_CAP)
        cap_notes.append(f"generic_only_cap:{_TRUE_GENERIC_ONLY_CAP:.2f}")
        if matches:
            scoring_path = "TEXT_GENERIC_ONLY"
    elif query_anchor_tokens and not matched_anchor_tokens and matched_by_class["generic"]:
        score = min(score, _MISSING_COMMON_ANCHOR_CAP)
        cap_notes.append(f"missing_anchor_generic_only_cap:{_MISSING_COMMON_ANCHOR_CAP:.2f}")
        scoring_path = "TEXT_MISSING_ANCHOR_GENERIC_ONLY"
    elif query_anchor_tokens and not matched_anchor_tokens and containment_flag and not containment_anchor:
        score = min(score, _MISSING_COMMON_ANCHOR_CAP)
        cap_notes.append(f"missing_anchor_containment_only_cap:{_MISSING_COMMON_ANCHOR_CAP:.2f}")
        scoring_path = "TEXT_MISSING_ANCHOR_CONTAINMENT_ONLY"
    elif not q_classes["distinctive"] and not q_classes["common_anchor"] and q_classes["semi_generic"]:
        score = min(score, 0.45)
        cap_notes.append("semi_generic_only_cap:0.45")
        scoring_path = "TEXT_SEMI_GENERIC_ONLY"
    elif q_classes["distinctive"] and not matched_by_class["distinctive"]:
        score = min(score, 0.55)
        cap_notes.append("missing_distinctive_anchor_cap:0.55")
    elif q_classes["distinctive"] and len(matched_by_class["distinctive"]) < len(q_classes["distinctive"]):
        score = min(score, 0.69)
        cap_notes.append("partial_distinctive_anchor_cap:0.69")
    elif q_classes["common_anchor"] and not matched_by_class["common_anchor"] and not common_anchor_containment:
        score = min(score, _MISSING_COMMON_ANCHOR_CAP)
        cap_notes.append(f"missing_common_anchor_cap:{_MISSING_COMMON_ANCHOR_CAP:.2f}")
        if matched_by_class["generic"] and not matched_by_class["semi_generic"]:
            scoring_path = "TEXT_MISSING_COMMON_ANCHOR"
    elif (
        q_classes["common_anchor"]
        and len(matched_by_class["common_anchor"]) < len(q_classes["common_anchor"])
        and not common_anchor_containment
    ):
        score = min(score, 0.69)
        cap_notes.append("partial_common_anchor_cap:0.69")

    added_matter_cap = added_matter_breakdown.get("score_cap")
    added_matter_effective_cap = added_matter_breakdown.get("calibrated_score_cap")
    added_matter_cap_reason = added_matter_breakdown.get("cap_reason")
    if (
        added_matter_cap is not None
        and added_matter_cap_reason
        and not weak_shared_anchor_guard.get("applies")
    ):
        score = min(score, added_matter_effective_cap or added_matter_cap)
        cap_notes.append(f"added_matter_{added_matter_cap_reason}_cap:{added_matter_cap:.2f}")

    weak_shared_anchor_cap = weak_shared_anchor_guard.get("score_cap")
    weak_shared_anchor_effective_cap = weak_shared_anchor_guard.get(
        "calibrated_score_cap"
    )
    weak_shared_anchor_cap_reason = weak_shared_anchor_guard.get("cap_reason")
    if weak_shared_anchor_guard.get("applies") and weak_shared_anchor_cap_reason:
        score = min(
            score,
            weak_shared_anchor_effective_cap or weak_shared_anchor_cap,
        )
        cap_notes.append(
            f"{weak_shared_anchor_cap_reason}:{weak_shared_anchor_cap:.2f}"
        )
        scoring_path = "TEXT_WEAK_SHARED_LOW_PROTECTABILITY_ANCHOR"

    short_acronym_subset_cap = short_acronym_subset_guard.get("score_cap")
    short_acronym_subset_effective_cap = short_acronym_subset_guard.get(
        "calibrated_score_cap"
    )
    short_acronym_subset_cap_reason = short_acronym_subset_guard.get("cap_reason")
    if short_acronym_subset_guard.get("applies") and short_acronym_subset_cap_reason:
        score = min(
            score,
            short_acronym_subset_effective_cap or short_acronym_subset_cap,
        )
        cap_notes.append(
            f"{short_acronym_subset_cap_reason}:{short_acronym_subset_cap:.2f}"
        )
        scoring_path = "TEXT_SHORT_ACRONYM_SUBSET"

    short_non_exact_anchor_added_matter_cap = (
        short_non_exact_anchor_added_matter_guard.get("score_cap")
    )
    short_non_exact_anchor_added_matter_cap_reason = (
        short_non_exact_anchor_added_matter_guard.get("cap_reason")
    )
    if (
        short_non_exact_anchor_added_matter_guard.get("applies")
        and short_non_exact_anchor_added_matter_cap_reason
    ):
        score = min(score, short_non_exact_anchor_added_matter_cap)
        cap_notes.append(
            f"{short_non_exact_anchor_added_matter_cap_reason}:"
            f"{short_non_exact_anchor_added_matter_cap:.2f}"
        )
        scoring_path = "TEXT_SHORT_NON_EXACT_ANCHOR_ADDED_MATTER"

    anchor_quality_cap = anchor_quality_guard.get("score_cap")
    anchor_quality_effective_cap = anchor_quality_guard.get("calibrated_score_cap")
    anchor_quality_cap_reason = anchor_quality_guard.get("cap_reason")
    if anchor_quality_guard.get("applies") and anchor_quality_cap_reason:
        score = min(score, anchor_quality_effective_cap or anchor_quality_cap)
        cap_notes.append(f"{anchor_quality_cap_reason}:{anchor_quality_cap:.2f}")
        scoring_path = "TEXT_WEAK_NON_EXACT_ANCHOR"

    if (
        breakdown["short_anchor_guard"]
        and query_anchor_tokens
        and not matched_anchor_tokens
    ):
        score = min(score, _SHORT_ANCHOR_NON_EXACT_CAP)
        cap_notes.append(
            f"short_anchor_non_exact_anchor_cap:{_SHORT_ANCHOR_NON_EXACT_CAP:.2f}"
        )

    if dominant_anchor_guard["applies"]:
        score = min(score, _MISSING_DOMINANT_ANCHOR_CAP)
        cap_notes.append(f"missing_dominant_anchor_cap:{_MISSING_DOMINANT_ANCHOR_CAP:.2f}")
        scoring_path = "TEXT_MISSING_DOMINANT_ANCHOR"

    if not lexical_anchor:
        score = min(score, 0.45)
        cap_notes.append("semantic_or_phonetic_without_lexical_anchor_cap:0.45")

    if score <= 0.0:
        scoring_path = "TEXT_LOW_EVIDENCE"

    score = round(_clamp_score(score), 4)
    breakdown["token_score"] = round(token_score, 4)
    breakdown["containment_score"] = round(containment_score, 4)
    breakdown["compound_containment_score"] = round(compound_containment_score, 4)
    breakdown["char_support_score"] = round(char_support_score, 4)
    breakdown["semantic_support_score"] = round(semantic_support_score, 4)
    breakdown["phonetic_support_score"] = round(phonetic_support_score, 4)
    breakdown["dominant_core_score"] = round(dominant_core_score, 4)
    breakdown["added_matter_breakdown"] = added_matter_breakdown
    breakdown["weak_shared_anchor_guard"] = weak_shared_anchor_guard
    breakdown["short_acronym_subset_guard"] = short_acronym_subset_guard
    breakdown["short_non_exact_anchor_added_matter_guard"] = (
        short_non_exact_anchor_added_matter_guard
    )
    breakdown["anchor_quality_guard"] = anchor_quality_guard
    breakdown["fuzzy_anchor_guard"] = anchor_quality_guard
    breakdown["calibration_breakdown"] = (
        anchor_quality_guard.get("calibration_breakdown")
        if anchor_quality_guard.get("applies")
        else weak_shared_anchor_guard.get("calibration_breakdown")
        if weak_shared_anchor_guard.get("applies")
        else short_acronym_subset_guard.get("calibration_breakdown")
        if short_acronym_subset_guard.get("applies")
        else added_matter_breakdown.get("calibration_breakdown", {})
    )
    breakdown["dominant_anchor_guard"] = dominant_anchor_guard
    breakdown["containment"] = containment_flag
    breakdown["caps_applied"] = cap_notes
    breakdown["scoring_path"] = scoring_path
    breakdown["total"] = score
    return score, breakdown


class HierarchicalTextScorer:
    """V2 deterministic trademark text scorer."""

    @staticmethod
    def score(
        query: str,
        target: str,
        text_sim: float = 0.0,
        semantic_sim: float = 0.0,
        phonetic_sim: float = 0.0,
        visual_sim: float = 0.0,
        use_translated_idf: bool = False,
    ) -> Tuple[float, Dict]:
        return _score_textual_path_v2(
            query=query,
            target=target,
            text_sim=text_sim,
            semantic_sim=semantic_sim,
            phonetic_sim=phonetic_sim,
            visual_sim=visual_sim,
            use_translated_idf=use_translated_idf,
        )


def compute_idf_weighted_score(
    query: str,
    target: str,
    text_sim: float = 0.0,
    semantic_sim: float = 0.0,
    phonetic_sim: float = 0.0,
    visual_sim: float = 0.0,
) -> Tuple[float, Dict]:
    """Run the hierarchical scorer against the original IDF corpus."""
    return HierarchicalTextScorer.score(
        query,
        target,
        text_sim,
        semantic_sim,
        phonetic_sim,
        visual_sim,
    )


def compute_idf_weighted_score_tr(
    query: str,
    target: str,
    text_sim: float = 0.0,
    semantic_sim: float = 0.0,
    phonetic_sim: float = 0.0,
    visual_sim: float = 0.0,
) -> Tuple[float, Dict]:
    """Run the hierarchical scorer against the translated-name IDF corpus."""
    return HierarchicalTextScorer.score(
        query,
        target,
        text_sim,
        semantic_sim,
        phonetic_sim,
        visual_sim,
        use_translated_idf=True,
    )


def _cap_collapsed_translation_path(
    candidate_name: str,
    candidate_name_tr: str,
    path_a_score: float,
    path_b_score: float,
    breakdown_b: Optional[Dict],
) -> Tuple[float, List[str], Optional[float]]:
    if not candidate_name_tr or not breakdown_b or path_b_score <= 0.0:
        return path_b_score, [], None

    original_compact = _compact_text(candidate_name)
    translated_compact = _compact_text(candidate_name_tr)
    if (
        not original_compact
        or not translated_compact
    ):
        return path_b_score, [], None

    if original_compact == translated_compact:
        capped_score = round(min(path_b_score, path_a_score), 4)
        cap_value = path_a_score if capped_score < path_b_score else None
        return capped_score, ["translation_duplicate_original"], cap_value

    path_b_short_subset = bool(
        (breakdown_b.get("short_acronym_subset_guard") or {}).get("applies")
    )
    path_b_exactish = (
        bool(breakdown_b.get("exact_match"))
        or path_b_score >= 0.90
        or path_b_short_subset
    )
    if not path_b_exactish:
        return path_b_score, [], None

    translated_tokens = re.findall(r"[a-z0-9]+", normalize_turkish(candidate_name_tr or ""))
    short_collapsed_translation = False
    if len(translated_tokens) == 1:
        translated_token = translated_tokens[0]
        exact_short_translation_match = any(
            match.get("match_type") == "exact"
            and match.get("score", 0.0) >= 0.98
            and match.get("target_word") == translated_token
            for match in breakdown_b.get("matched_words") or []
        )
        original_near_short_translation = (
            translated_compact in original_compact
            or (
                len(original_compact) <= 4
                and SequenceMatcher(None, original_compact, translated_compact).ratio()
                >= 0.72
            )
        )
        short_collapsed_translation = (
            0 < len(translated_token) <= 3
            and original_compact != translated_compact
            and len(original_compact) > len(translated_compact)
            and exact_short_translation_match
            and original_near_short_translation
        )

    if short_collapsed_translation:
        cap = round(max(path_a_score, 0.45), 4)
        capped_score = round(min(path_b_score, cap), 4)
        if capped_score < path_b_score:
            return (
                capped_score,
                [
                    "short_collapsed_candidate_translation",
                    "translation_short_anchor_subset_cap",
                ],
                cap,
            )

    original_contains_translation = (
        len(translated_compact) >= 4
        and translated_compact in original_compact
        and len(original_compact) - len(translated_compact) >= 3
    )
    near_latin_rewrite = (
        len(original_compact) >= 4
        and len(translated_compact) >= 4
        and SequenceMatcher(None, original_compact, translated_compact).ratio() >= 0.72
    )

    if not original_contains_translation and not near_latin_rewrite:
        return path_b_score, [], None

    cap = round(max(path_a_score, 0.70), 4)
    capped_score = round(min(path_b_score, cap), 4)
    if capped_score >= path_b_score:
        return path_b_score, [], None

    flags = ["collapsed_candidate_translation"]
    if near_latin_rewrite and not original_contains_translation:
        flags.append("near_original_rewrite")

    return capped_score, flags, cap


def score_candidates(
    query: str,
    candidates: List[Dict],
    text_sim_key: str = "text_similarity",
    semantic_sim_key: str = "semantic_similarity",
) -> List[Dict]:
    """Score a candidate list in place and sort it by descending IDF score."""
    scored = []
    for candidate in candidates:
        name = candidate.get("name", "")
        text_sim = candidate.get(text_sim_key, 0.0)
        semantic_sim = candidate.get(semantic_sim_key, 0.0)
        score, breakdown = compute_idf_weighted_score(
            query=query,
            target=name,
            text_sim=text_sim,
            semantic_sim=semantic_sim,
        )
        candidate["idf_score"] = score
        candidate["idf_breakdown"] = breakdown
        scored.append(candidate)

    scored.sort(key=lambda item: item["idf_score"], reverse=True)
    return scored


def calculate_adjusted_score(
    raw_similarity: float,
    query_text: str,
    candidate_text: str,
    include_details: bool = False,
) -> dict:
    """DEPRECATED. Wraps the canonical hierarchical scorer."""
    score, breakdown = compute_idf_weighted_score(
        query_text,
        candidate_text,
        raw_similarity,
    )
    result = {
        "raw_score": round(raw_similarity, 4),
        "adjusted_score": round(score, 4),
        "applied_weight": 1.0,
        "idf_weight": 1.0,
        "blended_weight": score,
        "blend_factor": 0.0,
        "query_weight": 1.0,
        "candidate_weight": 1.0,
    }
    if include_details:
        result["details"] = {
            "query_words": breakdown.get("matched_words", []),
            "candidate_words": breakdown.get("scoring_path", ""),
            "breakdown": breakdown,
        }
    return result


def calculate_text_similarity(query: str, target: str) -> float:
    """DEPRECATED. Wraps the canonical hierarchical scorer."""
    score, _ = compute_idf_weighted_score(query, target)
    return score


def calculate_risk_score(
    text_similarity: float,
    image_similarity: Optional[float],
    class_overlap_ratio: float,
    query_text: str,
    candidate_text: str,
) -> dict:
    """DEPRECATED. Combine text, image, and class overlap into one risk score."""
    idf_result = calculate_adjusted_score(
        text_similarity,
        query_text,
        candidate_text,
    )
    adjusted_text_sim = idf_result["adjusted_score"]

    text_weight = 0.5
    image_weight = 0.3
    class_weight = 0.2

    if image_similarity is not None and image_similarity > 0:
        text_component = adjusted_text_sim * text_weight
        image_component = image_similarity * image_weight
    else:
        text_component = adjusted_text_sim * (text_weight + image_weight)
        image_component = 0.0

    class_component = class_overlap_ratio * class_weight
    final_score = text_component + image_component + class_component

    return {
        "overall_score": round(final_score, 4),
        "risk_level": get_risk_level(final_score),
        "components": {
            "text": {
                "raw": round(text_similarity, 4),
                "adjusted": round(adjusted_text_sim, 4),
                "idf_weight": idf_result["applied_weight"],
                "contribution": round(text_component, 4),
            },
            "image": {
                "score": round(image_similarity, 4) if image_similarity else None,
                "contribution": round(image_component, 4),
            },
            "class_overlap": {
                "ratio": round(class_overlap_ratio, 4),
                "contribution": round(class_component, 4),
            },
        },
    }


def calculate_combined_score(
    text_similarity: float = None,
    image_similarity: float = None,
    search_type: str = "combined",
) -> dict:
    """DEPRECATED: Use risk_engine.score_pair() instead. Delegates to V2 max-plus combining."""
    text_sim = _clamp_score(text_similarity)
    image_sim = _clamp_score(image_similarity)

    if search_type == "image" or text_similarity is None or text_sim < 0.1:
        overall = image_sim
        return {
            "overall_score": round(overall, 3),
            "text_score": round(text_sim, 3),
            "image_score": round(image_sim, 3),
            "search_type": "image",
            "risk_level": get_risk_level(overall),
        }

    if search_type == "text" or image_similarity is None or image_sim < 0.1:
        overall = text_sim
        return {
            "overall_score": round(overall, 3),
            "text_score": round(text_sim, 3),
            "image_score": round(image_sim, 3),
            "search_type": "text",
            "risk_level": get_risk_level(overall),
        }

    combined = _combine_text_visual_v2(text_sim, image_sim)
    overall = combined["total"]

    return {
        "overall_score": round(overall, 3),
        "text_score": round(text_sim, 3),
        "image_score": round(image_sim, 3),
        "search_type": "combined",
        "risk_level": get_risk_level(overall),
        "dynamic_weights": combined["dynamic_weights"],
        "decision_reason": combined["decision_reason"],
    }


def calculate_comprehensive_score(
    query_text: str,
    result_text: str,
    raw_similarity: float = None,
    include_details: bool = False,
) -> Dict:
    """DEPRECATED. Wraps the canonical hierarchical scorer."""
    raw = raw_similarity if raw_similarity is not None else 0.0
    score, breakdown = compute_idf_weighted_score(query_text, result_text, raw)

    risk_level = "low"
    if score >= 0.70:
        risk_level = "critical"
    elif score >= 0.50:
        risk_level = "high"
    elif score >= 0.30:
        risk_level = "medium"

    result = {
        "raw_score": round(raw, 3),
        "final_score": round(score, 3),
        "factors": {
            "word_match": 0.0,
            "length_ratio": 0.0,
            "coverage": 0.0,
            "idf": 0.0,
        },
        "weighted_factor": round(score, 3),
        "risk_level": risk_level,
    }

    if include_details:
        result["details"] = breakdown
    return result


def calculate_alert_risk_score(
    query_text: str,
    result_text: str,
    raw_text_similarity: float,
    image_similarity: Optional[float],
    class_overlap_ratio: float,
    include_details: bool = False,
) -> Dict:
    """DEPRECATED. Calculate watchlist-style alert risk from shared scoring signals."""
    text_result = calculate_comprehensive_score(
        query_text=query_text,
        result_text=result_text,
        raw_similarity=raw_text_similarity,
        include_details=include_details,
    )

    adjusted_text_score = text_result["final_score"]
    text_weight = 0.50
    image_weight = 0.25
    class_weight = 0.25

    text_component = adjusted_text_score * text_weight
    if image_similarity is not None and image_similarity > 0:
        image_component = image_similarity * image_weight
    else:
        text_component = adjusted_text_score * (text_weight + image_weight)
        image_component = 0.0

    class_component = class_overlap_ratio * class_weight
    overall_score = text_component + image_component + class_component

    risk_level = "low"
    if overall_score >= 0.65:
        risk_level = "critical"
    elif overall_score >= 0.45:
        risk_level = "high"
    elif overall_score >= 0.30:
        risk_level = "medium"

    return {
        "overall_score": round(overall_score, 3),
        "risk_level": risk_level,
        "components": {
            "text": {
                "raw": round(raw_text_similarity, 3),
                "adjusted": round(adjusted_text_score, 3),
                "factors": text_result["factors"],
                "contribution": round(text_component, 3),
            },
            "image": {
                "score": round(image_similarity, 3) if image_similarity else None,
                "contribution": round(image_component, 3),
            },
            "class_overlap": {
                "ratio": round(class_overlap_ratio, 3),
                "contribution": round(class_component, 3),
            },
        },
        "text_details": text_result.get("details") if include_details else None,
    }


def adjust_image_similarity(raw_score: float) -> float:
    """DEPRECATED: Use calculate_visual_similarity() instead."""
    if raw_score >= 0.98:
        return raw_score
    if raw_score >= 0.95:
        return 0.90 + (raw_score - 0.95) * 2
    if raw_score >= 0.80:
        normalized = (raw_score - 0.80) / 0.15
        return 0.60 + (normalized * 0.30)
    if raw_score >= 0.60:
        normalized = (raw_score - 0.60) / 0.20
        return 0.35 + (normalized * 0.25)
    if raw_score >= 0.40:
        normalized = (raw_score - 0.40) / 0.20
        return 0.20 + (normalized * 0.15)
    return raw_score * 0.5


_ocr_reader = None
_ocr_available = False


def _load_ocr_reader():
    """Lazily load the shared EasyOCR reader."""
    global _ocr_reader, _ocr_available
    if _ocr_reader is not None:
        return _ocr_reader

    try:
        import easyocr
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _ocr_reader = easyocr.Reader(["en", "tr"], gpu=(device == "cuda"), verbose=False)
        _ocr_available = True
        logger.info("EasyOCR loaded on %s", device)
        return _ocr_reader
    except ImportError:
        logger.warning("EasyOCR not available - OCR features disabled")
        _ocr_available = False
        return None
    except Exception as exc:
        logger.error("Failed to load EasyOCR: %s", exc)
        _ocr_available = False
        return None


def extract_ocr_text(image_path: str) -> str:
    """Extract text from an image using the shared OCR reader."""
    reader = _load_ocr_reader()
    if reader is None:
        return ""

    try:
        results = reader.readtext(image_path, detail=0, paragraph=True)
        return turkish_lower(" ".join(results).strip())
    except Exception as exc:
        logger.warning("OCR extraction failed: %s", exc)
        return ""


def calculate_ocr_similarity(ocr_text: str, trademark_name: str) -> float:
    """DEPRECATED: Use calculate_visual_similarity() instead."""
    warnings.warn(
        "calculate_ocr_similarity is deprecated, use risk_engine.calculate_visual_similarity",
        DeprecationWarning,
        stacklevel=2,
    )
    if not ocr_text or not trademark_name:
        return 0.0
    return SequenceMatcher(
        None,
        turkish_lower(ocr_text.strip()),
        turkish_lower(trademark_name.strip()),
    ).ratio()


def combine_visual_scores(
    clip_sim: float = 0.0,
    dino_sim: float = 0.0,
    color_sim: float = 0.0,
    ocr_text_query: str = "",
    ocr_text_target: str = "",
) -> dict:
    """DEPRECATED: Use calculate_visual_similarity() instead."""
    warnings.warn(
        "combine_visual_scores is deprecated, use risk_engine.calculate_visual_similarity",
        DeprecationWarning,
        stacklevel=2,
    )
    score, breakdown = _calculate_visual_breakdown(
        clip_sim=clip_sim,
        dinov2_sim=dino_sim,
        color_sim=color_sim,
        ocr_text_a=ocr_text_query,
        ocr_text_b=ocr_text_target,
    )
    return {
        "combined_score": score,
        "clip_score": clip_sim,
        "dino_score": dino_sim,
        "color_score": 0.0,
        "ocr_score": breakdown["components"]["ocr"],
        "components_used": breakdown["active_components"],
        "visual_breakdown": breakdown,
    }


def calculate_image_score_with_ocr(
    raw_image_similarity: float,
    query_ocr_text: str,
    trademark_ocr_text: str = None,
) -> dict:
    """DEPRECATED: Use calculate_visual_similarity() instead."""
    warnings.warn(
        "calculate_image_score_with_ocr is deprecated, use risk_engine.calculate_visual_similarity",
        DeprecationWarning,
        stacklevel=2,
    )
    score, breakdown = _calculate_visual_breakdown(
        clip_sim=raw_image_similarity,
        ocr_text_a=query_ocr_text or "",
        ocr_text_b=trademark_ocr_text or "",
    )
    return {
        "final_score": score,
        "visual_score": raw_image_similarity,
        "ocr_boost": 0.0,
        "ocr_similarity": breakdown["components"]["ocr"],
        "ocr_query_text": query_ocr_text or "",
        "ocr_target_text": trademark_ocr_text or "",
        "risk_level": get_risk_level(score),
        "visual_breakdown": breakdown,
    }


def _dynamic_combine(
    text_idf_score: float,
    visual_sim: float,
) -> dict:
    """Compatibility wrapper for the V2 independent max-plus combiner."""
    return _combine_text_visual_v2(text_idf_score, visual_sim)


def _combine_text_visual_v2(
    text_score: float,
    visual_score: float,
    allow_agreement_boost: bool = True,
) -> dict:
    text_score = _clamp_score(text_score)
    visual_score = _clamp_score(visual_score)

    if text_score <= 0 and visual_score <= 0:
        return {
            "total": 0.0,
            "dynamic_weights": {"text": 0.0, "visual": 0.0},
            "decision_reason": "no active text or visual evidence",
        }

    if visual_score <= 0:
        return {
            "total": round(text_score, 4),
            "dynamic_weights": {"text": 1.0 if text_score > 0 else 0.0, "visual": 0.0},
            "decision_reason": "text-only score",
        }

    if text_score <= 0:
        return {
            "total": round(visual_score, 4),
            "dynamic_weights": {"text": 0.0, "visual": 1.0},
            "decision_reason": "visual-only score",
        }

    base = max(text_score, visual_score)
    support = min(text_score, visual_score)
    if not allow_agreement_boost:
        boost = 0.0
        reason = "guarded text visual max-only score"
    elif text_score >= 0.40 and visual_score >= 0.40:
        boost = min(0.08, support * 0.10)
        reason = "text and visual agreement boost"
    else:
        boost = min(0.03, support * 0.05)
        reason = "weak secondary signal boost"

    total = round(_clamp_score(base + boost), 4)
    active_total = text_score + visual_score
    return {
        "total": total,
        "dynamic_weights": {
            "text": round(text_score / active_total, 4),
            "visual": round(visual_score / active_total, 4),
        },
        "decision_reason": reason,
    }


def _has_weak_text_cap(breakdown: Optional[Dict]) -> bool:
    if not breakdown:
        return False
    for cap_note in breakdown.get("caps_applied") or []:
        if any(str(cap_note).startswith(marker) for marker in _WEAK_TEXT_CAP_MARKERS):
            return True
    return False


def _has_limited_text_cap(breakdown: Optional[Dict]) -> bool:
    if not breakdown:
        return False
    for cap_note in breakdown.get("caps_applied") or []:
        if any(str(cap_note).startswith(marker) for marker in _LIMITED_TEXT_CAP_MARKERS):
            return True
    return False


def _has_weak_fuzzy_anchor_cap(breakdown: Optional[Dict]) -> bool:
    if not breakdown:
        return False
    return any(
        str(cap_note).startswith("weak_fuzzy_anchor_")
        for cap_note in breakdown.get("caps_applied") or []
    )


def _has_weak_phonetic_anchor_cap(breakdown: Optional[Dict]) -> bool:
    if not breakdown:
        return False
    return any(
        str(cap_note).startswith("weak_phonetic_anchor_")
        for cap_note in breakdown.get("caps_applied") or []
    )


def _has_weak_anchor_quality_cap(breakdown: Optional[Dict]) -> bool:
    return _has_weak_fuzzy_anchor_cap(breakdown) or _has_weak_phonetic_anchor_cap(
        breakdown
    )


def _short_non_exact_anchor_visual_guard(
    breakdown: Optional[Dict],
    visual_score: float,
    visual_breakdown: Optional[Dict],
) -> Dict:
    del breakdown, visual_breakdown
    record = {
        "applies": False,
        "reason": "",
        "anchor_token": "",
        "match_type": "",
        "ocr_disagreement": False,
        "visual_score": round(_clamp_score(visual_score), 4),
    }
    # OCR disagreement is retained as diagnostic data only. It no longer
    # suppresses visual/text agreement because OCR is too noisy on screenshots,
    # figure labels, and non-word logos.
    return record


def _visual_component_value(visual_breakdown: Optional[Dict], component: str) -> float:
    if not visual_breakdown:
        return 0.0
    components = visual_breakdown.get("components") or {}
    return _clamp_score(components.get(component, 0.0))


def _visual_has_strong_ocr(visual_breakdown: Optional[Dict]) -> bool:
    if not visual_breakdown:
        return False
    if visual_breakdown.get("ocr_strong_match") is True:
        return True
    return _visual_component_value(visual_breakdown, "ocr") >= _OCR_STRONG_MATCH_MIN


def _visual_has_very_strong_clip_dino(visual_breakdown: Optional[Dict]) -> bool:
    if not visual_breakdown:
        return False
    if visual_breakdown.get("very_strong_visual_components") is True:
        return True
    return (
        _visual_component_value(visual_breakdown, "clip") >= _VERY_STRONG_VISUAL_COMPONENT_MIN
        and _visual_component_value(visual_breakdown, "dinov2") >= _VERY_STRONG_VISUAL_COMPONENT_MIN
    )


def _logo_profile_is_plain_text(profile: Optional[Dict]) -> bool:
    return bool(
        isinstance(profile, dict)
        and (
            profile.get("is_plain_text_wordmark") is True
            or profile.get("kind") == "plain_text_wordmark"
        )
    )


def _plain_text_wordmark_visual_guard(
    *,
    query_name: str,
    candidate_name: str,
    selected_text_score: float,
    visual_score: float,
    visual_breakdown: Optional[Dict],
) -> Tuple[float, Dict, Dict]:
    visual_score = _clamp_score(visual_score)
    visual_diag = dict(visual_breakdown or {})
    profiles = visual_diag.get("logo_profile") or {}
    query_profile = profiles.get("query") if isinstance(profiles, dict) else None
    candidate_profile = profiles.get("candidate") if isinstance(profiles, dict) else None
    query_plain = _logo_profile_is_plain_text(query_profile)
    candidate_plain = _logo_profile_is_plain_text(candidate_profile)

    ocr_texts = visual_diag.get("ocr_text_normalized") or {}
    ocr_query = ocr_texts.get("query", "") if isinstance(ocr_texts, dict) else ""
    ocr_candidate = ocr_texts.get("candidate", "") if isinstance(ocr_texts, dict) else ""
    ocr_agreement = _visual_component_value(visual_diag, "ocr")
    direct_name_agreement = calculate_name_similarity(query_name or "", candidate_name or "")
    query_ocr_name_agreement = (
        calculate_name_similarity(query_name or "", ocr_query) if query_name and ocr_query else 0.0
    )
    candidate_ocr_name_agreement = (
        calculate_name_similarity(candidate_name or "", ocr_candidate)
        if candidate_name and ocr_candidate
        else 0.0
    )
    text_agreement = max(
        _clamp_score(selected_text_score),
        _clamp_score(direct_name_agreement),
        _clamp_score(ocr_agreement),
    )

    record = {
        "applies": False,
        "reason": "",
        "query_plain_text_wordmark": query_plain,
        "candidate_plain_text_wordmark": candidate_plain,
        "visual_before_guard": round(visual_score, 4),
        "visual_after_guard": round(visual_score, 4),
        "ocr_text_agreement": round(ocr_agreement, 4),
        "direct_name_agreement": round(direct_name_agreement, 4),
        "text_agreement": round(_clamp_score(text_agreement), 4),
        "strong_text_agreement_min": _PLAIN_TEXT_WORDMARK_STRONG_TEXT_AGREEMENT,
    }

    if not (query_plain or candidate_plain) or visual_score <= 0.0:
        visual_diag["plain_text_wordmark_visual_guard"] = record
        return visual_score, visual_diag, record

    if text_agreement >= _PLAIN_TEXT_WORDMARK_STRONG_TEXT_AGREEMENT:
        record["reason"] = "text_or_ocr_agrees"
        visual_diag["plain_text_wordmark_visual_guard"] = record
        return visual_score, visual_diag, record

    both_plain = query_plain and candidate_plain
    floor = 0.35 if both_plain else 0.45
    ceiling = (
        _PLAIN_TEXT_WORDMARK_BOTH_CAP
        if both_plain
        else _PLAIN_TEXT_WORDMARK_ONE_SIDE_CAP
    )
    evidence = text_agreement / max(_PLAIN_TEXT_WORDMARK_STRONG_TEXT_AGREEMENT, 0.01)
    cap_limit = _bounded_score(floor, ceiling, evidence)
    calibration = _calibration_record(
        floor=floor,
        ceiling=ceiling,
        evidence=evidence,
        calibrated_score=cap_limit,
        factors={
            "both_plain_text_wordmarks": both_plain,
            "ocr_text_agreement": round(ocr_agreement, 4),
            "direct_name_agreement": round(direct_name_agreement, 4),
            "selected_text_score": round(_clamp_score(selected_text_score), 4),
            "query_ocr_name_agreement": round(query_ocr_name_agreement, 4),
            "candidate_ocr_name_agreement": round(candidate_ocr_name_agreement, 4),
        },
    )
    if visual_score > cap_limit:
        visual_score = round(cap_limit, 4)
        record.update(
            {
                "applies": True,
                "reason": (
                    "both_plain_text_wordmarks_text_disagrees"
                    if both_plain
                    else "plain_text_wordmark_text_disagrees"
                ),
                "cap": round(ceiling, 4),
                "calibrated_limit": round(cap_limit, 4),
                "visual_after_guard": visual_score,
                "calibration": calibration,
            }
        )
        visual_diag["total"] = visual_score
        visual_diag["input_visual_similarity"] = visual_score
        visual_diag["visual_before_plain_text_guard"] = record["visual_before_guard"]
        visual_diag["cap_applied"] = "plain_text_wordmark_visual_guard"
        visual_diag["cap_reason"] = record["reason"]
        caps = list(visual_diag.get("caps_applied") or [])
        caps.append(f"plain_text_wordmark_visual_guard:{ceiling:.2f}")
        visual_diag["caps_applied"] = caps
    else:
        record["reason"] = "visual_already_below_plain_text_limit"
        record["calibrated_limit"] = round(cap_limit, 4)
        record["calibration"] = calibration

    visual_diag["plain_text_wordmark_visual_guard"] = record
    return visual_score, visual_diag, record


def _apply_image_only_visual_quality_guard(
    visual_score: float,
    visual_breakdown: Optional[Dict],
) -> Tuple[float, Dict, Dict]:
    """Calibrate image-only visual matches that have no textual corroboration."""
    visual_score = _clamp_score(visual_score)
    visual_diag = dict(visual_breakdown or {})
    clip = _visual_component_value(visual_diag, "clip")
    dinov2 = _visual_component_value(visual_diag, "dinov2")
    ocr = _visual_component_value(visual_diag, "ocr")
    has_clip = clip > 0.0
    has_dinov2 = dinov2 > 0.0
    neural_values = [value for value in (clip, dinov2) if value > 0.0]
    neural_total = _clamp_score(
        visual_diag.get(
            "neural_total_without_ocr",
            sum(neural_values) / len(neural_values) if neural_values else 0.0,
        )
    )
    wordmark_driver = bool(
        (visual_diag.get("wordmark_ocr_visual_driver") or {}).get("applies")
    )
    profiles = visual_diag.get("logo_profile") or {}
    query_profile = profiles.get("query") if isinstance(profiles, dict) else None
    candidate_profile = profiles.get("candidate") if isinstance(profiles, dict) else None
    both_graphic_mixed = bool(
        isinstance(query_profile, dict)
        and isinstance(candidate_profile, dict)
        and query_profile.get("kind") == "graphic_or_mixed"
        and candidate_profile.get("kind") == "graphic_or_mixed"
        and not _logo_profile_is_plain_text(query_profile)
        and not _logo_profile_is_plain_text(candidate_profile)
    )
    near_duplicate = bool(
        has_clip
        and has_dinov2
        and clip >= _IMAGE_ONLY_NEAR_DUPLICATE_COMPONENT_MIN
        and dinov2 >= _IMAGE_ONLY_NEAR_DUPLICATE_COMPONENT_MIN
        and neural_total >= _IMAGE_ONLY_NEAR_DUPLICATE_TOTAL_MIN
    )
    strongest_neural = max(neural_values) if neural_values else 0.0
    weakest_neural = min(neural_values) if neural_values else 0.0
    neural_spread = strongest_neural - weakest_neural if neural_values else 0.0
    strict_layout_variant = bool(
        has_clip
        and has_dinov2
        and not near_duplicate
        and both_graphic_mixed
        and strongest_neural >= _IMAGE_ONLY_LAYOUT_VARIANT_STRONG_COMPONENT_MIN
        and weakest_neural >= _IMAGE_ONLY_LAYOUT_VARIANT_SECONDARY_COMPONENT_MIN
        and neural_total >= _IMAGE_ONLY_LAYOUT_VARIANT_NEURAL_MIN
    )
    balanced_layout_variant = bool(
        has_clip
        and has_dinov2
        and not near_duplicate
        and not strict_layout_variant
        and both_graphic_mixed
        and strongest_neural >= _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_STRONG_MIN
        and weakest_neural >= _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SECONDARY_MIN
        and neural_total >= _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_NEURAL_MIN
        and neural_spread <= _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SPREAD_MAX
    )
    layout_variant_same_logo = strict_layout_variant or balanced_layout_variant
    layout_variant_mode = (
        "strict"
        if strict_layout_variant
        else "balanced"
        if balanced_layout_variant
        else None
    )
    record = {
        "applies": False,
        "reason": "",
        "visual_before_guard": round(visual_score, 4),
        "visual_after_guard": round(visual_score, 4),
        "clip": round(clip, 4),
        "dinov2": round(dinov2, 4),
        "ocr": round(ocr, 4),
        "neural_total_without_ocr": round(neural_total, 4),
        "wordmark_ocr_visual_driver": wordmark_driver,
        "near_duplicate_neural_match": near_duplicate,
        "layout_variant_same_logo": layout_variant_same_logo,
        "layout_variant_mode": layout_variant_mode,
        "both_graphic_mixed": both_graphic_mixed,
        "neural_component_spread": round(neural_spread, 4),
        "near_duplicate_component_min": _IMAGE_ONLY_NEAR_DUPLICATE_COMPONENT_MIN,
        "near_duplicate_total_min": _IMAGE_ONLY_NEAR_DUPLICATE_TOTAL_MIN,
        "layout_variant_thresholds": {
            "strong_component_min": _IMAGE_ONLY_LAYOUT_VARIANT_STRONG_COMPONENT_MIN,
            "secondary_component_min": _IMAGE_ONLY_LAYOUT_VARIANT_SECONDARY_COMPONENT_MIN,
            "neural_total_min": _IMAGE_ONLY_LAYOUT_VARIANT_NEURAL_MIN,
            "cap": _IMAGE_ONLY_LAYOUT_VARIANT_CAP,
            "balanced_strong_component_min": _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_STRONG_MIN,
            "balanced_secondary_component_min": _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SECONDARY_MIN,
            "balanced_neural_total_min": _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_NEURAL_MIN,
            "balanced_component_spread_max": _IMAGE_ONLY_LAYOUT_VARIANT_BALANCED_SPREAD_MAX,
        },
    }

    if visual_score <= 0.0 or wordmark_driver or near_duplicate or not neural_values:
        record["reason"] = (
            "wordmark_ocr_driver"
            if wordmark_driver
            else "near_duplicate_visual"
            if near_duplicate
            else "not_applicable"
        )
        visual_diag["image_only_visual_quality_guard"] = record
        return visual_score, visual_diag, record

    if layout_variant_same_logo:
        record["reason"] = "layout_variant_visual_corroboration"
        capped_score = round(min(visual_score, _IMAGE_ONLY_LAYOUT_VARIANT_CAP), 4)
        if visual_score > capped_score:
            visual_score = capped_score
            record["applies"] = True
            record["visual_after_guard"] = visual_score
            record["cap"] = _IMAGE_ONLY_LAYOUT_VARIANT_CAP
            visual_diag["total"] = visual_score
            visual_diag["input_visual_similarity"] = visual_score
            visual_diag["visual_before_image_only_quality_guard"] = record[
                "visual_before_guard"
            ]
            visual_diag["cap_applied"] = "image_only_layout_variant_cap"
            visual_diag["cap_reason"] = record["reason"]
            caps = list(visual_diag.get("caps_applied") or [])
            caps.append(
                f"image_only_layout_variant_cap:{_IMAGE_ONLY_LAYOUT_VARIANT_CAP:.2f}"
            )
            visual_diag["caps_applied"] = caps
        visual_diag["image_only_visual_quality_guard"] = record
        return visual_score, visual_diag, record

    neural_evidence = min(neural_total / _IMAGE_ONLY_NEAR_DUPLICATE_TOTAL_MIN, 1.0)
    corroboration_evidence = min(
        weakest_neural / _IMAGE_ONLY_NEAR_DUPLICATE_COMPONENT_MIN,
        1.0,
    )
    # OCR is deliberately low weight because EasyOCR can be noisy on logo crops.
    ocr_evidence = min(ocr / max(_OCR_STRONG_MATCH_MIN, 0.01), 1.0)
    evidence = (
        0.50 * neural_evidence
        + 0.40 * corroboration_evidence
        + 0.10 * ocr_evidence
    )
    cap_limit = _bounded_score(
        _IMAGE_ONLY_VISUAL_GUARD_FLOOR,
        _IMAGE_ONLY_VISUAL_GUARD_CAP,
        evidence,
    )
    calibration = _calibration_record(
        floor=_IMAGE_ONLY_VISUAL_GUARD_FLOOR,
        ceiling=_IMAGE_ONLY_VISUAL_GUARD_CAP,
        evidence=evidence,
        calibrated_score=cap_limit,
        factors={
            "neural_evidence": round(neural_evidence, 4),
            "corroboration_evidence": round(corroboration_evidence, 4),
            "ocr_evidence": round(ocr_evidence, 4),
            "ocr_weight": 0.10,
            "clip": round(clip, 4),
            "dinov2": round(dinov2, 4),
            "ocr": round(ocr, 4),
        },
    )
    record.update(
        {
            "reason": "image_only_moderate_neural_weak_ocr",
            "cap": _IMAGE_ONLY_VISUAL_GUARD_CAP,
            "calibrated_limit": round(cap_limit, 4),
            "calibration": calibration,
        }
    )
    if visual_score > cap_limit:
        visual_score = round(cap_limit, 4)
        record["applies"] = True
        record["visual_after_guard"] = visual_score
        visual_diag["total"] = visual_score
        visual_diag["input_visual_similarity"] = visual_score
        visual_diag["visual_before_image_only_quality_guard"] = record[
            "visual_before_guard"
        ]
        visual_diag["cap_applied"] = "image_only_visual_quality_guard"
        visual_diag["cap_reason"] = record["reason"]
        caps = list(visual_diag.get("caps_applied") or [])
        caps.append(f"image_only_moderate_visual_cap:{_IMAGE_ONLY_VISUAL_GUARD_CAP:.2f}")
        visual_diag["caps_applied"] = caps

    visual_diag["image_only_visual_quality_guard"] = record
    return visual_score, visual_diag, record


def _apply_weak_text_visual_cap(
    total: float,
    visual_score: float,
    has_weak_text_cap: bool,
    visual_breakdown: Optional[Dict] = None,
) -> Tuple[float, Optional[float], Optional[str], Optional[Dict]]:
    visual_score = _clamp_score(visual_score)
    if not has_weak_text_cap or visual_score <= 0.0:
        return total, None, None, None

    strong_ocr = _visual_has_strong_ocr(visual_breakdown)
    very_strong_clip_dino = _visual_has_very_strong_clip_dino(visual_breakdown)

    if visual_score < _WEAK_TEXT_VISUAL_LOW_MAX:
        evidence = visual_score / _WEAK_TEXT_VISUAL_LOW_MAX
        calibrated_limit = _bounded_score(0.20, _WEAK_TEXT_VISUAL_LOW_CAP, evidence)
        calibration = _calibration_record(
            floor=0.20,
            ceiling=_WEAK_TEXT_VISUAL_LOW_CAP,
            evidence=evidence,
            calibrated_score=calibrated_limit,
            factors={"visual_score": round(visual_score, 4)},
        )
        if total > calibrated_limit:
            return (
                round(calibrated_limit, 4),
                _WEAK_TEXT_VISUAL_LOW_CAP,
                "weak_text_visual_low_cap",
                calibration,
            )
        return total, None, None, calibration

    if (
        visual_score < _WEAK_TEXT_VISUAL_MID_MAX
        and not strong_ocr
    ):
        evidence = (
            (visual_score - _WEAK_TEXT_VISUAL_LOW_MAX)
            / max(_WEAK_TEXT_VISUAL_MID_MAX - _WEAK_TEXT_VISUAL_LOW_MAX, 0.01)
        )
        calibrated_limit = _bounded_score(0.55, _WEAK_TEXT_VISUAL_MID_CAP, evidence)
        calibration = _calibration_record(
            floor=0.55,
            ceiling=_WEAK_TEXT_VISUAL_MID_CAP,
            evidence=evidence,
            calibrated_score=calibrated_limit,
            factors={
                "visual_score": round(visual_score, 4),
                "strong_ocr": strong_ocr,
            },
        )
        if total > calibrated_limit:
            return (
                round(calibrated_limit, 4),
                _WEAK_TEXT_VISUAL_MID_CAP,
                "weak_text_visual_mid_cap",
                calibration,
            )
        return total, None, None, calibration

    if (
        visual_score >= _WEAK_TEXT_VISUAL_MID_MAX
        and not (strong_ocr or very_strong_clip_dino)
    ):
        evidence = (visual_score - _WEAK_TEXT_VISUAL_MID_MAX) / 0.10
        calibrated_limit = _bounded_score(0.66, _WEAK_TEXT_VISUAL_MID_CAP, evidence)
        calibration = _calibration_record(
            floor=0.66,
            ceiling=_WEAK_TEXT_VISUAL_MID_CAP,
            evidence=evidence,
            calibrated_score=calibrated_limit,
            factors={
                "visual_score": round(visual_score, 4),
                "strong_ocr": strong_ocr,
                "very_strong_clip_dino": very_strong_clip_dino,
            },
        )
        if total > calibrated_limit:
            return (
                round(calibrated_limit, 4),
                _WEAK_TEXT_VISUAL_MID_CAP,
                "weak_text_visual_uncorroborated_high_cap",
                calibration,
            )
        return total, None, None, calibration

    return total, None, None, None


def _limited_text_visual_cap_limit(visual_score: float) -> Tuple[float, Dict]:
    visual_score = _clamp_score(visual_score)
    if visual_score < _STRONG_VISUAL_INDEPENDENCE_MIN:
        evidence = visual_score / max(_STRONG_VISUAL_INDEPENDENCE_MIN, 0.01)
        calibrated_limit = _bounded_score(0.58, 0.66, evidence)
        return calibrated_limit, _calibration_record(
            floor=0.58,
            ceiling=0.66,
            evidence=evidence,
            calibrated_score=calibrated_limit,
            factors={"visual_score": round(visual_score, 4)},
        )

    evidence = (
        (visual_score - _STRONG_VISUAL_INDEPENDENCE_MIN)
        / max(1.0 - _STRONG_VISUAL_INDEPENDENCE_MIN, 0.01)
    )
    calibrated_limit = _bounded_score(0.62, _WEAK_TEXT_VISUAL_MID_CAP, evidence)
    return calibrated_limit, _calibration_record(
        floor=0.62,
        ceiling=_WEAK_TEXT_VISUAL_MID_CAP,
        evidence=evidence,
        calibrated_score=calibrated_limit,
        factors={"visual_score": round(visual_score, 4)},
    )


def _limited_text_visual_guard_active(
    has_limited_text_cap: bool,
    visual_score: float,
    visual_breakdown: Optional[Dict] = None,
) -> bool:
    visual_score = _clamp_score(visual_score)
    if not has_limited_text_cap or visual_score <= 0.0:
        return False
    if visual_score < _STRONG_VISUAL_INDEPENDENCE_MIN:
        return True
    return not (
        _visual_has_strong_ocr(visual_breakdown)
        or _visual_has_very_strong_clip_dino(visual_breakdown)
    )


def _apply_limited_text_visual_cap(
    total: float,
    visual_score: float,
    has_limited_text_cap: bool,
    visual_breakdown: Optional[Dict] = None,
) -> Tuple[float, Optional[float], Optional[str], Optional[Dict]]:
    if not _limited_text_visual_guard_active(
        has_limited_text_cap,
        visual_score,
        visual_breakdown,
    ):
        return total, None, None, None

    calibrated_limit, calibration = _limited_text_visual_cap_limit(visual_score)
    if total > calibrated_limit:
        return (
            round(calibrated_limit, 4),
            _WEAK_TEXT_VISUAL_MID_CAP,
            "limited_text_visual_moderate_cap",
            calibration,
        )

    return total, None, None, calibration


def _score_pair_visual_breakdown(
    visual_score: float,
    visual_breakdown: Optional[Dict],
) -> Dict:
    if visual_breakdown:
        breakdown = dict(visual_breakdown)
        breakdown.setdefault("source", "component_visual_breakdown")
        breakdown["total"] = round(_clamp_score(breakdown.get("total", visual_score)), 4)
        breakdown["input_visual_similarity"] = round(_clamp_score(visual_score), 4)
        breakdown.setdefault("active_components", [])
        return breakdown

    return {
        "total": round(visual_score, 4),
        "input_visual_similarity": round(visual_score, 4),
        "source": "precomputed_score_pair_input",
        "active_components": ["precomputed"] if visual_score > 0 else [],
    }


def score_pair(
    query_name,
    candidate_name,
    text_sim=0.0,
    semantic_sim=0.0,
    visual_sim=0.0,
    phonetic_sim=0.0,
    candidate_translations=None,
    visual_breakdown=None,
):
    """Score a query name against a candidate name using V2 text/visual logic."""
    visual_sim = _clamp_score(visual_sim)
    if visual_breakdown and isinstance(visual_breakdown, dict):
        visual_sim = _clamp_score(visual_breakdown.get("total", visual_sim))
    visual_diag = _score_pair_visual_breakdown(visual_sim, visual_breakdown)

    if not query_name or not query_name.strip():
        (
            visual_sim,
            visual_diag,
            plain_text_wordmark_visual_guard,
        ) = _plain_text_wordmark_visual_guard(
            query_name=query_name or "",
            candidate_name=candidate_name or "",
            selected_text_score=0.0,
            visual_score=visual_sim,
            visual_breakdown=visual_diag,
        )
        (
            visual_sim,
            visual_diag,
            image_only_visual_quality_guard,
        ) = _apply_image_only_visual_quality_guard(
            visual_sim,
            visual_diag,
        )
        combined = _combine_text_visual_v2(0.0, visual_sim)
        if plain_text_wordmark_visual_guard.get("applies"):
            combined["decision_reason"] += "; plain text wordmark visual cap applied"
        if image_only_visual_quality_guard.get("applies"):
            combined["decision_reason"] += "; image-only visual quality cap applied"
        return {
            "score_version": SCORE_VERSION,
            "exact_match": False,
            "containment": 0.0,
            "token_overlap": 0.0,
            "weighted_overlap": 0.0,
            "distinctive_match": 0.0,
            "common_anchor_match": 0.0,
            "semi_generic_match": 0.0,
            "generic_match": 0.0,
            "text_similarity": 0.0,
            "semantic_similarity": 0.0,
            "phonetic_similarity": 0.0,
            "visual_similarity": combined["total"],
            "translation_similarity": 0.0,
            "matched_words": [],
            "scoring_path": "IMAGE_ONLY",
            "scoring_path_source": "ORIGINAL",
            "text_idf_score": 0.0,
            "total": combined["total"],
            "path_a_score": 0.0,
            "path_b_score": 0.0,
            "dynamic_weights": combined["dynamic_weights"],
            "textual_breakdown": {
                "selected_path": "ORIGINAL",
                "path_a_score": 0.0,
                "path_b_score": 0.0,
            },
            "visual_breakdown": visual_diag,
            "text_visual_guard": {
                "plain_text_wordmark_visual_guard": plain_text_wordmark_visual_guard,
                "plain_text_wordmark_visual_guard_active": bool(
                    plain_text_wordmark_visual_guard.get("applies")
                ),
                "image_only_visual_quality_guard": image_only_visual_quality_guard,
                "image_only_visual_quality_guard_active": bool(
                    image_only_visual_quality_guard.get("applies")
                ),
            },
            "decision_reason": combined["decision_reason"],
        }

    lex_turkish = calculate_name_similarity(query_name, candidate_name)
    text_sim_a = max(text_sim, lex_turkish)

    idf_total_a, breakdown_a = compute_idf_weighted_score(
        query=query_name,
        target=candidate_name,
        text_sim=text_sim_a,
        semantic_sim=semantic_sim,
        phonetic_sim=phonetic_sim,
        visual_sim=visual_sim,
    )

    path_a_score = round(idf_total_a, 4)
    candidate_name_tr = ((candidate_translations or {}).get("name_tr") or "").strip()
    path_b_score = 0.0
    idf_total_b = 0.0
    breakdown_b = None
    translation_quality_flags = []
    translation_path_b_cap = None

    if candidate_name_tr:
        text_sim_b = calculate_name_similarity(query_name, candidate_name_tr)
        phonetic_sim_b = calculate_phonetic_similarity(query_name, candidate_name_tr)

        idf_total_b, breakdown_b = compute_idf_weighted_score_tr(
            query=query_name,
            target=candidate_name_tr,
            text_sim=text_sim_b,
            semantic_sim=semantic_sim,
            phonetic_sim=phonetic_sim_b,
            visual_sim=visual_sim,
        )

        path_b_score = round(idf_total_b, 4)
        raw_path_b_score = path_b_score
        path_b_score, translation_quality_flags, translation_path_b_cap = _cap_collapsed_translation_path(
            candidate_name=candidate_name,
            candidate_name_tr=candidate_name_tr,
            path_a_score=path_a_score,
            path_b_score=path_b_score,
            breakdown_b=breakdown_b,
        )
        translation_weak_fuzzy_anchor = _has_weak_fuzzy_anchor_cap(breakdown_b)
        translation_weak_phonetic_anchor = _has_weak_phonetic_anchor_cap(breakdown_b)
        translation_weak_anchor_quality = _has_weak_anchor_quality_cap(breakdown_b)
        if translation_weak_anchor_quality and path_b_score > path_a_score:
            translation_quality_flags = list(translation_quality_flags)
            if "translation_weak_fuzzy_anchor" not in translation_quality_flags:
                translation_quality_flags.append("translation_weak_fuzzy_anchor")
            if (
                translation_weak_phonetic_anchor
                and "translation_weak_phonetic_anchor" not in translation_quality_flags
            ):
                translation_quality_flags.append("translation_weak_phonetic_anchor")
            if "translation_weak_non_exact_anchor" not in translation_quality_flags:
                translation_quality_flags.append("translation_weak_non_exact_anchor")
        breakdown_b["translation_weak_fuzzy_anchor"] = translation_weak_fuzzy_anchor
        breakdown_b["translation_weak_phonetic_anchor"] = translation_weak_phonetic_anchor
        breakdown_b["translation_weak_non_exact_anchor"] = translation_weak_anchor_quality
        if translation_quality_flags:
            if translation_path_b_cap is not None:
                breakdown_b["raw_total"] = raw_path_b_score
            breakdown_b["total"] = path_b_score
            breakdown_b["translation_quality_flags"] = translation_quality_flags
            breakdown_b["translation_path_b_cap"] = translation_path_b_cap
            breakdown_b["translation_duplicate_original"] = (
                "translation_duplicate_original" in translation_quality_flags
            )
            breakdown_b["short_collapsed_candidate_translation"] = (
                "short_collapsed_candidate_translation" in translation_quality_flags
            )
            if translation_path_b_cap is not None:
                if breakdown_b["translation_duplicate_original"]:
                    translation_cap_note = "translation_duplicate_original_cap"
                elif breakdown_b["short_collapsed_candidate_translation"]:
                    translation_cap_note = "translation_short_anchor_subset_cap"
                else:
                    translation_cap_note = "collapsed_translation_cap"
                breakdown_b.setdefault("caps_applied", []).append(
                    f"{translation_cap_note}:{translation_path_b_cap:.2f}"
                )

    path_a_diag = dict(breakdown_a)
    path_b_diag = dict(breakdown_b) if breakdown_b is not None else None

    if path_b_score > path_a_score and breakdown_b is not None:
        selected_source = "TRANSLATED"
        selected_text_score = path_b_score
        selected_breakdown = breakdown_b
    else:
        selected_source = "ORIGINAL"
        selected_text_score = path_a_score
        selected_breakdown = breakdown_a

    direct_text_similarity = round(
        _clamp_score(breakdown_a.get("text_similarity", text_sim_a)),
        4,
    )
    direct_semantic_similarity = round(
        _clamp_score(breakdown_a.get("semantic_similarity", semantic_sim)),
        4,
    )
    direct_phonetic_similarity = round(
        _clamp_score(breakdown_a.get("phonetic_similarity", phonetic_sim)),
        4,
    )
    selected_path_text_similarity = round(
        _clamp_score(selected_breakdown.get("text_similarity", selected_text_score)),
        4,
    )
    selected_path_semantic_similarity = round(
        _clamp_score(selected_breakdown.get("semantic_similarity", semantic_sim)),
        4,
    )
    selected_path_phonetic_similarity = round(
        _clamp_score(selected_breakdown.get("phonetic_similarity", phonetic_sim)),
        4,
    )

    (
        visual_sim,
        visual_diag,
        plain_text_wordmark_visual_guard,
    ) = _plain_text_wordmark_visual_guard(
        query_name=query_name,
        candidate_name=candidate_name,
        selected_text_score=selected_text_score,
        visual_score=visual_sim,
        visual_breakdown=visual_diag,
    )

    weak_text_cap_active = _has_weak_text_cap(selected_breakdown)
    limited_text_cap_active = _has_limited_text_cap(selected_breakdown)
    limited_text_visual_guard_active = _limited_text_visual_guard_active(
        limited_text_cap_active,
        visual_sim,
        visual_diag,
    )
    short_non_exact_anchor_added_matter_guard = (
        selected_breakdown.get("short_non_exact_anchor_added_matter_guard") or {}
    )
    short_non_exact_anchor_added_matter_guard_active = bool(
        short_non_exact_anchor_added_matter_guard.get("applies")
    )
    independent_strong_visual = bool(
        _visual_has_strong_ocr(visual_diag)
        or _visual_has_very_strong_clip_dino(visual_diag)
    )
    short_non_exact_anchor_added_matter_visual_guard_active = bool(
        short_non_exact_anchor_added_matter_guard_active
        and visual_sim > 0.0
        and not independent_strong_visual
    )
    short_non_exact_anchor_visual_guard = _short_non_exact_anchor_visual_guard(
        selected_breakdown,
        visual_sim,
        visual_diag,
    )
    short_non_exact_anchor_visual_guard_active = bool(
        short_non_exact_anchor_visual_guard.get("applies")
    )
    plain_text_wordmark_visual_guard_active = bool(
        plain_text_wordmark_visual_guard.get("applies")
    )
    agreement_boost_suppressed = (
        limited_text_visual_guard_active
        or short_non_exact_anchor_added_matter_visual_guard_active
        or short_non_exact_anchor_visual_guard_active
        or plain_text_wordmark_visual_guard_active
    )
    combiner_text_score = (
        0.0 if weak_text_cap_active and visual_sim > 0 else selected_text_score
    )
    combined = _combine_text_visual_v2(
        combiner_text_score,
        visual_sim,
        allow_agreement_boost=not agreement_boost_suppressed,
    )
    (
        final_total,
        weak_text_visual_cap,
        weak_text_visual_cap_reason,
        weak_text_visual_calibration,
    ) = _apply_weak_text_visual_cap(
        combined["total"],
        visual_sim,
        weak_text_cap_active,
        visual_diag,
    )
    (
        final_total,
        limited_text_visual_cap,
        limited_text_visual_cap_reason,
        limited_text_visual_calibration,
    ) = (
        _apply_limited_text_visual_cap(
            final_total,
            visual_sim,
            limited_text_cap_active,
            visual_diag,
        )
    )
    short_non_exact_anchor_added_matter_visual_cap = None
    short_non_exact_anchor_added_matter_visual_cap_reason = None
    if (
        short_non_exact_anchor_added_matter_visual_guard_active
        and final_total > _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP
    ):
        final_total = _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP
        short_non_exact_anchor_added_matter_visual_cap = (
            _SHORT_NON_EXACT_ANCHOR_ADDED_MATTER_CAP
        )
        short_non_exact_anchor_added_matter_visual_cap_reason = (
            "short_non_exact_anchor_added_matter_visual_cap"
        )
    breakdown = dict(selected_breakdown)
    caps_applied = list(breakdown.get("caps_applied") or [])
    if weak_text_visual_cap is not None:
        caps_applied.append(f"{weak_text_visual_cap_reason}:{weak_text_visual_cap:.2f}")
    if limited_text_visual_cap is not None:
        caps_applied.append(
            f"{limited_text_visual_cap_reason}:{limited_text_visual_cap:.2f}"
        )
    if short_non_exact_anchor_added_matter_visual_cap is not None:
        caps_applied.append(
            f"{short_non_exact_anchor_added_matter_visual_cap_reason}:"
            f"{short_non_exact_anchor_added_matter_visual_cap:.2f}"
        )
    breakdown["caps_applied"] = caps_applied
    breakdown["score_version"] = SCORE_VERSION
    breakdown["total"] = round(final_total, 4)
    breakdown["text_idf_score"] = selected_text_score
    breakdown["text_similarity"] = direct_text_similarity
    breakdown["semantic_similarity"] = direct_semantic_similarity
    breakdown["phonetic_similarity"] = direct_phonetic_similarity
    breakdown["direct_text_similarity"] = direct_text_similarity
    breakdown["direct_semantic_similarity"] = direct_semantic_similarity
    breakdown["direct_phonetic_similarity"] = direct_phonetic_similarity
    breakdown["selected_path_text_similarity"] = selected_path_text_similarity
    breakdown["selected_path_semantic_similarity"] = selected_path_semantic_similarity
    breakdown["selected_path_phonetic_similarity"] = selected_path_phonetic_similarity
    breakdown["selected_text_similarity"] = selected_path_text_similarity
    breakdown["selected_semantic_similarity"] = selected_path_semantic_similarity
    breakdown["selected_phonetic_similarity"] = selected_path_phonetic_similarity
    breakdown["dynamic_weights"] = combined["dynamic_weights"]
    breakdown["scoring_path_source"] = selected_source
    breakdown["visual_similarity"] = round(visual_sim, 4)
    breakdown["path_a_score"] = path_a_score
    breakdown["path_b_score"] = path_b_score
    meaningful_translation_similarity = (
        0.0
        if "translation_duplicate_original" in translation_quality_flags
        else path_b_score
    )
    breakdown["translation_similarity"] = meaningful_translation_similarity
    breakdown["translation_path_similarity"] = path_b_score
    text_visual_guard = {
        "weak_text_cap_active": weak_text_cap_active,
        "limited_text_cap_active": limited_text_cap_active,
        "limited_text_visual_guard_active": limited_text_visual_guard_active,
        "short_non_exact_anchor_added_matter_guard_active": (
            short_non_exact_anchor_added_matter_guard_active
        ),
        "short_non_exact_anchor_added_matter_guard": (
            short_non_exact_anchor_added_matter_guard
        ),
        "short_non_exact_anchor_added_matter_visual_guard_active": (
            short_non_exact_anchor_added_matter_visual_guard_active
        ),
        "short_non_exact_anchor_added_matter_visual_cap": (
            short_non_exact_anchor_added_matter_visual_cap
        ),
        "short_non_exact_anchor_added_matter_visual_cap_reason": (
            short_non_exact_anchor_added_matter_visual_cap_reason
        ),
        "short_non_exact_anchor_visual_guard": short_non_exact_anchor_visual_guard,
        "short_non_exact_anchor_visual_guard_active": (
            short_non_exact_anchor_visual_guard_active
        ),
        "plain_text_wordmark_visual_guard": plain_text_wordmark_visual_guard,
        "plain_text_wordmark_visual_guard_active": (
            plain_text_wordmark_visual_guard_active
        ),
        "agreement_boost_suppressed": agreement_boost_suppressed,
        "effective_text_score_for_combiner": round(combiner_text_score, 4),
        "weak_text_visual_cap": weak_text_visual_cap,
        "weak_text_visual_cap_reason": weak_text_visual_cap_reason,
        "weak_text_visual_calibration": weak_text_visual_calibration,
        "limited_text_visual_cap": limited_text_visual_cap,
        "limited_text_visual_cap_reason": limited_text_visual_cap_reason,
        "limited_text_visual_calibration": limited_text_visual_calibration,
        "weak_text_visual_low_max": _WEAK_TEXT_VISUAL_LOW_MAX,
        "weak_text_visual_mid_max": _WEAK_TEXT_VISUAL_MID_MAX,
        "weak_text_visual_low_cap": _WEAK_TEXT_VISUAL_LOW_CAP,
        "weak_text_visual_mid_cap": _WEAK_TEXT_VISUAL_MID_CAP,
        "strong_ocr": _visual_has_strong_ocr(visual_diag),
        "very_strong_clip_dino": _visual_has_very_strong_clip_dino(visual_diag),
        "strong_visual_independence_min": _STRONG_VISUAL_INDEPENDENCE_MIN,
    }
    breakdown["text_visual_guard"] = text_visual_guard
    breakdown["textual_breakdown"] = {
        "selected_path": selected_source,
        "selected_text_score": selected_text_score,
        "path_a_score": path_a_score,
        "path_b_score": path_b_score,
        "translation_similarity": meaningful_translation_similarity,
        "translation_path_similarity": path_b_score,
        "path_a": path_a_diag,
        "path_b": path_b_diag,
        "translation_quality_flags": translation_quality_flags,
        "translation_path_b_cap": translation_path_b_cap,
        "translation_duplicate_original": (
            "translation_duplicate_original" in translation_quality_flags
        ),
        "short_collapsed_candidate_translation": (
            "short_collapsed_candidate_translation" in translation_quality_flags
        ),
        "text_visual_guard": text_visual_guard,
    }
    visual_diag = dict(visual_diag)
    visual_diag["text_visual_guard"] = {
        "weak_text_cap_active": weak_text_cap_active,
        "limited_text_cap_active": limited_text_cap_active,
        "limited_text_visual_guard_active": limited_text_visual_guard_active,
        "short_non_exact_anchor_added_matter_guard_active": (
            short_non_exact_anchor_added_matter_guard_active
        ),
        "short_non_exact_anchor_added_matter_guard": (
            short_non_exact_anchor_added_matter_guard
        ),
        "short_non_exact_anchor_added_matter_visual_guard_active": (
            short_non_exact_anchor_added_matter_visual_guard_active
        ),
        "short_non_exact_anchor_visual_guard_active": (
            short_non_exact_anchor_visual_guard_active
        ),
        "short_non_exact_anchor_visual_guard": short_non_exact_anchor_visual_guard,
        "plain_text_wordmark_visual_guard_active": (
            plain_text_wordmark_visual_guard_active
        ),
        "plain_text_wordmark_visual_guard": plain_text_wordmark_visual_guard,
        "agreement_boost_suppressed": agreement_boost_suppressed,
        "weak_text_visual_cap": weak_text_visual_cap,
        "weak_text_visual_cap_reason": weak_text_visual_cap_reason,
        "weak_text_visual_calibration": weak_text_visual_calibration,
        "limited_text_visual_cap": limited_text_visual_cap,
        "limited_text_visual_cap_reason": limited_text_visual_cap_reason,
        "limited_text_visual_calibration": limited_text_visual_calibration,
        "short_non_exact_anchor_added_matter_visual_cap": (
            short_non_exact_anchor_added_matter_visual_cap
        ),
        "short_non_exact_anchor_added_matter_visual_cap_reason": (
            short_non_exact_anchor_added_matter_visual_cap_reason
        ),
    }
    breakdown["visual_breakdown"] = visual_diag
    breakdown["decision_reason"] = (
        f"{selected_source.lower()} textual path selected; "
        f"{combined['decision_reason']}"
    )
    if weak_text_visual_cap is not None:
        breakdown["decision_reason"] += "; weak text visual cap applied"
    if limited_text_visual_guard_active:
        breakdown["decision_reason"] += "; limited text visual agreement suppressed"
    if short_non_exact_anchor_visual_guard_active:
        breakdown["decision_reason"] += "; short non-exact anchor visual agreement suppressed"
    if short_non_exact_anchor_added_matter_visual_guard_active:
        breakdown["decision_reason"] += "; short non-exact anchor added matter visual cap applied"
    if plain_text_wordmark_visual_guard_active:
        breakdown["decision_reason"] += "; plain text wordmark visual cap applied"
    if limited_text_visual_cap is not None:
        breakdown["decision_reason"] += "; limited text visual cap applied"

    logger.info(
        "V2_TEXT_VISUAL_SCORE: %s vs %s | pathA=%.4f, pathB=%.4f | winner=%s | final=%s",
        query_name,
        candidate_name,
        path_a_score,
        path_b_score,
        breakdown["scoring_path_source"],
        breakdown["total"],
    )
    return breakdown


__all__ = [
    "_GENERIC_SUFFIXES",
    "_calculate_visual_breakdown",
    "RISK_THRESHOLDS",
    "_dynamic_combine",
    "HierarchicalTextScorer",
    "adjust_image_similarity",
    "build_logo_image_profile",
    "calculate_adjusted_score",
    "calculate_alert_risk_score",
    "calculate_combined_score",
    "calculate_comprehensive_score",
    "calculate_multilevel_similarity",
    "calculate_name_similarity",
    "calculate_image_score_with_ocr",
    "calculate_ocr_similarity",
    "calculate_risk_score",
    "calculate_token_overlap",
    "calculate_text_similarity",
    "calculate_turkish_similarity",
    "calculate_visual_similarity",
    "combine_visual_scores",
    "compute_idf_weighted_score",
    "compute_idf_weighted_score_tr",
    "check_substring_containment",
    "extract_ocr_text",
    "fuzzy_match",
    "get_risk_level",
    "normalize_turkish",
    "resolve_logo_image_path",
    "score_candidates",
    "score_pair",
    "tokenize",
]
