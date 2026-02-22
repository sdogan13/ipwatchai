"""
Tests for graduated phonetic scoring (utils/phonetic.py).

Covers:
- calculate_phonetic_similarity() — main entry point
- _metaphone_overlap() — Jaccard of dMetaphone code sets
- _metaphone_code_distance() — Levenshtein on primary codes
- _turkish_voicing_similarity() — d↔t, b↔p, g↔k voicing
- _first_syllable_similarity() — initial impression emphasis
- _normalize_for_phonetic() — Turkish char folding
- _levenshtein() — edit distance
- Edge cases: empty strings, single chars, multi-word names
"""
import sys
import os

import pytest


from utils.phonetic import (
    calculate_phonetic_similarity,
    _metaphone_overlap,
    _metaphone_code_distance,
    _turkish_voicing_similarity,
    _first_syllable_similarity,
    _normalize_for_phonetic,
    _extract_first_syllable,
    _levenshtein,
    _apply_voicing_map,
)


# ============================================================
# Normalization
# ============================================================

class TestNormalization:
    def test_turkish_chars_folded(self):
        assert _normalize_for_phonetic("DOĞAN") == "dogan"

    def test_turkish_i_handling(self):
        assert _normalize_for_phonetic("İSTANBUL") == "istanbul"

    def test_empty_string(self):
        assert _normalize_for_phonetic("") == ""

    def test_none_handling(self):
        assert _normalize_for_phonetic(None) == ""

    def test_strips_non_alphanumeric(self):
        assert _normalize_for_phonetic("A-B C") == "abc"

    def test_uppercase_i_becomes_i_dotless(self):
        # Turkish I (without dot) -> ı -> i after folding
        result = _normalize_for_phonetic("I")
        assert result == "i"


# ============================================================
# Levenshtein Distance
# ============================================================

class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("abc", "abc") == 0

    def test_empty_vs_nonempty(self):
        assert _levenshtein("", "abc") == 3

    def test_single_substitution(self):
        assert _levenshtein("abc", "adc") == 1

    def test_deletion(self):
        assert _levenshtein("abcd", "abc") == 1

    def test_insertion(self):
        assert _levenshtein("abc", "abcd") == 1


# ============================================================
# First Syllable Extraction
# ============================================================

class TestFirstSyllable:
    def test_samsung(self):
        assert _extract_first_syllable("samsung") == "sam"

    def test_nike(self):
        assert _extract_first_syllable("nike") == "nik"

    def test_apple(self):
        assert _extract_first_syllable("apple") == "ap"

    def test_single_consonant(self):
        assert _extract_first_syllable("b") == "b"

    def test_empty(self):
        assert _extract_first_syllable("") == ""

    def test_multiword_uses_first(self):
        assert _extract_first_syllable("red bull") == "red"


# ============================================================
# Turkish Voicing Map
# ============================================================

class TestVoicingMap:
    def test_d_to_t(self):
        # d→t AND g→k both apply: "dogan" → "tokan"
        assert _apply_voicing_map("dogan") == "tokan"

    def test_b_to_p(self):
        assert _apply_voicing_map("balik") == "palik"

    def test_g_to_k(self):
        assert _apply_voicing_map("gunes") == "kunes"

    def test_unchanged_vowels(self):
        assert _apply_voicing_map("aeiou") == "aeiou"


# ============================================================
# Individual Signals
# ============================================================

class TestMetaphoneOverlap:
    def test_identical_words(self):
        score = _metaphone_overlap("nike", "nike")
        assert score == 1.0

    def test_different_words(self):
        score = _metaphone_overlap("apple", "zebra")
        assert score == 0.0

    def test_empty_input(self):
        assert _metaphone_overlap("", "nike") == 0.0
        assert _metaphone_overlap("nike", "") == 0.0


class TestMetaphoneCodeDistance:
    def test_identical(self):
        score = _metaphone_code_distance("nike", "nike")
        assert score == 1.0

    def test_similar_codes(self):
        # Similar sounding words should have high code similarity
        score = _metaphone_code_distance("nike", "naik")
        assert score > 0.5

    def test_empty_input(self):
        assert _metaphone_code_distance("", "nike") == 0.0


class TestTurkishVoicing:
    def test_d_t_pair(self):
        # dogan vs togan — should be high after d→t mapping
        score = _turkish_voicing_similarity("dogan", "togan")
        assert score == 1.0

    def test_b_p_pair(self):
        score = _turkish_voicing_similarity("balik", "palik")
        assert score == 1.0

    def test_g_k_pair(self):
        score = _turkish_voicing_similarity("gunes", "kunes")
        assert score == 1.0

    def test_unrelated(self):
        score = _turkish_voicing_similarity("apple", "zebra")
        assert score < 0.4

    def test_empty(self):
        assert _turkish_voicing_similarity("", "abc") == 0.0


class TestFirstSyllableSimilarity:
    def test_same_first_syllable(self):
        score = _first_syllable_similarity("samsung", "samsun")
        assert score == 1.0

    def test_different_first_syllable(self):
        score = _first_syllable_similarity("nike", "adidas")
        assert score < 0.5

    def test_empty(self):
        assert _first_syllable_similarity("", "abc") == 0.0


# ============================================================
# Main Entry Point: calculate_phonetic_similarity
# ============================================================

class TestCalculatePhoneticSimilarity:
    def test_exact_match_returns_one(self):
        assert calculate_phonetic_similarity("NIKE", "NIKE") == 1.0

    def test_empty_returns_zero(self):
        assert calculate_phonetic_similarity("", "NIKE") == 0.0
        assert calculate_phonetic_similarity("NIKE", "") == 0.0
        assert calculate_phonetic_similarity("", "") == 0.0

    def test_none_returns_zero(self):
        assert calculate_phonetic_similarity(None, "NIKE") == 0.0
        assert calculate_phonetic_similarity("NIKE", None) == 0.0

    def test_nike_vs_naik_graduated(self):
        """NIKE vs NAIK: should produce intermediate score, not binary 1.0."""
        score = calculate_phonetic_similarity("NIKE", "NAIK")
        assert 0.4 < score < 0.95, f"Expected intermediate, got {score}"

    def test_samsung_vs_samsun(self):
        """SAMSUNG vs SAMSUN: nearly identical, should be high."""
        score = calculate_phonetic_similarity("SAMSUNG", "SAMSUN")
        assert score >= 0.65, f"Expected high, got {score}"

    def test_dogan_vs_togan_turkish_voicing(self):
        """DOGAN vs TOGAN: Turkish d↔t voicing should produce high score."""
        score = calculate_phonetic_similarity("DOGAN", "TOGAN")
        assert score >= 0.5, f"Expected >=0.5 for voicing pair, got {score}"

    def test_gunes_vs_kunes_turkish_voicing(self):
        """GUNES vs KUNES: Turkish g↔k voicing."""
        score = calculate_phonetic_similarity("GUNES", "KUNES")
        assert score >= 0.5, f"Expected >=0.5 for voicing pair, got {score}"

    def test_completely_different(self):
        """Unrelated names should score low."""
        score = calculate_phonetic_similarity("APPLE", "ZEBRA")
        assert score < 0.4, f"Expected low for unrelated, got {score}"

    def test_turkish_chars_normalized(self):
        """Turkish chars should be folded before comparison."""
        score1 = calculate_phonetic_similarity("DOĞAN", "DOGAN")
        assert score1 == 1.0  # These are the same after normalization

    def test_case_insensitive(self):
        s1 = calculate_phonetic_similarity("Nike", "NIKE")
        assert s1 == 1.0

    def test_multiword_names(self):
        """Multi-word names should still produce reasonable scores."""
        score = calculate_phonetic_similarity("RED BULL", "RED BOLL")
        assert score > 0.5

    def test_single_char_names(self):
        """Single character names should not crash."""
        score = calculate_phonetic_similarity("A", "A")
        assert score == 1.0
        score2 = calculate_phonetic_similarity("A", "B")
        assert 0.0 <= score2 <= 1.0

    def test_score_between_zero_and_one(self):
        """All scores must be in [0.0, 1.0] range."""
        pairs = [
            ("NIKE", "NAIK"), ("BOSCH", "BOSS"), ("SAMSUNG", "SAMSUN"),
            ("APPLE", "APPEL"), ("GOOGLE", "GUGLE"), ("X", "Y"),
        ]
        for a, b in pairs:
            score = calculate_phonetic_similarity(a, b)
            assert 0.0 <= score <= 1.0, f"Out of range for {a}/{b}: {score}"

    def test_symmetry(self):
        """Phonetic similarity should be symmetric: sim(a,b) == sim(b,a)."""
        pairs = [
            ("NIKE", "NAIK"), ("DOGAN", "TOGAN"), ("SAMSUNG", "SAMSUN"),
        ]
        for a, b in pairs:
            assert calculate_phonetic_similarity(a, b) == calculate_phonetic_similarity(b, a)

    def test_bosch_vs_boss(self):
        """BOSCH vs BOSS: partial overlap."""
        score = calculate_phonetic_similarity("BOSCH", "BOSS")
        assert 0.3 < score < 0.85, f"Expected intermediate for BOSCH/BOSS, got {score}"

    def test_nike_vs_mike(self):
        """NIKE vs MIKE: different initial but similar structure."""
        score = calculate_phonetic_similarity("NIKE", "MIKE")
        assert 0.2 < score < 0.7, f"Expected moderate for NIKE/MIKE, got {score}"
