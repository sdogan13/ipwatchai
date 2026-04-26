from pathlib import Path

from utils.model_cache import find_hf_snapshot_dir, find_hf_snapshot_file, find_torch_hub_repo


FIXTURE_ROOT = Path("tests/fixtures/model_cache").resolve()


def test_find_hf_snapshot_dir_prefers_snapshot_with_required_files(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(FIXTURE_ROOT / "hf-hub"))

    resolved = find_hf_snapshot_dir(
        "facebook/nllb-200-distilled-600M",
        required_files=["config.json", "model.safetensors"],
    )

    assert resolved == FIXTURE_ROOT / "hf-hub" / "models--facebook--nllb-200-distilled-600M" / "snapshots" / "0001"


def test_find_hf_snapshot_file_returns_cached_file(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(FIXTURE_ROOT / "hf-hub"))

    assert find_hf_snapshot_file("facebook/fasttext-language-identification", "model.bin") == (
        FIXTURE_ROOT / "hf-hub" / "models--facebook--fasttext-language-identification" / "snapshots" / "0001" / "model.bin"
    )


def test_find_torch_hub_repo_prefers_local_main_checkout(monkeypatch):
    monkeypatch.setenv("TORCH_HOME", str(FIXTURE_ROOT / "torch-home"))

    assert find_torch_hub_repo("facebookresearch/dinov2") == (
        FIXTURE_ROOT / "torch-home" / "hub" / "facebookresearch_dinov2_main"
    )
