"""Unit tests for pure helpers in ``embeddings_patent``.

Model-loading + image/text inference are exercised by the live smoke
test (gated on GPU + bulletin data on disk) at the bottom.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from embeddings_patent import (
    CLIP_DIM,
    DINOV2_DIM,
    TEXT_DIM,
    figure_already_embedded,
    mean_pool,
    record_already_embedded,
    select_embeddable_figures,
)


# ---------------------------------------------------------------------------
# mean_pool
# ---------------------------------------------------------------------------


def test_mean_pool_averages_equal_length_vectors() -> None:
    assert mean_pool([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]) == [2.5, 3.5, 4.5]


def test_mean_pool_empty_input_returns_empty() -> None:
    assert mean_pool([]) == []


def test_mean_pool_zero_width_returns_empty() -> None:
    """All-empty vectors: width=0 short-circuit returns []."""
    assert mean_pool([[], []]) == []


def test_mean_pool_raises_on_unequal_widths() -> None:
    with pytest.raises(ValueError, match="equal-length vectors"):
        mean_pool([[1.0, 2.0], [3.0]])


def test_mean_pool_single_vector_passes_through() -> None:
    assert mean_pool([[1.0, 2.0, 3.0]]) == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# figure_already_embedded
# ---------------------------------------------------------------------------


def test_figure_already_embedded_true_when_both_present() -> None:
    fig = {"image_path": "figures/x.tif", "embeddings": {
        "dinov2_vitl14": [0.1] * DINOV2_DIM,
        "clip_vitb32":   [0.2] * CLIP_DIM,
    }}
    assert figure_already_embedded(fig) is True


def test_figure_already_embedded_false_when_missing_one() -> None:
    fig = {"embeddings": {"dinov2_vitl14": [0.1] * DINOV2_DIM}}
    assert figure_already_embedded(fig) is False


def test_figure_already_embedded_false_when_no_embeddings_key() -> None:
    assert figure_already_embedded({"image_path": "x"}) is False


def test_figure_already_embedded_false_when_empty_lists() -> None:
    """Empty list shouldn't count as embedded."""
    fig = {"embeddings": {"dinov2_vitl14": [], "clip_vitb32": []}}
    assert figure_already_embedded(fig) is False


# ---------------------------------------------------------------------------
# record_already_embedded
# ---------------------------------------------------------------------------


def test_record_already_embedded_true_when_text_only_and_no_figures() -> None:
    """Record with no embeddable figures only needs the text embedding."""
    rec = {
        "title_abstract_embedding": [0.0] * TEXT_DIM,
        "figures": [],
    }
    assert record_already_embedded(rec) is True


def test_record_already_embedded_true_when_only_dedup_dropped_figures() -> None:
    """Figures with image_path=None (PDF dedup'd against CD TIFF) don't
    count as 'embeddable' — text-only embedding is enough."""
    rec = {
        "title_abstract_embedding": [0.0] * TEXT_DIM,
        "figures": [{"page": 1847, "image_path": None}],
    }
    assert record_already_embedded(rec) is True


def test_record_already_embedded_false_when_figure_unembedded() -> None:
    rec = {
        "title_abstract_embedding": [0.0] * TEXT_DIM,
        "primary_figure_embedding": [0.0] * DINOV2_DIM,
        "figures": [{"image_path": "figures/x.tif"}],   # no embeddings key
    }
    assert record_already_embedded(rec) is False


def test_record_already_embedded_false_when_missing_primary() -> None:
    """Has figures + text + per-figure embeddings, but no aggregate."""
    rec = {
        "title_abstract_embedding": [0.0] * TEXT_DIM,
        "figures": [{
            "image_path": "figures/x.tif",
            "embeddings": {
                "dinov2_vitl14": [0.0] * DINOV2_DIM,
                "clip_vitb32":   [0.0] * CLIP_DIM,
            },
        }],
    }
    assert record_already_embedded(rec) is False


def test_record_already_embedded_true_when_complete() -> None:
    rec = {
        "title_abstract_embedding": [0.0] * TEXT_DIM,
        "primary_figure_embedding": [0.0] * DINOV2_DIM,
        "figures": [{
            "image_path": "figures/x.tif",
            "embeddings": {
                "dinov2_vitl14": [0.0] * DINOV2_DIM,
                "clip_vitb32":   [0.0] * CLIP_DIM,
            },
        }],
    }
    assert record_already_embedded(rec) is True


def test_record_already_embedded_false_when_no_text() -> None:
    rec = {"figures": [], "title_abstract_embedding": []}
    assert record_already_embedded(rec) is False


# ---------------------------------------------------------------------------
# select_embeddable_figures
# ---------------------------------------------------------------------------


def test_select_embeddable_figures_filters_null_image_paths() -> None:
    """PDF figures dedup'd against CD TIFFs have image_path=None — they
    keep their page/xref metadata but no file on disk so can't embed."""
    rec = {"figures": [
        {"image_path": "figures/2023_018085.tif"},
        {"image_path": None, "page": 1847},
        {"image_path": "", "page": 99},
        {"image_path": "figures/other.png", "page": 100},
    ]}
    result = select_embeddable_figures(rec)
    assert len(result) == 2
    assert result[0]["image_path"] == "figures/2023_018085.tif"
    assert result[1]["image_path"] == "figures/other.png"


def test_select_embeddable_figures_no_figures_key() -> None:
    """Defensive: record without a figures field returns []."""
    assert select_embeddable_figures({}) == []
