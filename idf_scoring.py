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


def normalize_turkish(text: str) -> str:
    """Normalize Turkish characters to ASCII equivalents."""
    if not text:
        return ""
    replacements = {
        'ğ': 'g', 'Ğ': 'g',
        'ı': 'i', 'İ': 'i', 'I': 'i',
        'ö': 'o', 'Ö': 'o',
        'ü': 'u', 'Ü': 'u',
        'ş': 's', 'Ş': 's',
        'ç': 'c', 'Ç': 'c',
    }
    for tr_char, en_char in replacements.items():
        text = text.replace(tr_char, en_char)
    return text.lower().strip()


def tokenize(text: str) -> Set[str]:
    """Extract unique words from text (min length 2)."""
    normalized = normalize_turkish(text)
    words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
    return {w for w in words if len(w) > 1}


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
    # CHECK 2: Token-level containment
    # Uses token sets instead of raw substring matching to avoid
    # false positives like "nike" in "nikex" (different words).
    # Scores are fixed (not boosted by text_sim) — 1.0 is reserved
    # for exact match only (CHECK 1).
    # ==========================================
    q_tokens_temp = tokenize(query)
    t_tokens_temp = tokenize(target)

    # 2a: All query tokens found in target (target is broader or equal set)
    if q_tokens_temp and t_tokens_temp and q_tokens_temp.issubset(t_tokens_temp):
        has_distinctive = any(
            IDFLookup.get_word_class(w) == 'distinctive'
            for w in q_tokens_temp
        )
        if has_distinctive:
            breakdown["containment"] = 1.0
            breakdown["distinctive_weight_matched"] = 1.0
            breakdown["semi_generic_weight_matched"] = 1.0
            breakdown["scoring_path"] = "CONTAINMENT (all query tokens in target, has distinctive)"
            final_score = 0.95
            breakdown["total"] = round(final_score, 4)
            return final_score, breakdown
        else:
            breakdown["containment"] = 0.15
            breakdown["scoring_path"] = "CONTAINMENT (query tokens in target, GENERIC ONLY - penalized)"
            final_score = 0.15
            breakdown["total"] = round(final_score, 4)
            return final_score, breakdown

    # 2b: All target tokens found in query (query is broader)
    if (t_tokens_temp and q_tokens_temp
            and t_tokens_temp.issubset(q_tokens_temp)
            and t_tokens_temp != q_tokens_temp):
        has_distinctive = any(
            IDFLookup.get_word_class(w) == 'distinctive'
            for w in t_tokens_temp
        )
        if has_distinctive:
            breakdown["containment"] = 0.9
            breakdown["distinctive_weight_matched"] = 0.9
            breakdown["semi_generic_weight_matched"] = 0.9
            breakdown["scoring_path"] = "CONTAINMENT (target tokens in query, has distinctive)"
            final_score = 0.93
            breakdown["total"] = round(final_score, 4)
            return final_score, breakdown
        else:
            breakdown["containment"] = 0.15
            breakdown["scoring_path"] = "CONTAINMENT (target tokens in query, GENERIC ONLY - penalized)"
            final_score = 0.15
            breakdown["total"] = round(final_score, 4)
            return final_score, breakdown

    # ==========================================
    # CHECK 3: IDF-weighted token matching
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
            # Check for fuzzy match with length-adaptive threshold.
            # Short words (≤4 chars) need higher similarity to match because
            # a single char diff in a 4-char word always gives 0.75, causing
            # false positives like "nike"/"mike", "star"/"scar", "gold"/"bold".
            best_ratio = 0
            best_target = None

            for t_word in t_tokens:
                min_len = min(len(q_word), len(t_word))
                if min_len <= 4:
                    threshold = 0.85
                elif min_len <= 5:
                    threshold = 0.80
                else:
                    threshold = 0.75
                ratio = SequenceMatcher(None, q_word, t_word).ratio()
                if ratio > best_ratio and ratio >= threshold:
                    best_ratio = ratio
                    best_target = t_word

            if best_target:
                adjusted_weight = weight_mult * best_ratio

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

    # ==========================================
    # SCORING LOGIC (3-tier aware)
    # ==========================================

    # Normalize distinctive match to percentage of total distinctive weight
    if distinctive_weight_total > 0:
        distinctive_pct = distinctive_match / distinctive_weight_total
    else:
        distinctive_pct = 0.0

    # Case A: High distinctive match (>= 80%)
    # "dogan patent" matches "d.p dogan patent" - both distinctive words match
    if distinctive_pct >= 0.8:
        final_score = max(0.92, weighted_overlap, text_sim)
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "A: High distinctive match (>=80%)"
        return final_score, breakdown

    # Case B: Good distinctive match (>= 50%)
    elif distinctive_pct >= 0.5:
        base = max(text_sim, semantic_sim, phonetic_sim)
        final_score = max(0.75, base + distinctive_pct * 0.2)
        final_score = min(1.0, final_score)
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "B: Good distinctive match (>=50%)"
        return final_score, breakdown

    # Case C: Some distinctive match (> 0)
    elif distinctive_match > 0:
        base = max(text_sim, semantic_sim, phonetic_sim)
        final_score = max(0.50, base + distinctive_pct * 0.15)
        final_score = min(1.0, final_score)
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "C: Some distinctive match"
        return final_score, breakdown

    # Case D: Only semi-generic words match (e.g., "kent patent" vs "dogan patent")
    # Only "patent" matches - noise, not a real risk → cap at 18%
    elif semi_generic_match > 0:
        base = max(text_sim, semantic_sim, phonetic_sim)
        semi_contribution = semi_generic_match / max(1.0, total_weight) * 0.5
        final_score = max(0.10, min(0.18, base * 0.3 + semi_contribution))
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "D: Semi-generic only (PENALIZED)"
        return final_score, breakdown

    # Case E: Only generic words match (e.g., "ltd" matches)
    # Minimal score → cap at 10%
    elif generic_match > 0:
        base = max(text_sim, semantic_sim, phonetic_sim)
        generic_contribution = generic_match / max(1.0, total_weight) * 0.2
        final_score = max(0.03, min(0.10, base * 0.2 + generic_contribution))
        breakdown["total"] = round(final_score, 4)
        breakdown["scoring_path"] = "E: Generic only (HEAVILY PENALIZED)"
        return final_score, breakdown

    # Case F: No token match at all
    else:
        final_score = max(text_sim, semantic_sim, phonetic_sim) * 0.7
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
