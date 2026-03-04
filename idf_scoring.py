"""
IDF-Weighted Scoring Module (Hierarchical Engine)

Uses 6-tier cascading classification for trademark similarity scoring:
Tier 1: Exact Match (100%)
Tier 2: Phrase-Level Containment (85-95%)
Tier 3: Word-Level Exact Match (70-85%)
Tier 4: Token-Level Fuzzy/Phonetic Match (50-75%)
Tier 5: Semantic & Cross-Lingual Match (40-60%)
Tier 6: Generic Only / Floor (< 30%)
"""

import re
from typing import Dict, Set, Tuple, List
from difflib import SequenceMatcher
from utils.idf_scoring import normalize_turkish
from idf_lookup import IDFLookup

def tokenize(text: str) -> Set[str]:
    """Extract unique words from text (min length 2)."""
    normalized = normalize_turkish(text)
    words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
    return {w for w in words if len(w) > 1}

def fuzzy_match(w1: str, w2: str) -> float:
    if not w1 or not w2:
        return 0.0
    return SequenceMatcher(None, w1, w2).ratio()

class HierarchicalTextScorer:
    """Implement 6-Tier descending risk evaluator."""

    @staticmethod
    def score(
        query: str,
        target: str,
        text_sim: float = 0.0,
        semantic_sim: float = 0.0,
        phonetic_sim: float = 0.0,
        visual_sim: float = 0.0
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
            
            # Backwards compatibility flags for downstream test/API parsers
            "exact_match": False,
            "containment": 0.0,
            "token_overlap": 0.0,
            "weighted_overlap": 0.0,
            "distinctive_match": 0.0,
            "semi_generic_match": 0.0,
            "generic_match": 0.0,
            "matched_words": [],
        }

        # Handle empty cases
        q_tokens = tokenize(query)
        t_tokens = tokenize(target)

        if not q_tokens or not t_tokens:
            final_score = max(text_sim, semantic_sim, phonetic_sim) * 0.8
            breakdown["scoring_path"] = "EMPTY_QUERY"
            breakdown["total"] = final_score
            import logging
            logging.info(f"HTS_DEBUG_EMPTY: q={q_norm}, t={t_norm}")
            return final_score, breakdown

        import logging
        logging.info(f"HTS_DEBUG_VARS: q_norm='{q_norm}' t_norm='{t_norm}' IN={(q_norm in t_norm)}")

        overlap = len(q_tokens.intersection(t_tokens)) / len(q_tokens)
        breakdown["token_overlap"] = overlap

        # Token Grouping by IDF
        q_distinctive = {w for w in q_tokens if IDFLookup.get_word_class(w) == 'distinctive'}
        q_semi = {w for w in q_tokens if IDFLookup.get_word_class(w) == 'semi_generic'}
        q_generic = {w for w in q_tokens if IDFLookup.get_word_class(w) == 'generic'}

        t_distinctive = {w for w in t_tokens if IDFLookup.get_word_class(w) == 'distinctive'}
        t_semi = {w for w in t_tokens if IDFLookup.get_word_class(w) == 'semi_generic'}
        t_generic = {w for w in t_tokens if IDFLookup.get_word_class(w) == 'generic'}
        # Determine exact word matches
        exact_dist = q_distinctive.intersection(t_tokens)
        exact_semi = q_semi.intersection(t_tokens)
        exact_generic = q_generic.intersection(t_tokens)

        # Build matched_words history for tests
        for w in exact_dist: breakdown["matched_words"].append({"query_word": w, "match_type": "exact"})
        for w in exact_semi: breakdown["matched_words"].append({"query_word": w, "match_type": "exact"})
        for w in exact_generic: breakdown["matched_words"].append({"query_word": w, "match_type": "exact"})

        # Safeguard: If query consists ONLY of generic words, bypass Tiers 1-5 and go straight to Tier 6
        if q_distinctive or q_semi:
            # Tier 1: Exact Sentence-Level Match
            if q_norm == t_norm:
                breakdown["scoring_path"] = "TIER_1_EXACT"
                breakdown["exact_match"] = True
                breakdown["containment"] = 1.0
                breakdown["total"] = 1.0
                return 1.0, breakdown

            # Tier 2: Phrase-Level Containment
            import re
            def has_explicit_phrase(phrase: str, full_text: str) -> bool:
                # 1. Standard spaced containment with word boundaries
                # Use regex to ensure we match whole words within the phrase
                pattern = r'\b' + re.escape(phrase) + r'\b'
                if re.search(pattern, full_text):
                    return True
                
                # 2. Space-agnostic "squeezed" containment 
                # Require higher length for squeezed to avoid accidental substrings (e.g. "paten" in "patent")
                q_sq = phrase.replace(" ", "")
                t_sq = full_text.replace(" ", "")
                if len(q_sq) >= 7 and q_sq in t_sq: # Increased threshold from 5 to 7
                    return True
                return False

            if has_explicit_phrase(q_norm, t_norm) and (q_distinctive or q_semi):
                # Use squeezed lengths for coverage to avoid whitespace bias
                coverage = len(q_norm.replace(" ", "")) / len(t_norm.replace(" ", ""))
                breakdown["containment"] = 1.0
                score = 0.85 + (0.10 * coverage) # 0.85 to 0.95
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

            # Tier 3: Word-Level Exact Match
            if q_distinctive:
                dist_ratio = len(exact_dist) / len(q_distinctive)
                breakdown["distinctive_match"] = dist_ratio
                if dist_ratio == 1.0:
                    score = 0.70 + (0.15 * overlap) # 0.70 to 0.85
                    breakdown["scoring_path"] = "TIER_3_EXACT_DISTINCTIVE_TOKEN"
                    breakdown["total"] = score
                    return score, breakdown
                elif dist_ratio >= 0.5:
                    score = 0.60 + (0.15 * dist_ratio)
                    breakdown["scoring_path"] = "TIER_3_PARTIAL_DISTINCTIVE_TOKEN"
                    breakdown["total"] = score
                    return score, breakdown

            # Tier 4: Token-Level Fuzzy / Phonetic Match
            best_fuzzy_distinctive = 0.0
            for qw in q_distinctive.union(q_semi): # Check both distinctive AND semi
                for tw in t_tokens:
                    sim = fuzzy_match(qw, tw)
                    if sim > best_fuzzy_distinctive:
                        best_fuzzy_distinctive = sim
                        if sim >= 0.75:
                            breakdown["matched_words"].append({"query_word": qw, "match_type": "fuzzy"})

            # Tier 4: Token-Level Fuzzy / Phonetic Match
            # Only trigger if the signal comes from a non-generic token
            if (best_fuzzy_distinctive >= 0.75 or phonetic_sim >= 0.80) and (q_distinctive or q_semi):
                score = max(best_fuzzy_distinctive * 0.75, phonetic_sim * 0.75, text_sim * 0.75)
                score = max(0.50, min(0.75, score))
                breakdown["scoring_path"] = "TIER_4_FUZZY_PHONETIC"
                breakdown["total"] = score
                return score, breakdown

            # Tier 5: Semantic & Cross-Lingual Match
            if semantic_sim >= 0.60:
                score = max(0.40, min(0.60, semantic_sim * 0.60))
                breakdown["scoring_path"] = "TIER_5_SEMANTIC"
                breakdown["total"] = score
                return score, breakdown

        # Tier 6: Generic Only / Floor
        if exact_semi:
            semi_ratio = len(exact_semi) / len(q_semi)
            score = 0.20 + (0.10 * semi_ratio)
            breakdown["scoring_path"] = "TIER_6_SEMI_GENERIC_ONLY"
            breakdown["semi_generic_match"] = semi_ratio
            breakdown["total"] = score
            return score, breakdown
            
        if exact_generic:
            gen_ratio = len(exact_generic) / len(q_generic)
            score = 0.10 + (0.10 * gen_ratio)
            breakdown["scoring_path"] = "TIER_6_GENERIC_ONLY"
            breakdown["generic_match"] = gen_ratio
            breakdown["total"] = score
            return score, breakdown
        
        # Absolute floor
        best_signal = max(text_sim, semantic_sim, phonetic_sim)
        score = best_signal * 0.30  # Penalize isolated weak signals
        breakdown["scoring_path"] = "TIER_6_FLOOR_NO_MATCH"
        breakdown["total"] = score
        return score, breakdown

def compute_idf_weighted_score(
    query: str,
    target: str,
    text_sim: float = 0.0,
    semantic_sim: float = 0.0,
    phonetic_sim: float = 0.0,
    visual_sim: float = 0.0
) -> Tuple[float, Dict]:
    return HierarchicalTextScorer.score(query, target, text_sim, semantic_sim, phonetic_sim, visual_sim)

def score_candidates(
    query: str,
    candidates: List[Dict],
    text_sim_key: str = "text_similarity",
    semantic_sim_key: str = "semantic_similarity"
) -> List[Dict]:
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

    scored.sort(key=lambda x: x['idf_score'], reverse=True)
    return scored
