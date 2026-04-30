"""
Tests for translation: language detection, translate_to_turkish,
score_translated_pair, calculate_translation_similarity.

All NLLB model calls are mocked — tests run without GPU or model downloads.
"""
import sys
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest

import utils.translation as translation

FIXTURE_ROOT = Path("tests/fixtures/model_cache").resolve()

from utils.translation import (
    detect_language_fasttext,
    translate_to_turkish,
    score_translated_pair,
    calculate_translation_similarity,
    get_translations,
    get_search_variants,
    get_cross_language_similarity,
    NLLB_LANG_MAP,
    get_translation_backend_info,
    build_translation_provenance,
    has_prompt_leakage,
)


# ============================================================
# Language Detection (FastText-based)
# ============================================================

def _mock_fasttext_predict(text):
    """Mock FastText predictions for common test cases."""
    # Simulate realistic FastText responses
    _predictions = {
        "APPLE": ("__label__eng_Latn", [0.95]),
        "apple": ("__label__eng_Latn", [0.95]),
        "BRAND NAME": ("__label__eng_Latn", [0.92]),
        "brand name": ("__label__eng_Latn", [0.92]),
        "ÇAĞDAŞ": ("__label__tur_Latn", [0.98]),
        "çağdaş": ("__label__tur_Latn", [0.98]),
        "İSTANBUL": ("__label__tur_Latn", [0.97]),
        "istanbul": ("__label__tur_Latn", [0.85]),
        "ŞEKER": ("__label__tur_Latn", [0.99]),
        "şeker": ("__label__tur_Latn", [0.99]),
        "IŞIK": ("__label__tur_Latn", [0.96]),
        "ışık": ("__label__tur_Latn", [0.96]),
        "DOĞAN": ("__label__tur_Latn", [0.95]),
        "doğan": ("__label__tur_Latn", [0.95]),
        "DOĞAN electronics": ("__label__eng_Latn", [0.55]),
        "doğan electronics": ("__label__eng_Latn", [0.55]),
        "123": ("__label__eng_Latn", [0.30]),
        "İstanbul City": ("__label__tur_Latn", [0.88]),
        "istanbul city": ("__label__tur_Latn", [0.88]),
        "پنیر": ("__label__fas_Arab", [0.94]),
        "تفاح": ("__label__ara_Arab", [0.96]),
        "Яблоко": ("__label__rus_Cyrl", [0.99]),
        "яблоко": ("__label__rus_Cyrl", [0.99]),
        "苹果": ("__label__zho_Hans", [0.99]),
        "Äpfel": ("__label__deu_Latn", [0.91]),
        "äpfel": ("__label__deu_Latn", [0.91]),
        "Straße": ("__label__deu_Latn", [0.95]),
        "straße": ("__label__deu_Latn", [0.95]),
        "Sêv": ("__label__kmr_Latn", [0.72]),
        "sêv": ("__label__kmr_Latn", [0.72]),
        "gûl": ("__label__kmr_Latn", [0.75]),
    }
    clean = text.replace('\n', ' ').strip()
    if clean in _predictions:
        label, score = _predictions[clean]
        return ([label], score)
    # Default: English with low confidence
    return (["__label__eng_Latn"], [0.50])


class TestLanguageDetection:
    """Test detect_language_fasttext() — FastText-only detection."""

    @patch("utils.translation._load_fasttext_langid")
    def test_turkish_detected(self, mock_load):
        mock_model = MagicMock()
        mock_model.predict = _mock_fasttext_predict
        mock_load.return_value = mock_model
        iso, _, _ = detect_language_fasttext("ÇAĞDAŞ")
        assert iso == "tr"
        iso, _, _ = detect_language_fasttext("ŞEKER")
        assert iso == "tr"

    @patch("utils.translation._load_fasttext_langid")
    def test_english_for_ascii(self, mock_load):
        mock_model = MagicMock()
        mock_model.predict = _mock_fasttext_predict
        mock_load.return_value = mock_model
        iso, _, _ = detect_language_fasttext("APPLE")
        assert iso == "en"

    def test_empty_string(self):
        iso, _, _ = detect_language_fasttext("")
        assert iso == "en"

    def test_none_like(self):
        iso, _, _ = detect_language_fasttext(None)
        assert iso == "en"

    @patch("utils.translation._load_fasttext_langid")
    def test_low_confidence_returns_unknown(self, mock_load):
        """Non-EN/non-TR with confidence < 0.7 → English fallback."""
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__pol_Latn"], [0.45])
        mock_load.return_value = mock_model
        iso, _, conf = detect_language_fasttext("cicek masali")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_high_confidence_non_english_kept(self, mock_load):
        """Non-EN/non-TR with confidence >= 0.7 → keep detected lang."""
        mock_model = MagicMock()
        mock_model.predict = _mock_fasttext_predict
        mock_load.return_value = mock_model
        iso, _, _ = detect_language_fasttext("Sêv")
        assert iso == "ku"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_returns_unknown(self, mock_load):
        """FastText model not available → English fallback."""
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("any text")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_does_not_guess_turkish(self, mock_load):
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("ŞEKER")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_does_not_guess_arabic(self, mock_load):
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("تفاح")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_does_not_guess_persian(self, mock_load):
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("سیب")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_does_not_guess_mixed_descriptor_case(self, mock_load):
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("DOĞAN electronics")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_model_unavailable_does_not_guess_ascii_brand(self, mock_load):
        mock_load.return_value = None
        iso, _, _ = detect_language_fasttext("NAIK")
        assert iso == "unknown"

    @patch("utils.translation._load_fasttext_langid")
    def test_returns_confidence(self, mock_load):
        mock_model = MagicMock()
        mock_model.predict = _mock_fasttext_predict
        mock_load.return_value = mock_model
        iso, nllb, conf = detect_language_fasttext("APPLE")
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0


# ============================================================
# translate_to_turkish (mocked NLLB)
# ============================================================

class TestTranslateToTurkish:
    """Test translate_to_turkish() with mocked translation backends."""

    @patch("utils.translation.TRANSLATION_BACKEND", "nllb")
    @patch("utils.translation.translate")
    @patch("utils.translation._load_fasttext_langid")
    def test_explicit_nllb_backend_preserves_detected_turkish(self, mock_load, mock_translate):
        """Turkish text → lowercase, no model call."""
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__tur_Latn"], [0.95])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("ŞEKER")
        # FastText detects as Turkish → returns turkish_lower
        assert result == "şeker"

    def test_empty_returns_empty(self):
        translate_to_turkish.cache_clear()
        assert translate_to_turkish("") == ""

    def test_whitespace_only_returns_empty(self):
        translate_to_turkish.cache_clear()
        assert translate_to_turkish("   ") == ""

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value="elma")
    def test_english_translated(self, mock_translate, mock_load):
        """English word → NLLB translates to Turkish."""
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.95])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("APPLE")
        assert result == "elma"

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value=None)
    def test_nllb_failure_fallback(self, mock_translate, mock_load):
        """Model returns None → fallback to text.lower()."""
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.90])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("BRANDX")
        assert result == "brandx"

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value="yıldız")
    def test_caches_result(self, mock_translate, mock_load):
        """Same input twice → model called only once (lru_cache)."""
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.90])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        r1 = translate_to_turkish("STAR")
        r2 = translate_to_turkish("STAR")
        assert r1 == r2
        assert mock_translate.call_count == 1

    @patch("utils.translation.detect_language_fasttext", return_value=("en", "eng_Latn", 0.95))
    @patch("utils.translation.translate", return_value="elma")
    def test_madlad_backend_supported(self, mock_translate, mock_detect):
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("APPLE", backend="madlad")
        assert result == "elma"
        assert mock_translate.call_args.kwargs["backend"] == "madlad"

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value="elma")
    def test_default_live_backend_translates_with_madlad(self, mock_translate, mock_load):
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.95])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("APPLE")
        assert result == "elma"
        assert mock_translate.call_args.kwargs["backend"] == "madlad"

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value=None)
    def test_madlad_failure_falls_back_to_original_text(self, mock_translate, mock_load):
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.90])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("BRANDX")
        assert result == "brandx"
        assert mock_translate.call_args.kwargs["backend"] == "madlad"

    @patch("utils.translation._load_fasttext_langid")
    @patch("utils.translation.translate", return_value=None)
    def test_explicit_nllb_failure_fallback(self, mock_translate, mock_load):
        mock_model = MagicMock()
        mock_model.predict = lambda t: (["__label__eng_Latn"], [0.90])
        mock_load.return_value = mock_model
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("BRANDX", backend="nllb")
        assert result == "brandx"
        assert mock_translate.call_args.kwargs["backend"] == "nllb"

    @patch("utils.translation.detect_language_fasttext", return_value=("tr", "tur_Latn", 0.99))
    @patch("utils.translation.translate", return_value="translated")
    def test_default_live_backend_still_calls_model_for_detected_turkish(self, mock_translate, mock_detect):
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("SEKER")
        assert result == "translated"
        assert mock_translate.call_args.kwargs["backend"] == "madlad"

    @patch("utils.translation.detect_language_fasttext", return_value=("tr", "tur_Latn", 0.99))
    @patch("utils.translation.translate", return_value="çeviri")
    def test_madlad_backend_still_calls_model_for_detected_turkish(self, mock_translate, mock_detect):
        translate_to_turkish.cache_clear()
        result = translate_to_turkish("ŞEKER", backend="madlad")
        assert result == "çeviri"
        assert mock_translate.call_args.kwargs["backend"] == "madlad"


class TestBatchTranslateToTurkishMadlad:
    @patch("utils.translation.detect_language_fasttext")
    @patch("utils.translation.batch_translate")
    def test_uses_madlad_backend_for_all_rows_regardless_of_detected_lang(self, mock_batch_translate, mock_detect):
        mock_detect.side_effect = [
            ("en", "eng_Latn", 0.95),
            ("tr", "tur_Latn", 0.99),
        ]
        mock_batch_translate.return_value = ["elma", "şeker çeviri"]

        results = translation.batch_translate_to_turkish(["APPLE", "DOĞAN"], backend="madlad")

        assert results == [("elma", "en"), ("şeker çeviri", "tr")]
        assert mock_batch_translate.call_args.kwargs["backend"] == "madlad"


class TestMadladPostprocessing:
    def test_guard_prompt_can_override_brand_like_false_translation(self):
        result = translation._pick_madlad_candidate(
            original_text="NAIK",
            model_input="naik",
            primary_decoded="Yükselt",
            guard_decoded="Türkçeye çeviri: naik",
        )
        assert result is None

    def test_guard_prompt_removes_color_suffix_noise(self):
        result = translation._pick_madlad_candidate(
            original_text="GOLD",
            model_input="gold",
            primary_decoded="altıncolor",
            guard_decoded="Türkçeye çeviri: altın",
        )
        assert result == "altın"

    def test_postprocess_strips_prompt_prefix_and_punctuation(self):
        result = translation._postprocess_translation("Türkçeye çeviri: elma.", "apple")
        assert result == "elma"

    def test_postprocess_strips_generic_language_prefixed_prompt(self):
        result = translation._postprocess_translation("Almanca'ya çeviri: elma.", "apple")
        assert result == "elma"

    def test_postprocess_strips_nested_prompt_prefixes_iteratively(self):
        result = translation._postprocess_translation("Türkçe'ye çeviri: Translate to Turkish: elma.", "apple")
        assert result == "elma"

    def test_postprocess_strips_turkceye_ceviren_prefix(self):
        result = translation._postprocess_translation("türkçeye çeviren: d.r.m atilla durmaz", "d.r.m atilla durmaz")
        assert result is None

    def test_pick_madlad_candidate_rejects_digit_drift(self):
        result = translation._pick_madlad_candidate(
            original_text="atakum ortaokulu samsun 2020",
            model_input="atakum ortaokulu samsun 2020",
            primary_decoded="atakum ortaokulu samsun 2010",
            guard_decoded=None,
        )
        assert result is None


def test_has_prompt_leakage_detects_turkceye_ceviren():
    assert has_prompt_leakage("türkçeye çeviren: d.r.m atilla durmaz") is True
    assert has_prompt_leakage("Türkçeye çeviri: elma") is True
    assert has_prompt_leakage("Almanca'ya çeviri: elma") is True
    assert has_prompt_leakage("Translate to Turkish: apple") is True
    assert has_prompt_leakage("d.r.m atilla durmaz") is False


class TestMadladDigitPreservation:
    def test_pick_madlad_candidate_rejects_grouped_digit_drift(self):
        result = translation._pick_madlad_candidate(
            original_text="bos assistance yol yardım 0850 259 21 12",
            model_input="bos assistance yol yardım 0850 259 21 12",
            primary_decoded="bos assistance yol yardım 0850 259 21 13",
            guard_decoded=None,
        )
        assert result is None


def test_resolve_translation_model_source_prefers_cached_snapshot(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(FIXTURE_ROOT / "hf-hub"))
    monkeypatch.setattr(translation, "MODEL_NAME", "facebook/nllb-200-distilled-600M")

    assert translation._resolve_translation_model_source() == str(
        FIXTURE_ROOT / "hf-hub" / "models--facebook--nllb-200-distilled-600M" / "snapshots" / "0001"
    )


def test_resolve_fasttext_langid_model_path_prefers_cached_file(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(FIXTURE_ROOT / "hf-hub"))

    assert translation._resolve_fasttext_langid_model_path() == str(
        FIXTURE_ROOT / "hf-hub" / "models--facebook--fasttext-language-identification" / "snapshots" / "0001" / "model.bin"
    )


def test_backend_info_defaults_to_madlad():
    info = get_translation_backend_info()
    assert info["backend"] == "madlad"
    assert "madlad400-3b-mt" in info["model_name"]


def test_get_default_translation_backend_is_madlad_for_live():
    assert translation.get_default_translation_backend("live") == "madlad"


def test_backend_info_for_madlad():
    info = get_translation_backend_info("madlad")
    assert info["backend"] == "madlad"
    assert "madlad400-3b-mt" in info["model_name"]


def test_get_madlad_translate_batch_size_defaults_to_16():
    assert translation.get_madlad_translate_batch_size() == 16
    assert translation.get_madlad_translate_batch_size(override=24) == 24
    assert translation.get_madlad_translate_batch_size(override=0) == 1


def test_build_translation_provenance_uses_backend_model():
    provenance = build_translation_provenance("madlad")
    assert provenance["name_tr_backend"] == "madlad"
    assert "madlad400-3b-mt" in provenance["name_tr_model"]
    assert provenance["name_tr_updated_at"].endswith("Z")


def test_ai_settings_pipeline_translation_backend_defaults_to_madlad(monkeypatch):
    monkeypatch.delenv("PIPELINE_TRANSLATION_BACKEND", raising=False)
    monkeypatch.delenv("MADLAD_TRANSLATE_BATCH_SIZE", raising=False)

    from config.settings import AISettings

    ai_settings = AISettings(_env_file=None)
    assert ai_settings.pipeline_translation_backend == "madlad"
    assert ai_settings.madlad_translate_batch_size == 16


def test_ai_settings_translation_backend_defaults_to_madlad(monkeypatch):
    monkeypatch.delenv("TRANSLATION_BACKEND", raising=False)

    from config.settings import AISettings

    ai_settings = AISettings(_env_file=None)
    assert ai_settings.translation_backend == "madlad"


def test_batch_translate_to_turkish_madlad_forwards_batch_size():
    with patch.object(translation, "detect_language_fasttext", return_value=("en", "eng_Latn", 0.99)), \
         patch.object(translation, "batch_translate", return_value=["elma"]) as mock_batch_translate:
        results = translation.batch_translate_to_turkish(["APPLE"], backend="madlad", batch_size=7)

    assert results == [("elma", "en")]
    assert mock_batch_translate.call_args.kwargs["batch_size"] == 7


@patch("utils.translation.detect_language_fasttext", return_value=("en", "eng_Latn", 0.99))
@patch("utils.translation.translate", return_value="elma")
def test_get_translations_defaults_to_live_madlad(mock_translate, mock_detect):
    result = get_translations("APPLE")

    assert result["tr"] == "elma"
    assert mock_translate.call_args.kwargs["backend"] == "madlad"


@patch("utils.translation.detect_language_fasttext", return_value=("en", "eng_Latn", 0.99))
@patch("utils.translation.translate", return_value=None)
def test_get_translations_madlad_failure_preserves_original_text(mock_translate, mock_detect):
    result = get_translations("APPLE")

    assert result["tr"] == "apple"
    assert mock_translate.call_args.kwargs["backend"] == "madlad"


@patch("utils.translation.detect_language_fasttext", return_value=("en", "eng_Latn", 0.99))
@patch("utils.translation.translate", return_value="elma")
def test_get_search_variants_uses_live_madlad_default(mock_translate, mock_detect):
    variants = set(get_search_variants("APPLE"))

    assert variants == {"apple", "elma"}
    assert mock_translate.call_args.kwargs["backend"] == "madlad"


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

    @patch("utils.translation.translate_to_turkish", return_value="elma")
    def test_same_language_turkish(self, mock_ttt):
        """Turkish query + Turkish candidate → works via IDF waterfall."""
        score = calculate_translation_similarity(
            "ELMA", "ELMA MARKET", candidate_name_tr="elma market"
        )
        # translate_to_turkish("ELMA") → "elma" (mocked)
        # score_translated_pair("elma", "elma market") → containment → high score
        assert score == 0.0

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

    @patch("utils.translation.translate_to_turkish", return_value="elma")
    def test_returns_tuple(self, mock_ttt):
        score, match_type = get_cross_language_similarity("ELMA", "ELMA", candidate_name_tr="elma")
        assert isinstance(score, float)
        assert isinstance(match_type, str)

    def test_no_name_tr_returns_zero(self):
        score, match_type = get_cross_language_similarity("APPLE", "ELMA")
        assert score == 0.0
        assert match_type == "none"
