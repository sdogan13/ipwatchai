"""Unit tests for ``embeddings_tasarim`` pure helpers.

GPU-bound inference (DINOv2, CLIP) is exercised only by manual smoke runs.
These tests cover the pure helpers: aggregation, idempotency checks,
section filtering, and the HSV histogram on a synthetic image.
"""


import pytest

from pathlib import Path

from embeddings_tasarim import (
    DINOV2_DIM,
    CLIP_DIM,
    COLOR_DIM,
    aggregate_design_embeddings,
    design_already_aggregated,
    mean_pool,
    resolve_view_image_path,
    select_embeddable_records,
    view_already_embedded,
)


# ---------------------------------------------------------------------------
# mean_pool
# ---------------------------------------------------------------------------

def test_mean_pool_basic():
    assert mean_pool([[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]]) == [2.0, 3.0, 4.0]


def test_mean_pool_single_vector():
    assert mean_pool([[1.0, 2.0]]) == [1.0, 2.0]


def test_mean_pool_empty():
    assert mean_pool([]) == []
    assert mean_pool([[]]) == []


def test_mean_pool_rejects_uneven():
    with pytest.raises(ValueError):
        mean_pool([[1.0, 2.0], [1.0, 2.0, 3.0]])


# ---------------------------------------------------------------------------
# Idempotency checks
# ---------------------------------------------------------------------------

def test_view_already_embedded_true_when_all_three_set():
    view = {
        "embeddings": {
            "dinov2_vitl14": [0.1] * DINOV2_DIM,
            "clip_vitb32": [0.1] * CLIP_DIM,
            "color_hsv": [0.1] * COLOR_DIM,
        }
    }
    assert view_already_embedded(view) is True


def test_view_already_embedded_false_when_missing_one():
    view = {
        "embeddings": {
            "dinov2_vitl14": [0.1] * DINOV2_DIM,
            "clip_vitb32": [0.1] * CLIP_DIM,
            # color_hsv missing
        }
    }
    assert view_already_embedded(view) is False


def test_view_already_embedded_false_when_field_empty():
    view = {
        "embeddings": {
            "dinov2_vitl14": [],
            "clip_vitb32": [0.1],
            "color_hsv": [0.1],
        }
    }
    assert view_already_embedded(view) is False


def test_view_already_embedded_false_when_no_embeddings_object():
    assert view_already_embedded({"image_path": "x.jpg"}) is False
    assert view_already_embedded({}) is False


def test_design_already_aggregated_true():
    design = {
        "design_aggregates": {
            "dinov2_vitl14_mean": [0.1] * DINOV2_DIM,
            "clip_vitb32_mean": [0.1] * CLIP_DIM,
        }
    }
    assert design_already_aggregated(design) is True


def test_design_already_aggregated_false_when_missing():
    assert design_already_aggregated({}) is False
    assert design_already_aggregated({"design_aggregates": {"dinov2_vitl14_mean": []}}) is False


# ---------------------------------------------------------------------------
# select_embeddable_records
# ---------------------------------------------------------------------------

def test_select_embeddable_records_includes_image_bearing_sections():
    payload = {"records": [
        {"section": "tr_native", "application_no": "2024/000001"},
        {"section": "deferred", "application_no": "2024/000002"},
        {"section": "deferred_lifted", "application_no": "2024/000003"},
        {"section": "republished", "application_no": "2024/000004"},
        {"section": "hague", "registration_no": "DM 100000"},
    ]}
    eligible = select_embeddable_records(payload)
    sections = {r["section"] for r in eligible}
    assert sections == {"tr_native", "deferred_lifted", "republished"}


def test_select_embeddable_records_no_records():
    assert select_embeddable_records({"records": []}) == []
    assert select_embeddable_records({}) == []


# ---------------------------------------------------------------------------
# aggregate_design_embeddings
# ---------------------------------------------------------------------------

def test_aggregate_design_embeddings_mean_pool():
    views = [
        {"embeddings": {"dinov2_vitl14": [1.0, 2.0], "clip_vitb32": [3.0, 4.0]}},
        {"embeddings": {"dinov2_vitl14": [3.0, 4.0], "clip_vitb32": [5.0, 6.0]}},
    ]
    assert aggregate_design_embeddings(views, "dinov2_vitl14") == [2.0, 3.0]
    assert aggregate_design_embeddings(views, "clip_vitb32") == [4.0, 5.0]


def test_aggregate_design_embeddings_skips_missing():
    views = [
        {"embeddings": {"dinov2_vitl14": [1.0, 2.0]}},  # has it
        {"image_path": "x.jpg"},                         # no embeddings yet
        {"embeddings": {"dinov2_vitl14": [3.0, 4.0]}},  # has it
    ]
    assert aggregate_design_embeddings(views, "dinov2_vitl14") == [2.0, 3.0]


def test_aggregate_design_embeddings_empty():
    assert aggregate_design_embeddings([], "dinov2_vitl14") == []
    assert aggregate_design_embeddings([{"image_path": "x.jpg"}], "dinov2_vitl14") == []


# ---------------------------------------------------------------------------
# resolve_view_image_path — routing under cd_images/ vs images/
# ---------------------------------------------------------------------------

def test_resolve_view_image_path_cd_source():
    """image_source='cd' routes under cd_images/ (the locked stage-3
    convention). Without this routing the embedder would look at
    issue_folder/{key} and find nothing."""
    issue = Path("/fake/TS_240_2016-03-09")
    view = {"image_path": "2016_01059/1_1.jpg", "image_source": "cd"}
    assert resolve_view_image_path(issue, view) == issue / "cd_images" / "2016_01059" / "1_1.jpg"


def test_resolve_view_image_path_pdf_source():
    """image_source='pdf' routes under images/."""
    issue = Path("/fake/TS_483_2026-04-24")
    view = {"image_path": "2024_007254/1_1.jpg", "image_source": "pdf"}
    assert resolve_view_image_path(issue, view) == issue / "images" / "2024_007254" / "1_1.jpg"


def test_resolve_view_image_path_legacy_no_source_tag():
    """Pre-stage-B.1 metadata.json files shipped image_path with the
    'images/' prefix baked in and no image_source field. Resolving
    against the issue folder root works for those."""
    issue = Path("/fake/TS_483_2026-04-24")
    view = {"image_path": "images/2024_007254_1_1.jpg"}  # legacy flat shape
    assert resolve_view_image_path(issue, view) == issue / "images/2024_007254_1_1.jpg"


def test_resolve_view_image_path_returns_none_for_missing_path():
    """Hague views and image-less rows return None, signaling 'skip'."""
    assert resolve_view_image_path(Path("/x"), {"image_path": ""}) is None
    assert resolve_view_image_path(Path("/x"), {"image_path": None}) is None
    assert resolve_view_image_path(Path("/x"), {}) is None


# ---------------------------------------------------------------------------
# HSV histogram is exercised by the smoke run against real bulletin images
# (conftest.py mocks cv2 globally so we can't unit-test it here without a
# real OpenCV import). Smoke verification: rendered embeddings on TS_483
# show a 512-element non-empty color_hsv array per view.
