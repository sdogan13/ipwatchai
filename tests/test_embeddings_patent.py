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


# ---------------------------------------------------------------------------
# embed_image — failure path testable without GPU
# ---------------------------------------------------------------------------


def test_embed_image_returns_zero_vectors_on_pil_failure(monkeypatch, tmp_path: Path) -> None:
    """PIL open failure (corrupt file) returns zero-vectors of the
    right dimensions instead of raising. Stage 6 must keep going
    through bad inputs.

    conftest.py mocks PIL.Image globally so we can't rely on a
    non-existent path — instead, monkeypatch the real PIL.Image.open
    that embed_image's local import will resolve to.
    """
    from embeddings_patent import LoadedModels, embed_image
    import PIL.Image as _real_pil_image

    def _raise(_path):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(_real_pil_image, "open", _raise)

    fake_models = LoadedModels(
        device="cpu", dinov2=None, dinov2_transform=None,
        clip=None, clip_transform=None, text_encoder=None,
    )

    out = embed_image(tmp_path / "anything.tif", fake_models)

    assert list(out.keys()) == ["dinov2_vitl14", "clip_vitb32"]
    assert len(out["dinov2_vitl14"]) == DINOV2_DIM
    assert len(out["clip_vitb32"]) == CLIP_DIM
    assert all(x == 0.0 for x in out["dinov2_vitl14"])
    assert all(x == 0.0 for x in out["clip_vitb32"])


# ---------------------------------------------------------------------------
# embed_text + _build_text_prompt
# ---------------------------------------------------------------------------


def test_build_text_prompt_combines_title_and_abstract() -> None:
    from embeddings_patent import _build_text_prompt
    assert _build_text_prompt("ÇİFT FONKSİYONLU GÖRÜŞ SİSTEMİ", "Bu buluş...") == (
        "passage: ÇİFT FONKSİYONLU GÖRÜŞ SİSTEMİ. Bu buluş..."
    )


def test_build_text_prompt_title_only() -> None:
    from embeddings_patent import _build_text_prompt
    assert _build_text_prompt("Title", None) == "passage: Title"
    assert _build_text_prompt("Title", "") == "passage: Title"
    assert _build_text_prompt("Title", "   ") == "passage: Title"


def test_build_text_prompt_abstract_only() -> None:
    from embeddings_patent import _build_text_prompt
    assert _build_text_prompt(None, "Abstract") == "passage: Abstract"
    assert _build_text_prompt("", "Abstract") == "passage: Abstract"


def test_build_text_prompt_strips_whitespace() -> None:
    from embeddings_patent import _build_text_prompt
    assert _build_text_prompt("  Title  ", "  Abstract  ") == "passage: Title. Abstract"


def test_build_text_prompt_empty_returns_blank() -> None:
    """Both inputs blank → empty string; embed_text uses this to
    short-circuit to a zero vector."""
    from embeddings_patent import _build_text_prompt
    assert _build_text_prompt(None, None) == ""
    assert _build_text_prompt("", "") == ""
    assert _build_text_prompt("   ", "   ") == ""


def test_embed_text_returns_zero_vector_on_empty_input() -> None:
    """No model call needed when there's nothing to embed."""
    from embeddings_patent import LoadedModels, embed_text
    fake_models = LoadedModels(
        device="cpu", dinov2=None, dinov2_transform=None,
        clip=None, clip_transform=None, text_encoder=None,
    )
    out = embed_text("", "", fake_models)
    assert len(out) == TEXT_DIM
    assert all(x == 0.0 for x in out)


def test_embed_text_routes_through_text_encoder() -> None:
    """Happy path: with non-empty prompt, calls models.text_encoder.encode
    with normalize_embeddings=True and returns the .tolist() of the
    result."""
    from embeddings_patent import LoadedModels, embed_text

    captured = {}

    class _FakeEncoder:
        def encode(self, prompt, normalize_embeddings=False, show_progress_bar=True):
            captured["prompt"] = prompt
            captured["normalize"] = normalize_embeddings
            captured["show_progress_bar"] = show_progress_bar
            class _V:
                def tolist(self_inner):
                    return [0.5] * TEXT_DIM
            return _V()

    fake_models = LoadedModels(
        device="cpu", dinov2=None, dinov2_transform=None,
        clip=None, clip_transform=None, text_encoder=_FakeEncoder(),
    )
    out = embed_text("Title", "Abstract", fake_models)

    assert captured["prompt"] == "passage: Title. Abstract"
    assert captured["normalize"] is True
    # Progress bar suppressed to keep --all output legible across 113
    # bulletins × 2316 records each.
    assert captured["show_progress_bar"] is False
    assert len(out) == TEXT_DIM
    assert all(x == 0.5 for x in out)


# ---------------------------------------------------------------------------
# embed_record (per-record orchestrator)
# ---------------------------------------------------------------------------


def _stub_models(text_vec=None, image_vec=None):
    """LoadedModels with stubs that return canned vectors so embed_record
    runs end-to-end without GPU."""
    from embeddings_patent import LoadedModels
    text_vec = text_vec if text_vec is not None else [0.1] * TEXT_DIM
    image_vec_d = image_vec if image_vec is not None else [0.2] * DINOV2_DIM
    image_vec_c = [0.3] * CLIP_DIM

    class _TxtEnc:
        def encode(self, prompt, normalize_embeddings=False, show_progress_bar=True):
            class _V:
                def tolist(self_inner):
                    return list(text_vec)
            return _V()

    return LoadedModels(
        device="cpu", dinov2=None, dinov2_transform=None,
        clip=None, clip_transform=None, text_encoder=_TxtEnc(),
    ), image_vec_d, image_vec_c


def _stub_embed_image(monkeypatch, dvec, cvec):
    """Replace embed_image with a stub returning canned vectors."""
    import embeddings_patent
    monkeypatch.setattr(
        embeddings_patent, "embed_image",
        lambda path, models: {"dinov2_vitl14": list(dvec), "clip_vitb32": list(cvec)},
    )


def test_embed_record_happy_path_text_plus_two_figures(monkeypatch, tmp_path) -> None:
    """Record with title + abstract + 2 figures: text embedded, both
    figures embedded, primary_figure_embedding mean-pooled."""
    from embeddings_patent import embed_record
    models, dvec, cvec = _stub_models()
    _stub_embed_image(monkeypatch, dvec, cvec)

    record = {
        "application_no": "X",
        "title": "Test title",
        "abstract": "Test abstract.",
        "figures": [
            {"image_path": "figures/X.tif"},
            {"image_path": "figures/X_p100_2.png"},
        ],
    }

    summary = embed_record(record, tmp_path, models)

    assert summary == {
        "text_embedded": 1, "figures_embedded": 2,
        "primary_aggregated": 1, "skipped": False,
    }
    assert len(record["title_abstract_embedding"]) == TEXT_DIM
    assert len(record["primary_figure_embedding"]) == DINOV2_DIM
    # mean-pool of two identical dino vectors == the same vector
    assert record["primary_figure_embedding"] == list(dvec)
    for fig in record["figures"]:
        assert "embeddings" in fig
        assert len(fig["embeddings"]["dinov2_vitl14"]) == DINOV2_DIM
        assert len(fig["embeddings"]["clip_vitb32"]) == CLIP_DIM


def test_embed_record_text_only_when_no_embeddable_figures(monkeypatch, tmp_path) -> None:
    """Record whose only figures have null image_path (PDF dedup'd
    against CD TIFF) gets text embedding only — no primary aggregate."""
    from embeddings_patent import embed_record
    models, dvec, cvec = _stub_models()
    _stub_embed_image(monkeypatch, dvec, cvec)

    record = {
        "title": "T", "abstract": "A",
        "figures": [{"page": 1847, "image_path": None}],
    }

    summary = embed_record(record, tmp_path, models)

    assert summary == {
        "text_embedded": 1, "figures_embedded": 0,
        "primary_aggregated": 0, "skipped": False,
    }
    assert len(record["title_abstract_embedding"]) == TEXT_DIM
    assert "primary_figure_embedding" not in record
    # Original figure dict untouched (no embeddings key added)
    assert "embeddings" not in record["figures"][0]


def test_embed_record_skips_when_already_embedded(monkeypatch, tmp_path) -> None:
    """Default force=False: a fully-embedded record short-circuits
    without calling encoders."""
    from embeddings_patent import embed_record
    models, _, _ = _stub_models()
    # If embed_image gets called we've broken idempotency:
    def _explode(*a, **k):
        raise AssertionError("embed_image must not run when record is complete")
    import embeddings_patent
    monkeypatch.setattr(embeddings_patent, "embed_image", _explode)

    record = {
        "title": "T", "abstract": "A",
        "title_abstract_embedding": [0.5] * TEXT_DIM,
        "primary_figure_embedding": [0.5] * DINOV2_DIM,
        "figures": [{
            "image_path": "figures/X.tif",
            "embeddings": {
                "dinov2_vitl14": [0.5] * DINOV2_DIM,
                "clip_vitb32":   [0.5] * CLIP_DIM,
            },
        }],
    }

    summary = embed_record(record, tmp_path, models)

    assert summary["skipped"] is True
    assert summary["text_embedded"] == 0
    assert summary["figures_embedded"] == 0


def test_embed_record_force_re_embeds_everything(monkeypatch, tmp_path) -> None:
    """force=True: re-embeds even when record_already_embedded would
    return True. Useful for switching models or fixing a bad batch."""
    from embeddings_patent import embed_record
    models, dvec, cvec = _stub_models(text_vec=[0.9] * TEXT_DIM)
    _stub_embed_image(monkeypatch, dvec, cvec)

    record = {
        "title": "T", "abstract": "A",
        "title_abstract_embedding": [0.5] * TEXT_DIM,        # stale
        "primary_figure_embedding": [0.5] * DINOV2_DIM,
        "figures": [{
            "image_path": "figures/X.tif",
            "embeddings": {
                "dinov2_vitl14": [0.5] * DINOV2_DIM,
                "clip_vitb32":   [0.5] * CLIP_DIM,
            },
        }],
    }

    summary = embed_record(record, tmp_path, models, force=True)

    assert summary["skipped"] is False
    assert summary["text_embedded"] == 1
    assert summary["figures_embedded"] == 1
    assert record["title_abstract_embedding"] == [0.9] * TEXT_DIM   # refreshed
    assert record["figures"][0]["embeddings"]["dinov2_vitl14"] == list(dvec)


def test_embed_record_partial_text_only_does_not_aggregate(monkeypatch, tmp_path) -> None:
    """When record has text already but figures need embedding, only
    figures get processed; text stays. Aggregate computed at the end."""
    from embeddings_patent import embed_record
    models, dvec, cvec = _stub_models(text_vec=[0.7] * TEXT_DIM)
    _stub_embed_image(monkeypatch, dvec, cvec)

    record = {
        "title": "T", "abstract": "A",
        "title_abstract_embedding": [0.4] * TEXT_DIM,        # already set
        "figures": [{"image_path": "figures/X.tif"}],
    }

    summary = embed_record(record, tmp_path, models)

    assert summary["text_embedded"] == 0                     # not re-run
    assert summary["figures_embedded"] == 1
    assert summary["primary_aggregated"] == 1
    # text NOT changed
    assert record["title_abstract_embedding"] == [0.4] * TEXT_DIM
    assert record["primary_figure_embedding"] == list(dvec)


# ---------------------------------------------------------------------------
# embed_bulletin (per-bulletin file orchestration)
# ---------------------------------------------------------------------------


def test_embed_bulletin_writes_metadata_back_with_embeddings(monkeypatch, tmp_path) -> None:
    """One full bulletin pass: load metadata.json, embed each record,
    write back. Subsequent run on the same folder skips (idempotent)."""
    import json
    from embeddings_patent import embed_bulletin

    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "bulletin_no": "2025/8",
        "records": [
            {"application_no": "X1", "title": "T1", "abstract": "A1",
             "figures": [{"image_path": "figures/X1.tif"}]},
            {"application_no": "X2", "title": "T2", "abstract": "A2",
             "figures": []},
        ],
    }), encoding="utf-8")

    models, dvec, cvec = _stub_models()
    _stub_embed_image(monkeypatch, dvec, cvec)
    # Make the figure file exist on disk so embed_image's path-resolution
    # would find it (the stub doesn't actually read it but Path.is_file
    # checks could matter for future code paths).
    (parent / "figures").mkdir()
    (parent / "figures" / "X1.tif").write_bytes(b"FAKE")

    summary = embed_bulletin(parent, models)

    assert summary["status"] == "ok"
    assert summary["records_processed"] == 2
    assert summary["text_embedded"] == 2
    assert summary["figures_embedded"] == 1     # only X1 had a figure
    assert summary["primary_aggregated"] == 1   # only X1 has a primary
    assert summary["skipped"] == 0

    # Round-trip: written-back JSON has the embeddings
    payload = json.loads((parent / "metadata.json").read_text(encoding="utf-8"))
    rec1, rec2 = payload["records"]
    assert len(rec1["title_abstract_embedding"]) == TEXT_DIM
    assert len(rec1["primary_figure_embedding"]) == DINOV2_DIM
    assert "embeddings" in rec1["figures"][0]
    assert len(rec2["title_abstract_embedding"]) == TEXT_DIM
    assert "primary_figure_embedding" not in rec2     # no figures

    # Re-run: everything skipped
    summary2 = embed_bulletin(parent, models)
    assert summary2["skipped"] == 2
    assert summary2["text_embedded"] == 0
    assert summary2["figures_embedded"] == 0


def test_embed_bulletin_no_metadata_returns_status(tmp_path) -> None:
    from embeddings_patent import embed_bulletin
    models, _, _ = _stub_models()
    parent = tmp_path / "PT_X"
    parent.mkdir()
    summary = embed_bulletin(parent, models)
    assert summary == {"status": "no_metadata", "bulletin": "PT_X"}


def test_embed_bulletin_limit_caps_records(monkeypatch, tmp_path) -> None:
    import json
    from embeddings_patent import embed_bulletin

    parent = tmp_path / "PT_2025_8_2025-08-21"
    parent.mkdir()
    (parent / "metadata.json").write_text(json.dumps({
        "records": [
            {"application_no": str(i), "title": f"T{i}", "abstract": f"A{i}",
             "figures": []}
            for i in range(10)
        ],
    }), encoding="utf-8")

    models, _, _ = _stub_models()
    _stub_embed_image(monkeypatch, [0.0]*DINOV2_DIM, [0.0]*CLIP_DIM)

    summary = embed_bulletin(parent, models, limit=3)

    assert summary["records_processed"] == 3
    assert summary["text_embedded"] == 3
    payload = json.loads((parent / "metadata.json").read_text(encoding="utf-8"))
    assert "title_abstract_embedding" in payload["records"][0]
    assert "title_abstract_embedding" not in payload["records"][3]


# ---------------------------------------------------------------------------
# CLI parse_argv
# ---------------------------------------------------------------------------


def test_parse_argv_all_mode() -> None:
    from embeddings_patent import parse_argv
    args = parse_argv(["--all", "--bulletins-dir", "/data/bulletins"])
    assert args.all_mode is True
    assert args.bulletins_dir == Path("/data/bulletins")
    assert args.bulletin_names == []


def test_parse_argv_specific_bulletins() -> None:
    from embeddings_patent import parse_argv
    args = parse_argv([
        "--bulletin", "PT_2025_8_2025-08-21",
        "--bulletin", "PT_2024_6_2024-06-21",
    ])
    assert args.bulletin_names == ["PT_2025_8_2025-08-21", "PT_2024_6_2024-06-21"]
    assert args.all_mode is False


def test_parse_argv_no_args_errors() -> None:
    from embeddings_patent import parse_argv
    with pytest.raises(SystemExit):
        parse_argv([])


def test_parse_argv_all_and_bulletin_mutex() -> None:
    from embeddings_patent import parse_argv
    with pytest.raises(SystemExit):
        parse_argv(["--all", "--bulletin", "PT_x"])


def test_find_bulletin_folders_all_mode_skips_non_pt(tmp_path) -> None:
    """--all only matches PT_-prefixed dirs; ignores other dirs/files."""
    from embeddings_patent import CLIArgs, find_bulletin_folders
    (tmp_path / "PT_2025_8_2025-08-21").mkdir()
    (tmp_path / "PT_2024_6_2024-06-21").mkdir()
    (tmp_path / "scratch").mkdir()              # non-PT dir, ignored
    (tmp_path / "stray.txt").write_text("x")    # file, ignored

    args = CLIArgs(
        bulletins_dir=tmp_path, bulletin_names=[], all_mode=True,
        device=None, force=False, limit=None,
    )
    folders = find_bulletin_folders(args)
    assert {p.name for p in folders} == {
        "PT_2025_8_2025-08-21", "PT_2024_6_2024-06-21",
    }
