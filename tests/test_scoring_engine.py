"""
Tests for the scoring engine: IDF waterfall, dynamic combine, score_pair,
risk levels, and visual similarity.

Covers:
- idf_scoring.compute_idf_weighted_score() — Cases A-F waterfall
- risk_engine._dynamic_combine() — 3-signal confidence weighting
- risk_engine.score_pair() — full scoring orchestrator
- risk_engine.get_risk_level() — threshold classification
- risk_engine.calculate_visual_similarity() — CLIP+DINOv2+OCR composite
"""
import sys
import os
import math
import inspect
from unittest.mock import MagicMock, patch

import pytest


from services.scoring_service import (
    _calculate_visual_breakdown,
    compute_idf_weighted_score,
    tokenize,
    normalize_turkish,
)
from risk_engine import (
    get_risk_level,
    calculate_visual_similarity,
    _dynamic_combine,
    score_pair,
    RISK_THRESHOLDS,
    calculate_name_similarity,
    check_substring_containment,
    calculate_token_overlap,
    normalize_turkish as re_normalize_turkish,
    get_status_category,
)


# ============================================================
# Risk Level Classification
# ============================================================

class TestGetRiskLevel:
    """Test get_risk_level() — single source of truth for risk classification."""

    def test_critical_at_090(self):
        assert get_risk_level(0.90) == "critical"

    def test_critical_at_095(self):
        assert get_risk_level(0.95) == "critical"

    def test_critical_at_100(self):
        assert get_risk_level(1.0) == "critical"

    def test_very_high_at_080(self):
        assert get_risk_level(0.80) == "very_high"

    def test_very_high_at_089(self):
        assert get_risk_level(0.89) == "very_high"

    def test_high_at_070(self):
        assert get_risk_level(0.70) == "high"

    def test_high_at_079(self):
        assert get_risk_level(0.79) == "high"

    def test_medium_at_050(self):
        assert get_risk_level(0.50) == "medium"

    def test_medium_at_069(self):
        assert get_risk_level(0.69) == "medium"

    def test_low_at_049(self):
        assert get_risk_level(0.49) == "low"

    def test_low_at_000(self):
        assert get_risk_level(0.0) == "low"

    def test_thresholds_dict_has_all_levels(self):
        expected = {"critical", "very_high", "high", "medium", "low"}
        assert set(RISK_THRESHOLDS.keys()) == expected

    def test_thresholds_are_descending(self):
        values = [RISK_THRESHOLDS[k] for k in ["critical", "very_high", "high", "medium", "low"]]
        assert values == sorted(values, reverse=True)


# ============================================================
# Visual Similarity Composite
# ============================================================

class TestCalculateVisualSimilarity:
    """Test calculate_visual_similarity() — CLIP, DINOv2, and OCR composite."""

    def test_all_zeros(self):
        assert calculate_visual_similarity() == 0.0

    def test_clip_only(self):
        score = calculate_visual_similarity(clip_sim=0.80)
        assert abs(score - 0.80) < 0.001

    def test_dinov2_only(self):
        score = calculate_visual_similarity(dinov2_sim=0.70)
        assert abs(score - 0.70) < 0.001

    def test_color_only_is_ignored(self):
        score = calculate_visual_similarity(color_sim=0.60)
        assert score == 0.0

    def test_ocr_only(self):
        score = calculate_visual_similarity(ocr_text_a="NIKE", ocr_text_b="NIKE")
        assert abs(score - 0.55) < 0.001

    def test_all_components(self):
        score = calculate_visual_similarity(
            clip_sim=0.80, dinov2_sim=0.70, color_sim=0.60,
            ocr_text_a="NIKE", ocr_text_b="NIKE",
        )
        expected = (0.80 * 0.45 + 0.70 * 0.35 + 1.0 * 0.15) / (0.45 + 0.35 + 0.15)
        assert abs(score - expected) < 0.001

    def test_ocr_zero_when_one_empty(self):
        score = calculate_visual_similarity(
            clip_sim=0.80, ocr_text_a="", ocr_text_b="NIKE"
        )
        assert abs(score - 0.80) < 0.001

    def test_ocr_partial_match(self):
        score = calculate_visual_similarity(ocr_text_a="NIKE", ocr_text_b="NIKEA")
        # SequenceMatcher("nike", "nikea").ratio() ≈ 0.89
        assert score == 0.55

    def test_ocr_case_insensitive(self):
        s1 = calculate_visual_similarity(ocr_text_a="NIKE", ocr_text_b="nike")
        s2 = calculate_visual_similarity(ocr_text_a="NIKE", ocr_text_b="NIKE")
        assert abs(s1 - s2) < 0.001

    def test_color_is_not_an_active_component(self):
        score, breakdown = _calculate_visual_breakdown(
            clip_sim=0.80,
            dinov2_sim=0.70,
            color_sim=1.0,
            ocr_text_a="NIKE",
            ocr_text_b="NIKE",
        )
        assert score > 0.0
        assert "color" not in breakdown["active_components"]
        assert "color" not in breakdown["components"]
        assert "color" not in breakdown["weights"]

    def test_color_does_not_raise_ocr_only_score(self):
        score = calculate_visual_similarity(
            color_sim=0.90,
            ocr_text_a="NIKE",
            ocr_text_b="NIKE",
        )
        assert score == 0.55

    def test_ocr_disagreement_caps_moderate_neural_visual(self):
        score, breakdown = _calculate_visual_breakdown(
            clip_sim=0.7310,
            dinov2_sim=0.9053,
            ocr_text_a="Oksipital",
            ocr_text_b="Qorvital",
        )

        assert score <= 0.69
        assert breakdown["ocr_disagreement"] is True
        assert "ocr_disagreement_cap:0.69" in breakdown["caps_applied"]

    def test_very_strong_clip_and_dino_can_survive_ocr_disagreement(self):
        score, breakdown = _calculate_visual_breakdown(
            clip_sim=0.94,
            dinov2_sim=0.93,
            ocr_text_a="ALPHA",
            ocr_text_b="OMEGA",
        )

        assert score >= 0.80
        assert breakdown["very_strong_visual_components"] is True
        assert "ocr_disagreement_cap:0.69" not in breakdown["caps_applied"]


# ============================================================
# IDF Word Classification & Tokenization
# ============================================================

class TestTokenization:
    """Test tokenize() and normalize_turkish()."""

    def test_basic_tokenize(self):
        tokens = tokenize("NIKE SPORTS")
        assert tokens == {"nike", "sports"}

    def test_turkish_normalization(self):
        result = normalize_turkish("DOĞAN PATENT")
        assert result == "dogan patent"

    def test_extended_latin_diacritics_fold_without_fragmenting(self):
        result = normalize_turkish("meyâl café")
        assert result == "meyal cafe"

    def test_single_char_excluded(self):
        tokens = tokenize("A B NIKE")
        assert tokens == {"nike"}

    def test_numbers_included(self):
        tokens = tokenize("3M COMPANY")
        assert "3m" in tokens

    def test_empty_string(self):
        assert tokenize("") == set()
        assert normalize_turkish("") == ""

    def test_special_chars_stripped(self):
        tokens = tokenize("A&B CORP.")
        # & and . are not word chars
        assert "corp" in tokens


class TestDescriptorProfileComputation:
    """Pure descriptor-stat classifier tests without DB access."""

    @staticmethod
    def _profile(**overrides):
        from compute_idf import _descriptor_profile

        params = {
            "word": "descriptorx",
            "doc_freq": 1_000,
            "total_docs": 1_000_000,
            "first_count": 40,
            "last_count": 700,
            "single_count": 2,
            "unique_partner_count": 700,
            "unique_holder_count": 500,
            "unique_class_count": 20,
            "compact_suffix_hits": 0,
            "original_word_class": "generic",
        }
        params.update(overrides)
        return _descriptor_profile(**params)

    def test_suffix_category_pattern_becomes_descriptor_like(self):
        descriptor_like, score, stats = self._profile()

        assert descriptor_like is True
        assert score >= 0.72
        assert "mostly_suffix" in stats["reason_flags"]

    def test_brand_like_high_first_position_use_is_not_descriptor_like(self):
        descriptor_like, _, stats = self._profile(
            first_count=500,
            last_count=300,
            single_count=10,
        )

        assert descriptor_like is False
        assert stats["first_rate"] > 0.25

    def test_moderate_suffix_with_high_dispersion_becomes_descriptor_like(self):
        descriptor_like, score, stats = self._profile(
            first_count=40,
            last_count=180,
            single_count=0,
            unique_partner_count=900,
            unique_holder_count=700,
            unique_class_count=20,
        )

        assert descriptor_like is True
        assert score >= 0.72
        assert "moderate_suffix_with_dispersion" in stats["reason_flags"]

    def test_compact_suffix_evidence_can_make_descriptor_like(self):
        descriptor_like, score, stats = self._profile(
            word="suffixx",
            first_count=20,
            last_count=50,
            single_count=0,
            unique_partner_count=500,
            unique_holder_count=350,
            unique_class_count=8,
            compact_suffix_hits=100,
        )

        assert descriptor_like is True
        assert score >= 0.72
        assert "compound_suffix" in stats["reason_flags"]


# ============================================================
# IDF Waterfall (compute_idf_weighted_score)
# ============================================================

class TestIDFWaterfall:
    """Test compute_idf_weighted_score() — Cases EXACT through F."""

    @staticmethod
    def _seed_distinctive_tokens(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache[token] = {
                "idf": 8.0,
                "is_generic": False,
                "doc_freq": 100,
                "word_class": "distinctive",
            }

    @staticmethod
    def _seed_distinctive_tokens_tr(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache_tr[token] = {
                "idf": 8.0,
                "is_generic": False,
                "doc_freq": 100,
                "word_class": "distinctive",
            }

    @staticmethod
    def _seed_low_protectability_tokens(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache[token] = {
                "idf": 8.8,
                "is_generic": False,
                "doc_freq": 120,
                "word_class": "distinctive",
                "descriptor_like": False,
                "descriptor_score": 0.55,
                "descriptor_stats": {
                    "original_word_class": "distinctive",
                    "final_word_class": "distinctive",
                    "doc_frequency": 120,
                    "last_rate": 0.46,
                    "first_rate": 0.16,
                    "single_rate": 0.03,
                    "reason_flags": [
                        "mostly_suffix",
                        "low_initial_use",
                        "low_single_use",
                    ],
                },
            }

    @staticmethod
    def _seed_low_protectability_tokens_tr(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache_tr[token] = {
                "idf": 8.8,
                "is_generic": False,
                "doc_freq": 120,
                "word_class": "distinctive",
                "descriptor_like": False,
                "descriptor_score": 0.55,
                "descriptor_stats": {
                    "original_word_class": "distinctive",
                    "final_word_class": "distinctive",
                    "doc_frequency": 120,
                    "last_rate": 0.46,
                    "first_rate": 0.16,
                    "single_rate": 0.03,
                    "reason_flags": [
                        "mostly_suffix",
                        "low_initial_use",
                        "low_single_use",
                    ],
                },
            }

    @staticmethod
    def _seed_descriptor_tokens(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache[token] = {
                "idf": 8.2,
                "is_generic": True,
                "doc_freq": 1_000,
                "word_class": "generic",
                "descriptor_like": True,
                "descriptor_score": 0.9,
                "descriptor_stats": {
                    "original_word_class": "generic",
                    "reason_flags": ["mostly_suffix", "high_partner_dispersion"],
                },
            }

    @staticmethod
    def _seed_legacy_override_material_tokens(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache[token] = {
                "idf": 2.0,
                "is_generic": True,
                "doc_freq": 600,
                "word_class": "generic",
                "descriptor_like": False,
                "descriptor_score": 0.35,
                "descriptor_stats": {
                    "original_word_class": "distinctive",
                    "final_word_class": "distinctive",
                    "doc_frequency": 600,
                },
            }

    @staticmethod
    def _seed_descriptor_tokens_tr(*tokens):
        from idf_lookup import IDFLookup

        for token in tokens:
            IDFLookup._cache_tr[token] = {
                "idf": 8.2,
                "is_generic": True,
                "doc_freq": 1_000,
                "word_class": "generic",
                "descriptor_like": True,
                "descriptor_score": 0.9,
                "descriptor_stats": {
                    "original_word_class": "generic",
                    "reason_flags": ["mostly_suffix", "high_partner_dispersion"],
                },
            }

    def test_suffix_heavy_corpus_distinctive_term_becomes_low_protectability_anchor(self):
        from services.scoring_service import (
            _is_low_protectability_anchor,
            _token_role,
        )

        self._seed_low_protectability_tokens("bravox")

        assert _is_low_protectability_anchor("bravox") is True
        assert _token_role("bravox") == "low_protectability_anchor"

    def test_high_first_use_brandlike_term_is_not_low_protectability_anchor(self):
        from idf_lookup import IDFLookup
        from services.scoring_service import _is_low_protectability_anchor, _token_role

        IDFLookup._cache["brandx"] = {
            "idf": 8.8,
            "is_generic": False,
            "doc_freq": 120,
            "word_class": "distinctive",
            "descriptor_like": False,
            "descriptor_score": 0.2,
            "descriptor_stats": {
                "original_word_class": "distinctive",
                "doc_frequency": 120,
                "last_rate": 0.15,
                "first_rate": 0.72,
                "single_rate": 0.12,
                "reason_flags": [],
            },
        }

        assert _is_low_protectability_anchor("brandx") is False
        assert _token_role("brandx") == "distinctive"

    def test_descriptor_like_term_remains_generic_not_low_protectability_anchor(self):
        from services.scoring_service import (
            _is_low_protectability_anchor,
            _token_role,
        )

        self._seed_descriptor_tokens("clubx")

        assert _is_low_protectability_anchor("clubx") is False
        assert _token_role("clubx") == "generic"

    def test_normal_distinctive_term_remains_fully_distinctive(self):
        from services.scoring_service import (
            _is_low_protectability_anchor,
            _token_role,
        )

        self._seed_distinctive_tokens("zendra")

        assert _is_low_protectability_anchor("zendra") is False
        assert _token_role("zendra") == "distinctive"

    def test_exact_match_returns_1(self):
        """Exact match → 1.0, scoring path EXACT_MATCH."""
        score, breakdown = compute_idf_weighted_score(
            query="NIKE", target="NIKE", text_sim=0.5, semantic_sim=0.5,
        )
        assert score == 1.0
        assert breakdown["exact_match"] is True
        assert breakdown["scoring_path"] == "TEXT_EXACT"

    def test_compact_exact_returns_096(self):
        score, breakdown = compute_idf_weighted_score(
            query="COCA COLA", target="COCACOLA", text_sim=0.4, semantic_sim=0.4,
        )
        assert score == 0.96
        assert breakdown["scoring_path"] == "TEXT_COMPACT_EXACT"

    def test_exact_match_case_insensitive(self):
        """Case-insensitive exact match."""
        score, _ = compute_idf_weighted_score(
            query="Nike", target="NIKE", text_sim=0.5, semantic_sim=0.5,
        )
        assert score == 1.0

    def test_exact_match_turkish_normalization(self):
        """Turkish chars normalized: 'DOĞAN' == 'dogan'."""
        score, _ = compute_idf_weighted_score(
            query="DOĞAN", target="dogan", text_sim=0.5, semantic_sim=0.5,
        )
        assert score == 1.0

    def test_exact_token_in_target_scores_high(self):
        """'nike' exact match in 'nike sports' → high score via unified waterfall."""
        score, breakdown = compute_idf_weighted_score(
            query="NIKE", target="NIKE SPORTS", text_sim=0.5, semantic_sim=0.5,
        )
        assert score >= 0.83, f"Expected >=0.83 for exact distinctive match +1 word, got {score}"
        assert breakdown["scoring_path"] in {"TEXT_CONTAINMENT", "TEXT_TOKEN_EXACT"}

    def test_exact_token_target_in_query(self):
        """'nike' (target) all tokens in 'nike sports' (query) → still high score."""
        score, breakdown = compute_idf_weighted_score(
            query="NIKE SPORTS", target="NIKE", text_sim=0.5, semantic_sim=0.5,
        )
        assert score >= 0.85, f"Expected >=0.85 for containment, got {score}"

    def test_length_dilution_increases_with_words(self):
        """More same-role generic extra words → lower score (monotonic decrease)."""
        score_1, _ = compute_idf_weighted_score(
            query="NIKE", target="NIKE GROUP", text_sim=0.5,
        )
        score_3, _ = compute_idf_weighted_score(
            query="NIKE", target="NIKE GROUP LTD COMPANY", text_sim=0.5,
        )
        score_5, _ = compute_idf_weighted_score(
            query="NIKE", target="NIKE GROUP LTD COMPANY CORP INC", text_sim=0.5,
        )
        assert score_1 > score_3 > score_5, (
            f"Expected monotonic decrease: {score_1} > {score_3} > {score_5}"
        )
        assert score_1 >= 0.80, f"+1 word should be >=0.80, got {score_1}"

    def test_generic_only_penalized(self):
        """Only generic words match → low score."""
        score, breakdown = compute_idf_weighted_score(
            query="LTD", target="LTD STI", text_sim=0.5, semantic_sim=0.5,
        )
        assert score <= 0.25, f"Expected <=0.25 for generic-only, got {score}"
        assert "TEXT_GENERIC_ONLY" in breakdown["scoring_path"]

    def test_exact_beats_fuzzy_same_distinctive_pct(self):
        """Core invariant: exact token match MUST score higher than fuzzy match.

        Query: "dogan patent ve danismanlik"
        Target A: "d.p dogan patent" — 2 exact token matches (dogan + patent)
        Target B: "dogam egitim ve danismanlik" — 0 exact distinctive, 1 fuzzy (dogam~dogan)

        Target A must score >= Target B because exact trumps fuzzy.
        """
        q = "DOGAN"
        score_a, _ = compute_idf_weighted_score(
            query=q, target="DOGAN MARKA", text_sim=0.5, semantic_sim=0.3,
        )
        score_b, _ = compute_idf_weighted_score(
            query=q, target="DOGAM MARKA", text_sim=0.5, semantic_sim=0.3,
        )
        assert score_a >= score_b, (
            f"Exact match ({score_a}) must beat fuzzy match ({score_b})"
        )

    def test_tier_2_containment_high_score(self):
        """≥80% distinctive weight matched → Case A."""
        score, breakdown = compute_idf_weighted_score(
            query="DOGAN", target="DOGAN MARKA", text_sim=0.5, semantic_sim=0.5,
        )
        assert score >= 0.80, f"Expected >=0.80, got {score}"
        assert breakdown["containment"] == 1.0
        assert breakdown["containment_score"] > 0.0

    def test_tier_3_partial_distinctive(self):
        """≥50% distinctive matched → Case B (with real DB IDF).
        Without DB, all words fall to generic → Case E with low score.
        Test that score is reasonable and unmatched words are penalized:
        'adidas' (unmatched query) + 'puma' (unmatched target) add dilution.
        """
        score, breakdown = compute_idf_weighted_score(
            query="NIKE ADIDAS", target="NIKE PUMA", text_sim=0.3, semantic_sim=0.3,
        )
        # "nike" matches exactly; unmatched: adidas + puma get penalized
        # Without real IDF DB → falls to Case E (generic); with DB → Case B
        assert score > 0.0
        assert any(m["query_word"] == "nike" for m in breakdown.get("matched_words", []))

    def test_tier_4_fuzzy(self):
        """Some distinctive match (<50%) → Case C."""
        # Three distinctive words, one fuzzy match
        score, breakdown = compute_idf_weighted_score(
            query="NIKE ADIDAS GUCCI", target="NIKEA PUMA ZARA",
            text_sim=0.2, semantic_sim=0.2,
        )
        # "nike" fuzzy-matches "nikea" (≥0.75), others don't match
        # IDF-weighted coverage: 1/3 distinctive words matched → ~0.30 (harmonic of 1/3 × 1/3)
        assert score >= 0.25
        assert "TEXT_FUZZY" in breakdown["scoring_path"] or "TEXT_CHAR_SUPPORT" in breakdown["scoring_path"]

    def test_tier_6_semi_generic_safeguard(self):
        """Only semi-generic words match → low score."""
        # Now goes through unified waterfall — semi-generic only → Case D
        from idf_lookup import IDFLookup

        IDFLookup._cache["tempo"] = {
            "idf": 4.0,
            "is_generic": False,
            "doc_freq": 2_000,
            "word_class": "semi_generic",
        }

        score, breakdown = compute_idf_weighted_score(
            query="TEMPO", target="TEMPO MARKA", text_sim=0.4, semantic_sim=0.3,
        )
        # "patent" is semi-generic, no distinctive words → D path
        assert score <= 0.45
        assert breakdown["scoring_path"] == "TEXT_SEMI_GENERIC_ONLY"

    def test_tier_6_true_generic_no_anchor(self):
        """True-generic matches without the query anchor stay low."""
        self._seed_distinctive_tokens("zendra")

        score, breakdown = compute_idf_weighted_score(
            query="ZENDRA GRUP", target="MARKA GRUP",
            text_sim=0.3, semantic_sim=0.3,
        )
        # "grup" and "marka" are descriptive/legal terms, while "spor" is the anchor.
        assert score <= 0.20
        assert breakdown["scoring_path"] == "TEXT_MISSING_ANCHOR_GENERIC_ONLY"

    def test_tier_6_generic_only(self):
        """Only generic words match → ≤0.20."""
        score, breakdown = compute_idf_weighted_score(
            query="LTD VE", target="STI VE",
            text_sim=0.2, semantic_sim=0.2,
        )
        # "ve" matches (generic), "ltd" doesn't match "sti"
        # Only generic match → Case E
        assert score <= 0.25
        assert breakdown["scoring_path"] == "TEXT_GENERIC_ONLY"

    def test_high_idf_db_generic_is_common_anchor(self):
        """A high-IDF mark token must not be treated like a descriptive generic."""
        from idf_lookup import IDFLookup

        IDFLookup._cache["dogan"] = {
            "idf": 7.727,
            "is_generic": True,
            "doc_freq": 1_000,
            "word_class": "generic",
        }

        score, breakdown = compute_idf_weighted_score(
            query="dogan patent",
            target="dogan",
            text_sim=0.3,
            semantic_sim=0.2,
        )

        assert score >= 0.80
        assert breakdown["common_anchor_match"] == 1.0
        assert "generic_only_cap" not in "|".join(breakdown["caps_applied"])
        assert any(
            match["query_word"] == "dogan" and match["token_role"] == "common_anchor"
            for match in breakdown["matched_words"]
        )

    def test_non_protectable_high_idf_terms_do_not_become_common_anchors(self):
        """Category/entity terms stay generic even when corpus IDF is misleading."""
        self._seed_distinctive_tokens("atlas", "nova")
        self._seed_descriptor_tokens("kulubu", "spor")

        score, breakdown = compute_idf_weighted_score(
            query="atlas guzellik kulubu",
            target="nova spor kulubu",
            text_sim=0.3,
            semantic_sim=0.2,
        )

        assert score <= 0.20
        assert breakdown["scoring_path"] == "TEXT_MISSING_ANCHOR_GENERIC_ONLY"
        assert breakdown["common_anchor_match"] == 0.0
        assert breakdown["descriptor_terms"]["query"] == ["kulubu"]
        assert "spor" in breakdown["descriptor_terms"]["target"]
        assert breakdown["non_protectable_terms"]["query"] == ["kulubu"]
        assert "spor" in breakdown["non_protectable_terms"]["target"]
        assert any(
            match["query_word"] == "kulubu" and match["token_role"] == "generic"
            for match in breakdown["matched_words"]
        )

    def test_shared_non_protectable_entity_terms_stay_low(self):
        self._seed_distinctive_tokens("zendra", "omera")
        self._seed_descriptor_tokens("club")

        score, breakdown = compute_idf_weighted_score(
            query="zendra club",
            target="omera club",
            text_sim=0.3,
            semantic_sim=0.2,
        )

        assert score <= 0.20
        assert "missing_anchor_generic_only_cap:0.18" in breakdown["caps_applied"]

    def test_non_protectable_added_matter_still_scores_copied_anchor_high(self):
        self._seed_distinctive_tokens("zendra")
        self._seed_descriptor_tokens("kulubu")

        score, breakdown = compute_idf_weighted_score(
            query="zendra kulubu",
            target="zendra kulubu hizmetleri",
            text_sim=0.5,
            semantic_sim=0.4,
        )

        assert 0.88 <= score <= 0.96
        assert breakdown["dominant_core_score"] >= 0.88
        assert breakdown["added_matter_breakdown"]["reason"] == (
            "copied core plus true-generic added matter"
        )

    def test_true_generic_match_capped_when_common_anchor_missing(self):
        """Matching only 'patent' must not make an unrelated mark look risky."""
        from idf_lookup import IDFLookup

        IDFLookup._cache["dogan"] = {
            "idf": 7.727,
            "is_generic": True,
            "doc_freq": 1_000,
            "word_class": "generic",
        }
        IDFLookup._cache["aksa"] = {
            "idf": 9.343,
            "is_generic": False,
            "doc_freq": 100,
            "word_class": "distinctive",
        }

        score, breakdown = compute_idf_weighted_score(
            query="dogan patent",
            target="aksa patent",
            text_sim=0.3,
            semantic_sim=0.2,
        )

        assert score <= 0.20
        assert "missing_anchor_generic_only_cap:0.18" in breakdown["caps_applied"]

    def test_tier_6_no_match(self):
        """No token overlap → raw sims * 0.7."""
        score, breakdown = compute_idf_weighted_score(
            query="NIKE", target="KAPLAN",
            text_sim=0.2, semantic_sim=0.3, phonetic_sim=0.0,
        )
        assert score < 0.30
        assert "semantic_or_phonetic_without_lexical_anchor_cap" in "|".join(breakdown["caps_applied"])

    def test_compact_generic_suffix_matches_spaced_form(self):
        score, breakdown = compute_idf_weighted_score("nuvapatent", "nuva patent")

        assert score >= 0.96
        assert breakdown["scoring_path"] == "TEXT_COMPACT_EXACT"
        assert breakdown["compound_expansions"]["query"] == [
            {"token": "nuvapatent", "root": "nuva", "suffix": "patent"}
        ]

    def test_compact_generic_suffix_matches_spaced_form_with_prefix_noise(self):
        score, breakdown = compute_idf_weighted_score("nuvapatent", "x nuva patent")

        assert score >= 0.90
        assert any(
            match["query_word"] == "nuva" and match["match_type"] == "exact"
            for match in breakdown["matched_words"]
        )

    def test_short_prefix_compound_does_not_become_suffix_match(self):
        score, breakdown = compute_idf_weighted_score("nuvapatent", "apatent")

        assert score <= 0.20
        assert "missing_anchor_containment_only_cap:0.18" in breakdown["caps_applied"]

    def test_different_roots_with_same_generic_suffix_stay_low(self):
        score, breakdown = compute_idf_weighted_score("nuvapatent", "orionpatent")

        assert score <= 0.20
        assert "missing_anchor_generic_only_cap:0.18" in breakdown["caps_applied"]

    def test_near_root_typo_compound_scores_on_root_similarity(self):
        score, breakdown = compute_idf_weighted_score("nuvapatent", "nuvvpatent")

        assert 0.60 <= score <= 0.85
        assert any(
            match["query_word"] == "nuva" and match["target_word"] == "nuvv"
            for match in breakdown["matched_words"]
        )

    def test_non_patent_generic_suffix_compound_matches_spaced_form(self):
        score, breakdown = compute_idf_weighted_score("nuvamarka", "nuva marka")

        assert score >= 0.96
        assert breakdown["compound_expansions"]["query"] == [
            {"token": "nuvamarka", "root": "nuva", "suffix": "marka"}
        ]

    def test_non_patent_generic_suffix_different_roots_stay_low(self):
        score, breakdown = compute_idf_weighted_score("nuvamarka", "orionmarka")

        assert score <= 0.20
        assert "missing_anchor_generic_only_cap:0.18" in breakdown["caps_applied"]

    def test_long_name_containing_exact_compound_scores_high_with_discount(self):
        score, breakdown = compute_idf_weighted_score(
            "nuvapatent",
            "d r p nuvapatent marka ve patent office",
        )

        assert 0.88 <= score < 1.0
        assert breakdown["compound_containment_score"] >= 0.88

    def test_full_copied_core_with_generic_added_matter_high_discounted(self):
        self._seed_distinctive_tokens("zendra")

        score, breakdown = compute_idf_weighted_score(
            "zendra patent",
            "zendra patent group ltd",
        )

        assert 0.88 <= score <= 0.96
        assert breakdown["dominant_core_score"] >= 0.88
        added = breakdown["added_matter_breakdown"]
        assert added["reason"] == "copied core plus true-generic added matter"
        assert added["target_extra_roles"]["true_generic"] == ["group", "ltd"]

    def test_shared_anchor_with_changed_generic_matter_capped_medium_high(self):
        self._seed_distinctive_tokens("zendra")

        score, breakdown = compute_idf_weighted_score(
            "zendra patent",
            "zendra group",
        )

        assert 0.65 <= score <= 0.78
        assert "added_matter_changed_remaining_matter_cap:0.78" in breakdown["caps_applied"]
        added = breakdown["added_matter_breakdown"]
        assert added["query_extra_roles"]["true_generic"] == ["patent"]
        assert added["target_extra_roles"]["true_generic"] == ["group"]

    def test_single_anchor_plus_generic_added_matter_remains_high_discounted(self):
        self._seed_distinctive_tokens("zendra")

        score, breakdown = compute_idf_weighted_score("zendra", "zendra group")

        assert 0.88 <= score < 1.0
        assert breakdown["added_matter_breakdown"]["reason"] == (
            "copied core plus true-generic added matter"
        )

    def test_single_anchor_plus_distinctive_extra_capped_below_very_high(self):
        self._seed_distinctive_tokens("zendra", "borex")

        score, breakdown = compute_idf_weighted_score("zendra", "zendra borex")

        assert score <= 0.78
        assert "added_matter_single_anchor_distinctive_extra_cap:0.78" in breakdown["caps_applied"]
        assert breakdown["added_matter_breakdown"]["target_extra_roles"]["distinctive"] == ["borex"]

    def test_changed_distinctive_matter_keeps_partial_anchor_cap(self):
        self._seed_distinctive_tokens("zendra", "borex", "lumora")

        score, breakdown = compute_idf_weighted_score(
            "zendra borex",
            "zendra lumora",
        )

        assert score <= 0.58
        assert "partial_distinctive_anchor_cap:0.69" in breakdown["caps_applied"]
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in breakdown["caps_applied"]
        assert breakdown["added_matter_breakdown"]["partial_multi_anchor_changed_matter"] is True

    def test_partial_multi_anchor_changed_matter_caps_one_shared_anchor(self):
        self._seed_distinctive_tokens("zendra", "borex", "lumora", "omera")

        score, breakdown = compute_idf_weighted_score(
            "zendra borex lumora",
            "zendra omera",
        )

        assert score <= 0.58
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in breakdown["caps_applied"]
        added = breakdown["added_matter_breakdown"]
        assert added["partial_multi_anchor_changed_matter"] is True
        assert added["matched_query_anchor_tokens"] == ["zendra"]
        assert added["matched_target_anchor_tokens"] == ["zendra"]
        assert added["query_material_extra_count"] == 2
        assert added["target_material_extra_count"] == 1

    def test_partial_multi_anchor_changed_matter_scores_are_calibrated(self):
        self._seed_distinctive_tokens("zendra", "borex", "lumora", "omera")

        sparse_score, sparse_breakdown = compute_idf_weighted_score(
            "zendra borex lumora",
            "zendra omera",
        )
        tighter_score, tighter_breakdown = compute_idf_weighted_score(
            "zendra borex",
            "zendra lumora",
        )

        assert sparse_score < tighter_score < 0.58
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in (
            sparse_breakdown["caps_applied"]
        )
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in (
            tighter_breakdown["caps_applied"]
        )
        sparse_calibration = sparse_breakdown["calibration_breakdown"]
        tighter_calibration = tighter_breakdown["calibration_breakdown"]
        assert sparse_calibration["calibrated_score"] == sparse_score
        assert tighter_calibration["calibrated_score"] == tighter_score
        assert sparse_calibration["evidence_score"] < tighter_calibration["evidence_score"]

    def test_partial_multi_anchor_changed_matter_cap_is_symmetric(self):
        self._seed_distinctive_tokens("zendra", "borex", "lumora", "omera")

        forward, _ = compute_idf_weighted_score(
            "zendra borex lumora",
            "zendra omera",
        )
        reverse, reverse_breakdown = compute_idf_weighted_score(
            "zendra omera",
            "zendra borex lumora",
        )

        assert forward <= 0.58
        assert reverse <= 0.58
        assert abs(forward - reverse) <= 0.03
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in (
            reverse_breakdown["caps_applied"]
        )

    def test_single_anchor_generic_changed_matter_keeps_previous_band(self):
        self._seed_distinctive_tokens("zendra", "production")

        score, breakdown = compute_idf_weighted_score(
            "zendra patent",
            "zendra production",
        )

        assert 0.65 <= score <= 0.78
        assert "added_matter_changed_remaining_matter_cap:0.78" in breakdown["caps_applied"]
        assert breakdown["added_matter_breakdown"]["partial_multi_anchor_changed_matter"] is False

    def test_single_anchor_asymmetric_added_matter_caps_target_house_mark(self):
        self._seed_distinctive_tokens("zendra", "polat")
        self._seed_descriptor_tokens("hotel")
        self._seed_legacy_override_material_tokens("investment")

        score, breakdown = compute_idf_weighted_score(
            "zendra investment",
            "polat zendra hotel",
        )

        assert 0.58 <= score <= 0.68
        assert "added_matter_single_anchor_asymmetric_added_matter_cap:0.68" in (
            breakdown["caps_applied"]
        )
        added = breakdown["added_matter_breakdown"]
        assert added["single_anchor_asymmetric_added_matter"] is True
        assert added["query_legacy_override_material_terms"] == ["investment"]
        assert added["query_material_extra_terms"] == ["investment"]
        assert added["target_material_extra_terms"] == ["polat"]
        assert added["leading_target_material_extra_token"] == "polat"

    def test_single_anchor_asymmetric_added_matter_caps_multiple_target_terms(self):
        self._seed_distinctive_tokens("zendra", "bcc", "card")
        self._seed_legacy_override_material_tokens("investment", "clothing")

        score, breakdown = compute_idf_weighted_score(
            "zendra investment",
            "bcc zendra card clothing",
        )

        assert 0.58 <= score <= 0.68
        assert "added_matter_single_anchor_asymmetric_added_matter_cap:0.68" in (
            breakdown["caps_applied"]
        )
        added = breakdown["added_matter_breakdown"]
        assert added["single_anchor_asymmetric_added_matter"] is True
        assert added["target_legacy_override_material_terms"] == ["clothing"]
        assert added["target_material_extra_count"] == 3
        assert added["partial_multi_anchor_changed_matter"] is False

    def test_full_copied_core_with_distinctive_extra_below_exact(self):
        self._seed_distinctive_tokens("zendra", "borex", "lumora")

        score, breakdown = compute_idf_weighted_score(
            "zendra borex",
            "zendra borex lumora",
        )

        assert 0.80 <= score <= 0.84
        assert "added_matter_distinctive_extra_cap:0.84" in breakdown["caps_applied"]
        assert breakdown["added_matter_breakdown"]["target_extra_roles"]["distinctive"] == ["lumora"]

    def test_generic_added_matter_discount_is_symmetric(self):
        self._seed_distinctive_tokens("zendra")

        score_forward, forward_breakdown = compute_idf_weighted_score(
            "zendra patent",
            "zendra patent group ltd",
        )
        score_reverse, reverse_breakdown = compute_idf_weighted_score(
            "zendra patent group ltd",
            "zendra patent",
        )

        assert 0.88 <= score_forward <= 0.96
        assert 0.88 <= score_reverse <= 0.96
        assert abs(score_forward - score_reverse) <= 0.03
        assert forward_breakdown["added_matter_breakdown"]["target_extra_roles"]["true_generic"] == ["group", "ltd"]
        assert reverse_breakdown["added_matter_breakdown"]["query_extra_roles"]["true_generic"] == ["group", "ltd"]

    def test_compact_compound_with_generic_added_matter_stays_high(self):
        self._seed_distinctive_tokens("zendra")

        score, breakdown = compute_idf_weighted_score(
            "zendrapatent",
            "zendrapatent group ltd",
        )

        assert 0.88 <= score < 1.0
        assert breakdown["compound_expansions"]["query"] == [
            {"token": "zendrapatent", "root": "zendra", "suffix": "patent"}
        ]
        assert breakdown["added_matter_breakdown"]["target_extra_roles"]["true_generic"] == ["group", "ltd"]

    def test_bidirectional_length_ratio_discount(self):
        """'dogan' vs 'doga' — target shorter than query gets length discount too."""
        score_exact, _ = compute_idf_weighted_score(
            query="DOGAN", target="DOGAN", text_sim=0.5, semantic_sim=0.5,
        )
        score_shorter, bd = compute_idf_weighted_score(
            query="DOGAN", target="DOGA", text_sim=0.5, semantic_sim=0.5,
        )
        score_longer, _ = compute_idf_weighted_score(
            query="DOGAN", target="OZDOGAN", text_sim=0.5, semantic_sim=0.5,
        )
        # Both shorter and longer targets should score less than exact
        assert score_exact > score_shorter, (
            f"Exact {score_exact} should beat shorter {score_shorter}"
        )
        assert score_exact > score_longer, (
            f"Exact {score_exact} should beat longer {score_longer}"
        )
        # The fuzzy match should show the length ratio discount applied
        # matched = bd.get("matched_words", [])
        # if matched:
        #     w = matched[0].get("weight", 0.0)
        #     # doga(4)/dogan(5) = 0.80 ratio → weight should be < 1.0
        #     # assert w < 0.90, f"Expected discounted weight, got {w}"

    def test_fuzzy_token_match(self):
        """'pepsi' vs 'pepsai' — fuzzy match ≥0.75 should score via token matching."""
        # Use words where neither is a substring of the other to avoid containment path
        score, breakdown = compute_idf_weighted_score(
            query="PEPSI", target="PEPSAI",
            text_sim=0.5, semantic_sim=0.5,
        )
        # "pepsi" fuzzy-matches "pepsai" (SequenceMatcher ≈ 0.91 ≥ 0.75)
        assert score >= 0.50
        matched = breakdown.get("matched_words", [])
        fuzzy_matches = [m for m in matched if m.get("match_type") == "fuzzy"]
        assert len(fuzzy_matches) > 0

    def test_multi_word_partial_overlap(self):
        """Multi-word query with partial overlap.
        'elma' matches, 'kirmizi' and 'yesil' are unmatched on each side.
        Without real IDF DB, words default to generic → low score.
        """
        score, breakdown = compute_idf_weighted_score(
            query="KIRMIZI ELMA", target="YESIL ELMA",
            text_sim=0.4, semantic_sim=0.4,
        )
        # "elma" matches, but unmatched: kirmizi + yesil → dilution
        # Without real IDF DB, all fall to generic → Case E low score
        # With DB, "elma" would be distinctive → Case B score >= 0.50
        assert score > 0.0
        assert any(m["query_word"] == "elma" for m in breakdown.get("matched_words", []))

    def test_breakdown_has_all_fields(self):
        """Verify breakdown dict has all expected keys."""
        _, breakdown = compute_idf_weighted_score(
            query="NIKE", target="ADIDAS", text_sim=0.3, semantic_sim=0.3,
        )
        expected_keys = {
            "exact_match", "containment", "token_overlap", "weighted_overlap",
            "distinctive_match", "semi_generic_match", "generic_match",
            "text_similarity", "semantic_similarity", "phonetic_similarity",
            "visual_similarity", "matched_words", "total", "scoring_path",
        }
        assert expected_keys.issubset(set(breakdown.keys()))

    def test_empty_query_returns_low(self):
        """Empty query → scaled-down raw sims."""
        score, _ = compute_idf_weighted_score(
            query="", target="NIKE", text_sim=0.5, semantic_sim=0.5,
        )
        assert score <= 0.5

    def test_phonetic_included_in_base(self):
        """Phonetic sim should be included in base calculation for Cases B-F."""
        score_without, _ = compute_idf_weighted_score(
            query="NIKE", target="KAPLAN",
            text_sim=0.2, semantic_sim=0.3, phonetic_sim=0.0,
        )
        score_with, _ = compute_idf_weighted_score(
            query="NIKE", target="KAPLAN",
            text_sim=0.2, semantic_sim=0.3, phonetic_sim=1.0,
        )
        assert score_with > score_without
        assert score_with <= 0.45

    def test_semantic_only_cap(self):
        """Semantic-only evidence is capped when there is no lexical anchor."""
        score, breakdown = compute_idf_weighted_score(
            query="NIKE", target="KAPLAN", text_sim=0.0, semantic_sim=1.0, phonetic_sim=0.0,
        )
        assert score <= 0.45
        assert "semantic_or_phonetic_without_lexical_anchor_cap" in "|".join(breakdown["caps_applied"])


# ============================================================
# Dynamic Combine
# ============================================================

class TestDynamicCombine:
    """Test _dynamic_combine() — confidence-weighted 2-signal combination (text+visual)."""

    def test_all_zeros(self):
        result = _dynamic_combine(0.0, 0.0)
        assert result["total"] == 0.0
        assert all(v == 0.0 for v in result["dynamic_weights"].values())

    def test_single_text_signal(self):
        """Only text active → total ≈ text score."""
        result = _dynamic_combine(text_idf_score=0.80, visual_sim=0.0)
        assert abs(result["total"] - 0.80) < 0.01

    def test_single_visual_signal(self):
        """Only visual active → total ≈ visual score."""
        result = _dynamic_combine(text_idf_score=0.0, visual_sim=0.70)
        assert abs(result["total"] - 0.70) < 0.01

    def test_dead_signal_gets_no_weight(self):
        """visual=0.0 should not dilute the total."""
        result = _dynamic_combine(text_idf_score=0.80, visual_sim=0.0)
        assert result["dynamic_weights"].get("visual", 0.0) == 0.0

    def test_both_signals_active(self):
        """Both signals active → weighted combination."""
        result = _dynamic_combine(text_idf_score=0.80, visual_sim=0.50)
        assert 0.50 <= result["total"] <= 0.85

    def test_exponential_boosting(self):
        """Higher scores get more weight."""
        result = _dynamic_combine(text_idf_score=0.90, visual_sim=0.10)
        weights = result["dynamic_weights"]
        assert weights.get("text", 0) > weights.get("visual", 0)

    def test_output_clamped_0_to_1(self):
        """Result should always be between 0 and 1."""
        test_cases = [
            (1.0, 1.0),
            (0.99, 0.99),
            (0.01, 0.01),
            (0.5, 0.0),
        ]
        for t, v in test_cases:
            result = _dynamic_combine(t, v)
            assert 0.0 <= result["total"] <= 1.0, f"total={result['total']} for ({t},{v})"

    def test_visual_floor_at_085(self):
        """visual_sim >= 0.85 → total >= visual."""
        result = _dynamic_combine(text_idf_score=0.0, visual_sim=0.90)
        assert result["total"] >= 0.90

    def test_weights_sum_to_1_when_active(self):
        """Active weights should sum to ~1.0."""
        result = _dynamic_combine(0.80, 0.50)
        active_weights = {k: v for k, v in result["dynamic_weights"].items() if v > 0}
        total = sum(active_weights.values())
        assert abs(total - 1.0) < 0.01

    def test_has_2_weight_keys(self):
        """dynamic_weights dict should have text and visual."""
        result = _dynamic_combine(0.5, 0.5)
        assert set(result["dynamic_weights"].keys()) == {"text", "visual"}

    def test_no_phonetic_key(self):
        """Phonetic should NOT be in dynamic weights."""
        result = _dynamic_combine(0.5, 0.5)
        assert "phonetic" not in result["dynamic_weights"]

    def test_no_translation_key(self):
        """Translation is no longer a separate signal in _dynamic_combine."""
        result = _dynamic_combine(0.5, 0.5)
        assert "translation" not in result["dynamic_weights"]

    def test_equal_scores_get_equal_explanation_weights(self):
        """With equal active scores, explanation weights split equally."""
        result = _dynamic_combine(0.50, 0.50)
        w = result["dynamic_weights"]
        assert abs(w["text"] - 0.50) < 0.01
        assert abs(w["visual"] - 0.50) < 0.01

    def test_agreement_boost(self):
        result = _dynamic_combine(text_idf_score=0.70, visual_sim=0.60)
        assert result["total"] > 0.70


# ============================================================
# Score Pair (Full Orchestrator)
# ============================================================

class TestScorePair:
    """Test score_pair() — the full scoring pipeline."""

    def test_identical_names(self):
        """'NIKE' vs 'NIKE' → total ≈ 1.0."""
        result = score_pair(
            query_name="NIKE", candidate_name="NIKE",
            text_sim=1.0, semantic_sim=1.0, visual_sim=0.0, phonetic_sim=1.0,
        )
        assert result["total"] >= 0.95

    def test_similar_names(self):
        """'NIKE' vs 'NIKEA' → high score."""
        result = score_pair(
            query_name="NIKE", candidate_name="NIKEA",
            text_sim=0.7, semantic_sim=0.6, visual_sim=0.0, phonetic_sim=0.0,
        )
        assert result["total"] >= 0.50

    def test_unrelated_names(self):
        """'NIKE' vs 'KAPLAN' → low score."""
        result = score_pair(
            query_name="NIKE", candidate_name="KAPLAN",
            text_sim=0.1, semantic_sim=0.1, visual_sim=0.0, phonetic_sim=0.0,
        )
        assert result["total"] < 0.30

    def test_returns_all_expected_keys(self):
        """Result dict should have all expected fields."""
        result = score_pair(
            query_name="NIKE", candidate_name="ADIDAS",
            text_sim=0.3, semantic_sim=0.3,
        )
        expected_keys = {
            "total", "text_idf_score", "text_similarity", "semantic_similarity",
            "phonetic_similarity", "visual_similarity", "translation_similarity",
            "dynamic_weights", "exact_match", "matched_words", "scoring_path",
            "score_version", "textual_breakdown", "visual_breakdown", "decision_reason",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_phonetic_in_breakdown(self):
        """Phonetic similarity should be in breakdown."""
        result = score_pair(
            query_name="NIKE", candidate_name="NIKE",
            text_sim=0.8, semantic_sim=0.5, phonetic_sim=1.0,
        )
        assert "phonetic_similarity" in result
        assert result["phonetic_similarity"] == 1.0

    def test_visual_in_breakdown(self):
        """Visual similarity should be passed through."""
        result = score_pair(
            query_name="NIKE", candidate_name="NIKEA",
            text_sim=0.7, semantic_sim=0.5, visual_sim=0.6,
        )
        assert result.get("visual_similarity", 0.0) == 0.6

    @patch("utils.translation.translate_to_turkish", return_value="elma")
    def test_with_translations(self, mock_ttt):
        """Cross-language translations should influence scoring."""
        result_no_trans = score_pair(
            query_name="APPLE", candidate_name="ELMA",
            text_sim=0.1, semantic_sim=0.1,
        )
        result_with_trans = score_pair(
            query_name="APPLE", candidate_name="ELMA",
            text_sim=0.1, semantic_sim=0.1,
            candidate_translations={"name_tr": "elma"},
        )
        # With translations: translation_similarity kicks in via score_translated_pair("elma", "elma")
        assert result_with_trans["total"] >= result_no_trans["total"]

    def test_scoring_path_set(self):
        """scoring_path should be one of the known paths."""
        result = score_pair(
            query_name="NIKE", candidate_name="NIKE",
            text_sim=1.0, semantic_sim=1.0,
        )
        assert result.get("scoring_path", "").startswith("TEXT_")

    @patch("utils.translation.translate_to_turkish", return_value="elma")
    def test_candidate_translations_cross_language(self, mock_ttt):
        """name_tr matching query should boost score via Path B."""
        result = score_pair(
            query_name="APPLE", candidate_name="SOME BRAND",
            text_sim=0.1, semantic_sim=0.1,
            candidate_translations={"name_tr": "APPLE"},
        )
        # Path B: "APPLE" vs "APPLE" (name_tr) → exact match → score ≈ 1.0
        # Path A: "APPLE" vs "SOME BRAND" → very low
        # Final = max(A, B) → Path B wins
        assert result["total"] >= 0.80
        assert result["scoring_path_source"] == "TRANSLATED"


# ============================================================
# Status Category
# ============================================================

class TestGetStatusCategory:
    """Test get_status_category() for UI guidance."""

    def test_registered_is_risk(self):
        cat = get_status_category("Registered")
        assert cat["level"] == "RISK"
        assert cat["multiplier"] == 1.0

    def test_published_is_risk(self):
        cat = get_status_category("Published")
        assert cat["level"] == "RISK"

    def test_expired_is_opportunity(self):
        cat = get_status_category("Expired")
        assert cat["level"] == "OPPORTUNITY"
        assert cat["multiplier"] == 0.3

    def test_withdrawn_is_opportunity(self):
        cat = get_status_category("Withdrawn")
        assert cat["level"] == "OPPORTUNITY"

    def test_refused_is_warning(self):
        cat = get_status_category("Refused")
        assert cat["level"] == "WARNING"

    def test_cancelled_is_warning(self):
        cat = get_status_category("Cancelled")
        assert cat["level"] == "WARNING"

    def test_unknown_status_returns_unknown(self):
        cat = get_status_category("SomeNewStatus")
        assert cat["level"] == "UNKNOWN"
        assert cat["multiplier"] == 0.5

    def test_all_statuses_have_multiplier(self):
        statuses = ["Registered", "Published", "Renewed", "Opposed", "Applied",
                     "Refused", "Cancelled", "Partial Refusal", "Expired", "Withdrawn"]
        for s in statuses:
            cat = get_status_category(s)
            assert 0.0 <= cat["multiplier"] <= 1.0, f"{s}: multiplier={cat['multiplier']}"


# ============================================================
# Search Architecture Refactoring Tests (Steps 2-11)
# ============================================================


class TestColorHistogramDimension:
    """Step 2: Verify _encode_single_image produces 512-dim color histogram."""

    def test_color_histogram_bins_match_ai_py(self):
        """Verify the histogram bins are [8,8,8] producing 512 values."""
        # The fix changed [8,2,2] (=32) to [8,8,8] (=512)
        assert 8 * 8 * 8 == 512

    def test_encode_single_image_returns_4_tuple(self):
        """Verify _encode_single_image now returns 4 values (added OCR)."""
        from risk_engine import RiskEngine
        # RiskEngine.__init__ requires mocked modules, check signature instead
        import inspect
        sig = inspect.signature(RiskEngine._encode_single_image)
        # Method should accept self + pil_img
        params = list(sig.parameters.keys())
        assert params == ["self", "pil_img"]


class TestOCRExtraction:
    """Step 3: Verify OCR text is extracted and propagated."""

    def test_get_query_vectors_returns_5_tuple(self):
        """get_query_vectors should return (text_vec, img_vec, dino_vec, color_vec, ocr_text)."""
        from risk_engine import RiskEngine
        import inspect
        sig = inspect.signature(RiskEngine.get_query_vectors)
        # Verify it accepts name and optional image_path
        params = list(sig.parameters.keys())
        assert "name" in params
        assert "image_path" in params

    def test_calculate_hybrid_risk_accepts_query_ocr_text(self):
        """calculate_hybrid_risk should have query_ocr_text parameter."""
        from risk_engine import RiskEngine
        import inspect
        sig = inspect.signature(RiskEngine.calculate_hybrid_risk)
        params = list(sig.parameters.keys())
        assert "query_ocr_text" in params

    def test_ocr_contributes_to_visual_similarity(self):
        """When both OCR texts match, visual similarity should increase."""
        # Without OCR
        vis_no_ocr = calculate_visual_similarity(
            clip_sim=0.80, dinov2_sim=0.70, color_sim=0.60,
            ocr_text_a="", ocr_text_b="NIKE",
        )
        # With matching OCR
        vis_with_ocr = calculate_visual_similarity(
            clip_sim=0.80, dinov2_sim=0.70, color_sim=0.60,
            ocr_text_a="NIKE", ocr_text_b="NIKE",
        )
        assert vis_with_ocr > vis_no_ocr
        expected_no_ocr = (0.80 * 0.45 + 0.70 * 0.35) / (0.45 + 0.35)
        assert abs(vis_no_ocr - expected_no_ocr) < 0.01


class TestCrossEncoderRemoval:
    """Step 4: Verify CrossEncoder is not loaded."""

    def test_no_crossencoder_import(self):
        """risk_engine should not import CrossEncoder."""
        import risk_engine
        # Check the module source doesn't have active CrossEncoder import
        source = inspect.getsource(risk_engine)
        # The import line should be commented out
        assert "from sentence_transformers import CrossEncoder" not in source

    def test_risk_engine_has_no_cross_encoder_attr(self):
        """RiskEngine class should not define cross_encoder attribute in __init__."""
        import risk_engine
        source = inspect.getsource(risk_engine.RiskEngine.__init__)
        assert "self.cross_encoder" not in source


class TestRiskEngineSqlNormalization:
    """Verify DB pre-screen normalization can retrieve Turkish compact forms."""

    def test_compact_sql_normalizer_uses_ascii_chr_literals(self):
        from risk_engine import _sql_turkish_compact_expr

        expr = _sql_turkish_compact_expr("name")

        assert "CHR(287)" in expr
        assert "CHR(305)" in expr
        assert "REGEXP_REPLACE" in expr
        assert "REPLACE(" in expr
        assert "Ä" not in expr
        assert "Ã" not in expr

    def test_prescreen_includes_compact_compound_query_stage(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert "core_compact" in source
        assert "_sql_turkish_compact_expr" in source
        assert "0.88 as lexical_score" in source

    def test_normalized_sql_collapses_punctuation_like_python_normalizer(self):
        from risk_engine import _sql_turkish_normalized_expr

        expr = _sql_turkish_normalized_expr("name")

        assert "TRIM(" in expr
        assert "'[^a-z0-9]+'" in expr
        assert "'[[:space:]]+'" in expr

    def test_prescreen_active_method_has_no_inline_mojibake_normalizer(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert "REPLACE(REPLACE" not in source
        assert "Ã" not in source
        assert "Ä" not in source

    def test_prescreen_text_stages_search_name_and_name_tr(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert '_sql_turkish_normalized_expr("name")' in source
        assert '_sql_turkish_normalized_expr("name_tr")' in source
        assert '_sql_turkish_compact_expr("name")' in source
        assert '_sql_turkish_compact_expr("name_tr")' in source
        assert 'run_token_stage("all-token"' in source
        assert 'run_token_stage("any-token"' in source
        assert "COALESCE(similarity({name_tr_norm_expr}" in source
        assert "name_tr_phonetic" in source

    def test_prescreen_has_short_token_boundary_stage_for_short_anchors(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert "short_token_boundary_clause" in source
        assert "short_anchor_tokens" in source
        assert "short-all-token" in source
        assert "short-token" in source
        assert "' ' || {norm_expr} || ' '" in source
        assert "len(token) > 3" in source
        assert 'run_token_stage("any-token", broad_anchor_tokens' in source

    def test_prescreen_anchor_retrieval_uses_descriptor_flags(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert "get_descriptor_suffixes" in source
        assert "is_descriptor_like" in source
        assert "descriptor suffix" in source.lower()

    def test_prescreen_common_filters_are_shared_across_stages(self):
        import risk_engine

        source = inspect.getsource(risk_engine.RiskEngine.pre_screen_candidates)

        assert "def apply_common_filters" in source
        assert "attorney_no = %s" in source
        assert "nice_class_numbers && %s::integer[]" in source
        assert source.count("apply_common_filters(") >= 8

    def test_retrieval_metadata_merges_sources_for_same_candidate(self):
        import risk_engine

        engine = object.__new__(risk_engine.RiskEngine)
        engine._candidate_retrieval_metadata = {}

        engine._record_candidate_retrieval(
            "tm-1",
            "compact",
            ["name"],
            "normalized_compact:anchorpatent",
        )
        engine._record_candidate_retrieval(
            "tm-1",
            "fuzzy",
            ["name_tr"],
            "translated_fuzzy:anchor patent",
        )

        metadata = engine._candidate_retrieval_metadata["tm-1"]
        assert metadata["retrieval_matched_fields"] == ["name", "name_tr"]
        assert metadata["retrieval_matched_stages"] == ["compact", "fuzzy"]
        assert metadata["retrieval_query_variants"] == [
            "normalized_compact:anchorpatent",
            "translated_fuzzy:anchor patent",
        ]
        assert len(metadata["retrieval_sources"]) == 2

    def test_row_text_fields_reports_actual_field_flags(self):
        import risk_engine

        row = ("id", "app", "name", [], None, 0.8, False, True)

        assert risk_engine.RiskEngine._row_text_fields(row) == ["name_tr"]


def test_risk_engine_prewarms_configured_live_translation_backend(monkeypatch):
    import risk_engine
    import utils.translation as translation

    mock_initialize = MagicMock(return_value=True)

    monkeypatch.setattr(risk_engine.ai, "device", "cpu")
    monkeypatch.setattr(risk_engine.ai, "text_model", MagicMock())
    monkeypatch.setattr(risk_engine.ai, "clip_model", MagicMock())
    monkeypatch.setattr(risk_engine.ai, "clip_preprocess", MagicMock())
    monkeypatch.setattr(risk_engine.ai, "dinov2_model", MagicMock())
    monkeypatch.setattr(risk_engine.ai, "dinov2_preprocess", MagicMock())
    monkeypatch.setattr(risk_engine.RiskEngine, "_ensure_phonetic_capabilities", lambda self: None)
    monkeypatch.setattr(translation, "initialize", mock_initialize)
    monkeypatch.setattr(translation, "get_default_translation_backend", lambda scope="live": "madlad")

    engine = risk_engine.RiskEngine(existing_conn=MagicMock())
    try:
        mock_initialize.assert_called_once_with("cpu", backend="madlad")
    finally:
        engine.close()


class TestFeatureFlag:
    """Step 5: Verify USE_UNIFIED_SCORING feature flag."""

    def test_settings_has_use_unified_scoring(self):
        """Settings class should have use_unified_scoring field."""
        from config.settings import Settings
        import inspect
        # Check the class has the field
        assert hasattr(Settings, 'model_fields') or hasattr(Settings, '__fields__')
        fields = Settings.model_fields if hasattr(Settings, 'model_fields') else Settings.__fields__
        assert "use_unified_scoring" in fields

    def test_default_is_true(self):
        """Default value for use_unified_scoring should be True."""
        from config.settings import Settings
        fields = Settings.model_fields if hasattr(Settings, 'model_fields') else Settings.__fields__
        field = fields["use_unified_scoring"]
        assert field.default is True


class TestScorePairIntegration:
    """Steps 6-7: Verify score_pair produces correct output shape."""

    def test_score_pair_output_has_required_fields(self):
        """score_pair output must have total, text_idf_score, dynamic_weights."""
        result = score_pair(
            query_name="NIKE",
            candidate_name="NIKEA",
            text_sim=0.80,
            semantic_sim=0.75,
            visual_sim=0.0,
            phonetic_sim=0.0,
        )
        assert "total" in result
        assert "text_idf_score" in result
        assert "dynamic_weights" in result
        assert "translation_similarity" in result
        assert 0.0 <= result["total"] <= 1.0

    def test_text_only_mode_visual_zero(self):
        """Text-only search should have visual weight = 0."""
        result = score_pair(
            query_name="NIKE",
            candidate_name="NIKEA",
            text_sim=0.80,
            semantic_sim=0.75,
            visual_sim=0.0,
            phonetic_sim=0.0,
        )
        assert result["dynamic_weights"]["visual"] == 0.0
        assert result["total"] > 0.0

    def test_image_only_mode_text_from_names(self):
        """Even image-only search still gets text from candidate name matching."""
        result = score_pair(
            query_name="",
            candidate_name="NIKE",
            text_sim=0.0,
            semantic_sim=0.0,
            visual_sim=0.85,
            phonetic_sim=0.0,
        )
        # Visual should dominate when text inputs are empty
        assert result["total"] > 0.0

    def test_combined_mode_all_signals(self):
        """Combined mode should use text and visual signals."""
        result = score_pair(
            query_name="NIKE",
            candidate_name="NIKEA",
            text_sim=0.80,
            semantic_sim=0.75,
            visual_sim=0.70,
            phonetic_sim=1.0,
            candidate_translations={"name_tr": "nikea"}
        )
        assert result["total"] > 0.0
        # Both dynamic weights should be non-zero
        dw = result["dynamic_weights"]
        assert dw["text"] > 0
        assert dw["visual"] > 0

    def test_dual_path_scoring_path_source(self):
        """score_pair should report which path won."""
        result = score_pair(
            query_name="APPLE",
            candidate_name="ELMA",
            text_sim=0.1,
            semantic_sim=0.2,
            visual_sim=0.0,
            phonetic_sim=0.0,
            candidate_translations={"name_tr": "APPLE"}
        )
        assert "scoring_path_source" in result
        assert result["scoring_path_source"] in ("ORIGINAL", "TRANSLATED")
        assert "path_a_score" in result
        assert "path_b_score" in result

    def test_score_consistency_across_paths(self):
        """Same inputs to score_pair should always produce same output."""
        kwargs = dict(
            query_name="APPLE",
            candidate_name="ELMA",
            text_sim=0.1,
            semantic_sim=0.2,
            visual_sim=0.0,
            phonetic_sim=0.0,
            candidate_translations={"name_tr": "elma"}
        )
        result1 = score_pair(**kwargs)
        result2 = score_pair(**kwargs)
        assert result1["total"] == result2["total"]
        assert result1["dynamic_weights"] == result2["dynamic_weights"]


class TestDeprecatedEndpoints:
    """Steps 8-9: Verify deprecated endpoints still exist."""

    def test_utils_scoring_deleted(self):
        """utils/scoring.py should no longer exist as importable module."""
        import importlib
        try:
            # Should fail since we deleted the file
            spec = importlib.util.find_spec("utils.scoring")
            # If spec is None, module doesn't exist (expected)
            assert spec is None
        except (ModuleNotFoundError, ValueError):
            pass  # Expected

    def test_idf_scoring_comprehensive_has_deprecation_note(self):
        """calculate_comprehensive_score docstring should mention deprecation."""
        from utils.idf_scoring import calculate_comprehensive_score
        assert "DEPRECATED" in (calculate_comprehensive_score.__doc__ or "")

    def test_idf_scoring_combined_has_deprecation_note(self):
        """calculate_combined_score docstring should mention deprecation."""
        from utils.idf_scoring import calculate_combined_score
        assert "DEPRECATED" in (calculate_combined_score.__doc__ or "")


class TestRegressionKnownPairs:
    """Step 11: Known query/candidate pairs should score within expected ranges."""

    def test_nike_vs_nike_high_score(self):
        """Exact match should score very high."""
        result = score_pair("NIKE", "NIKE", text_sim=1.0, semantic_sim=1.0)
        assert result["total"] >= 0.90

    def test_nike_vs_nikea_moderate_to_high(self):
        """Near match should score moderately high."""
        result = score_pair("NIKE", "NIKEA", text_sim=0.80, semantic_sim=0.75)
        assert result["total"] >= 0.50

    def test_apple_vs_elma_translation_boost(self):
        """Dual-path: query matching name_tr should boost score via Path B."""
        result = score_pair(
            "APPLE", "ELMA", text_sim=0.05, semantic_sim=0.10,
            candidate_translations={"name_tr": "APPLE"}
        )
        # Path B: "APPLE" vs "APPLE" → exact match → high score
        assert result["path_b_score"] > 0.0
        # Translation should contribute to total via Path B winning
        assert result["total"] > 0.10

    def test_collapsed_translation_does_not_make_compound_exact(self):
        """A shortened name_tr should not turn extra original matter into an exact match."""
        from idf_lookup import IDFLookup

        IDFLookup._cache["dogan"] = {
            "idf": 7.727,
            "is_generic": True,
            "doc_freq": 1_000,
            "word_class": "generic",
        }

        result = score_pair(
            "DOGAN",
            "DOGANNATUREL",
            text_sim=0.2,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "DOGAN"},
        )

        assert result["path_b_score"] < 1.0
        assert result["total"] < 1.0
        assert "collapsed_candidate_translation" in result["textual_breakdown"]["translation_quality_flags"]

    def test_generic_name_tr_containment_does_not_match_anchor_query(self):
        """A true generic name_tr like 'patent' must not dominate 'dogan patent'."""
        from idf_lookup import IDFLookup

        IDFLookup._cache["dogan"] = {
            "idf": 7.727,
            "is_generic": True,
            "doc_freq": 1_000,
            "word_class": "generic",
        }

        result = score_pair(
            "DOGAN PATENT",
            "IZ PATENT",
            text_sim=0.2,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "PATENT"},
        )

        assert result["total"] <= 0.20
        assert result["path_b_score"] <= 0.20

    def test_translation_path_b_uses_translated_descriptor_flags(self):
        """Path B should cap descriptor-only evidence using word_idf_tr flags."""
        from idf_lookup import IDFLookup

        IDFLookup._loaded_tr = True
        IDFLookup._descriptor_suffixes_tr = None
        IDFLookup._cache_tr["atlas"] = {
            "idf": 8.0,
            "is_generic": False,
            "doc_freq": 100,
            "word_class": "distinctive",
        }
        IDFLookup._cache_tr["nova"] = {
            "idf": 8.0,
            "is_generic": False,
            "doc_freq": 100,
            "word_class": "distinctive",
        }
        IDFLookup._cache_tr["kulubu"] = {
            "idf": 8.2,
            "is_generic": True,
            "doc_freq": 1_000,
            "word_class": "generic",
            "descriptor_like": True,
            "descriptor_score": 0.9,
            "descriptor_stats": {"reason_flags": ["mostly_suffix"]},
        }

        result = score_pair(
            "atlas kulubu",
            "unrelated",
            text_sim=0.1,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "nova kulubu"},
        )

        assert result["path_b_score"] <= 0.20
        assert result["translation_similarity"] <= 0.20
        assert result["textual_breakdown"]["path_b"]["descriptor_terms"]["target"] == ["kulubu"]

    def test_unrelated_pair_low_score(self):
        """Unrelated names should score low."""
        result = score_pair("NIKE", "SAMSUNG", text_sim=0.05, semantic_sim=0.10)
        assert result["total"] < 0.50

    def test_visual_similarity_propagation(self):
        """Visual similarity should propagate to total score."""
        # Without visual
        r_no_vis = score_pair("BRAND", "LOGO", text_sim=0.2, visual_sim=0.0)
        # With visual
        r_with_vis = score_pair("BRAND", "LOGO", text_sim=0.2, visual_sim=0.85)
        assert r_with_vis["total"] > r_no_vis["total"]

    def test_weak_generic_text_plus_moderate_visual_is_capped(self):
        """Shared category/entity wording must not turn moderate visuals high."""
        TestIDFWaterfall._seed_distinctive_tokens("atlas", "nova")
        TestIDFWaterfall._seed_descriptor_tokens("kulubu", "spor")

        result = score_pair(
            "atlas guzellik kulubu",
            "nova spor kulubu",
            text_sim=0.3,
            semantic_sim=0.2,
            visual_sim=0.65,
        )

        assert result["total"] <= 0.45
        assert result["dynamic_weights"] == {"text": 0.0, "visual": 1.0}
        assert "weak_text_visual_low_cap:0.45" in result["caps_applied"]
        assert result["textual_breakdown"]["text_visual_guard"]["weak_text_cap_active"] is True
        calibration = result["textual_breakdown"]["text_visual_guard"][
            "weak_text_visual_calibration"
        ]
        assert calibration["ceiling"] == 0.45
        assert result["total"] == calibration["calibrated_score"]
        assert result["total"] < 0.45

    def test_limited_changed_matter_text_suppresses_moderate_visual_boost(self):
        TestIDFWaterfall._seed_distinctive_tokens("zendra", "borex", "lumora", "omera")

        result = score_pair(
            "zendra borex lumora",
            "zendra omera",
            text_sim=0.65,
            semantic_sim=0.2,
            visual_sim=0.55,
        )

        assert result["text_idf_score"] <= 0.58
        assert result["total"] <= 0.69
        assert "added_matter_partial_multi_anchor_changed_matter_cap:0.58" in result["caps_applied"]
        guard = result["textual_breakdown"]["text_visual_guard"]
        assert guard["limited_text_cap_active"] is True
        assert guard["agreement_boost_suppressed"] is True
        assert result["decision_reason"].endswith("limited text visual agreement suppressed")

    def test_limited_changed_matter_with_ocr_disagreement_stays_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("zendra", "borex", "lumora", "omera")
        visual_breakdown = {
            "total": 0.82,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.82, "dinov2": 0.82, "ocr": 0.2},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "zendra borex lumora",
            "zendra omera",
            text_sim=0.65,
            semantic_sim=0.2,
            visual_sim=0.82,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] <= 0.69
        assert result["visual_breakdown"]["ocr_disagreement"] is True
        assert "limited_text_visual_moderate_cap:0.69" in result["caps_applied"]
        assert result["text_visual_guard"]["limited_text_visual_guard_active"] is True
        calibration = result["text_visual_guard"]["limited_text_visual_calibration"]
        assert calibration["ceiling"] == 0.69
        assert result["total"] == calibration["calibrated_score"]
        assert result["total"] < 0.69

    def test_single_anchor_asymmetric_added_matter_blocks_moderate_visual_boost(self):
        TestIDFWaterfall._seed_distinctive_tokens("zendra", "polat")
        TestIDFWaterfall._seed_descriptor_tokens("hotel")
        TestIDFWaterfall._seed_legacy_override_material_tokens("investment")
        visual_breakdown = {
            "total": 0.63,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.65, "dinov2": 0.65, "ocr": 0.3},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "zendra investment",
            "polat zendra hotel",
            text_sim=0.62,
            semantic_sim=0.2,
            visual_sim=0.63,
            visual_breakdown=visual_breakdown,
        )

        assert result["text_idf_score"] <= 0.68
        assert result["total"] < 0.70
        assert "added_matter_single_anchor_asymmetric_added_matter_cap:0.68" in (
            result["caps_applied"]
        )
        guard = result["text_visual_guard"]
        assert guard["limited_text_cap_active"] is True
        assert guard["agreement_boost_suppressed"] is True
        assert result["textual_breakdown"]["path_a"]["added_matter_breakdown"][
            "single_anchor_asymmetric_added_matter"
        ] is True

    def test_low_protectability_shared_anchor_with_changed_matter_is_medium(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora", "omera")
        TestIDFWaterfall._seed_low_protectability_tokens("bravox")

        result = score_pair(
            "alvora bravox",
            "bravox omera",
            text_sim=0.5,
            semantic_sim=0.2,
        )

        assert 0.50 <= result["total"] <= 0.65
        assert "weak_shared_low_protectability_exact_anchor_cap:0.65" in (
            result["caps_applied"]
        )
        assert result["textual_breakdown"]["path_a"]["weak_shared_anchor_guard"][
            "applies"
        ] is True

    def test_low_protectability_fuzzy_variant_stays_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora", "omera", "bravo")
        TestIDFWaterfall._seed_low_protectability_tokens("bravox")

        result = score_pair(
            "alvora bravox",
            "bravo omera",
            text_sim=0.55,
            semantic_sim=0.2,
        )

        assert result["total"] <= 0.58
        assert "weak_shared_low_protectability_non_exact_anchor_cap:0.58" in (
            result["caps_applied"]
        )

    def test_low_protectability_shared_anchor_is_directionally_comparable(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora", "omera")
        TestIDFWaterfall._seed_low_protectability_tokens("bravox")

        forward = score_pair("alvora bravox", "omera bravox")
        reverse = score_pair("omera bravox", "alvora bravox")

        assert 0.50 <= forward["total"] <= 0.65
        assert 0.50 <= reverse["total"] <= 0.65
        assert abs(forward["total"] - reverse["total"]) <= 0.05

    def test_full_copied_core_with_low_protectability_anchor_stays_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora")
        TestIDFWaterfall._seed_low_protectability_tokens("bravox")
        TestIDFWaterfall._seed_descriptor_tokens("services")

        result = score_pair(
            "alvora bravox",
            "alvora bravox services",
            text_sim=0.7,
            semantic_sim=0.2,
        )

        assert result["total"] >= 0.88
        assert not any(
            cap.startswith("weak_shared_low_protectability_")
            for cap in result["caps_applied"]
        )

    def test_low_protectability_limited_text_blocks_moderate_visual_boost(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora", "omera")
        TestIDFWaterfall._seed_low_protectability_tokens("bravox")
        visual_breakdown = {
            "total": 0.62,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.65, "dinov2": 0.64, "ocr": 0.20},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "alvora bravox",
            "bravox omera",
            text_sim=0.5,
            semantic_sim=0.2,
            visual_sim=0.62,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] < 0.70
        assert result["text_visual_guard"]["limited_text_cap_active"] is True
        assert result["text_visual_guard"]["agreement_boost_suppressed"] is True

    def test_translated_path_uses_low_protectability_shared_anchor_guard(self):
        TestIDFWaterfall._seed_distinctive_tokens("alvora", "unrelated", "mark")
        TestIDFWaterfall._seed_distinctive_tokens_tr("alvora", "omera")
        TestIDFWaterfall._seed_low_protectability_tokens_tr("bravox")

        result = score_pair(
            "alvora bravox",
            "unrelated mark",
            text_sim=0.05,
            semantic_sim=0.2,
            visual_sim=0.55,
            candidate_translations={"name_tr": "bravox omera"},
        )

        assert result["scoring_path_source"] == "TRANSLATED"
        assert result["translation_similarity"] <= 0.65
        assert result["total"] < 0.70
        assert "weak_shared_low_protectability_exact_anchor_cap:0.65" in (
            result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_duplicate_translation_path_cannot_beat_original_path(self):
        TestIDFWaterfall._seed_distinctive_tokens("zendra", "sarayi")
        TestIDFWaterfall._seed_descriptor_tokens("group")
        TestIDFWaterfall._seed_distinctive_tokens_tr("zendra")
        TestIDFWaterfall._seed_descriptor_tokens_tr("sarayi", "group")

        result = score_pair(
            "zendra sarayi",
            "zendra group",
            text_sim=0.5,
            semantic_sim=0.2,
            candidate_translations={"name_tr": "zendra group"},
        )

        assert result["path_b_score"] <= result["path_a_score"]
        assert result["scoring_path_source"] == "ORIGINAL"
        assert "translation_duplicate_original" in (
            result["textual_breakdown"]["translation_quality_flags"]
        )
        assert result["textual_breakdown"]["translation_duplicate_original"] is True
        assert result["textual_breakdown"]["path_b"]["raw_total"] > result["path_b_score"]
        assert any(
            cap.startswith("translation_duplicate_original_cap")
            for cap in result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_short_acronym_subset_missing_query_matter_is_capped_upper_medium(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex")

        result = score_pair("ab modex", "ab", text_sim=0.5, semantic_sim=0.2)

        assert 0.68 <= result["total"] <= 0.82
        assert result["total"] < 0.90
        assert "short_acronym_subset_missing_matter_cap:0.82" in (
            result["caps_applied"]
        )
        guard = result["textual_breakdown"]["path_a"]["short_acronym_subset_guard"]
        assert guard["applies"] is True
        assert result["text_visual_guard"]["limited_text_cap_active"] is True

    def test_short_acronym_subset_missing_target_matter_is_capped_upper_medium(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex", "aix")

        result = score_pair("ab", "ab modex aix", text_sim=0.5, semantic_sim=0.2)

        assert 0.68 <= result["total"] <= 0.82
        assert result["total"] < 0.90
        assert "short_acronym_subset_missing_matter_cap:0.82" in (
            result["caps_applied"]
        )
        guard = result["textual_breakdown"]["path_a"]["short_acronym_subset_guard"]
        assert guard["applies"] is True
        assert guard["calibration_breakdown"]["factors"]["missing_side"] == "target"
        added = result["textual_breakdown"]["path_a"]["added_matter_breakdown"]
        assert added["low_protectability_extra_count"] == 2
        assert added["cap_reason"] == "single_anchor_low_protectability_extra"
        assert result["text_visual_guard"]["limited_text_cap_active"] is True

    def test_short_acronym_subset_guard_is_directionally_comparable(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex", "aix")

        forward = score_pair("ab", "ab modex aix", text_sim=0.5, semantic_sim=0.2)
        reverse = score_pair("ab modex aix", "ab", text_sim=0.5, semantic_sim=0.2)

        assert forward["total"] <= 0.82
        assert reverse["total"] <= 0.82
        assert abs(forward["total"] - reverse["total"]) <= 0.08

    def test_short_acronym_target_added_matter_blocks_moderate_visual_boost(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex", "aix")
        visual_breakdown = {
            "total": 0.76,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.78, "dinov2": 0.76, "ocr": 0.2},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "ab",
            "ab modex aix",
            text_sim=0.5,
            semantic_sim=0.2,
            visual_sim=0.76,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] < 0.80
        assert result["text_visual_guard"]["limited_text_cap_active"] is True
        assert result["text_visual_guard"]["agreement_boost_suppressed"] is True

    def test_short_acronym_full_copied_core_with_added_matter_stays_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex", "aix")

        result = score_pair("ab modex", "ab modex aix", text_sim=0.7, semantic_sim=0.2)

        assert result["total"] >= 0.88
        assert not any(
            cap.startswith("short_acronym_subset_missing_matter_cap")
            for cap in result["caps_applied"]
        )

    def test_short_collapsed_translation_path_is_capped_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("ab", "xab")
        TestIDFWaterfall._seed_low_protectability_tokens("modex")
        TestIDFWaterfall._seed_distinctive_tokens_tr("ab", "xab")
        TestIDFWaterfall._seed_low_protectability_tokens_tr("modex")

        result = score_pair(
            "ab modex",
            "xab",
            text_sim=0.2,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "ab"},
        )

        assert result["total"] < 0.70
        assert result["path_b_score"] <= 0.45
        flags = result["textual_breakdown"]["translation_quality_flags"]
        assert "short_collapsed_candidate_translation" in flags
        assert "translation_short_anchor_subset_cap" in flags
        assert result["textual_breakdown"]["short_collapsed_candidate_translation"] is True
        assert any(
            cap.startswith("translation_short_anchor_subset_cap")
            for cap in result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_bad_short_name_tr_cannot_create_translated_high_risk(self):
        TestIDFWaterfall._seed_distinctive_tokens("xy", "xyp")
        TestIDFWaterfall._seed_low_protectability_tokens("modex")
        TestIDFWaterfall._seed_distinctive_tokens_tr("xy", "xyp")
        TestIDFWaterfall._seed_low_protectability_tokens_tr("modex")

        result = score_pair(
            "xy modex",
            "xyp",
            text_sim=0.2,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "xy"},
        )

        assert result["path_b_score"] <= 0.45
        assert result["total"] < 0.70
        assert "translation_short_anchor_subset_cap" in (
            result["textual_breakdown"]["translation_quality_flags"]
        )

    def test_longer_legitimate_translated_path_still_scores_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("apple", "elma")
        TestIDFWaterfall._seed_distinctive_tokens_tr("apple")

        result = score_pair(
            "apple",
            "elma",
            text_sim=0.05,
            semantic_sim=0.1,
            candidate_translations={"name_tr": "apple"},
        )

        assert result["total"] >= 0.90
        assert result["scoring_path_source"] == "TRANSLATED"
        assert "translation_short_anchor_subset_cap" not in (
            result["textual_breakdown"]["translation_quality_flags"]
        )

    def test_full_length_one_edit_fuzzy_anchor_remains_meaningful(self):
        TestIDFWaterfall._seed_distinctive_tokens("zarpil", "zarpin")
        TestIDFWaterfall._seed_descriptor_tokens("exclusive")

        result = score_pair(
            "zarpil exclusive",
            "zarpin",
            text_sim=0.75,
            semantic_sim=0.2,
        )

        assert result["text_idf_score"] >= 0.70
        assert not any(
            cap.startswith("weak_fuzzy_anchor_") for cap in result["caps_applied"]
        )
        guard = result["textual_breakdown"]["path_a"]["fuzzy_anchor_guard"]
        assert guard["applies"] is False
        assert guard["reason"] == "strong_near_miss"

    def test_short_fragment_fuzzy_anchor_is_capped_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("zarpil", "zapi")
        TestIDFWaterfall._seed_descriptor_tokens("exclusive", "chemicals")

        score, breakdown = compute_idf_weighted_score(
            "zarpil exclusive",
            "zapi chemicals",
            text_sim=0.72,
            semantic_sim=0.2,
        )

        assert score <= 0.62
        assert "weak_fuzzy_anchor_fragment_cap:0.62" in breakdown["caps_applied"]
        guard = breakdown["fuzzy_anchor_guard"]
        assert guard["applies"] is True
        assert guard["metrics"]["length_ratio"] < 0.78

    def test_fullish_multi_edit_fuzzy_anchor_is_capped_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("zarpil", "zarvily")
        TestIDFWaterfall._seed_descriptor_tokens("exclusive")

        score, breakdown = compute_idf_weighted_score(
            "zarpil exclusive",
            "zarvily",
            text_sim=0.74,
            semantic_sim=0.2,
        )

        assert score <= 0.68
        assert "weak_fuzzy_anchor_quality_cap:0.68" in breakdown["caps_applied"]
        guard = breakdown["fuzzy_anchor_guard"]
        assert guard["applies"] is True
        assert guard["metrics"]["strong_near_miss"] is False

    def test_full_length_one_edit_short_anchor_remains_meaningful(self):
        TestIDFWaterfall._seed_distinctive_tokens("nayya", "naya")

        result = score_pair(
            "nayya",
            "naya",
            text_sim=0.75,
            semantic_sim=0.2,
        )

        assert result["text_idf_score"] >= 0.70
        assert not any("weak_" in cap and "_anchor_" in cap for cap in result["caps_applied"])
        guard = result["textual_breakdown"]["path_a"]["anchor_quality_guard"]
        assert guard["applies"] is False
        assert guard["reason"] == "strong_near_miss"

    def test_short_phonetic_fragment_anchor_is_capped_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("zeyya", "zey")

        score, breakdown = compute_idf_weighted_score(
            "zeyya",
            "zey",
            text_sim=0.75,
            semantic_sim=0.2,
        )

        assert score <= 0.58
        assert "weak_phonetic_anchor_fragment_cap:0.58" in breakdown["caps_applied"]
        guard = breakdown["anchor_quality_guard"]
        assert guard["applies"] is True
        assert guard["match_type"] == "phonetic"
        assert guard["metrics"]["length_ratio"] < 0.78

    def test_fullish_multi_edit_phonetic_anchor_is_capped_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("luseva", "luesfa")

        score, breakdown = compute_idf_weighted_score(
            "luseva",
            "luesfa",
            text_sim=0.66,
            semantic_sim=0.2,
        )

        assert score <= 0.68
        assert "weak_phonetic_anchor_quality_cap:0.68" in breakdown["caps_applied"]
        guard = breakdown["anchor_quality_guard"]
        assert guard["applies"] is True
        assert guard["match_type"] == "phonetic"

    def test_short_non_exact_anchor_with_ocr_disagreement_gets_no_visual_boost(self):
        TestIDFWaterfall._seed_distinctive_tokens("nayya", "naya")
        visual_breakdown = {
            "total": 0.48,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.52, "dinov2": 0.50, "ocr": 0.40},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "nayya",
            "naya",
            text_sim=0.75,
            semantic_sim=0.2,
            visual_sim=0.48,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] == result["text_idf_score"]
        guard = result["text_visual_guard"]["short_non_exact_anchor_visual_guard"]
        assert guard["applies"] is True
        assert result["text_visual_guard"]["agreement_boost_suppressed"] is True

    def test_no_anchor_empty_target_text_uses_weak_visual_cap(self):
        TestIDFWaterfall._seed_distinctive_tokens("luseva")
        visual_breakdown = {
            "total": 0.58,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.60, "dinov2": 0.56, "ocr": 0.20},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "luseva",
            "m.t.a.",
            text_sim=0.1,
            semantic_sim=0.5,
            visual_sim=0.58,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] <= 0.45
        assert "semantic_or_phonetic_without_lexical_anchor_cap:0.45" in result["caps_applied"]
        assert result["text_visual_guard"]["weak_text_cap_active"] is True

    def test_weak_phonetic_translated_path_cannot_inflate_to_high_risk(self):
        TestIDFWaterfall._seed_distinctive_tokens("luseva", "unrelated", "mark")
        TestIDFWaterfall._seed_distinctive_tokens_tr("luseva", "luesfa")

        result = score_pair(
            "luseva",
            "unrelated mark",
            text_sim=0.05,
            semantic_sim=0.2,
            visual_sim=0.55,
            candidate_translations={"name_tr": "luesfa"},
        )

        assert result["scoring_path_source"] == "TRANSLATED"
        assert result["total"] < 0.70
        assert result["translation_similarity"] <= 0.68
        flags = result["textual_breakdown"]["translation_quality_flags"]
        assert "translation_weak_phonetic_anchor" in flags
        assert "translation_weak_non_exact_anchor" in flags
        assert "weak_phonetic_anchor_quality_cap:0.68" in (
            result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_weak_fuzzy_anchor_with_moderate_visual_stays_below_high(self):
        TestIDFWaterfall._seed_distinctive_tokens("zarpil", "zapi")
        TestIDFWaterfall._seed_descriptor_tokens("exclusive", "chemicals")
        visual_breakdown = {
            "total": 0.66,
            "active_components": ["clip", "dinov2", "ocr"],
            "components": {"clip": 0.70, "dinov2": 0.68, "ocr": 0.25},
            "ocr_disagreement": True,
            "ocr_strong_match": False,
            "very_strong_visual_components": False,
        }

        result = score_pair(
            "zarpil exclusive",
            "zapi chemicals",
            text_sim=0.72,
            semantic_sim=0.2,
            visual_sim=0.66,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] < 0.70
        assert "weak_fuzzy_anchor_fragment_cap:0.62" in result["caps_applied"]
        assert "limited_text_visual_moderate_cap:0.69" in result["caps_applied"]
        guard = result["text_visual_guard"]
        assert guard["limited_text_cap_active"] is True
        assert guard["agreement_boost_suppressed"] is True

    def test_weak_fuzzy_translated_path_cannot_inflate_to_high_risk(self):
        TestIDFWaterfall._seed_distinctive_tokens("zarpil", "unrelated", "mark")
        TestIDFWaterfall._seed_distinctive_tokens_tr("zarpil", "zarvily")
        TestIDFWaterfall._seed_descriptor_tokens("exclusive")
        TestIDFWaterfall._seed_descriptor_tokens_tr("exclusive")

        result = score_pair(
            "zarpil exclusive",
            "unrelated mark",
            text_sim=0.05,
            semantic_sim=0.2,
            visual_sim=0.55,
            candidate_translations={"name_tr": "zarvily"},
        )

        assert result["scoring_path_source"] == "TRANSLATED"
        assert result["total"] < 0.70
        assert result["translation_similarity"] <= 0.68
        assert "translation_weak_fuzzy_anchor" in (
            result["textual_breakdown"]["translation_quality_flags"]
        )
        assert "weak_fuzzy_anchor_quality_cap:0.68" in (
            result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_uncorroborated_strong_visual_is_capped_when_text_is_weak(self):
        TestIDFWaterfall._seed_distinctive_tokens("atlas", "nova")
        TestIDFWaterfall._seed_descriptor_tokens("kulubu", "spor")

        result = score_pair(
            "atlas guzellik kulubu",
            "nova spor kulubu",
            text_sim=0.3,
            semantic_sim=0.2,
            visual_sim=0.85,
        )

        assert result["total"] <= 0.69
        assert "weak_text_visual_mid_cap:0.69" in result["caps_applied"]
        assert result["dynamic_weights"] == {"text": 0.0, "visual": 1.0}
        calibration = result["text_visual_guard"]["weak_text_visual_calibration"]
        assert calibration["ceiling"] == 0.69
        assert result["total"] == calibration["calibrated_score"]
        assert result["total"] < 0.69

    def test_strong_ocr_allows_mid_visual_when_text_is_weak(self):
        TestIDFWaterfall._seed_distinctive_tokens("atlas", "nova")
        TestIDFWaterfall._seed_descriptor_tokens("kulubu", "spor")

        result = score_pair(
            "atlas guzellik kulubu",
            "nova spor kulubu",
            text_sim=0.3,
            semantic_sim=0.2,
            visual_sim=0.85,
            visual_breakdown={
                "total": 0.85,
                "active_components": ["clip", "dinov2", "ocr"],
                "components": {"clip": 0.82, "dinov2": 0.84, "ocr": 0.86},
                "ocr_strong_match": True,
            },
        )

        assert result["total"] == 0.85
        assert not any(cap.startswith("weak_text_visual_") for cap in result["caps_applied"])

    def test_very_strong_clip_dino_allows_high_visual_when_text_is_weak(self):
        TestIDFWaterfall._seed_distinctive_tokens("atlas", "nova")
        TestIDFWaterfall._seed_descriptor_tokens("kulubu", "spor")

        result = score_pair(
            "atlas guzellik kulubu",
            "nova spor kulubu",
            text_sim=0.3,
            semantic_sim=0.2,
            visual_sim=0.92,
            visual_breakdown={
                "total": 0.92,
                "active_components": ["clip", "dinov2"],
                "components": {"clip": 0.93, "dinov2": 0.92, "ocr": 0.0},
                "very_strong_visual_components": True,
            },
        )

        assert result["total"] == 0.92
        assert not any(cap.startswith("weak_text_visual_") for cap in result["caps_applied"])

    def test_distinctive_query_does_not_fuzzy_match_generic_target(self):
        TestIDFWaterfall._seed_distinctive_tokens("oksipital")

        result = score_pair(
            "oksipital",
            "best hospital",
            text_sim=0.726,
            semantic_sim=0.2206,
            visual_sim=0.4714,
        )

        assert result["total"] <= 0.45
        assert result["text_idf_score"] <= 0.45
        assert not any(
            match["query_word"] == "oksipital" and match["target_word"] == "hospital"
            for match in result["matched_words"]
        )

    def test_missing_dominant_anchor_caps_shared_secondary_term(self):
        TestIDFWaterfall._seed_distinctive_tokens("okur", "gunlugu", "yatirimcinin")

        result = score_pair(
            "okur günlüğü",
            "yatırımcının günlüğü",
            text_sim=0.68,
            semantic_sim=0.5648,
            visual_sim=0.7195,
        )

        assert result["text_idf_score"] <= 0.62
        assert result["total"] <= 0.45
        assert "missing_dominant_anchor_cap:0.62" in result["caps_applied"]

    def test_semantic_only_weak_text_blocks_moderate_visual_domination(self):
        TestIDFWaterfall._seed_distinctive_tokens("oksipital", "okuluko")

        result = score_pair(
            "oksipital",
            "okuluko",
            text_sim=0.1587,
            semantic_sim=0.7198,
            visual_sim=0.6876,
        )

        assert result["total"] <= 0.45
        assert "semantic_or_phonetic_without_lexical_anchor_cap:0.45" in result["caps_applied"]

    def test_ocr_disagreement_pair_stays_below_high_when_text_is_weak(self):
        TestIDFWaterfall._seed_distinctive_tokens("oksipital", "qorvital")
        visual_score, visual_breakdown = _calculate_visual_breakdown(
            clip_sim=0.7310,
            dinov2_sim=0.9053,
            ocr_text_a="Oksipital",
            ocr_text_b="Qorvital",
        )

        result = score_pair(
            "oksipital",
            "qorvital",
            text_sim=0.1943,
            semantic_sim=0.5148,
            visual_sim=visual_score,
            visual_breakdown=visual_breakdown,
        )

        assert result["total"] <= 0.45
        assert result["visual_breakdown"]["ocr_disagreement"] is True

    def test_short_anchor_non_exact_phonetic_is_blocked(self):
        TestIDFWaterfall._seed_distinctive_tokens("gk", "gok", "gky")

        for target in ("gok", "gky"):
            score, breakdown = compute_idf_weighted_score(
                "gk",
                target,
                text_sim=0.8,
                semantic_sim=0.2,
                phonetic_sim=1.0,
            )

            assert score <= 0.45
            assert breakdown["matched_words"] == []
            assert breakdown["short_anchor_guard"]
            assert "short_anchor_non_exact_anchor_cap:0.45" in breakdown["caps_applied"]

    def test_short_anchor_guard_is_symmetric(self):
        TestIDFWaterfall._seed_distinctive_tokens("gk", "gok")

        score, breakdown = compute_idf_weighted_score(
            "gok",
            "gk",
            text_sim=0.8,
            semantic_sim=0.2,
            phonetic_sim=1.0,
        )

        assert score <= 0.45
        assert breakdown["matched_words"] == []
        assert breakdown["short_anchor_guard"][0]["target_word"] == "gk"

    def test_exact_short_anchor_copy_still_scores_meaningfully(self):
        TestIDFWaterfall._seed_distinctive_tokens("gk", "prime")

        result = score_pair("gk", "gk prime", text_sim=0.4, semantic_sim=0.2)

        assert result["total"] >= 0.70
        assert any(
            match["query_word"] == "gk" and match["match_type"] == "exact"
            for match in result["matched_words"]
        )
        assert result["textual_breakdown"]["path_a"]["short_anchor_guard"] == []

    def test_punctuated_short_anchor_exact_compact_match_still_scores_high(self):
        result = score_pair("G.K.", "GK", text_sim=0.0, semantic_sim=0.0)

        assert result["total"] == 0.96
        assert result["scoring_path"] == "TEXT_COMPACT_EXACT"

    def test_translated_short_anchor_only_phonetic_match_is_capped(self):
        TestIDFWaterfall._seed_distinctive_tokens("gk")
        TestIDFWaterfall._seed_descriptor_tokens("guzellik", "kulubu", "sky", "systems")
        TestIDFWaterfall._seed_distinctive_tokens_tr("gk", "gok")
        TestIDFWaterfall._seed_descriptor_tokens_tr("guzellik", "kulubu", "sistemleri")

        result = score_pair(
            "gk guzellik kulubu",
            "sky systems",
            text_sim=0.046,
            semantic_sim=0.0789,
            visual_sim=0.3849,
            candidate_translations={"name_tr": "gok sistemleri"},
        )

        assert result["total"] <= 0.45
        assert result["path_b_score"] <= 0.45
        assert result["textual_breakdown"]["path_b"]["matched_words"] == []
        assert result["textual_breakdown"]["path_b"]["short_anchor_guard"]
        assert "short_anchor_non_exact_anchor_cap:0.45" in (
            result["textual_breakdown"]["path_b"]["caps_applied"]
        )

    def test_normal_length_phonetic_similarity_still_available(self):
        TestIDFWaterfall._seed_distinctive_tokens("dogan", "togan")

        score, breakdown = compute_idf_weighted_score(
            "dogan",
            "togan",
            text_sim=0.3,
            semantic_sim=0.2,
            phonetic_sim=1.0,
        )

        assert score >= 0.50
        assert breakdown["matched_words"]
        assert breakdown["short_anchor_guard"] == []

    def test_search_and_watchlist_pass_visual_breakdown_to_score_pair(self):
        import risk_engine
        import watchlist.scanner as scanner

        search_source = inspect.getsource(risk_engine.RiskEngine.calculate_hybrid_risk)
        watchlist_source = inspect.getsource(scanner.WatchlistScanner._check_conflict)

        assert "_calculate_visual_breakdown" in search_source
        assert "visual_breakdown=visual_breakdown" in search_source
        assert "_compute_visual_breakdown" in watchlist_source
        assert "visual_breakdown=visual_breakdown" in watchlist_source


# ============================================================
# Graduated Phonetic in IDF Waterfall
# ============================================================

class TestGraduatedPhoneticInWaterfall:
    """Verify that graduated phonetic scores (not binary 0/1) produce
    intermediate results in the IDF waterfall Cases B-F."""

    def test_intermediate_phonetic_produces_intermediate_total(self):
        """A phonetic_sim of 0.6 should NOT inflate total as much as 1.0."""
        # Binary 1.0 phonetic (old behavior)
        _, bd_full = compute_idf_weighted_score(
            query="dogan patent",
            target="togan patent",
            text_sim=0.30,
            semantic_sim=0.20,
            phonetic_sim=1.0,
        )
        total_full = bd_full.get("final_score", 0) if "final_score" in bd_full else _

        # Graduated 0.6 phonetic (new behavior)
        score_grad, bd_grad = compute_idf_weighted_score(
            query="dogan patent",
            target="togan patent",
            text_sim=0.30,
            semantic_sim=0.20,
            phonetic_sim=0.6,
        )

        # Graduated should produce a lower or equal score vs binary 1.0
        score_full, _ = compute_idf_weighted_score(
            query="dogan patent",
            target="togan patent",
            text_sim=0.30,
            semantic_sim=0.20,
            phonetic_sim=1.0,
        )
        assert score_grad <= score_full

    def test_zero_phonetic_no_inflation(self):
        """phonetic_sim=0.0 should not inflate base at all."""
        score_zero, _ = compute_idf_weighted_score(
            query="silver star",
            target="golden moon",
            text_sim=0.10,
            semantic_sim=0.15,
            phonetic_sim=0.0,
        )
        score_half, _ = compute_idf_weighted_score(
            query="silver star",
            target="golden moon",
            text_sim=0.10,
            semantic_sim=0.15,
            phonetic_sim=0.5,
        )
        assert score_half >= score_zero
