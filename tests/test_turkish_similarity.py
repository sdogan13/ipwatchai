"""
Tests for Turkish text comparison functions in risk_engine.py:
- normalize_turkish()
- check_substring_containment()
- calculate_token_overlap()
- calculate_name_similarity()
"""



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

    def test_extended_latin_diacritic_fold(self):
        assert normalize_turkish("meyâl") == "meyal"


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

    def test_containment_penalized_by_extra_words(self):
        """'ELMA' in 'ELMA DÜNYASI' → no longer 1.0, penalized by extra word."""
        score = calculate_name_similarity("ELMA", "ELMA DÜNYASI")
        assert 0.50 <= score <= 0.85, f"Containment with extra word should be moderate, got {score}"

    def test_reverse_containment_similar_score(self):
        """Bidirectional: 'ELMA DÜNYASI' vs 'ELMA' → close to forward (small asymmetry OK)."""
        fwd = calculate_name_similarity("ELMA", "ELMA DÜNYASI")
        rev = calculate_name_similarity("ELMA DÜNYASI", "ELMA")
        assert abs(fwd - rev) < 0.10, f"Forward {fwd} and reverse {rev} should be close"

    def test_completely_different(self):
        """'ELMA' vs 'KAPLAN' → low score."""
        score = calculate_name_similarity("ELMA", "KAPLAN")
        assert score < 0.50

    def test_token_overlap_partial(self):
        """'NIKE SPORTS' vs 'NIKE FASHION' → partial match."""
        score = calculate_name_similarity("NIKE SPORTS", "NIKE FASHION")
        assert 0.40 <= score <= 0.80

    def test_sequencematcher_similar(self):
        """'NIKEA' vs 'NIKE' — near match but different word."""
        score = calculate_name_similarity("NIKEA", "NIKE")
        assert score >= 0.35, f"Near match should score moderately, got {score}"

    def test_empty_query(self):
        assert calculate_name_similarity("", "NIKE") == 0.0

    def test_empty_target(self):
        assert calculate_name_similarity("NIKE", "") == 0.0

    def test_both_empty(self):
        assert calculate_name_similarity("", "") == 0.0

    def test_returns_multilevel_score(self):
        """Result uses multi-level scoring, not simple max of methods."""
        # "DOGAN PATENT" vs "D.P DOGAN PATENT"
        # Word overlap: {dogan, patent} match, {d.p} extra in target → penalized
        score = calculate_name_similarity("DOGAN PATENT", "D.P DOGAN PATENT")
        assert 0.60 <= score <= 0.90, f"Multi-level score should be moderate-high, got {score}"

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
