"""
Tests for upgraded translation similarity scoring.

Tests that score_translated_pair() uses the IDF waterfall,
that calculate_translation_similarity() uses pre-stored name_tr,
and that the translation score floor works correctly.

Runs without NLLB model or DB — we seed IDFLookup with known test
words so the 3-tier classification works correctly in CI.
"""
import sys
import os
import time
import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.translation import (
    score_translated_pair,
    calculate_translation_similarity,
    translate_to_turkish,
)


# ============================================
# Fixture: seed IDFLookup with test words
# ============================================

@pytest.fixture(autouse=True)
def seed_idf_lookup():
    """
    Pre-populate IDFLookup cache so tests don't need a live DB.
    Without this, unknown words get default IDF=5.0 which falls
    in the 'generic' bucket (< 5.3).  We seed common test words
    with realistic IDF values.
    """
    from idf_lookup import IDFLookup

    # Prevent DB load attempt
    IDFLookup._loaded = True
    IDFLookup._total_docs = 2_300_000

    # Seed distinctive words (IDF >= 6.9)
    distinctive = [
        "elma", "kaplan", "nike", "bmw", "motors", "dunya",
        "kirmizi", "yesil", "gunes", "market", "dünyası",
    ]
    for w in distinctive:
        IDFLookup._cache[w] = {"idf": 8.0, "is_generic": False, "doc_freq": 50}

    # Seed generic words (IDF < 5.3)
    generic = ["ve", "ltd", "sti", "san", "tic"]
    for w in generic:
        IDFLookup._cache[w] = {"idf": 2.0, "is_generic": True, "doc_freq": 500_000}

    # Seed semi-generic words (5.3 <= IDF < 6.9)
    semi = ["patent", "marka", "grup"]
    for w in semi:
        IDFLookup._cache[w] = {"idf": 6.0, "is_generic": False, "doc_freq": 5_000}

    yield

    # Cleanup: mark as not loaded so other tests reload fresh
    IDFLookup._loaded = False
    IDFLookup._cache = {}


# ============================================
# 1. score_translated_pair() — IDF waterfall
# ============================================

class TestScoreTranslatedPair:
    """Test that score_translated_pair uses the IDF waterfall correctly."""

    def test_exact_match_returns_1(self):
        result = score_translated_pair("elma", "elma")
        assert result["translation_similarity"] == 1.0

    def test_exact_match_case_insensitive(self):
        result = score_translated_pair("Elma", "elma")
        assert result["translation_similarity"] == 1.0

    def test_empty_query_returns_0(self):
        result = score_translated_pair("", "elma")
        assert result["translation_similarity"] == 0.0

    def test_empty_candidate_returns_0(self):
        result = score_translated_pair("elma", "")
        assert result["translation_similarity"] == 0.0

    def test_none_query_returns_0(self):
        result = score_translated_pair(None, "elma")
        assert result["translation_similarity"] == 0.0

    def test_none_candidate_returns_0(self):
        result = score_translated_pair("elma", None)
        assert result["translation_similarity"] == 0.0

    def test_containment_query_in_target_high_score(self):
        """'elma' contained in 'elma market' → high score via IDF containment."""
        result = score_translated_pair("elma", "elma market")
        assert result["translation_similarity"] >= 0.85, \
            f"Containment should score high, got {result['translation_similarity']}"

    def test_containment_target_in_query_high_score(self):
        """'elma' (target) is substring of 'elma market' (query) → high score."""
        result = score_translated_pair("elma market", "elma")
        assert result["translation_similarity"] >= 0.85

    def test_no_overlap_low_score(self):
        """Completely unrelated words → low score."""
        result = score_translated_pair("elma", "kaplan")
        assert result["translation_similarity"] < 0.50, \
            f"No overlap should score low, got {result['translation_similarity']}"

    def test_returns_scoring_path(self):
        """Result should include the IDF waterfall scoring path."""
        result = score_translated_pair("elma", "elma")
        assert "translation_scoring_path" in result

    def test_returns_text_sim(self):
        """Result should include the intermediate text_sim."""
        result = score_translated_pair("elma", "kaplan")
        assert "translation_text_sim" in result

    def test_multi_word_exact(self):
        """Multi-word exact match."""
        result = score_translated_pair("kirmizi elma", "kirmizi elma")
        assert result["translation_similarity"] == 1.0

    def test_partial_word_overlap(self):
        """One distinctive word matches, other doesn't."""
        result = score_translated_pair("kirmizi elma", "yesil elma")
        # "elma" matches (distinctive), "kirmizi" vs "yesil" doesn't
        score = result["translation_similarity"]
        assert 0.30 <= score <= 0.95, f"Partial overlap should be moderate, got {score}"

    def test_acronym_containment(self):
        """Acronym in longer name."""
        result = score_translated_pair("bmw", "bmw motors")
        assert result["translation_similarity"] >= 0.60

    def test_generic_only_match_capped_low(self):
        """Only generic words match → IDF Case D/E caps the score."""
        result = score_translated_pair("ve ltd", "ve sti")
        # Only "ve" matches (generic) — should be capped low
        assert result["translation_similarity"] < 0.40


# ============================================
# 2. calculate_translation_similarity() — new signature
# ============================================

class TestCalculateTranslationSimilarity:
    """Test that the main function uses pre-stored name_tr."""

    def test_null_name_tr_returns_0(self):
        """NULL name_tr must return 0.0 — no fallback."""
        assert calculate_translation_similarity("APPLE", "ELMA", candidate_name_tr=None) == 0.0

    def test_empty_name_tr_returns_0(self):
        """Empty string name_tr must return 0.0."""
        assert calculate_translation_similarity("APPLE", "ELMA", candidate_name_tr="") == 0.0

    def test_empty_query_returns_0(self):
        assert calculate_translation_similarity("", "ELMA", candidate_name_tr="elma") == 0.0

    def test_backward_compat_no_name_tr_returns_0(self):
        """Old callers without candidate_name_tr get 0.0."""
        assert calculate_translation_similarity("APPLE", "ELMA") == 0.0

    def test_same_language_turkish(self):
        """Turkish query with Turkish candidate — translate_to_turkish returns as-is."""
        # "ELMA" detected as Turkish → returns "elma" (no NLLB call)
        # candidate_name_tr = "elma market"
        score = calculate_translation_similarity(
            "ELMA", "ELMA MARKET", candidate_name_tr="elma market"
        )
        assert score >= 0.80, f"Same-language containment should score high, got {score}"


# ============================================
# 3. Performance — no NLLB per candidate
# ============================================

class TestPerformance:
    """Verify that scoring 500 candidates is fast (no NLLB per candidate)."""

    def test_500_candidates_under_2_seconds(self):
        """score_translated_pair for 500 candidates should be under 2s."""
        start = time.time()
        for i in range(500):
            score_translated_pair("elma", f"marka {i}")
        elapsed = time.time() - start
        assert elapsed < 2.0, f"500 candidates took {elapsed:.2f}s — should be under 2s"


# ============================================
# 4. Translation score floor in _dynamic_combine
# ============================================

class TestTranslationScoreFloor:
    """Test that near-perfect translation match floors total at 0.90."""

    def test_floor_applied_when_trans_sim_high(self):
        """translation_sim >= 0.95 should floor total at 0.90."""
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(
            text_idf_score=0.0,
            visual_sim=0.0,
            translation_sim=0.98,
        )
        assert result["total"] >= 0.90, \
            f"Translation floor should give total >= 0.90, got {result['total']}"

    def test_floor_not_applied_when_trans_sim_below_threshold(self):
        """translation_sim < 0.95 should NOT apply the floor."""
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(
            text_idf_score=0.0,
            visual_sim=0.0,
            translation_sim=0.50,
        )
        assert result["total"] < 0.90, \
            f"Below threshold should not floor, got {result['total']}"

    def test_floor_does_not_lower_existing_high_total(self):
        """Floor should only raise, never lower."""
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(
            text_idf_score=0.98,
            visual_sim=0.0,
            translation_sim=0.98,
        )
        assert result["total"] >= 0.90


# ============================================
# 5. IDF waterfall integration
# ============================================

class TestIDFWaterfallIntegration:
    """Verify that translation scoring uses IDF Cases A-F properly."""

    def test_exact_match_path(self):
        result = score_translated_pair("elma", "elma")
        assert result["translation_scoring_path"] == "EXACT_MATCH"

    def test_containment_path(self):
        """Containment should produce a containment scoring path."""
        result = score_translated_pair("elma", "elma market")
        path = result.get("translation_scoring_path", "")
        # Should be CONTAINMENT or Case A (high distinctive match)
        assert "CONTAINMENT" in path or "A:" in path, \
            f"Expected containment/Case A path, got: {path}"

    def test_no_match_low_score(self):
        """Completely different words → Case F or low."""
        result = score_translated_pair("elma", "kaplan")
        assert result["translation_similarity"] < 0.50

    def test_score_is_float_0_to_1(self):
        """Score should always be in [0, 1]."""
        test_cases = [
            ("elma", "elma"),
            ("elma", "kaplan"),
            ("", "elma"),
            ("elma", ""),
            ("kirmizi elma", "yesil elma"),
        ]
        for q, c in test_cases:
            result = score_translated_pair(q, c)
            score = result["translation_similarity"]
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for ({q!r}, {c!r})"


# ============================================
# 6. Visual composite scoring
# ============================================

class TestVisualComposite:
    """Verify calculate_visual_similarity uses all 4 components."""

    def test_clip_only(self):
        from risk_engine import calculate_visual_similarity
        score = calculate_visual_similarity(clip_sim=0.80)
        assert abs(score - 0.80 * 0.35) < 0.01

    def test_all_components(self):
        from risk_engine import calculate_visual_similarity
        score = calculate_visual_similarity(
            clip_sim=0.80, dinov2_sim=0.70, color_sim=0.60,
            ocr_text_a="NIKE", ocr_text_b="NIKE",
        )
        expected = 0.80 * 0.35 + 0.70 * 0.30 + 0.60 * 0.15 + 1.0 * 0.20
        assert abs(score - expected) < 0.01

    def test_no_ocr_when_one_empty(self):
        from risk_engine import calculate_visual_similarity
        score = calculate_visual_similarity(
            clip_sim=0.80, dinov2_sim=0.70, color_sim=0.60,
            ocr_text_a="", ocr_text_b="NIKE",
        )
        expected = 0.80 * 0.35 + 0.70 * 0.30 + 0.60 * 0.15
        assert abs(score - expected) < 0.01

    def test_zero_when_no_signals(self):
        from risk_engine import calculate_visual_similarity
        assert calculate_visual_similarity() == 0.0


# ============================================
# 7. Phonetic not double-counted
# ============================================

class TestPhoneticNotDoubleCounted:
    """Verify phonetic is only in IDF waterfall, not in _dynamic_combine."""

    def test_dynamic_combine_has_no_phonetic_weight(self):
        """_dynamic_combine should only have text, visual, translation."""
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(
            text_idf_score=0.80,
            visual_sim=0.50,
            translation_sim=0.30,
        )
        weights = result["dynamic_weights"]
        assert "phonetic" not in weights
        assert "text" in weights
        assert "visual" in weights
        assert "translation" in weights

    def test_dynamic_combine_3_params_only(self):
        """_dynamic_combine should reject phonetic_sim parameter."""
        from risk_engine import _dynamic_combine
        import inspect
        sig = inspect.signature(_dynamic_combine)
        param_names = list(sig.parameters.keys())
        assert "phonetic_sim" not in param_names
        assert len(param_names) == 3  # text_idf_score, visual_sim, translation_sim

    def test_score_pair_still_has_phonetic_in_breakdown(self):
        """score_pair() should still include phonetic_similarity in breakdown."""
        from risk_engine import score_pair
        result = score_pair(
            query_name="ELMA",
            candidate_name="ELMA",
            text_sim=0.80,
            semantic_sim=0.50,
            visual_sim=0.0,
            phonetic_sim=1.0,
        )
        assert "phonetic_similarity" in result
        assert result["phonetic_similarity"] == 1.0


# ============================================
# Run with pytest
# ============================================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
