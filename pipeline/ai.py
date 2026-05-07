import os
import sys
import time
from datetime import datetime, timezone

# ===================== WINDOWS CONSOLE FIX =====================
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ===================== CRITICAL STABILITY FIX =====================
os.environ["XFORMERS_DISABLED"] = "1"

import json
import torch
import open_clip
import numpy as np
import cv2
import hashlib
import redis
from pathlib import Path

_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_DEFAULT_BULLETINS_ROOT = _LOCAL_PROJECT_ROOT / "bulletins" / "Marka"


def _resolve_local_ai_root(value: str | None, default: Path) -> Path:
    if not value:
        return default.resolve()

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _LOCAL_PROJECT_ROOT / path
    return path.resolve()


# Make easyocr optional - not required for CLIP image search
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    easyocr = None
    EASYOCR_AVAILABLE = False
from PIL import Image, UnidentifiedImageError, ImageFile
from torchvision import transforms
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from utils.model_cache import find_hf_snapshot_dir, find_hf_snapshot_file, find_torch_hub_repo
from pipeline.ingest_rules import clean_name

# ===================== STRUCTURED LOGGING =====================
from logging_config import get_logger, setup_logging, log_timing, log_batch_stats

# Initialize logging
setup_logging()
logger = get_logger(__name__)

_TEXT_EMBEDDING_SOURCE_NAME_KEY = "text_embedding_source_name"
_TRANSLATION_SOURCE_NAME_KEY = "name_tr_source_name"


def _clean_ai_trademark_name(record):
    return clean_name((record.get("TRADEMARK") or {}).get("NAME"))


def _clear_name_derived_ai_features(record, *, clear_text=True, clear_translation=True):
    if clear_text:
        record["text_embedding"] = None
        record[_TEXT_EMBEDDING_SOURCE_NAME_KEY] = None
    if clear_translation:
        record["name_tr"] = None
        record["detected_lang"] = None
        record["name_tr_backend"] = None
        record["name_tr_model"] = None
        record["name_tr_updated_at"] = None
        record[_TRANSLATION_SOURCE_NAME_KEY] = None


def _prepare_name_derived_ai_features(record):
    cleaned_name = _clean_ai_trademark_name(record)
    if not cleaned_name:
        _clear_name_derived_ai_features(record)
        return None

    raw_name = ((record.get("TRADEMARK") or {}).get("NAME") or "").strip()
    name_was_cleaned = cleaned_name != " ".join(raw_name.split())
    if name_was_cleaned:
        if record.get(_TEXT_EMBEDDING_SOURCE_NAME_KEY) != cleaned_name:
            _clear_name_derived_ai_features(record, clear_text=True, clear_translation=False)
        if record.get(_TRANSLATION_SOURCE_NAME_KEY) != cleaned_name:
            _clear_name_derived_ai_features(record, clear_text=False, clear_translation=True)
    return cleaned_name

# ===================== CENTRALIZED SETTINGS =====================
# Try to import from config, fall back to defaults if not available
try:
    from config.settings import settings

    ROOT = Path(settings.paths.data_root)
    BATCH_SIZE = settings.pipeline.embedding_batch_size
    EMBEDDING_CACHE_TTL = settings.redis.embedding_cache_ttl

    # Redis settings from config
    REDIS_HOST = settings.redis.host
    REDIS_PORT = settings.redis.port
    REDIS_DB = settings.redis.cache_db
    REDIS_PASSWORD = settings.redis.password

    # AI settings from config
    CLIP_MODEL = settings.ai.clip_model
    CLIP_PRETRAINED = settings.ai.clip_pretrained
    DINO_MODEL = settings.ai.dino_model
    TEXT_MODEL = settings.ai.text_model
    USE_FP16 = settings.ai.use_fp16
    USE_TF32 = settings.ai.use_tf32
    PIPELINE_TRANSLATION_BACKEND = settings.ai.pipeline_translation_backend

    # Pipeline settings for batch processing
    SKIP_IF_PROCESSED = settings.pipeline.skip_if_embeddings_exist

    logger.info("Configuration loaded", source="config/settings.py")
except ImportError:
    # Fallback to local defaults when centralized settings are unavailable.
    ROOT = _resolve_local_ai_root(
        os.environ.get("PIPELINE_BULLETINS_ROOT") or os.environ.get("DATA_ROOT"),
        _LOCAL_DEFAULT_BULLETINS_ROOT,
    )
    BATCH_SIZE = 128
    EMBEDDING_CACHE_TTL = 86400

    REDIS_HOST = "localhost"
    REDIS_PORT = 6379
    REDIS_DB = 0
    REDIS_PASSWORD = None

    CLIP_MODEL = "ViT-B-32"
    CLIP_PRETRAINED = "laion2b_s34b_b79k"
    DINO_MODEL = "dinov2_vitb14"
    TEXT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    USE_FP16 = True
    USE_TF32 = True
    PIPELINE_TRANSLATION_BACKEND = "madlad"
    SKIP_IF_PROCESSED = True

    logger.warning("Using default configuration", reason="config/settings.py not found")

SKIP_MODEL_LOAD = os.getenv("AI_SKIP_MODEL_LOAD", "").lower() in ("1", "true", "yes")
SAVE_BATCH_SIZE = 1000

ImageFile.LOAD_TRUNCATED_IMAGES = True

# ===================== GPU PERFORMANCE SETUP =====================
device = 'cuda' if torch.cuda.is_available() else 'cpu'
if device == 'cuda':
    torch.backends.cuda.enable_flash_sdp(False)
    torch.backends.cuda.enable_mem_efficient_sdp(False)
    torch.backends.cuda.enable_math_sdp(True)
    torch.backends.cudnn.benchmark = True
    if USE_TF32:
        torch.backends.cuda.matmul.allow_tf32 = True  # TF32 for faster matmul on Ampere+

# === MODEL SETUP ===
logger.info("Initializing GPU pipeline", device=device.upper())


def _resolve_clip_pretrained_source(model_name: str, pretrained_tag: str) -> str:
    try:
        pretrained_cfg = open_clip.pretrained.get_pretrained_cfg(model_name, pretrained_tag) or {}
    except Exception:
        pretrained_cfg = {}

    repo_id = str(pretrained_cfg.get("hf_hub", "")).strip("/")
    if repo_id:
        cached_weights = (
            find_hf_snapshot_file(repo_id, "open_clip_model.safetensors")
            or find_hf_snapshot_file(repo_id, "open_clip_pytorch_model.bin")
        )
        if cached_weights is not None:
            logger.info("Using cached OpenCLIP weights", repo=repo_id, path=str(cached_weights))
            return str(cached_weights)

    return pretrained_tag


def _resolve_dinov2_repo_source() -> tuple[str, str | None]:
    cached_repo = find_torch_hub_repo("facebookresearch/dinov2")
    if cached_repo is not None:
        logger.info("Using cached DINOv2 repo", path=str(cached_repo))
        return str(cached_repo), "local"
    return "facebookresearch/dinov2", None


def _resolve_text_model_source(model_name: str) -> str:
    model_path = Path(model_name).expanduser()
    if model_path.exists():
        return str(model_path)

    cached_snapshot = find_hf_snapshot_dir(model_name, required_files=["config.json"])
    if cached_snapshot is not None:
        logger.info("Using cached sentence transformer", model=model_name, path=str(cached_snapshot))
        return str(cached_snapshot)

    return model_name

if SKIP_MODEL_LOAD:
    logger.warning("AI model loading skipped", reason="AI_SKIP_MODEL_LOAD")

    class _DummyVisionModel:
        def eval(self):
            return self

        def half(self):
            return self

        def to(self, _device):
            return self

        def encode_image(self, tensor):
            return torch.zeros((tensor.shape[0], 512), device=tensor.device)

        def __call__(self, tensor):
            return torch.zeros((tensor.shape[0], 768), device=tensor.device)

    def _dummy_preprocess(_image):
        return torch.zeros((3, 224, 224))

    class _DummyTextModel:
        def encode(self, texts, **_kwargs):
            if isinstance(texts, str):
                return np.zeros(384)
            return np.zeros((len(texts), 384))

    clip_model = _DummyVisionModel()
    clip_preprocess = _dummy_preprocess
    dinov2_model = _DummyVisionModel()
    dinov2_preprocess = _dummy_preprocess
    text_model = _DummyTextModel()
else:
    _model_load_start = time.perf_counter()
    clip_pretrained_source = _resolve_clip_pretrained_source(CLIP_MODEL, CLIP_PRETRAINED)
    logger.info("Loading OpenCLIP model", model=CLIP_MODEL, pretrained=CLIP_PRETRAINED, fp16=USE_FP16)
    clip_model, _, clip_preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL, pretrained=clip_pretrained_source, device=device
    )
    if USE_FP16 and device == 'cuda':
        clip_model.eval().half()
    else:
        clip_model.eval()
    logger.info("OpenCLIP loaded", duration_ms=round((time.perf_counter() - _model_load_start) * 1000, 2))

    _model_load_start = time.perf_counter()
    logger.info("Loading DINOv2 model", model=DINO_MODEL, fp16=USE_FP16)
    dinov2_repo, dinov2_source = _resolve_dinov2_repo_source()
    if dinov2_source is None:
        dinov2_model = torch.hub.load(dinov2_repo, DINO_MODEL)
    else:
        dinov2_model = torch.hub.load(dinov2_repo, DINO_MODEL, source=dinov2_source)
    if USE_FP16 and device == 'cuda':
        dinov2_model.to(device).half().eval()
    else:
        dinov2_model.to(device).eval()

    # FIX #1: Define custom padding class to prevent "Center Crop" issues on rectangular logos
    class SquarePad:
        def __call__(self, image):
            w, h = image.size
            max_wh = max(w, h)
            hp = int((max_wh - w) / 2)
            vp = int((max_wh - h) / 2)
            padding = (hp, vp, max_wh - w - hp, max_wh - h - vp)
            return transforms.functional.pad(image, padding, fill=255, padding_mode='constant')

    # FIX #1: Replaced CenterCrop with SquarePad and resized to 224 directly
    dinov2_preprocess = transforms.Compose([
        SquarePad(),
        transforms.Resize(224, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    logger.info("DINOv2 loaded", duration_ms=round((time.perf_counter() - _model_load_start) * 1000, 2))

    _model_load_start = time.perf_counter()
    logger.info("Loading text model", model=TEXT_MODEL.split('/')[-1])
    text_model = SentenceTransformer(_resolve_text_model_source(TEXT_MODEL), device=device)
    logger.info("Text model loaded", duration_ms=round((time.perf_counter() - _model_load_start) * 1000, 2))

# ===================== OCR SETUP (Optional) =====================
ocr_reader = None
if EASYOCR_AVAILABLE and not SKIP_MODEL_LOAD:
    _model_load_start = time.perf_counter()
    logger.info("Loading EasyOCR model")
    try:
        from config.settings import settings as _cfg
        _ocr_langs = _cfg.ai.ocr_languages
    except Exception:
        _ocr_langs = ['en', 'tr']
    # EasyOCR limitation: Arabic script is only compatible with English.
    # Split into latin-based and arabic-based readers if needed.
    _arabic_langs = {'ar', 'fa', 'ur', 'ug'}
    _latin_ocr = [l for l in _ocr_langs if l not in _arabic_langs]
    if not _latin_ocr:
        _latin_ocr = ['en']
    ocr_reader = easyocr.Reader(_latin_ocr, gpu=device == 'cuda', verbose=False)
    logger.info("EasyOCR loaded", duration_ms=round((time.perf_counter() - _model_load_start) * 1000, 2))
elif SKIP_MODEL_LOAD:
    logger.warning("EasyOCR load skipped", reason="AI_SKIP_MODEL_LOAD")
else:
    logger.warning("EasyOCR not available - OCR features disabled")

# ===================== TRANSLATION (delegated to utils/translation.py) =====================
try:
    from utils.translation import (
        get_translations as _get_translations_raw,
        detect_language_fasttext,
        initialize as _init_translation,
        is_ready as _translation_ready,
        translate as translate_text,
        translate_to_turkish,
        batch_translate_to_turkish,
        get_translation_backend_info,
    )
    _TRANSLATION_IMPORT_OK = True
except ImportError:
    _TRANSLATION_IMPORT_OK = False
    logger.warning("utils.translation not available - translation disabled")
    def detect_language_fasttext(text): return 'en', 'eng_Latn', 0.0
    def translate_text(text, src, tgt, backend=None): return None
    def _get_translations_raw(text, backend=None): return {'original': text, 'detected_lang': 'unknown', 'tr': None}
    def _init_translation(device=None, backend=None): return False
    def _translation_ready(backend=None): return False
    def translate_to_turkish(text, backend=None): return text.lower() if text else ""
    def batch_translate_to_turkish(texts, backend=None, batch_size=None): return [(t.lower() if t else "", "en") for t in texts]
    def get_translation_backend_info(backend=None): return {"backend": backend or "nllb", "model_name": "unavailable"}

TRANSLATION_AVAILABLE = _TRANSLATION_IMPORT_OK


def _load_translation_model():
    """Load TranslateGemma model (delegated to utils.translation)."""
    global TRANSLATION_AVAILABLE
    if not _TRANSLATION_IMPORT_OK:
        return False
    result = _init_translation(device)
    TRANSLATION_AVAILABLE = result
    return result


def get_translations(text: str) -> dict:
    """
    Get Turkish translation for a trademark name.

    Returns:
        {'name_original': str, 'name_tr': str|None, 'detected_lang': str}
    """
    raw = _get_translations_raw(text, backend=PIPELINE_TRANSLATION_BACKEND)
    return {
        'name_original': text,
        'detected_lang': raw.get('detected_lang', 'unknown'),
        'name_tr': raw.get('tr'),
    }


# ===================== REDIS CACHE SETUP (Optional) =====================
redis_client = None
REDIS_AVAILABLE = False

try:
    logger.info("Connecting to Redis", host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB)
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD)
    # Test connection
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info("Redis connected successfully")
except Exception as e:
    logger.warning("Redis not available - caching disabled", error=str(e))
    redis_client = None
    REDIS_AVAILABLE = False

def get_text_embedding_cached(text: str) -> list:
    """Get text embedding with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable."""
    if not text:
        return text_model.encode("", show_progress_bar=False).tolist()

    # If Redis not available, just generate embedding without caching
    if not REDIS_AVAILABLE:
        return text_model.encode(text, show_progress_bar=False).tolist()

    # Generate cache key from MD5 hash
    cache_key = f"text_emb:{hashlib.md5(text.encode('utf-8')).hexdigest()}"

    # Try to get from cache
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # Generate embedding
    embedding = text_model.encode(text, show_progress_bar=False).tolist()

    # Store in cache with TTL
    redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return embedding

def get_text_embeddings_batch_cached(texts: list) -> list:
    """Batch text embeddings with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable."""
    results = [None] * len(texts)

    # If Redis not available, just batch encode without caching
    if not REDIS_AVAILABLE:
        for i, text in enumerate(texts):
            if not text:
                results[i] = text_model.encode("", show_progress_bar=False).tolist()
            else:
                results[i] = text_model.encode(text, show_progress_bar=False).tolist()
        return results

    uncached_texts = []
    uncached_indices = []

    # Check cache for each text
    for i, text in enumerate(texts):
        if not text:
            results[i] = text_model.encode("", show_progress_bar=False).tolist()
            continue

        cache_key = f"text_emb:{hashlib.md5(text.encode('utf-8')).hexdigest()}"
        cached = redis_client.get(cache_key)

        if cached:
            results[i] = json.loads(cached)
        else:
            uncached_texts.append(text)
            uncached_indices.append(i)

    # Batch encode uncached texts
    if uncached_texts:
        embeddings = text_model.encode(uncached_texts, batch_size=64, show_progress_bar=False)

        for idx, (text, embedding) in enumerate(zip(uncached_texts, embeddings)):
            embedding_list = embedding.tolist()
            results[uncached_indices[idx]] = embedding_list

            # Cache the new embedding
            cache_key = f"text_emb:{hashlib.md5(text.encode('utf-8')).hexdigest()}"
            redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding_list))

    return results

# ===================== IMAGE EMBEDDING CACHE HELPERS =====================
def _get_image_bytes_hash(image_path: str) -> str:
    """Generate MD5 hash from image file bytes."""
    with open(image_path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

# FIX #2: Updated to handle transparency by pasting onto white background
def _load_and_preprocess_image(image_path: str):
    """Load image, handle transparency, and convert to RGB."""
    img = Image.open(image_path)

    # handling transparency - paste on white background
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        alpha = img.convert('RGBA').split()[-1]
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=alpha)
        return bg

    return img.convert('RGB')

# ===================== CLIP EMBEDDING CACHE =====================
@torch.inference_mode()
def get_clip_embedding_cached(image_path: str) -> list:
    """Get CLIP embedding with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable.

    Args:
        image_path: Path to the image file

    Returns:
        List of floats representing the CLIP embedding
    """
    # Generate embedding (always needed if not in cache)
    def _generate_embedding():
        pil_image = _load_and_preprocess_image(image_path)
        tensor = clip_preprocess(pil_image).unsqueeze(0).to(device)
        if USE_FP16 and device == 'cuda':
            tensor = tensor.half()
        feat = clip_model.encode_image(tensor)
        feat /= feat.norm(dim=-1, keepdim=True)
        return feat.float().cpu().squeeze().tolist()

    # If Redis not available, just generate embedding without caching
    if not REDIS_AVAILABLE:
        return _generate_embedding()

    # Generate cache key from MD5 hash of image bytes
    cache_key = f"clip_emb:{_get_image_bytes_hash(image_path)}"

    # Try to get from cache
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # Generate embedding
    embedding = _generate_embedding()

    # Store in cache with TTL
    redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return embedding

@torch.inference_mode()
def get_clip_embeddings_batch_cached(image_paths: list) -> list:
    """Batch CLIP embeddings with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable.

    Args:
        image_paths: List of paths to image files

    Returns:
        List of embeddings (each embedding is a list of floats)
    """
    results = [None] * len(image_paths)

    # If Redis not available, process all without caching
    if not REDIS_AVAILABLE:
        uncached_data = [(i, path, None) for i, path in enumerate(image_paths) if path is not None]
    else:
        uncached_data = []  # List of (index, image_path, hash)

        # Check cache for each image
        for i, image_path in enumerate(image_paths):
            if image_path is None:
                continue

            try:
                img_hash = _get_image_bytes_hash(image_path)
                cache_key = f"clip_emb:{img_hash}"
                cached = redis_client.get(cache_key)

                if cached:
                    results[i] = json.loads(cached)
                else:
                    uncached_data.append((i, image_path, img_hash))
            except Exception:
                continue

    # Batch encode uncached images
    if uncached_data:
        tensors = []
        valid_indices = []

        for idx, image_path, img_hash in uncached_data:
            try:
                pil_image = _load_and_preprocess_image(image_path)
                tensors.append(clip_preprocess(pil_image))
                valid_indices.append((idx, img_hash))
            except Exception:
                continue

        if tensors:
            batch_tensor = torch.stack(tensors).to(device)
            if USE_FP16 and device == 'cuda':
                batch_tensor = batch_tensor.half()
            feats = clip_model.encode_image(batch_tensor)
            feats /= feats.norm(dim=-1, keepdim=True)
            feats_list = feats.float().cpu().tolist()

            for (orig_idx, img_hash), embedding in zip(valid_indices, feats_list):
                results[orig_idx] = embedding
                # Cache the new embedding (only if Redis available)
                if REDIS_AVAILABLE and img_hash:
                    cache_key = f"clip_emb:{img_hash}"
                    redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return results

# ===================== DINO EMBEDDING CACHE =====================
@torch.inference_mode()
def get_dino_embedding_cached(image_path: str) -> list:
    """Get DINOv2 embedding with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable.

    Args:
        image_path: Path to the image file

    Returns:
        List of floats representing the DINOv2 embedding
    """
    # Generate embedding (always needed if not in cache)
    def _generate_embedding():
        pil_image = _load_and_preprocess_image(image_path)
        tensor = dinov2_preprocess(pil_image).unsqueeze(0).to(device)
        if USE_FP16 and device == 'cuda':
            tensor = tensor.half()
        feat = dinov2_model(tensor)
        return feat.float().cpu().squeeze().tolist()

    # If Redis not available, just generate embedding without caching
    if not REDIS_AVAILABLE:
        return _generate_embedding()

    # Generate cache key from MD5 hash of image bytes
    cache_key = f"dino_emb:{_get_image_bytes_hash(image_path)}"

    # Try to get from cache
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # Generate embedding
    embedding = _generate_embedding()

    # Store in cache with TTL
    redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return embedding

@torch.inference_mode()
def get_dino_embeddings_batch_cached(image_paths: list) -> list:
    """Batch DINOv2 embeddings with Redis caching (24h TTL). Falls back to non-cached if Redis unavailable.

    Args:
        image_paths: List of paths to image files

    Returns:
        List of embeddings (each embedding is a list of floats)
    """
    results = [None] * len(image_paths)

    # If Redis not available, process all without caching
    if not REDIS_AVAILABLE:
        uncached_data = [(i, path, None) for i, path in enumerate(image_paths) if path is not None]
    else:
        uncached_data = []  # List of (index, image_path, hash)

        # Check cache for each image
        for i, image_path in enumerate(image_paths):
            if image_path is None:
                continue

            try:
                img_hash = _get_image_bytes_hash(image_path)
                cache_key = f"dino_emb:{img_hash}"
                cached = redis_client.get(cache_key)

                if cached:
                    results[i] = json.loads(cached)
                else:
                    uncached_data.append((i, image_path, img_hash))
            except Exception:
                continue

    # Batch encode uncached images
    if uncached_data:
        tensors = []
        valid_indices = []

        for idx, image_path, img_hash in uncached_data:
            try:
                pil_image = _load_and_preprocess_image(image_path)
                tensors.append(dinov2_preprocess(pil_image))
                valid_indices.append((idx, img_hash))
            except Exception:
                continue

        if tensors:
            batch_tensor = torch.stack(tensors).to(device)
            if USE_FP16 and device == 'cuda':
                batch_tensor = batch_tensor.half()
            feats = dinov2_model(batch_tensor)
            feats_list = feats.float().cpu().tolist()

            for (orig_idx, img_hash), embedding in zip(valid_indices, feats_list):
                results[orig_idx] = embedding
                # Cache the new embedding (only if Redis available)
                if REDIS_AVAILABLE and img_hash:
                    cache_key = f"dino_emb:{img_hash}"
                    redis_client.setex(cache_key, EMBEDDING_CACHE_TTL, json.dumps(embedding))

    return results

logger.info("All AI models initialized successfully", device=device, fp16=USE_FP16, redis_cache=REDIS_AVAILABLE)

def get_image_path(folder_path, image_id):
    img_dir = folder_path / "images"
    if not img_dir.exists(): return None
    for ext in [".jpg", ".jpeg", ".png", ".bmp", ".tif"]:
        candidate = img_dir / f"{image_id}{ext}"
        if candidate.exists(): return candidate
    return None

# FIX #3: Increased histogram bins from [8, 2, 2] to [8, 8, 8]
def extract_color_histogram(pil_image):
    cv_img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
    hsv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv_img], [0, 1, 2], None, [8, 8, 8], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().tolist()

@torch.inference_mode()
def process_batch(batch_data, stats):
    """Process a batch of images with Redis-cached CLIP and DINOv2 embeddings."""
    if not batch_data:
        return

    batch_start = time.perf_counter()
    batch_size = len(batch_data)

    # Separate tracking for each embedding type
    clip_indices = []  # Indices needing CLIP embeddings
    clip_paths = []    # Corresponding image paths
    dino_indices = []  # Indices needing DINO embeddings
    dino_paths = []    # Corresponding image paths

    # First pass: process color histograms and identify what needs embedding
    for i, (rec, img_path) in enumerate(batch_data):
        try:
            # Color histogram (not cached - fast to compute)
            # Also regenerate if wrong dimension (old code produced 32-dim, correct is 512)
            existing_color = rec.get("color_histogram")
            if existing_color is None or (isinstance(existing_color, list) and len(existing_color) != 512):
                pil_image = _load_and_preprocess_image(str(img_path))
                rec["color_histogram"] = extract_color_histogram(pil_image)
                stats["color_gen"] += 1
            else:
                stats["color_skip"] += 1

            # Track which images need CLIP embeddings
            if rec.get("image_embedding") is None:
                clip_indices.append(i)
                clip_paths.append(str(img_path))
                stats["clip_gen"] += 1
            else:
                stats["clip_skip"] += 1

            # Track which images need DINO embeddings
            if rec.get("dinov2_embedding") is None:
                dino_indices.append(i)
                dino_paths.append(str(img_path))
                stats["dino_gen"] += 1
            else:
                stats["dino_skip"] += 1

        except Exception as e:
            logger.warning("Image processing failed", image=str(img_path), error=str(e))
            continue

    # Batch process CLIP embeddings with caching
    clip_cache_hits = 0
    if clip_paths:
        clip_start = time.perf_counter()
        clip_embeddings = get_clip_embeddings_batch_cached(clip_paths)
        for idx, embedding in zip(clip_indices, clip_embeddings):
            if embedding is not None:
                batch_data[idx][0]["image_embedding"] = embedding
        clip_duration = (time.perf_counter() - clip_start) * 1000
        logger.debug("CLIP batch processed", count=len(clip_paths), duration_ms=round(clip_duration, 2))

    # Batch process DINO embeddings with caching
    if dino_paths:
        dino_start = time.perf_counter()
        dino_embeddings = get_dino_embeddings_batch_cached(dino_paths)
        for idx, embedding in zip(dino_indices, dino_embeddings):
            if embedding is not None:
                batch_data[idx][0]["dinov2_embedding"] = embedding
        dino_duration = (time.perf_counter() - dino_start) * 1000
        logger.debug("DINO batch processed", count=len(dino_paths), duration_ms=round(dino_duration, 2))

    batch_duration = (time.perf_counter() - batch_start) * 1000
    logger.debug(
        "Batch processed",
        batch_size=batch_size,
        clip_generated=len(clip_paths),
        dino_generated=len(dino_paths),
        duration_ms=round(batch_duration, 2),
        images_per_sec=round(batch_size / (batch_duration / 1000), 1) if batch_duration > 0 else 0
    )

def process_folder(folder_path):
    json_path = folder_path / "metadata.json"
    img_dir = folder_path / "images"

    # Must have metadata.json
    if not json_path.exists():
        return

    has_images = img_dir.exists()

    folder_start = time.perf_counter()
    logger.info("Processing folder", folder=folder_path.name)

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Failed to load metadata", folder=folder_path.name, error=str(e))
        return

    total_records = len(data)

    # --- CLEANUP STEP REMOVED: Do not purge OCR fields ---

    # --- Identify records needing AI processing ---
    records_to_process = []
    for rec in data:
        cleaned_text_name = _prepare_name_derived_ai_features(rec)
        # Check features for backfill
        has_text = rec.get("text_embedding") is not None
        has_translation = rec.get("name_tr") is not None
        has_lang = rec.get("detected_lang") is not None
        name_text_done = cleaned_text_name is None or (has_text and has_translation and has_lang)

        if has_images:
            has_clip = rec.get("image_embedding") is not None
            has_dino = rec.get("dinov2_embedding") is not None
            has_color = rec.get("color_histogram") is not None
            has_ocr = rec.get("logo_ocr_text") is not None
            all_done = has_clip and has_dino and has_color and has_ocr and name_text_done
        else:
            # No images dir: only text features matter
            all_done = name_text_done

        if SKIP_IF_PROCESSED and all_done:
            continue

        records_to_process.append(rec)

    # --- Decision Logic ---
    if not records_to_process:
        logger.debug("Folder already processed", folder=folder_path.name)
        return

    # Task 1a: Name Embeddings (Redis Cached)
    # Skip records with null/empty names (logo-only trademarks cleaned by metadata.py)
    records_needing_text = [
        (i, r) for i, r in enumerate(records_to_process)
        if r.get("text_embedding") is None and _clean_ai_trademark_name(r)
    ]
    if records_needing_text:
        text_start = time.perf_counter()
        names_to_encode = [_clean_ai_trademark_name(r) or "" for _, r in records_needing_text]
        embeddings = get_text_embeddings_batch_cached(names_to_encode)
        for (orig_idx, r), emb in zip(records_needing_text, embeddings):
            r["text_embedding"] = emb
            r[_TEXT_EMBEDDING_SOURCE_NAME_KEY] = _clean_ai_trademark_name(r)
        logger.info(
            "Text embeddings processed",
            folder=folder_path.name,
            count=len(names_to_encode),
            duration_ms=round((time.perf_counter() - text_start) * 1000, 2)
        )

    # Task 1b: Translate to Turkish only (batched for GPU efficiency)
    records_needing_translation = [
        (i, r) for i, r in enumerate(records_to_process)
        if (r.get("name_tr") is None or r.get("detected_lang") is None) and _clean_ai_trademark_name(r)
    ]
    if records_needing_translation:
        trans_start = time.perf_counter()
        names = [_clean_ai_trademark_name(r) or "" for _, r in records_needing_translation]
        translations = batch_translate_to_turkish(names, backend=PIPELINE_TRANSLATION_BACKEND)
        provenance = get_translation_backend_info(PIPELINE_TRANSLATION_BACKEND)
        translation_timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        trans_count = 0
        for (orig_idx, r), (name_tr, lang) in zip(records_needing_translation, translations):
            r["name_tr"] = name_tr
            r["detected_lang"] = lang
            r["name_tr_backend"] = provenance["backend"]
            r["name_tr_model"] = provenance["model_name"]
            r["name_tr_updated_at"] = translation_timestamp
            r[_TRANSLATION_SOURCE_NAME_KEY] = _clean_ai_trademark_name(r)
            trans_count += 1
        logger.info(
            "Translations processed (batched TR)",
            folder=folder_path.name,
            count=trans_count,
            duration_ms=round((time.perf_counter() - trans_start) * 1000, 2)
        )

    stats = {
        "color_gen": 0, "color_skip": 0,
        "clip_gen": 0, "clip_skip": 0,
        "dino_gen": 0, "dino_skip": 0,
        "ocr_gen": 0, "ocr_skip": 0
    }

    # Task 2: Visual & Color & OCR (only if images directory exists)
    if has_images:
        visual_start = time.perf_counter()
        current_batch = []
        for i, rec in enumerate(tqdm(records_to_process, desc="   Extracting Features", leave=False)):
            img_path = get_image_path(folder_path, rec.get("IMAGE"))
            if not img_path:
                continue

            # --- NEW STEP: OCR Extraction (if available) ---
            if ocr_reader is not None and rec.get("logo_ocr_text") is None:
                try:
                    # detail=0 returns simple list of text strings found
                    ocr_res = ocr_reader.readtext(str(img_path), detail=0, paragraph=True)
                    rec["logo_ocr_text"] = " ".join(ocr_res)
                    stats["ocr_gen"] += 1
                except Exception as e:
                    logger.warning("OCR failed", image=str(img_path), error=str(e))
                    rec["logo_ocr_text"] = ""
            elif ocr_reader is None and rec.get("logo_ocr_text") is None:
                rec["logo_ocr_text"] = ""  # Set empty if OCR not available
            else:
                stats["ocr_skip"] += 1

            # Visual & Color Batch Accumulation
            if rec.get("image_embedding") is None or rec.get("dinov2_embedding") is None or rec.get("color_histogram") is None:
                current_batch.append((rec, img_path))
                if len(current_batch) >= BATCH_SIZE:
                    process_batch(current_batch, stats)
                    current_batch = []
            else:
                stats["color_skip"] += 1
                stats["clip_skip"] += 1
                stats["dino_skip"] += 1

            if (i + 1) % 2000 == 0:
                log_batch_stats(
                    operation="visual_embeddings",
                    total=2000,
                    processed=stats["clip_gen"] + stats["dino_gen"],
                    skipped=stats["clip_skip"] + stats["dino_skip"],
                    folder=folder_path.name
                )
                stats = {k: 0 for k in stats}

        if current_batch:
            process_batch(current_batch, stats)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    folder_duration = (time.perf_counter() - folder_start) * 1000
    log_batch_stats(
        operation="process_folder",
        total=total_records,
        processed=len(records_to_process),
        skipped=total_records - len(records_to_process),
        duration_ms=folder_duration,
        folder=folder_path.name,
        clip_generated=stats["clip_gen"],
        dino_generated=stats["dino_gen"],
        color_generated=stats["color_gen"],
        ocr_generated=stats["ocr_gen"]
    )


def run_embedding_generation(root_dir=None, settings=None) -> dict:
    """
    Batch-generate embeddings for all metadata.json files.
    Reads metadata.json -> processes images + text -> writes embeddings back to metadata.json.

    Args:
        root_dir: Root directory override. Defaults to ROOT from config.
        settings: Optional PipelineSettings override.

    Returns:
        { "processed": N, "skipped": N, "failed": N, "duration_seconds": N }
    """
    global ROOT, BATCH_SIZE, SKIP_IF_PROCESSED

    if settings is not None:
        ROOT = Path(settings.bulletins_root)
        BATCH_SIZE = settings.embedding_batch_size
        SKIP_IF_PROCESSED = settings.skip_if_embeddings_exist

    if root_dir is not None:
        ROOT = Path(root_dir)

    if not ROOT.exists():
        logger.error("Data root not found", path=str(ROOT))
        return {"processed": 0, "skipped": 0, "failed": 0, "duration_seconds": 0}

    t0 = time.time()
    all_dirs = sorted([p for p in ROOT.iterdir() if p.is_dir()])
    logger.info("Starting AI processing", total_folders=len(all_dirs), root=str(ROOT))

    processed = 0
    skipped = 0
    failed = 0

    for folder in all_dirs:
        json_path = folder / "metadata.json"
        img_dir = folder / "images"

        if not json_path.exists():
            skipped += 1
            continue

        try:
            process_folder(folder)
            processed += 1
        except Exception as e:
            failed += 1
            logger.error("Folder processing failed", folder=folder.name, error=str(e))

    duration = time.time() - t0
    logger.info("AI processing complete",
                total_folders=len(all_dirs),
                processed=processed,
                skipped=skipped,
                failed=failed,
                duration_seconds=round(duration, 1))

    return {
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "duration_seconds": round(duration, 1),
    }


def main():
    if not ROOT.exists():
        logger.error("Data root not found", path=str(ROOT))
        return

    all_dirs = sorted([p for p in ROOT.iterdir() if p.is_dir()])
    logger.info("Starting AI processing", total_folders=len(all_dirs), root=str(ROOT))

    for folder in all_dirs:
        process_folder(folder)

    logger.info("AI processing complete", total_folders=len(all_dirs))


if __name__ == "__main__":
    main()
