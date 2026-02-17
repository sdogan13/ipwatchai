"""
Edge case and regression tests.

Covers:
- Unicode and special character handling
- Empty / None / boundary inputs to scoring functions
- Large input handling
- Concurrent-safe singleton patterns
- Score clamping (never > 1.0 or < 0.0)
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# Unicode Edge Cases
# ============================================================

class TestUnicodeHandling:
    """Ensure Turkish and special Unicode chars don't crash scoring."""

    def test_turkish_i_variants(self):
        from risk_engine import normalize_turkish
        # All 4 Turkish I variants → all fold to lowercase 'i'
        # normalize_turkish now applies turkish_lower() first, then ASCII fold
        assert normalize_turkish("İ") == "i"   # Capital İ (dotted) → i
        assert normalize_turkish("ı") == "i"   # Lowercase ı (dotless) → i
        assert normalize_turkish("I") == "i"   # Capital I → ı (Turkish) → i
        assert normalize_turkish("i") == "i"   # Standard i (unchanged)

    def test_combined_turkish_chars(self):
        from risk_engine import normalize_turkish
        # All chars lowercased then ASCII-folded
        assert normalize_turkish("ğüşıöçİĞÜŞÖÇ") == "gusiocigusoc"

    def test_emoji_in_brand_name(self):
        """Emoji shouldn't crash, just pass through."""
        from risk_engine import normalize_turkish
        result = normalize_turkish("BRAND 🎯 NAME")
        assert "brand" in result
        assert "name" in result

    def test_rtl_arabic_chars(self):
        from risk_engine import normalize_turkish
        result = normalize_turkish("عربي")
        assert isinstance(result, str)

    def test_mixed_scripts(self):
        from risk_engine import calculate_name_similarity
        score = calculate_name_similarity("NIKE", "ナイキ")  # Japanese
        assert 0.0 <= score <= 1.0

    def test_very_long_brand_name(self):
        from risk_engine import calculate_name_similarity
        long_name = "A" * 1000
        score = calculate_name_similarity(long_name, "NIKE")
        assert 0.0 <= score <= 1.0

    def test_single_char(self):
        from risk_engine import calculate_name_similarity
        score = calculate_name_similarity("X", "Y")
        assert 0.0 <= score <= 1.0

    def test_newlines_and_tabs(self):
        from risk_engine import normalize_turkish
        result = normalize_turkish("LINE1\nLINE2\tTAB")
        assert isinstance(result, str)


# ============================================================
# Score Clamping (Regression)
# ============================================================

class TestScoreClamping:
    """Verify all scores stay in [0.0, 1.0] range."""

    def test_idf_waterfall_score_clamped(self):
        from idf_scoring import compute_idf_weighted_score
        # Returns tuple (score, details)
        score, details = compute_idf_weighted_score("NIKE NIKE NIKE", "NIKE NIKE NIKE")
        assert 0.0 <= score <= 1.0

    def test_dynamic_combine_returns_dict(self):
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(1.0, 1.0, 1.0)
        assert isinstance(result, dict)
        assert 0.0 <= result["total"] <= 1.0

    def test_dynamic_combine_all_zero(self):
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(0.0, 0.0, 0.0)
        assert result["total"] == 0.0

    def test_visual_similarity_clamped(self):
        from risk_engine import calculate_visual_similarity
        result = calculate_visual_similarity(
            clip_sim=1.0, dinov2_sim=1.0,
            color_sim=1.0, ocr_text_a="NIKE", ocr_text_b="NIKE"
        )
        assert 0.0 <= result <= 1.0

    def test_visual_similarity_all_zero(self):
        from risk_engine import calculate_visual_similarity
        result = calculate_visual_similarity(
            clip_sim=0.0, dinov2_sim=0.0,
            color_sim=0.0, ocr_text_a="", ocr_text_b=""
        )
        assert result == 0.0

    def test_score_pair_clamped(self):
        from risk_engine import score_pair
        result = score_pair("NIKE", "NIKEA", text_sim=0.85)
        assert 0.0 <= result["total"] <= 1.0

    def test_turkish_similarity_clamped(self):
        from risk_engine import calculate_name_similarity
        pairs = [
            ("", ""), ("A", "B"), ("NIKE", "NIKE"),
            ("X" * 500, "Y" * 500),
        ]
        for a, b in pairs:
            score = calculate_name_similarity(a, b)
            assert 0.0 <= score <= 1.0, f"Out of range for ({a[:20]!r}, {b[:20]!r})"

    def test_class_overlap_score_clamped(self):
        from utils.class_utils import calculate_class_overlap_score
        pairs = [
            ([], []), ([1], [1]), ([1, 2], [2, 3]),
            ([99], [5]), (list(range(1, 46)), [1]),
        ]
        for a, b in pairs:
            score = calculate_class_overlap_score(a, b)
            assert 0.0 <= score <= 1.0, f"Out of range for {a} vs {b}"


# ============================================================
# Empty / None Input Edge Cases
# ============================================================

class TestEmptyInputs:
    """Ensure functions handle empty and None inputs gracefully."""

    def test_normalize_turkish_empty(self):
        from risk_engine import normalize_turkish
        assert normalize_turkish("") == ""

    def test_containment_empty_strings(self):
        from risk_engine import check_substring_containment
        assert check_substring_containment("", "") == 0.0
        assert check_substring_containment("NIKE", "") == 0.0
        assert check_substring_containment("", "NIKE") == 0.0

    def test_token_overlap_empty(self):
        from risk_engine import calculate_token_overlap
        assert calculate_token_overlap("", "") == 0.0
        assert calculate_token_overlap("NIKE", "") == 0.0

    def test_turkish_similarity_empty(self):
        from risk_engine import calculate_name_similarity
        assert calculate_name_similarity("", "") == 0.0
        assert calculate_name_similarity("NIKE", "") == 0.0
        assert calculate_name_similarity("", "NIKE") == 0.0

    def test_idf_waterfall_empty_query(self):
        from idf_scoring import compute_idf_weighted_score
        score, details = compute_idf_weighted_score("", "NIKE")
        # Empty query may still get a small score from text similarity fallback
        assert 0.0 <= score <= 1.0

    def test_idf_waterfall_both_empty(self):
        from idf_scoring import compute_idf_weighted_score
        score, details = compute_idf_weighted_score("", "")
        # Empty vs empty = EXACT_MATCH (both normalize to same thing)
        assert 0.0 <= score <= 1.0

    def test_score_pair_empty_query(self):
        from risk_engine import score_pair
        result = score_pair("", "NIKE", text_sim=0.0)
        assert 0.0 <= result["total"] <= 1.0

    def test_deadline_none_input(self):
        from utils.deadline import calculate_appeal_deadline
        assert calculate_appeal_deadline(None) is None

    def test_language_detect_empty(self):
        from utils.translation import detect_language_fasttext
        iso, _, _ = detect_language_fasttext("")
        assert iso == "en"  # Empty → English fallback

    def test_language_detect_none(self):
        from utils.translation import detect_language_fasttext
        iso, _, _ = detect_language_fasttext(None)
        assert iso == "en"  # None → English fallback


# ============================================================
# IDF Waterfall Cases (Regression)
# ============================================================

class TestIDFWaterfallCases:
    """Ensure all IDF waterfall cases produce valid output."""

    def test_case_exact_match(self):
        from idf_scoring import compute_idf_weighted_score
        score, details = compute_idf_weighted_score("NIKE", "NIKE")
        assert details["scoring_path"] == "EXACT_MATCH"
        assert score == 1.0

    def test_case_containment(self):
        from idf_scoring import compute_idf_weighted_score
        score, details = compute_idf_weighted_score("NIKE", "NIKE SPORTS")
        assert "CONTAINMENT" in details["scoring_path"]
        assert score >= 0.85

    def test_case_generic_only(self):
        from idf_scoring import compute_idf_weighted_score
        score, details = compute_idf_weighted_score("VE LTD", "VE STI")
        assert details["scoring_path"].startswith("E") or details["scoring_path"].startswith("F")
        assert score < 0.40

    def test_all_results_have_required_fields(self):
        from idf_scoring import compute_idf_weighted_score
        pairs = [
            ("NIKE", "NIKE"), ("APPLE", "SAMSUNG"),
            ("ELMA", "ELMA MARKET"), ("VE LTD", "VE STI"),
        ]
        for q, c in pairs:
            score, details = compute_idf_weighted_score(q, c)
            assert "scoring_path" in details
            assert 0.0 <= score <= 1.0


# ============================================================
# IDFLookup Singleton
# ============================================================

class TestIDFLookupSingleton:
    """Verify IDFLookup behaves correctly as a singleton cache."""

    def test_loaded_flag(self):
        from idf_lookup import IDFLookup
        assert IDFLookup._loaded is True  # Set by conftest fixture

    def test_cache_has_entries(self):
        from idf_lookup import IDFLookup
        assert len(IDFLookup._cache) > 0

    def test_get_idf_known_word(self):
        from idf_lookup import IDFLookup
        idf = IDFLookup.get_idf("nike")
        assert idf == 8.0

    def test_get_idf_unknown_word(self):
        from idf_lookup import IDFLookup
        idf = IDFLookup.get_idf("totallyunknownword")
        # Unknown words get _default_idf (5.0)
        assert idf == IDFLookup._default_idf

    def test_get_word_class_values(self):
        from idf_lookup import IDFLookup
        assert IDFLookup.get_word_class("ve") == "generic"
        assert IDFLookup.get_word_class("patent") == "semi_generic"
        assert IDFLookup.get_word_class("nike") == "distinctive"


# ============================================================
# Dynamic Combine Regression (fixed 2026-02-09)
# ============================================================

class TestDynamicCombineRegression:
    """Verify the 3-signal dynamic combine doesn't double-count phonetic."""

    def test_no_phonetic_signal(self):
        """_dynamic_combine takes exactly 3 positional args."""
        from risk_engine import _dynamic_combine
        import inspect
        sig = inspect.signature(_dynamic_combine)
        params = list(sig.parameters.keys())
        assert len(params) == 3, f"Expected 3 params, got {params}"

    def test_text_only_signal(self):
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(0.8, 0.0, 0.0)
        assert 0.0 < result["total"] < 1.0

    def test_translation_boost(self):
        """High translation (>=0.95) should floor total at 0.90."""
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(0.3, 0.0, 0.98)
        assert result["total"] >= 0.90

    def test_visual_only(self):
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(0.0, 0.9, 0.0)
        assert 0.0 < result["total"] < 1.0

    def test_all_high(self):
        from risk_engine import _dynamic_combine
        result = _dynamic_combine(0.95, 0.90, 0.95)
        assert result["total"] >= 0.90


# ============================================================
# Settings Manager Edge Cases
# ============================================================

class TestSettingsManagerEdgeCases:
    """Test SettingsManager edge cases."""

    def test_get_before_init(self):
        from utils.settings_manager import SettingsManager
        sm = SettingsManager()
        assert sm.get("any_key") is None
        assert sm.get("any_key", default=42) == 42

    def test_multiple_invalidations(self):
        from utils.settings_manager import SettingsManager
        sm = SettingsManager()
        sm.invalidate_cache()
        sm.invalidate_cache()
        sm.invalidate_cache()
        assert sm._cache_timestamp == 0  # Still 0

    def test_zero_ttl(self):
        """TTL=0 means cache always invalid."""
        from utils.settings_manager import SettingsManager
        import time
        sm = SettingsManager(cache_ttl_seconds=0)
        sm._cache_timestamp = time.time()
        assert sm._cache_is_valid() is False
