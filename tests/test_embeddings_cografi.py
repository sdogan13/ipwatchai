"""Unit tests for ``embeddings_cografi`` pure helpers.

Avoids importing torch / sentence-transformers / open_clip so the suite
runs without GPU and without the heavy ML deps. The model-loading and
inference code paths are exercised manually at runtime via
``embeddings_cografi.py --all``; this file pins the pure-Python
helpers that the orchestration code depends on.
"""

from __future__ import annotations

import pytest

from embeddings_cografi import (
    CLIP_DIM,
    DINOV2_DIM,
    TEXT_BODY_SECTION_KEYS,
    TEXT_DIM,
    TEXT_HEADER_FIELDS,
    TEXT_MODEL_NAME,
    build_text_passage,
    figure_already_embedded,
    iter_records,
    mean_pool,
    record_already_embedded,
    select_embeddable_figures,
)


# ---------------------------------------------------------------------------
# Constants — pinned so accidental edits break loudly
# ---------------------------------------------------------------------------

def test_module_dimensions_match_documented_models():
    assert DINOV2_DIM == 1024
    assert CLIP_DIM == 512
    assert TEXT_DIM == 1024
    assert TEXT_MODEL_NAME == "intfloat/multilingual-e5-large"


def test_text_field_order_is_header_then_body():
    """Concatenation order is fixed and must include the major signal
    fields before the long body subsections (so truncation tail-loses
    body text first, not the name/region)."""
    assert TEXT_HEADER_FIELDS[0] == "name"
    assert "gi_type" in TEXT_HEADER_FIELDS
    assert "product_group" in TEXT_HEADER_FIELDS
    assert "geographical_boundary" in TEXT_HEADER_FIELDS
    assert "usage_description" in TEXT_HEADER_FIELDS
    assert "product_description" in TEXT_BODY_SECTION_KEYS
    assert "production_method" in TEXT_BODY_SECTION_KEYS


# ---------------------------------------------------------------------------
# mean_pool
# ---------------------------------------------------------------------------

def test_mean_pool_returns_empty_for_empty_input():
    assert mean_pool([]) == []


def test_mean_pool_averages_componentwise():
    vecs = [[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]]
    assert mean_pool(vecs) == [2.0, 3.0, 4.0]


def test_mean_pool_rejects_mismatched_widths():
    with pytest.raises(ValueError):
        mean_pool([[1.0, 2.0], [3.0]])


def test_mean_pool_single_vector_passthrough():
    assert mean_pool([[0.1, 0.2, 0.3]]) == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# build_text_passage
# ---------------------------------------------------------------------------

def test_build_text_passage_includes_e5_prefix_and_known_fields():
    record = {
        "name": "Karapınar Halısı",
        "gi_type": "Mahreç işareti",
        "product_group": "Halı / Halılar ve kilimler",
        "geographical_boundary": "Konya ili Karapınar ilçesi",
        "usage_description": "Karapınar Halısı ibaresi ve mahreç işareti amblemi ürünün ambalajı üzerinde yer alır.",
        "body_sections": {
            "product_description": "Karapınar Halısı; saf yün kullanılarak Türk düğüm tekniği ile dokunan bir halı türüdür.",
            "production_method": "Halı dokumacılığı yünün eğirilmesinden başlar.",
        },
    }
    passage = build_text_passage(record)
    assert passage.startswith("passage: ")
    # All non-empty fields appear in order.
    for fragment in [
        "Karapınar Halısı",
        "Mahreç işareti",
        "Halı / Halılar ve kilimler",
        "Konya ili Karapınar ilçesi",
        "Karapınar Halısı ibaresi",
        "saf yün kullanılarak Türk düğüm",
        "yünün eğirilmesinden",
    ]:
        assert fragment in passage


def test_build_text_passage_skips_empty_and_missing_fields():
    record = {
        "name": "Yalnız İsim",
        "gi_type": None,
        "product_group": "",
        "geographical_boundary": "  ",
        # usage_description missing entirely
    }
    passage = build_text_passage(record)
    assert passage == "passage: Yalnız İsim"


def test_build_text_passage_handles_body_sections_only():
    """Some legacy records have no header fields but do carry body
    free-text. The passage should still be emitted from body alone."""
    record = {
        "body_sections": {
            "product_description": "Sadece üründen söz eden kısa bir tanım.",
        },
    }
    passage = build_text_passage(record)
    assert passage == "passage: Sadece üründen söz eden kısa bir tanım."


def test_build_text_passage_returns_empty_when_no_text():
    """A bare art42 stub with only record_type / start_page has no
    embeddable text — caller short-circuits to a zero vector rather than
    feeding the encoder an empty prompt."""
    record = {"record_type": "GI", "start_page": 42}
    assert build_text_passage(record) == ""


def test_build_text_passage_skips_unknown_body_subsection_keys():
    """Body sections we don't recognise are not in the embed mix.
    Keeps the passage stable across schema additions."""
    record = {
        "name": "Test",
        "body_sections": {
            "unrecognised_subsection": "Should not appear in passage.",
            "product_description": "Should appear.",
        },
    }
    passage = build_text_passage(record)
    assert "Should appear." in passage
    assert "Should not appear in passage." not in passage


# ---------------------------------------------------------------------------
# figure_already_embedded / record_already_embedded
# ---------------------------------------------------------------------------

def test_figure_already_embedded_requires_both_vectors():
    assert figure_already_embedded({}) is False
    assert figure_already_embedded({"embeddings": {}}) is False
    assert figure_already_embedded({"embeddings": {"dinov2_vitl14": [0.1]}}) is False
    assert figure_already_embedded({
        "embeddings": {"dinov2_vitl14": [0.1], "clip_vitb32": [0.2]}
    }) is True


def test_figure_already_embedded_rejects_empty_lists():
    assert figure_already_embedded({
        "embeddings": {"dinov2_vitl14": [], "clip_vitb32": [0.2]}
    }) is False


def test_record_already_embedded_text_only_when_no_figures():
    """A record without figures is complete once it has text_embedding."""
    rec = {"text_embedding": [0.1] * 1024}
    assert record_already_embedded(rec) is True


def test_record_already_embedded_requires_primary_and_per_figure_when_figures_exist():
    rec = {
        "text_embedding": [0.1] * 1024,
        "figures": [{"image_path": "x/1.jpg"}],
    }
    # Has text but no primary_figure_embedding -> not complete
    assert record_already_embedded(rec) is False

    rec["primary_figure_embedding"] = [0.0] * 1024
    # Still missing per-figure embeddings
    assert record_already_embedded(rec) is False

    rec["figures"][0]["embeddings"] = {
        "dinov2_vitl14": [0.0] * 1024,
        "clip_vitb32": [0.0] * 512,
    }
    assert record_already_embedded(rec) is True


def test_record_already_embedded_no_text_means_not_done():
    """Even if all figure aggregates are present, missing text fails."""
    rec = {
        "figures": [],
        "primary_figure_embedding": [0.0] * 1024,
    }
    assert record_already_embedded(rec) is False


# ---------------------------------------------------------------------------
# select_embeddable_figures
# ---------------------------------------------------------------------------

def test_select_embeddable_figures_filters_missing_image_path():
    rec = {
        "figures": [
            {"image_path": "C2022_000469/1.jpg"},
            {"image_path": ""},
            {"image_path": None},
            {"page": 8},  # no image_path key at all
            {"image_path": "C2022_000469/2.jpg"},
        ]
    }
    out = select_embeddable_figures(rec)
    assert len(out) == 2
    assert out[0]["image_path"] == "C2022_000469/1.jpg"
    assert out[1]["image_path"] == "C2022_000469/2.jpg"


def test_select_embeddable_figures_handles_no_figures_key():
    assert select_embeddable_figures({}) == []
    assert select_embeddable_figures({"figures": None}) == []


# ---------------------------------------------------------------------------
# iter_records
# ---------------------------------------------------------------------------

def test_iter_records_flattens_all_section_keys():
    metadata = {
        "records": {
            "examined": [{"name": "A"}, {"name": "B"}],
            "registered": [{"name": "C"}],
            "article_42_change_requests": [{"name": "D"}],
            "corrections": [],
        }
    }
    out = iter_records(metadata)
    assert [r["name"] for r in out] == ["A", "B", "C", "D"]


def test_iter_records_safe_on_missing_records_key():
    assert iter_records({}) == []
    assert iter_records({"records": None}) == []
