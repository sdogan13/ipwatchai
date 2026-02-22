# idf_scoring.py
"""
IDF-Weighted Scoring Module

Uses 3-tier word classification for trademark similarity scoring:
- GENERIC (weight=0.1): Common words like "ve", "ltd", "insaat"
- SEMI_GENERIC (weight=0.5): Industry terms like "patent", "marka", "grup"
- DISTINCTIVE (weight=1.0): Unique brand names like "dogan", "nike", "apple"

Usage:
    from idf_scoring import compute_idf_weighted_score

    score, breakdown = compute_idf_weighted_score(
        query="dogan patent",
        target="d.p doğan patent",
        text_sim=0.5,
        semantic_sim=0.6
    )
"""

import re
from typing import Dict, Set, Tuple, List
from difflib import SequenceMatcher

from utils.idf_scoring import normalize_turkish  # canonical source



def tokenize(text: str) -> Set[str]:
    """Extract unique words from text (min length 2)."""
    normalized = normalize_turkish(text)
    words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
    return {w for w in words if len(w) > 1}


def _has_adjacent_transposition(w1: str, w2: str) -> bool:
    """Check if w1 and w2 differ by exactly one adjacent character swap.

    Examples:
        "naik" / "naki" → True  (swap positions 2,3: ik→ki)
        "nike" / "mike" → False (different characters)
        "star" / "rats" → False (multiple swaps)
    """
    if len(w1) != len(w2) or len(w1) < 2:
        return False
    diffs = [(i, c1, c2) for i, (c1, c2) in enumerate(zip(w1, w2)) if c1 != c2]
    if len(diffs) != 2:
        return False
    i, j = diffs[0][0], diffs[1][0]
    return j == i + 1 and w1[i] == w2[j] and w1[j] == w2[i]


def compute_idf_weighted_score(
    query: str,
    target: str,
    text_sim: float = 0.0,
    semantic_sim: float = 0.0,
    phonetic_sim: float = 0.0,
    visual_sim: float = 0.0
) -> Tuple[float, Dict]:
    """
    Compute IDF-weighted similarity score between query and target.

    Uses 3-tier classification:
    - DISTINCTIVE words (weight=1.0) are most important
    - SEMI_GENERIC words (weight=0.5) are moderately important
    - GENERIC words (weight=0.1) are least important

    Args:
        query: Search query (e.g., "dogan patent")
        target: Candidate trademark name
        text_sim: Lexical similarity score (0-1)
        semantic_sim: Semantic embedding similarity (0-1)
        phonetic_sim: Phonetic similarity score (0-1)
        visual_sim: Visual/image similarity score (0-1)

    Returns:
        Tuple of (final_score, breakdown_dict)
    """
    from idf_lookup import IDFLookup

    q_norm = normalize_turkish(query)
    t_norm = normalize_turkish(target)

    breakdown = {
        "exact_match": False,
        "containment": 0.0,
        "token_overlap": 0.0,
        "weighted_overlap": 0.0,
        "distinctive_match": 0.0,
        "semi_generic_match": 0.0,
        "generic_match": 0.0,
        "text_similarity": round(text_sim, 4),
        "semantic_similarity": round(semantic_sim, 4),
        "phonetic_similarity": round(phonetic_sim, 4),
        "visual_similarity": round(visual_sim, 4),
        "matched_words": [],
        "total": 0.0
    }

    # ==========================================
    # CHECK 1: Exact match (highest priority)
    # ==========================================
    if q_norm == t_norm:
        breakdown["exact_match"] = True
        breakdown["containment"] = 1.0
        breakdown["token_overlap"] = 1.0
        breakdown["weighted_overlap"] = 1.0
        breakdown["distinctive_match"] = 1.0
        breakdown["distinctive_weight_matched"] = 1.0
        breakdown["semi_generic_weight_matched"] = 1.0
        breakdown["generic_weight_matched"] = 1.0
        breakdown["total"] = 1.0
        breakdown["scoring_path"] = "EXACT_MATCH"
        return 1.0, breakdown

    # ==========================================
    # CHECK 2: Unified IDF-weighted token matching
    #      fuzzy match (discounted weight) against target tokens
    # exact token matches always outrank fuzzy matches regardless
    # ==========================================
    q_tokens = tokenize(query)
    t_tokens = tokenize(target)

    if not q_tokens:
        final_score = max(text_sim, semantic_sim, phonetic_sim) * 0.8
        breakdown["total"] = round(final_score, 4)
        return final_score, breakdown

    # Calculate simple token overlap for reference
    simple_overlap = len(q_tokens.intersection(t_tokens)) / len(q_tokens)
    breakdown["token_overlap"] = round(simple_overlap, 3)

    # Get IDF data for query tokens
    # Build weighted scores by word class
    distinctive_weight_total = 0.0
    semi_generic_weight_total = 0.0
    generic_weight_total = 0.0

    distinctive_match = 0.0
    semi_generic_match = 0.0
    generic_match = 0.0

    matched_words = []

    for q_word in q_tokens:
        # Get word classification from database (data-driven, not hardcoded)
        idf_score = IDFLookup.get_idf(q_word)
        doc_freq = IDFLookup.get_doc_frequency(q_word)
        word_class = IDFLookup.get_word_class(q_word)  # Uses word_idf table
        weight_mult = IDFLookup.get_weight_multiplier(q_word)  # 0.1, 0.5, or 1.0

        # Track weights by class
        if word_class == 'generic':
            generic_weight_total += weight_mult
        elif word_class == 'semi_generic':
            semi_generic_weight_total += weight_mult
        else:  # distinctive
            distinctive_weight_total += weight_mult

        # Check for exact word match
        if q_word in t_tokens:
            if word_class == 'distinctive':
                distinctive_match += weight_mult
            elif word_class == 'semi_generic':
                semi_generic_match += weight_mult
            else:
                generic_match += weight_mult

            matched_words.append({
                "query_word": q_word,
                "target_word": q_word,
                "match_type": "exact",
                "idf": round(idf_score, 2),
                "word_class": word_class,
                "weight": weight_mult
            })
        else:
            best_ratio = 0
            best_target = None

            for t_word in t_tokens:
                min_len = min(len(q_word), len(t_word))
                if min_len <= 4:
                    threshold = 0.75
                elif min_len <= 5:
                    threshold = 0.80
                else:
                    threshold = 0.75
                ratio = SequenceMatcher(None, q_word, t_word).ratio()
                # Adjacent transposition boost: "naik"↔"naki" is a common
                if _has_adjacent_transposition(q_word, t_word):
                    ratio = max(ratio, 0.92)
                if ratio > best_ratio and ratio >= threshold:
                    best_ratio = ratio
                    best_target = t_word

            if best_target:
                adjusted_weight = weight_mult * best_ratio

                len_q = len(q_word)
                len_t = len(best_target)
                if len_t != len_q:
                    len_ratio = min(len_q, len_t) / max(len_q, len_t)
                    adjusted_weight *= len_ratio

                if word_class == 'distinctive':
                    distinctive_match += adjusted_weight
                elif word_class == 'semi_generic':
                    semi_generic_match += adjusted_weight
                else:
                    generic_match += adjusted_weight

                matched_words.append({
                    "query_word": q_word,
                    "target_word": best_target,
                    "match_type": "fuzzy",
                    "similarity": round(best_ratio, 2),
                    "idf": round(idf_score, 2),
                    "word_class": word_class,
                    "weight": round(adjusted_weight, 3)
                })

    # Calculate total weights
    total_weight = distinctive_weight_total + semi_generic_weight_total + generic_weight_total
    total_match = distinctive_match + semi_generic_match + generic_match

    if total_weight > 0:
        weighted_overlap = total_match / total_weight
    else:
        weighted_overlap = 0.0

    breakdown["weighted_overlap"] = round(weighted_overlap, 3)
    breakdown["distinctive_match"] = round(distinctive_match, 3)
    breakdown["semi_generic_match"] = round(semi_generic_match, 3)
    breakdown["generic_match"] = round(generic_match, 3)
    breakdown["matched_words"] = matched_words

    # Calculate percentage of each class matched (for verification)
    if distinctive_weight_total > 0:
        breakdown["distinctive_weight_matched"] = round(distinctive_match / distinctive_weight_total, 3)
    else:
        breakdown["distinctive_weight_matched"] = 0.0

    if semi_generic_weight_total > 0:
        breakdown["semi_generic_weight_matched"] = round(semi_generic_match / semi_generic_weight_total, 3)
    else:
        breakdown["semi_generic_weight_matched"] = 0.0

    if generic_weight_total > 0:
        breakdown["generic_weight_matched"] = round(generic_match / generic_weight_total, 3)
    else:
        breakdown["generic_weight_matched"] = 0.0

    # ------------------------------------------------------------------
    # KEY METRICS for unified scoring
    # ------------------------------------------------------------------

    n_exact = sum(1 for m in matched_words if m.get("match_type") == "exact")
    n_fuzzy = sum(1 for m in matched_words if m.get("match_type") == "fuzzy")
    n_q = len(q_tokens)
    exact_token_ratio = n_exact / n_q if n_q > 0 else 0.0

    exact_weight = sum(
        m["weight"] for m in matched_words if m.get("match_type") == "exact"
    )
    exact_idf_ratio = exact_weight / total_weight if total_weight > 0 else 0.0

    q_in_t = q_tokens and t_tokens and q_tokens.issubset(t_tokens)
    t_in_q = t_tokens and q_tokens and t_tokens.issubset(q_tokens) and t_tokens != q_tokens
    breakdown["containment"] = 1.0 if q_in_t else (0.5 if t_in_q else 0.0)

    _matched_q_words = set()
    _matched_t_words = set()
    for m in matched_words:
        _matched_q_words.add(m.get("query_word", ""))
        _matched_t_words.add(m.get("target_word", ""))
    _q_unmatched = q_tokens - _matched_q_words
    _t_unmatched = t_tokens - _matched_t_words
    n_unmatched = len(_q_unmatched) + len(_t_unmatched)

    if distinctive_weight_total > 0:
        distinctive_pct = distinctive_match / distinctive_weight_total
    else:
        distinctive_pct = 0.0

    # Store extra metrics
    breakdown["exact_token_ratio"] = round(exact_token_ratio, 3)
    breakdown["exact_idf_ratio"] = round(exact_idf_ratio, 3)

    # ==========================================
    # SCORING LOGIC: Hierarchical token matching
    # Length dilution: extra unmatched words dilute the score
    # ==========================================

    def _compute_length_dilution():
        """Per-word dilution penalty for ALL unmatched words on both sides.

        Uses the pre-computed _q_unmatched / _t_unmatched sets so that
        dilution fires even when both sides have the same token count
        (e.g. "ip watch ai" vs "ip ikram pastanesi" — both 3 tokens
        but 4 unmatched words).
        """
        if not _q_unmatched and not _t_unmatched:
            return 0.0
        penalty = 0.0
        for w in (_q_unmatched | _t_unmatched):
            wclass = IDFLookup.get_word_class(w)
            if wclass == 'distinctive':
                penalty += 0.06
            elif wclass == 'semi_generic':
                penalty += 0.045
            else:
                penalty += 0.025
        return penalty

    # ------------------------------------------------------------------
    # Case A: High distinctive match (>= 80%)
    # ------------------------------------------------------------------
    if distinctive_pct >= 0.8:
        # Sub-case A1: Has exact distinctive token matches
        #   → "dogan" exact, "patent" exact → high score
        # Sub-case A2: Fuzzy-only distinctive matches
        #   → lower ceiling than exact matches

        has_exact_distinctive = any(
            m.get("match_type") == "exact" and m.get("word_class") == "distinctive"
            for m in matched_words
        )

        if has_exact_distinctive:
            # Score driven by exact coverage proportion
            base_score = max(0.90, exact_idf_ratio + 0.45, weighted_overlap + 0.20, text_sim)
            base_score = min(0.98, base_score)  # 1.0 reserved for exact match
        else:
            # Fuzzy-only: cap below exact-match territory
            base = max(weighted_overlap, text_sim, semantic_sim, phonetic_sim)
            base_score = max(0.55, base)
            base_score = min(0.82, base_score)

        # Length dilution for unmatched words
        dilution = _compute_length_dilution()
        final_score = max(0.50, base_score - dilution)

        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = (
            f"A: High distinctive match (>=80%)"
            f" [exact_tokens={n_exact}/{n_q}, fuzzy={n_fuzzy}]"
        )
        return final_score, breakdown

    # ------------------------------------------------------------------
    # Case B: Good distinctive match (>= 50%)
    # ------------------------------------------------------------------
    elif distinctive_pct >= 0.5:
        has_exact = any(
            m.get("match_type") == "exact" and m.get("word_class") == "distinctive"
            for m in matched_words
        )

        if has_exact:
            # Has exact distinctive tokens — higher ceiling
            exact_bonus = exact_idf_ratio * 0.20
            base = max(text_sim, semantic_sim, phonetic_sim)
            final_score = max(0.60, base + distinctive_pct * 0.15 + exact_bonus)
            final_score = min(0.92, final_score)
        else:
            if phonetic_sim > 0.80:
                ceiling = 0.80   # Words sound alike — high risk
            elif phonetic_sim > 0.50:
                ceiling = 0.73   # Moderate phonetic match
            else:
                ceiling = 0.67   # Coincidental text similarity
            base = max(weighted_overlap, text_sim, semantic_sim, phonetic_sim)
            final_score = max(0.50, base * 0.85)
            final_score = min(ceiling, final_score)

        # Length dilution
        dilution = _compute_length_dilution()
        final_score = max(0.45, final_score - dilution)

        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = (
            f"B: Good distinctive match (>=50%)"
            f" [exact_tokens={n_exact}/{n_q}, fuzzy={n_fuzzy}]"
        )
        return final_score, breakdown

    # ------------------------------------------------------------------
    # Case C: Some distinctive match (> 0)
    # ------------------------------------------------------------------
    elif distinctive_match > 0:
        exact_bonus = exact_idf_ratio * 0.15
        base = max(text_sim, semantic_sim, phonetic_sim)
        final_score = max(0.45, base + distinctive_pct * 0.15 + exact_bonus)
        final_score = min(0.85, final_score)

        # Length dilution
        dilution = _compute_length_dilution()
        final_score = max(0.35, final_score - dilution)

        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = (
            f"C: Some distinctive match"
            f" [exact_tokens={n_exact}/{n_q}, fuzzy={n_fuzzy}]"
        )
        return final_score, breakdown

    # ------------------------------------------------------------------
    # Case D: Only semi-generic words match
    # ------------------------------------------------------------------
    elif semi_generic_match > 0:
        base = max(text_sim, semantic_sim, phonetic_sim)
        semi_contribution = semi_generic_match / max(1.0, total_weight) * 0.5
        final_score = max(0.10, min(0.18, base * 0.3 + semi_contribution))
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "D: Semi-generic only (PENALIZED)"
        return final_score, breakdown

    # ------------------------------------------------------------------
    # Case E: Only generic words match
    # ------------------------------------------------------------------
    elif generic_match > 0:
        base = max(text_sim, semantic_sim, phonetic_sim)
        generic_contribution = generic_match / max(1.0, total_weight) * 0.2
        final_score = max(0.03, min(0.10, base * 0.2 + generic_contribution))
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "E: Generic only (HEAVILY PENALIZED)"
        return final_score, breakdown

    # ------------------------------------------------------------------
    # Case F: No token match at all
    # ------------------------------------------------------------------
    else:
        best_signal = max(text_sim, semantic_sim, phonetic_sim)
        # so that phonetic-near-misses like "NAIK"/"NAKO" (phonetic 0.92)
        if phonetic_sim > 0.80:
            final_score = best_signal * 0.80
        else:
            final_score = best_signal * 0.70
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "F: No token match"
        return final_score, breakdown


def score_candidates(
    query: str,
    candidates: List[Dict],
    text_sim_key: str = "text_similarity",
    semantic_sim_key: str = "semantic_similarity"
) -> List[Dict]:
    """
    Score a list of candidate trademarks against a query.

    Args:
        query: Search query
        candidates: List of candidate dicts with 'name' and similarity scores
        text_sim_key: Key for text similarity in candidate dict
        semantic_sim_key: Key for semantic similarity in candidate dict

    Returns:
        Candidates sorted by IDF-weighted score (descending)
    """
    scored = []

    for c in candidates:
        name = c.get('name', '')
        text_sim = c.get(text_sim_key, 0.0)
        semantic_sim = c.get(semantic_sim_key, 0.0)

        score, breakdown = compute_idf_weighted_score(
            query=query,
            target=name,
            text_sim=text_sim,
            semantic_sim=semantic_sim
        )

        c['idf_score'] = score
        c['idf_breakdown'] = breakdown
        scored.append(c)

    # Sort by IDF score descending
    scored.sort(key=lambda x: x['idf_score'], reverse=True)
    return scored


# ============================================
# CLI for testing
# ============================================
if __name__ == "__main__":
    print("="*60)
    print("IDF SCORING TEST")
    print("="*60)

    test_cases = [
        ("dogan patent", "d.p dogan patent"),
        ("dogan patent", "kent patent"),
        ("dogan patent", "vatan patent"),
        ("dogan patent", "dogan"),
        ("nike", "nike sports"),
        ("coca cola", "cola turka"),
    ]

    for query, target in test_cases:
        score, breakdown = compute_idf_weighted_score(query, target, 0.5, 0.5)
        print(f"\n'{query}' vs '{target}'")
        print(f"  Score: {score:.1%}")
        print(f"  Distinctive: {breakdown['distinctive_match']:.2f}")
        print(f"  Semi-generic: {breakdown['semi_generic_match']:.2f}")
        print(f"  Generic: {breakdown['generic_match']:.2f}")
        if breakdown['matched_words']:
            print(f"  Matches: {[m['query_word'] + '(' + m['word_class'][:4] + ')' for m in breakdown['matched_words']]}")
