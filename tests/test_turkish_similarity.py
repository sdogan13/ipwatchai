"""
Tests for Turkish text comparison functions in risk_engine.py:
- normalize_turkish()
- check_substring_containment()
- calculate_token_overlap()
- calculate_name_similarity()
"""
import sys
import os

import pytest


from risk_engine import (
    normalize_turkish,
    check_substring_containment,
    calculate_token_overlap,
    calculate_name_similarity,
)


# ============================================================
# Turkish Normalization
# ============================================================

class TestNormalizeTurkish:
    """Test normalize_turkish() char replacement.

    normalize_turkish now applies turkish_lower() first (İ→i, I→ı)
    then folds all Turkish chars to ASCII, always returning lowercase.
    """

    def test_g_breve(self):
        assert normalize_turkish("ğ") == "g"
        assert normalize_turkish("Ğ") == "g"  # lowercased then folded

    def test_dotless_i(self):
        assert normalize_turkish("ı") == "i"

    def test_i_with_dot(self):
        assert normalize_turkish("İ") == "i"  # İ→i (Turkish dotted)

    def test_capital_i(self):
        """Turkish: uppercase I is undotted, lowercases to ı, then folds to i."""
        assert normalize_turkish("I") == "i"

    def test_o_umlaut(self):
        assert normalize_turkish("ö") == "o"
        assert normalize_turkish("Ö") == "o"  # lowercased then folded

    def test_u_umlaut(self):
        assert normalize_turkish("ü") == "u"

    def test_s_cedilla(self):
        assert normalize_turkish("ş") == "s"

    def test_c_cedilla(self):
        assert normalize_turkish("ç") == "c"

    def test_full_word(self):
        assert normalize_turkish("DOĞAN") == "dogan"  # always lowercase

    def test_empty_string(self):
        assert normalize_turkish("") == ""

    def test_no_turkish_chars(self):
        assert normalize_turkish("APPLE") == "apple"  # always lowercase

    def test_mixed(self):
        assert normalize_turkish("İSTANBUL ŞEHRİ") == "istanbul sehri"


# ============================================================
# Substring Containment
# ============================================================

class TestCheckSubstringContainment:
    """Test check_substring_containment()."""

    def test_query_in_target(self):
        assert check_substring_containment("nike", "nike sports") == 1.0

    def test_target_in_query(self):
        assert check_substring_containment("nike sports", "nike") == 1.0

    def test_no_containment(self):
        assert check_substring_containment("nike", "adidas") == 0.0

    def test_turkish_normalization(self):
        """'dogan' should match 'doğan patent' after normalization."""
        assert check_substring_containment("dogan", "doğan patent") == 1.0

    def test_case_insensitive(self):
        assert check_substring_containment("Nike", "NIKE SPORTS") == 1.0

    def test_exact_match(self):
        assert check_substring_containment("NIKE", "NIKE") == 1.0

    def test_empty_query(self):
        assert check_substring_containment("", "NIKE") == 0.0

    def test_empty_target(self):
        assert check_substring_containment("NIKE", "") == 0.0

    def test_both_empty(self):
        assert check_substring_containment("", "") == 0.0


# ============================================================
# Token Overlap
# ============================================================

class TestCalculateTokenOverlap:
    """Test calculate_token_overlap()."""

    def test_identical(self):
        assert calculate_token_overlap("DOGAN PATENT", "DOGAN PATENT") == 1.0

    def test_full_overlap_different_order(self):
        assert calculate_token_overlap("DOGAN PATENT", "PATENT DOGAN") == 1.0

    def test_partial_overlap(self):
        result = calculate_token_overlap("DOGAN PATENT", "DOGAN MARKA")
        assert abs(result - 0.5) < 0.01  # 1 of 2 tokens match

    def test_no_overlap(self):
        assert calculate_token_overlap("NIKE", "ADIDAS") == 0.0

    def test_turkish_normalization(self):
        """'doğan' matches 'dogan' after normalization."""
        assert calculate_token_overlap("doğan patent", "dogan marka") == 0.5

    def test_empty_query(self):
        assert calculate_token_overlap("", "NIKE") == 0.0

    def test_superset_target(self):
        """All query tokens in target → 1.0."""
        assert calculate_token_overlap("DOGAN", "DOGAN PATENT MARKA") == 1.0


# ============================================================
# Turkish Similarity (Combined)
# ============================================================

class TestCalculateTurkishSimilarity:
    """Test calculate_name_similarity() — max of 3 methods."""

    def test_identical_strings(self):
        assert calculate_name_similarity("ELMA", "ELMA") == 1.0

    def test_case_insensitive(self):
        assert calculate_name_similarity("Nike", "NIKE") == 1.0

    def test_turkish_char_normalization(self):
        """'ÇAĞDAŞ' vs 'CAGDAS' should match after normalization."""
        assert calculate_name_similarity("ÇAĞDAŞ", "CAGDAS") == 1.0

    def test_containment_returns_1(self):
        """'ELMA' in 'ELMA DÜNYASI' → 1.0."""
        assert calculate_name_similarity("ELMA", "ELMA DÜNYASI") == 1.0

    def test_reverse_containment(self):
        """'ELMA' (target) is substring of 'ELMA DÜNYASI' (query) → 1.0."""
        assert calculate_name_similarity("ELMA DÜNYASI", "ELMA") == 1.0

    def test_completely_different(self):
        """'ELMA' vs 'KAPLAN' → low score."""
        score = calculate_name_similarity("ELMA", "KAPLAN")
        assert score < 0.50

    def test_token_overlap_partial(self):
        """'NIKE SPORTS' vs 'NIKE FASHION' → partial match."""
        score = calculate_name_similarity("NIKE SPORTS", "NIKE FASHION")
        assert 0.40 <= score <= 0.80

    def test_sequencematcher_similar(self):
        """'NIKEA' vs 'NIKE' — high sequence similarity."""
        score = calculate_name_similarity("NIKEA", "NIKE")
        assert score >= 0.70  # containment: "nike" in "nikea"

    def test_empty_query(self):
        assert calculate_name_similarity("", "NIKE") == 0.0

    def test_empty_target(self):
        assert calculate_name_similarity("NIKE", "") == 0.0

    def test_both_empty(self):
        assert calculate_name_similarity("", "") == 0.0

    def test_returns_max_of_methods(self):
        """Result should be the max of SequenceMatcher, containment, token overlap."""
        # "DOGAN PATENT" vs "D.P DOGAN PATENT"
        # Token overlap: {"dogan","patent"} vs {"d.p","dogan","patent"} → 2/2 = 1.0
        # Containment: "dogan patent" in "d.p dogan patent" → 1.0
        score = calculate_name_similarity("DOGAN PATENT", "D.P DOGAN PATENT")
        assert score == 1.0

    def test_output_between_0_and_1(self):
        pairs = [
            ("NIKE", "ADIDAS"),
            ("APPLE", "APPLE TECH"),
            ("X", "Y"),
            ("KIRMIZI ELMA", "YESIL ELMA"),
        ]
        for q, t in pairs:
            score = calculate_name_similarity(q, t)
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for ({q!r}, {t!r})"
