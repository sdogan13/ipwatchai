import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

FIXTURE_ROOT = Path("tests/fixtures/model_cache").resolve()

def _load_pipeline_ai_under_test(module_name: str):
    script_path = Path("pipeline/ai.py").resolve()
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pipeline_ai_prefers_cached_model_paths(monkeypatch):
    clip_weights = (
        FIXTURE_ROOT / "hf-hub" / "models--laion--CLIP-ViT-B-32-laion2B-s34B-b79K" / "snapshots" / "0001" / "open_clip_model.safetensors"
    )
    text_snapshot = (
        FIXTURE_ROOT / "hf-hub" / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2" / "snapshots" / "0001"
    )
    dino_repo = FIXTURE_ROOT / "torch-home" / "hub" / "facebookresearch_dinov2_main"

    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(FIXTURE_ROOT / "hf-hub"))
    monkeypatch.setenv("TORCH_HOME", str(FIXTURE_ROOT / "torch-home"))
    monkeypatch.delenv("AI_SKIP_MODEL_LOAD", raising=False)

    clip_model = MagicMock()
    clip_model.eval.return_value = clip_model
    clip_model.half.return_value = clip_model

    dinov2_model = MagicMock()
    dinov2_model.to.return_value = dinov2_model
    dinov2_model.eval.return_value = dinov2_model
    dinov2_model.half.return_value = dinov2_model

    torch_module = MagicMock()
    torch_module.cuda.is_available.return_value = False
    torch_module.hub.load.return_value = dinov2_model
    torch_module.inference_mode.return_value = lambda fn: fn
    torch_module.no_grad.return_value.__enter__ = MagicMock()
    torch_module.no_grad.return_value.__exit__ = MagicMock(return_value=False)
    torch_module.float16 = "float16"
    torch_module.float32 = "float32"
    monkeypatch.setitem(sys.modules, "torch", torch_module)

    open_clip_module = MagicMock()
    open_clip_module.pretrained.get_pretrained_cfg.return_value = {
        "hf_hub": "laion/CLIP-ViT-B-32-laion2B-s34B-b79K/"
    }
    open_clip_module.create_model_and_transforms.return_value = (clip_model, None, MagicMock())
    monkeypatch.setitem(sys.modules, "open_clip", open_clip_module)

    sentence_transformers_module = types.ModuleType("sentence_transformers")
    sentence_transformers_module.SentenceTransformer = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers_module)

    monkeypatch.setitem(sys.modules, "numpy", MagicMock())
    monkeypatch.setitem(sys.modules, "cv2", MagicMock())

    redis_module = MagicMock()
    redis_module.Redis.return_value = MagicMock(ping=MagicMock())
    monkeypatch.setitem(sys.modules, "redis", redis_module)

    easyocr_module = MagicMock()
    easyocr_module.Reader.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "easyocr", easyocr_module)

    pil_module = types.ModuleType("PIL")
    pil_image = MagicMock()
    pil_imagefile = MagicMock(LOAD_TRUNCATED_IMAGES=False)
    pil_module.Image = pil_image
    pil_module.UnidentifiedImageError = type("UnidentifiedImageError", (Exception,), {})
    pil_module.ImageFile = pil_imagefile
    monkeypatch.setitem(sys.modules, "PIL", pil_module)
    monkeypatch.setitem(sys.modules, "PIL.Image", pil_image)
    monkeypatch.setitem(sys.modules, "PIL.ImageFile", pil_imagefile)

    transforms_module = MagicMock()
    transforms_module.Compose.return_value = MagicMock()
    transforms_module.Resize.return_value = MagicMock()
    transforms_module.ToTensor.return_value = MagicMock()
    transforms_module.Normalize.return_value = MagicMock()
    transforms_module.InterpolationMode = types.SimpleNamespace(BICUBIC="bicubic")
    transforms_module.functional = types.SimpleNamespace(pad=MagicMock(return_value=MagicMock()))
    torchvision_module = types.ModuleType("torchvision")
    torchvision_module.transforms = transforms_module
    monkeypatch.setitem(sys.modules, "torchvision", torchvision_module)
    monkeypatch.setitem(sys.modules, "torchvision.transforms", transforms_module)

    tqdm_module = types.ModuleType("tqdm")
    tqdm_module.tqdm = MagicMock(side_effect=lambda iterable=None, *args, **kwargs: iterable if iterable is not None else [])
    monkeypatch.setitem(sys.modules, "tqdm", tqdm_module)

    logging_module = types.ModuleType("logging_config")
    logging_module.get_logger = MagicMock(return_value=MagicMock())
    logging_module.log_timing = lambda name: (lambda fn: fn)
    logging_module.log_batch_stats = MagicMock()
    logging_module.setup_logging = MagicMock()
    monkeypatch.setitem(sys.modules, "logging_config", logging_module)

    translation_module = types.ModuleType("utils.translation")
    translation_module.get_translations = lambda text: {"original": text, "detected_lang": "unknown", "tr": None}
    translation_module.detect_language_fasttext = lambda text: ("en", "eng_Latn", 0.0)
    translation_module.initialize = lambda device=None: False
    translation_module.is_ready = lambda: False
    translation_module.translate = lambda text, src, tgt: None
    translation_module.translate_to_turkish = lambda text: text.lower() if text else ""
    translation_module.batch_translate_to_turkish = lambda texts: [(t.lower() if t else "", "en") for t in texts]
    monkeypatch.setitem(sys.modules, "utils.translation", translation_module)

    module = _load_pipeline_ai_under_test("pipeline_ai_loading_test")

    assert module is not None
    open_clip_module.create_model_and_transforms.assert_called_once()
    assert open_clip_module.create_model_and_transforms.call_args.kwargs["pretrained"] == str(clip_weights)
    torch_module.hub.load.assert_called_once_with(str(dino_repo), "dinov2_vitb14", source="local")
    sentence_transformers_module.SentenceTransformer.assert_called_once_with(str(text_snapshot), device="cpu")
