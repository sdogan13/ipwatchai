"""Canonical scoring helpers shared across search, watchlist, and admin flows."""

import logging
import math
import re
import warnings
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set, Tuple

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
    """Combine CLIP, DINOv2, color, and OCR into one visual score."""
    if ocr_text_a and ocr_text_b:
        ocr_sim = SequenceMatcher(
            None,
            normalize_turkish(ocr_text_a),
            normalize_turkish(ocr_text_b),
        ).ratio()
    else:
        ocr_sim = 0.0

    return (
        clip_sim * 0.35
        + dinov2_sim * 0.30
        + color_sim * 0.15
        + ocr_sim * 0.20
    )


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
        "market",
        "store",
    }
)


def _strip_shared_generic_suffix(w1: str, w2: str) -> tuple:
    """Strip the same generic suffix from both words before fuzzy matching."""
    for suffix in sorted(_GENERIC_SUFFIXES, key=len, reverse=True):
        if len(suffix) < 4:
            continue
        if w1.endswith(suffix) and w2.endswith(suffix):
            root1 = w1[: -len(suffix)]
            root2 = w2[: -len(suffix)]
            if len(root1) >= 2 and len(root2) >= 2:
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
        if IDFLookup.get_word_class(suffix) == "generic":
            root1 = w1[:-common_len]
            root2 = w2[:-common_len]
            if len(root1) >= 2 and len(root2) >= 2:
                return root1, root2

    return w1, w2


def fuzzy_match(w1: str, w2: str) -> float:
    """Run fuzzy matching after removing a shared generic suffix."""
    if not w1 or not w2:
        return 0.0
    stripped_w1, stripped_w2 = _strip_shared_generic_suffix(w1, w2)
    return SequenceMatcher(None, stripped_w1, stripped_w2).ratio()


class HierarchicalTextScorer:
    """Six-tier hierarchical trademark text scorer."""

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
        q_norm = normalize_turkish(query)
        t_norm = normalize_turkish(target)

        breakdown = {
            "query": query,
            "target": target,
            "text_similarity": text_sim,
            "semantic_similarity": semantic_sim,
            "phonetic_similarity": phonetic_sim,
            "visual_similarity": visual_sim,
            "scoring_path": "",
            "total": 0.0,
            "exact_match": False,
            "containment": 0.0,
            "token_overlap": 0.0,
            "weighted_overlap": 0.0,
            "distinctive_match": 0.0,
            "semi_generic_match": 0.0,
            "generic_match": 0.0,
            "matched_words": [],
        }

        q_tokens = tokenize(query)
        t_tokens = tokenize(target)

        if not q_tokens or not t_tokens:
            final_score = max(text_sim, semantic_sim, phonetic_sim) * 0.8
            breakdown["scoring_path"] = "EMPTY_QUERY"
            breakdown["total"] = final_score
            logger.info("HTS_DEBUG_EMPTY: q=%s, t=%s", q_norm, t_norm)
            return final_score, breakdown

        logger.info(
            "HTS_DEBUG_VARS: q_norm=%r t_norm=%r IN=%s",
            q_norm,
            t_norm,
            q_norm in t_norm,
        )

        if len(q_tokens) > 1:
            compound_token = q_norm.replace(" ", "")
            if len(compound_token) >= 4:
                compound_in_target = (
                    compound_token in t_tokens
                    or max(
                        (fuzzy_match(compound_token, token) for token in t_tokens),
                        default=0.0,
                    )
                    >= 0.85
                )
                if compound_in_target:
                    q_tokens = q_tokens | {compound_token}

        overlap = len(q_tokens.intersection(t_tokens)) / len(q_tokens)
        breakdown["token_overlap"] = overlap

        get_class = IDFLookup.get_word_class_tr if use_translated_idf else IDFLookup.get_word_class
        q_distinctive = {word for word in q_tokens if get_class(word) == "distinctive"}
        q_semi = {word for word in q_tokens if get_class(word) == "semi_generic"}
        q_generic = {word for word in q_tokens if get_class(word) == "generic"}
        t_distinctive = {word for word in t_tokens if get_class(word) == "distinctive"}
        t_semi = {word for word in t_tokens if get_class(word) == "semi_generic"}

        exact_dist = q_distinctive.intersection(t_tokens)
        exact_semi = q_semi.intersection(t_tokens)
        exact_generic = q_generic.intersection(t_tokens)

        for word in exact_dist:
            breakdown["matched_words"].append({"query_word": word, "match_type": "exact"})
        for word in exact_semi:
            breakdown["matched_words"].append({"query_word": word, "match_type": "exact"})
        for word in exact_generic:
            breakdown["matched_words"].append({"query_word": word, "match_type": "exact"})

        if q_norm == t_norm:
            breakdown["scoring_path"] = "TIER_1_EXACT"
            breakdown["exact_match"] = True
            breakdown["containment"] = 1.0
            breakdown["total"] = 1.0
            return 1.0, breakdown

        q_sq = q_norm.replace(" ", "")
        t_sq = t_norm.replace(" ", "")
        if q_sq == t_sq and len(q_sq) >= 4:
            breakdown["scoring_path"] = "TIER_1_STRIPPED_EXACT"
            breakdown["exact_match"] = True
            breakdown["containment"] = 1.0
            breakdown["total"] = 0.95
            return 0.95, breakdown

        def generic_elements(tokens: Set[str]) -> Set[str]:
            result = set()
            for word in tokens:
                if word in _GENERIC_SUFFIXES or IDFLookup.get_word_class(word) == "generic":
                    result.add(word)
                else:
                    for suffix in sorted(_GENERIC_SUFFIXES, key=len, reverse=True):
                        if len(suffix) >= 4 and word.endswith(suffix) and len(word[: -len(suffix)]) >= 2:
                            result.add(suffix)
                            break
            return result

        q_generics = generic_elements(q_tokens)
        t_generics = generic_elements(t_tokens)
        has_generic_overlap = bool(q_generics & t_generics)

        if has_generic_overlap:
            def brand_roots(tokens: Set[str]) -> Set[str]:
                roots = set()
                for word in tokens:
                    if word in _GENERIC_SUFFIXES or IDFLookup.get_word_class(word) == "generic":
                        continue
                    stripped = word
                    for suffix in sorted(_GENERIC_SUFFIXES, key=len, reverse=True):
                        if len(suffix) >= 4 and word.endswith(suffix) and len(word[: -len(suffix)]) >= 2:
                            stripped = word[: -len(suffix)]
                            break
                    if stripped:
                        roots.add(stripped)
                return roots

            q_roots = brand_roots(q_tokens)
            t_roots = brand_roots(t_tokens)
            roots_match = (
                any(fuzzy_match(q_root, t_root) >= 0.75 for q_root in q_roots for t_root in t_roots)
                if q_roots and t_roots
                else False
            )

            if not roots_match:
                generic_penalty = 0.70
                text_sim *= generic_penalty
                semantic_sim *= generic_penalty
                phonetic_sim *= generic_penalty
                breakdown["text_similarity"] = text_sim
                breakdown["semantic_similarity"] = semantic_sim
                breakdown["phonetic_similarity"] = phonetic_sim
                note = breakdown.get("scoring_path_note", "")
                breakdown["scoring_path_note"] = (note + "|generic_overlap_penalty").lstrip("|")

        if not q_distinctive and not q_semi and q_generic:
            rarest = max(q_generic, key=lambda word: IDFLookup.get_idf(word))
            if IDFLookup.get_idf(rarest) >= 7.0:
                q_semi = {rarest}
                q_generic = q_generic - {rarest}
                breakdown["scoring_path_note"] = f"anchor_promoted:{rarest}"

        if q_distinctive or q_semi:
            def has_explicit_phrase(phrase: str, full_text: str) -> bool:
                pattern = r"\b" + re.escape(phrase) + r"\b"
                if re.search(pattern, full_text):
                    return True
                phrase_squeezed = phrase.replace(" ", "")
                full_text_squeezed = full_text.replace(" ", "")
                return len(phrase_squeezed) >= 7 and phrase_squeezed in full_text_squeezed

            if has_explicit_phrase(q_norm, t_norm) and (q_distinctive or q_semi):
                coverage = len(q_norm.replace(" ", "")) / len(t_norm.replace(" ", ""))
                score = 0.85 + (0.10 * coverage)
                penalty_map = {"distinctive": 0.05, "semi_generic": 0.04, "generic": 0.025}
                for extra_word in t_tokens - q_tokens:
                    score -= penalty_map.get(get_class(extra_word), 0.03)
                score = max(score, 0.65)
                breakdown["containment"] = 1.0
                breakdown["scoring_path"] = "TIER_2_CONTAINMENT"
                breakdown["total"] = score
                return score, breakdown

            if has_explicit_phrase(t_norm, q_norm) and (t_distinctive or t_semi):
                coverage = len(t_norm.replace(" ", "")) / len(q_norm.replace(" ", ""))
                breakdown["containment"] = 0.5
                score = 0.85 + (0.10 * coverage)
                breakdown["scoring_path"] = "TIER_2_CONTAINMENT"
                breakdown["total"] = score
                return score, breakdown

            if q_distinctive:
                dist_ratio = len(exact_dist) / len(q_distinctive)
                breakdown["distinctive_match"] = dist_ratio
                if dist_ratio == 1.0:
                    score = 0.70 + (0.15 * overlap)
                    breakdown["scoring_path"] = "TIER_3_EXACT_DISTINCTIVE_TOKEN"
                    breakdown["total"] = score
                    return score, breakdown
                if dist_ratio >= 0.5:
                    score = 0.60 + (0.15 * dist_ratio)
                    breakdown["scoring_path"] = "TIER_3_PARTIAL_DISTINCTIVE_TOKEN"
                    breakdown["total"] = score
                    return score, breakdown

            if q_distinctive and not exact_dist and q_generic:
                hidden_anchors = {word for word in q_generic if IDFLookup.get_idf(word) >= 7.0}
                hidden_exact = hidden_anchors.intersection(t_tokens)
                if hidden_exact:
                    anchor_coverage = len(hidden_exact) / max(len(t_tokens), 1)
                    score = 0.65 + 0.15 * anchor_coverage
                    breakdown["scoring_path"] = "TIER_3_HIDDEN_ANCHOR"
                    breakdown["total"] = score
                    return score, breakdown

            best_fuzzy_score = 0.0
            best_fuzzy_from_distinctive = False
            tier4_q_matched = set()
            tier4_t_matched = set()

            for query_word in q_distinctive.union(q_semi):
                is_distinctive_word = query_word in q_distinctive
                for target_word in t_tokens:
                    similarity = fuzzy_match(query_word, target_word)
                    if similarity > best_fuzzy_score:
                        best_fuzzy_score = similarity
                        best_fuzzy_from_distinctive = is_distinctive_word
                    if similarity >= 0.75:
                        tier4_q_matched.add(query_word)
                        tier4_t_matched.add(target_word)
                        breakdown["matched_words"].append(
                            {"query_word": query_word, "match_type": "fuzzy"}
                        )

            if (best_fuzzy_score >= 0.75 or phonetic_sim >= 0.80) and (q_distinctive or q_semi):
                idf_weight = {"distinctive": 0.85, "semi_generic": 0.55, "generic": 0.15}

                def token_weight(word: str) -> float:
                    if word in q_distinctive:
                        return idf_weight["distinctive"]
                    if word in q_semi:
                        return idf_weight["semi_generic"]
                    return idf_weight.get(get_class(word), 0.40)

                total_q_weight = sum(token_weight(word) for word in q_tokens) or 0.001
                matched_q_weight = sum(token_weight(word) for word in tier4_q_matched)
                matched_q_weight += sum(
                    token_weight(word) for word in (exact_dist | exact_semi) - tier4_q_matched
                )
                matched_q_weight = min(matched_q_weight, total_q_weight)

                weighted_q_coverage = matched_q_weight / total_q_weight
                t_coverage = len(tier4_t_matched) / max(len(t_tokens), 1)
                overlap_ratio = (
                    2 * (weighted_q_coverage * t_coverage) / (weighted_q_coverage + t_coverage)
                    if weighted_q_coverage + t_coverage > 0
                    else 0.0
                )

                all_distinctive_matched = q_distinctive and q_distinctive <= (tier4_q_matched | exact_dist)
                all_semi_matched = q_semi and q_semi <= (tier4_q_matched | exact_semi)
                if all_distinctive_matched and t_coverage >= 0.99:
                    overlap_ratio = max(overlap_ratio, 0.85)
                elif not q_distinctive and all_semi_matched and t_coverage >= 0.99:
                    overlap_ratio = max(overlap_ratio, 0.78)

                fuzzy_contribution = (
                    best_fuzzy_score * overlap_ratio if best_fuzzy_score >= 0.75 else 0.0
                )
                phonetic_contribution = phonetic_sim * 0.75 if phonetic_sim >= 0.80 else 0.0
                score = max(fuzzy_contribution, phonetic_contribution)

                triggered_by_distinctive_fuzzy = best_fuzzy_from_distinctive and best_fuzzy_score >= 0.75
                triggered_by_phonetic = phonetic_sim >= 0.80
                if not triggered_by_distinctive_fuzzy and not triggered_by_phonetic:
                    if weighted_q_coverage < 0.65:
                        score = min(score, 0.50)

                any_distinctive_matched = len(exact_dist) > 0 or triggered_by_distinctive_fuzzy
                if q_distinctive and not any_distinctive_matched and score > 0.55:
                    score = min(score * 0.60, 0.55)
                    note = breakdown.get("scoring_path_note", "")
                    breakdown["scoring_path_note"] = (note + "|MISSING_DISTINCTIVE_PENALTY").lstrip("|")

                breakdown["scoring_path"] = "TIER_4_FUZZY_PHONETIC"
                breakdown["total"] = max(score, 0.0)
                return breakdown["total"], breakdown

            if semantic_sim >= 0.60:
                score = max(0.40, min(0.60, semantic_sim * 0.60))
                breakdown["scoring_path"] = "TIER_5_SEMANTIC"
                breakdown["total"] = score
                return score, breakdown

        if exact_semi:
            semi_ratio = len(exact_semi) / len(q_semi)
            score = 0.20 + (0.10 * semi_ratio)
            breakdown["scoring_path"] = "TIER_6_SEMI_GENERIC_ONLY"
            breakdown["semi_generic_match"] = semi_ratio
            breakdown["total"] = score
            return score, breakdown

        if exact_generic:
            generic_ratio = len(exact_generic) / len(q_generic)
            score = 0.10 + (0.10 * generic_ratio)
            breakdown["scoring_path"] = "TIER_6_GENERIC_ONLY"
            breakdown["generic_match"] = generic_ratio
            breakdown["total"] = score
            return score, breakdown

        best_signal = max(text_sim, semantic_sim, phonetic_sim)
        score = best_signal * 0.30
        breakdown["scoring_path"] = "TIER_6_FLOOR_NO_MATCH"
        breakdown["total"] = score
        return score, breakdown


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
    """DEPRECATED: Use risk_engine.score_pair() instead."""
    text_sim = float(text_similarity) if text_similarity is not None else 0.0
    image_sim = float(image_similarity) if image_similarity is not None else 0.0

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

    if image_sim >= 0.80:
        overall = (image_sim * 0.80) + (text_sim * 0.20)
    elif text_sim >= 0.80:
        overall = (text_sim * 0.80) + (image_sim * 0.20)
    else:
        overall = (text_sim * 0.60) + (image_sim * 0.40)

    if image_sim >= 0.95 or text_sim >= 0.95:
        overall = max(overall, 0.85)
    if image_sim >= 0.99:
        overall = max(overall, 0.92)
    if text_sim >= 0.99:
        overall = max(overall, 0.92)

    return {
        "overall_score": round(overall, 3),
        "text_score": round(text_sim, 3),
        "image_score": round(image_sim, 3),
        "search_type": "combined",
        "risk_level": get_risk_level(overall),
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
    score = calculate_visual_similarity(
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
        "color_score": color_sim,
        "ocr_score": 0.0,
        "components_used": [],
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
    score = calculate_visual_similarity(
        clip_sim=raw_image_similarity,
        ocr_text_a=query_ocr_text or "",
        ocr_text_b=trademark_ocr_text or "",
    )
    return {
        "final_score": score,
        "visual_score": raw_image_similarity,
        "ocr_boost": 0.0,
        "ocr_similarity": 0.0,
        "ocr_query_text": query_ocr_text or "",
        "ocr_target_text": trademark_ocr_text or "",
        "risk_level": get_risk_level(score),
    }


def _dynamic_combine(
    text_idf_score: float,
    visual_sim: float,
) -> dict:
    """Combine text and visual signals with confidence-based weights."""
    base_weights = {
        "text": 0.70,
        "visual": 0.30,
    }
    steepness = 4.0

    signals = {
        "text": text_idf_score,
        "visual": visual_sim,
    }

    boosted_weights = {}
    for key, score in signals.items():
        if score > 0:
            boosted_weights[key] = base_weights[key] * math.exp(score * steepness)

    if not boosted_weights:
        return {
            "total": 0.0,
            "dynamic_weights": {key: 0.0 for key in base_weights},
        }

    total_weight = sum(boosted_weights.values())
    final_weights = {
        key: weight / total_weight for key, weight in boosted_weights.items()
    }

    total = sum(signals[key] * final_weights[key] for key in final_weights)
    total = max(0.0, min(1.0, total))

    if visual_sim >= 0.85:
        total = max(total, visual_sim)

    all_weights = {key: 0.0 for key in base_weights}
    all_weights.update(final_weights)

    return {
        "total": round(total, 4),
        "dynamic_weights": {key: round(value, 4) for key, value in all_weights.items()},
    }


def score_pair(
    query_name,
    candidate_name,
    text_sim=0.0,
    semantic_sim=0.0,
    visual_sim=0.0,
    phonetic_sim=0.0,
    candidate_translations=None,
):
    """Score a query name against a candidate name using the shared dual-path flow."""
    if not query_name or not query_name.strip():
        return {
            "exact_match": False,
            "containment": 0.0,
            "token_overlap": 0.0,
            "weighted_overlap": 0.0,
            "distinctive_match": 0.0,
            "text_similarity": 0.0,
            "semantic_similarity": 0.0,
            "phonetic_similarity": 0.0,
            "visual_similarity": round(visual_sim, 4),
            "translation_similarity": 0.0,
            "matched_words": [],
            "scoring_path": "IMAGE_ONLY",
            "scoring_path_source": "ORIGINAL",
            "text_idf_score": 0.0,
            "total": round(visual_sim, 4),
            "dynamic_weights": {"text": 0.0, "visual": 1.0},
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

    combined_a = _dynamic_combine(
        text_idf_score=idf_total_a,
        visual_sim=visual_sim,
    )

    if visual_sim > 0:
        text_only_a = _dynamic_combine(
            text_idf_score=idf_total_a,
            visual_sim=0.0,
        )
        if text_only_a["total"] > combined_a["total"]:
            combined_a["total"] = text_only_a["total"]

    path_a_total = combined_a["total"]
    candidate_name_tr = ((candidate_translations or {}).get("name_tr") or "").strip()
    path_b_total = 0.0
    idf_total_b = 0.0
    breakdown_b = None
    combined_b = None

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

        combined_b = _dynamic_combine(
            text_idf_score=idf_total_b,
            visual_sim=visual_sim,
        )

        if visual_sim > 0:
            text_only_b = _dynamic_combine(
                text_idf_score=idf_total_b,
                visual_sim=0.0,
            )
            if text_only_b["total"] > combined_b["total"]:
                combined_b["total"] = text_only_b["total"]

        path_b_total = combined_b["total"]

    if path_b_total > path_a_total and breakdown_b is not None and combined_b is not None:
        breakdown = breakdown_b
        breakdown["total"] = path_b_total
        breakdown["text_idf_score"] = idf_total_b
        breakdown["dynamic_weights"] = combined_b["dynamic_weights"]
        breakdown["scoring_path_source"] = "TRANSLATED"
        breakdown["visual_similarity"] = round(visual_sim, 4)
        breakdown["path_a_score"] = round(path_a_total, 4)
        breakdown["path_b_score"] = round(path_b_total, 4)
        breakdown["translation_similarity"] = round(path_b_total, 4)
    else:
        breakdown = breakdown_a
        breakdown["total"] = path_a_total
        breakdown["text_idf_score"] = idf_total_a
        breakdown["dynamic_weights"] = combined_a["dynamic_weights"]
        breakdown["scoring_path_source"] = "ORIGINAL"
        breakdown["path_a_score"] = round(path_a_total, 4)
        breakdown["path_b_score"] = round(path_b_total, 4)
        breakdown["translation_similarity"] = round(path_b_total, 4)

    logger.info(
        "DUAL_PATH_SCORE: %s vs %s | pathA=%.4f, pathB=%.4f | winner=%s | final=%s",
        query_name,
        candidate_name,
        path_a_total,
        path_b_total,
        breakdown["scoring_path_source"],
        breakdown["total"],
    )
    return breakdown


__all__ = [
    "_GENERIC_SUFFIXES",
    "RISK_THRESHOLDS",
    "_dynamic_combine",
    "HierarchicalTextScorer",
    "adjust_image_similarity",
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
    "score_candidates",
    "score_pair",
    "tokenize",
]
