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
    # CHECK 2: Unified IDF-weighted token matching
    #
    # All non-exact matches flow through a single waterfall that:
    #   1. Scores each query token as exact match (full weight) or
    #      fuzzy match (discounted weight) against target tokens
    #   2. Classifies by IDF tier: distinctive(1.0), semi_generic(0.5), generic(0.1)
    #   3. Computes exact_token_ratio (proportion of query tokens matched exactly)
    #   4. Routes to Cases A-F based on distinctive % matched
    #   5. Applies length dilution for extra unmatched words
    #
    # Containment (one side's tokens ⊂ the other) is detected as a
    # signal but NOT a separate early-return path, ensuring that
    # exact token matches always outrank fuzzy matches regardless
    # of which scoring path fires.
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

                # Penalize length-mismatched fuzzy matches in BOTH directions:
                # - "dogan" vs "ozdogan" (target longer → substring embed)
                # - "dogan" vs "doga" (target shorter → truncation/different word)
                # Either way the words are NOT near-equivalent.
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

    # 1. Exact token ratio: proportion of query tokens matched EXACTLY
    #    (not fuzzy). This is the PRIMARY signal — exact word matches
    #    always outrank fuzzy matches.
    n_exact = sum(1 for m in matched_words if m.get("match_type") == "exact")
    n_fuzzy = sum(1 for m in matched_words if m.get("match_type") == "fuzzy")
    n_q = len(q_tokens)
    exact_token_ratio = n_exact / n_q if n_q > 0 else 0.0

    # 2. Exact IDF-weighted ratio: how much of query's IDF weight is
    #    covered by EXACT matches (not fuzzy).
    exact_weight = sum(
        m["weight"] for m in matched_words if m.get("match_type") == "exact"
    )
    exact_idf_ratio = exact_weight / total_weight if total_weight > 0 else 0.0

    # 3. Containment signal (informational, not an early return)
    q_in_t = q_tokens and t_tokens and q_tokens.issubset(t_tokens)
    t_in_q = t_tokens and q_tokens and t_tokens.issubset(q_tokens) and t_tokens != q_tokens
    breakdown["containment"] = 1.0 if q_in_t else (0.5 if t_in_q else 0.0)

    # 4. Length difference (for dilution)
    n_target = len(t_tokens)
    n_query = len(q_tokens)
    extra_words = abs(n_target - n_query)

    # 5. Distinctive match percentage
    if distinctive_weight_total > 0:
        distinctive_pct = distinctive_match / distinctive_weight_total
    else:
        distinctive_pct = 0.0

    # Store extra metrics
    breakdown["exact_token_ratio"] = round(exact_token_ratio, 3)
    breakdown["exact_idf_ratio"] = round(exact_idf_ratio, 3)

    # ==========================================
    # SCORING LOGIC: Hierarchical token matching
    #
    # Priority order:
    #   1. Exact token coverage (exact_token_ratio, exact_idf_ratio)
    #   2. IDF-weighted overlap (distinctive_pct, weighted_overlap)
    #   3. Fuzzy/semantic signals (text_sim, phonetic_sim, etc.)
    #
    # Length dilution: extra unmatched words dilute the score
    # proportionally by their IDF class.
    # ==========================================

    def _compute_length_dilution(extra_count):
        """Per-extra-word dilution penalty for unmatched words."""
        if extra_count <= 0:
            return 0.0
        # Apply per-word penalty by examining actual extra words
        q_unmatched = q_tokens - {m.get("target_word") or m.get("query_word") for m in matched_words}
        t_unmatched = t_tokens - {m.get("target_word") for m in matched_words}
        extras = q_unmatched | t_unmatched
        penalty = 0.0
        for w in extras:
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
        #   "d.p dogan patent" vs query "dogan patent ve danismanlik"
        #   → "dogan" exact, "patent" exact → high score
        # Sub-case A2: Fuzzy-only distinctive matches
        #   "dogam egitim" vs query "dogan patent" → "dogam"~"dogan" fuzzy
        #   → lower ceiling than exact matches

        has_exact_distinctive = any(
            m.get("match_type") == "exact" and m.get("word_class") == "distinctive"
            for m in matched_words
        )

        if has_exact_distinctive:
            # Score driven by exact coverage proportion
            # exact_idf_ratio captures how much of the query's weight is
            # covered by exact matches — this is the primary signal.
            base_score = max(0.90, exact_idf_ratio + 0.45, weighted_overlap + 0.20, text_sim)
            base_score = min(0.98, base_score)  # 1.0 reserved for exact match
        else:
            # Fuzzy-only: cap below exact-match territory
            base = max(weighted_overlap, text_sim, semantic_sim, phonetic_sim)
            base_score = max(0.55, base)
            base_score = min(0.82, base_score)

        # Length dilution for unmatched words
        dilution = _compute_length_dilution(extra_words)
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
        # Exact matches boost more than fuzzy matches
        exact_bonus = exact_idf_ratio * 0.20  # bonus for exact coverage
        base = max(text_sim, semantic_sim, phonetic_sim)
        final_score = max(0.60, base + distinctive_pct * 0.15 + exact_bonus)
        final_score = min(0.92, final_score)

        # Length dilution
        dilution = _compute_length_dilution(extra_words)
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
        dilution = _compute_length_dilution(extra_words)
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
