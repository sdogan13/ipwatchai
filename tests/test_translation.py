"""
Tests for translation: language detection, translate_to_turkish,
score_translated_pair, calculate_translation_similarity.

All NLLB model calls are mocked — tests run without GPU or model downloads.
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.translation import (
    detect_language,
    translate_to_turkish,
    score_translated_pair,
    calculate_translation_similarity,
    get_translations,
    get_search_variants,
    get_cross_language_similarity,
    COMMON_TURKISH_WORDS,
    NLLB_LANG_MAP,
    TURKISH_CHARS,
)


# ============================================================
# Language Detection
# ============================================================

class TestLanguageDetection:
    """Test detect_language() heuristic chain."""

    def test_turkish_chars_detected(self):
        assert detect_language("ÇAĞDAŞ") == "tr"
        assert detect_language("İSTANBUL") == "tr"
        assert detect_language("ŞEKER") == "tr"

    def test_turkish_dotless_i(self):
        assert detect_language("IŞIK") == "tr"

    def test_turkish_g_breve(self):
        assert detect_language("DOĞAN") == "tr"

    def test_english_default_for_ascii(self):
        """Pure ASCII without Turkish words → English."""
        assert detect_language("APPLE") == "en"
        assert detect_language("BRAND NAME") == "en"

    def test_kurdish_chars(self):
        """Kurdish Kurmanji chars (ê, î, û)."""
        assert detect_language("Sêv") == "ku"
        assert detect_language("gûl") == "ku"

    def test_farsi_chars(self):
        """Farsi-specific chars (پ, چ, ژ, گ, ک, ی)."""
        assert detect_language("پنیر") == "fa"

    def test_arabic_chars(self):
        """Arabic characters."""
        assert detect_language("تفاح") == "ar"

    def test_german_chars(self):
        """German umlauts and ß."""
        assert detect_language("Äpfel") == "de"
        assert detect_language("Straße") == "de"

    def test_russian_cyrillic(self):
        assert detect_language("Яблоко") == "ru"

    def test_chinese_cjk(self):
        assert detect_language("苹果") == "zh"

    def test_common_turkish_word_lookup(self):
        """ASCII Turkish words detected via COMMON_TURKISH_WORDS set."""
        assert detect_language("ELMA") == "tr"
        assert detect_language("ASLAN") == "tr"
        assert detect_language("KAPLAN") == "tr"
        assert detect_language("SU") == "tr"

    def test_empty_string(self):
        assert detect_language("") == "unknown"

    def test_none_like(self):
        assert detect_language(None) == "unknown"

    def test_numbers_default_english(self):
        """Numbers only → English (no special chars)."""
        assert detect_language("123") == "en"

    def test_mixed_lang_turkish_wins(self):
        """Turkish chars present → Turkish regardless of other chars."""
        assert detect_language("İstanbul City") == "tr"

    def test_priority_order(self):
        """Turkish checked before Kurdish before Arabic."""
        # ö is in both Turkish and German, but Turkish is checked first
        assert detect_language("Ö") == "tr"

    def test_common_turkish_words_set_has_entries(self):
        """COMMON_TURKISH_WORDS should have a good number of entries."""
        assert len(COMMON_TURKISH_WORDS) > 100


# ============================================================
# translate_to_turkish (mocked NLLB)
# ============================================================

class TestTranslateToTurkish:
    """Test translate_to_turkish() with mocked NLLB model."""

    def test_already_turkish_returns_lowercase(self):
        """Turkish text → lowercase, no model call."""
        # Clear lru_cache
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("ELMA")
        # "ELMA" detected as Turkish (in COMMON_TURKISH_WORDS) → returns "elma"
        assert result == "elma"

    def test_empty_returns_empty(self):
        translate_to_turkish.cache_clear()
        assert translate_to_turkish("") == ""

    def test_whitespace_only_returns_empty(self):
        translate_to_turkish.cache_clear()
        assert translate_to_turkish("   ") == ""

    @patch("utils.translation.translate", return_value="elma")
    def test_english_translated(self, mock_translate):
        """English word → NLLB translates to Turkish."""
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("APPLE")
        assert result == "elma"

    @patch("utils.translation.translate", return_value=None)
    def test_nllb_failure_fallback(self, mock_translate):
        """Model returns None → fallback to text.lower()."""
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("BRANDX")
        assert result == "brandx"

    @patch("utils.translation.translate", return_value="yıldız")
    def test_caches_result(self, mock_translate):
        """Same input twice → model called only once (lru_cache)."""
        translate_to_turkish.cache_clear()
        r1 = translate_to_turkish("STAR")
        r2 = translate_to_turkish("STAR")
        assert r1 == r2
        assert mock_translate.call_count == 1


# ============================================================
# score_translated_pair (IDF waterfall on translations)
# ============================================================

class TestScoreTranslatedPair:
    """Test score_translated_pair() uses IDF waterfall."""

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

    def test_containment_high_score(self):
        """'elma' in 'elma market' → high score."""
        result = score_translated_pair("elma", "elma market")
        assert result["translation_similarity"] >= 0.85

    def test_no_overlap_low_score(self):
        """Completely unrelated words → low score."""
        result = score_translated_pair("elma", "kaplan")
        assert result["translation_similarity"] < 0.50

    def test_returns_scoring_path(self):
        result = score_translated_pair("elma", "elma")
        assert "translation_scoring_path" in result

    def test_returns_text_sim(self):
        result = score_translated_pair("elma", "kaplan")
        assert "translation_text_sim" in result

    def test_generic_only_capped_low(self):
        """Only generic words match → low score."""
        result = score_translated_pair("ve ltd", "ve sti")
        assert result["translation_similarity"] < 0.40

    def test_score_always_in_0_to_1(self):
        pairs = [
            ("elma", "elma"), ("", ""), ("elma", "kaplan"),
            ("kirmizi elma", "yesil elma"),
        ]
        for q, c in pairs:
            result = score_translated_pair(q, c)
            s = result["translation_similarity"]
            assert 0.0 <= s <= 1.0, f"Score {s} out of range for ({q!r}, {c!r})"


# ============================================================
# calculate_translation_similarity (main entry point)
# ============================================================

class TestCalculateTranslationSimilarity:
    """Test the main translation similarity function."""

    def test_null_name_tr_returns_0(self):
        assert calculate_translation_similarity("APPLE", "ELMA", candidate_name_tr=None) == 0.0

    def test_empty_name_tr_returns_0(self):
        assert calculate_translation_similarity("APPLE", "ELMA", candidate_name_tr="") == 0.0

    def test_empty_query_returns_0(self):
        assert calculate_translation_similarity("", "ELMA", candidate_name_tr="elma") == 0.0

    def test_no_name_tr_arg_returns_0(self):
        """Old callers without candidate_name_tr → 0.0."""
        assert calculate_translation_similarity("APPLE", "ELMA") == 0.0

    def test_same_language_turkish(self):
        """Turkish query + Turkish candidate → works via IDF waterfall."""
        translate_to_turkish.cache_clear()
        score = calculate_translation_similarity(
            "ELMA", "ELMA MARKET", candidate_name_tr="elma market"
        )
        # "ELMA" → detected Turkish → translate_to_turkish returns "elma"
        # score_translated_pair("elma", "elma market") → containment → high score
        assert score >= 0.80

    @patch("utils.translation.translate_to_turkish", return_value="elma")
    def test_cross_language_match(self, mock_ttt):
        """English 'APPLE' → Turkish 'elma' → matches candidate name_tr 'elma'."""
        score = calculate_translation_similarity(
            "APPLE", "ELMA", candidate_name_tr="elma"
        )
        assert score >= 0.95  # Exact match in Turkish

    @patch("utils.translation.translate_to_turkish", return_value="")
    def test_translation_returns_empty(self, mock_ttt):
        """translate_to_turkish returns empty → 0.0."""
        score = calculate_translation_similarity(
            "XYZ", "ABC", candidate_name_tr="abc"
        )
        assert score == 0.0


# ============================================================
# NLLB Language Map Coverage
# ============================================================

class TestNLLBLangMap:
    """Test NLLB language code mapping."""

    def test_primary_languages_mapped(self):
        assert "tr" in NLLB_LANG_MAP
        assert "en" in NLLB_LANG_MAP
        assert "ku" in NLLB_LANG_MAP
        assert "fa" in NLLB_LANG_MAP
        assert "ar" in NLLB_LANG_MAP

    def test_european_languages_mapped(self):
        for code in ["de", "fr", "es", "it", "nl", "ru"]:
            assert code in NLLB_LANG_MAP

    def test_nllb_codes_are_strings(self):
        for code in NLLB_LANG_MAP.values():
            assert isinstance(code, str)
            assert len(code) > 0


# ============================================================
# get_cross_language_similarity (legacy wrapper)
# ============================================================

class TestGetCrossLanguageSimilarity:
    """Test the legacy cross-language similarity wrapper."""

    def test_returns_tuple(self):
        score, match_type = get_cross_language_similarity("ELMA", "ELMA", candidate_name_tr="elma")
        assert isinstance(score, float)
        assert isinstance(match_type, str)

    def test_no_name_tr_returns_zero(self):
        score, match_type = get_cross_language_similarity("APPLE", "ELMA")
        assert score == 0.0
        assert match_type == "none"
