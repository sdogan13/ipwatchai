"""
Translation layer for trademark name handling.

Live query translation stays on NLLB by default. MADLAD is the default
pipeline/offline translation backend for name_tr generation and refresh.
FastText remains the canonical language detector.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import torch

from utils.idf_scoring import turkish_lower
from utils.model_cache import find_hf_snapshot_dir, find_hf_snapshot_file

logger = logging.getLogger(__name__)

TRANSLATION_BACKEND_NLLB = "nllb"
TRANSLATION_BACKEND_MADLAD = "madlad"
SUPPORTED_TRANSLATION_BACKENDS = {
    TRANSLATION_BACKEND_NLLB,
    TRANSLATION_BACKEND_MADLAD,
}

try:
    from config.settings import settings as _app_settings

    TRANSLATION_BACKEND = _app_settings.ai.translation_backend
    PIPELINE_TRANSLATION_BACKEND = _app_settings.ai.pipeline_translation_backend
    OFFLINE_TRANSLATION_BACKEND = _app_settings.ai.offline_translation_backend
    MODEL_NAME = _app_settings.ai.translation_model
    MADLAD_MODEL_NAME = _app_settings.ai.madlad_translation_model
    MADLAD_TRANSLATE_BATCH_SIZE = _app_settings.ai.madlad_translate_batch_size
    DEFAULT_DEVICE = _app_settings.ai.translation_device
except Exception:
    TRANSLATION_BACKEND = TRANSLATION_BACKEND_NLLB
    PIPELINE_TRANSLATION_BACKEND = TRANSLATION_BACKEND_MADLAD
    OFFLINE_TRANSLATION_BACKEND = TRANSLATION_BACKEND_MADLAD
    MODEL_NAME = "facebook/nllb-200-distilled-600M"
    MADLAD_MODEL_NAME = "google/madlad400-3b-mt"
    MADLAD_TRANSLATE_BATCH_SIZE = 16
    DEFAULT_DEVICE = "auto"

MEMORY_CACHE_SIZE = 100_000

# NLLB-200 language code mapping (ISO 639 -> NLLB flores-200 codes)
NLLB_LANG_MAP = {
    "tr": "tur_Latn",
    "en": "eng_Latn",
    "ku": "kmr_Latn",
    "fa": "fas_Arab",
    "ar": "ara_Arab",
    "de": "deu_Latn",
    "fr": "fra_Latn",
    "es": "spa_Latn",
    "it": "ita_Latn",
    "nl": "nld_Latn",
    "ru": "rus_Cyrl",
    "pl": "pol_Latn",
    "sv": "swe_Latn",
    "el": "ell_Grek",
    "pt": "por_Latn",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "ur": "urd_Arab",
}

# MADLAD prompt language tags. We always translate to Turkish on the MADLAD
# path instead of letting language detection decide whether a trademark should
# be sent to the model.
MADLAD_LANG_MAP = {
    "tr": "tr",
    "en": "en",
    "fa": "fa",
    "ar": "ar",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "nl": "nl",
    "ru": "ru",
    "pl": "pl",
    "sv": "sv",
    "el": "el",
    "pt": "pt",
    "zh": "zh",
    "ja": "ja",
    "ko": "ko",
    "he": "he",
    "hi": "hi",
    "ur": "ur",
}

LANG_NAMES = {
    "tr": "Turkish",
    "en": "English",
    "ku": "Kurdish",
    "fa": "Persian",
    "ar": "Arabic",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "nl": "Dutch",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
}

LANG_DISPLAY_NAMES = {
    "tr": "TÃ¼rkÃ§e",
    "en": "Ä°ngilizce",
    "ku": "KÃ¼rtÃ§e",
    "fa": "FarsÃ§a",
    "ar": "ArapÃ§a",
    "de": "Almanca",
    "fr": "FransÄ±zca",
    "ru": "RusÃ§a",
    "zh": "Ã‡ince",
}

TARGET_LANGUAGES = ["tr"]

_TRAILING_PUNCT_RE = re.compile(r"[\"'`“”‘’.,;:!?]+$")
_META_PREFIX_RE = re.compile(r"^\s*[\"'`“”‘’]*\s*([^:\n]{1,96})\s*:\s*")
_TRANSLATION_CUE_RE = re.compile(r"\b(?:cevir\w*|translation|translate(?:d)?)\b", re.IGNORECASE)
_PROMPT_META_ASCII_MAP = str.maketrans(
    {
        "ç": "c",
        "Ç": "c",
        "ğ": "g",
        "Ğ": "g",
        "ı": "i",
        "I": "i",
        "İ": "i",
        "ö": "o",
        "Ö": "o",
        "ş": "s",
        "Ş": "s",
        "ü": "u",
        "Ü": "u",
        "â": "a",
        "Â": "a",
        "î": "i",
        "Î": "i",
        "û": "u",
        "Û": "u",
    }
)

_FASTTEXT_UNAVAILABLE = object()
_fasttext_model = None
_NLLB_TO_ISO = {v: k for k, v in NLLB_LANG_MAP.items()}

_backend_states = {
    TRANSLATION_BACKEND_NLLB: {
        "model": None,
        "tokenizer": None,
        "device": None,
        "initialized": False,
    },
    TRANSLATION_BACKEND_MADLAD: {
        "model": None,
        "tokenizer": None,
        "device": None,
        "initialized": False,
    },
}


def _normalize_backend(backend: Optional[str], *, default_scope: str = "live") -> str:
    if backend:
        candidate = str(backend).strip().lower()
    elif default_scope == "pipeline":
        candidate = str(PIPELINE_TRANSLATION_BACKEND).strip().lower()
    elif default_scope == "offline":
        candidate = str(OFFLINE_TRANSLATION_BACKEND).strip().lower()
    else:
        candidate = str(TRANSLATION_BACKEND).strip().lower()

    if candidate not in SUPPORTED_TRANSLATION_BACKENDS:
        logger.warning("Unknown translation backend; falling back to NLLB: %s", candidate)
        return TRANSLATION_BACKEND_NLLB
    return candidate


def get_default_translation_backend(scope: str = "live") -> str:
    return _normalize_backend(None, default_scope=scope)


def get_translation_backend_info(backend: Optional[str] = None) -> Dict[str, str]:
    normalized = _normalize_backend(backend)
    if normalized == TRANSLATION_BACKEND_MADLAD:
        model_name = MADLAD_MODEL_NAME
    else:
        model_name = MODEL_NAME
    return {"backend": normalized, "model_name": model_name}


def get_madlad_translate_batch_size(override: Optional[int] = None) -> int:
    if override is None:
        value = MADLAD_TRANSLATE_BATCH_SIZE
    else:
        value = override
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = MADLAD_TRANSLATE_BATCH_SIZE
    return max(1, size)


def build_translation_provenance(
    backend: Optional[str] = None,
    updated_at: Optional[datetime] = None,
) -> Dict[str, Optional[str]]:
    info = get_translation_backend_info(backend)
    timestamp = (updated_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "name_tr_backend": info["backend"],
        "name_tr_model": info["model_name"],
        "name_tr_updated_at": timestamp,
    }


def _resolve_model_source(model_name: str, required_files: Optional[List[str]] = None) -> str:
    model_path = os.path.expanduser(model_name)
    if os.path.exists(model_path):
        return model_path

    cached_snapshot = find_hf_snapshot_dir(model_name, required_files=required_files or ["config.json"])
    if cached_snapshot is not None:
        logger.info("Using cached translation snapshot: model=%s path=%s", model_name, cached_snapshot)
        return str(cached_snapshot)

    return model_name


def _resolve_translation_model_source(model_name: Optional[str] = None) -> str:
    return _resolve_model_source(model_name or MODEL_NAME, required_files=["config.json"])


def _resolve_madlad_model_source(model_name: Optional[str] = None) -> str:
    return _resolve_model_source(model_name or MADLAD_MODEL_NAME, required_files=["config.json"])


def _resolve_fasttext_langid_model_path() -> Optional[str]:
    cached_path = find_hf_snapshot_file("facebook/fasttext-language-identification", "model.bin")
    if cached_path is not None:
        logger.info("Using cached FastText LangID model: %s", cached_path)
        return str(cached_path)
    return None


def _load_fasttext_langid():
    global _fasttext_model
    if _fasttext_model is _FASTTEXT_UNAVAILABLE:
        return None
    if _fasttext_model is not None:
        return _fasttext_model
    try:
        import fasttext
        from huggingface_hub import hf_hub_download
        import warnings

        warnings.filterwarnings("ignore", category=UserWarning, module="fasttext")
        model_path = _resolve_fasttext_langid_model_path() or hf_hub_download(
            repo_id="facebook/fasttext-language-identification",
            filename="model.bin",
        )
        _fasttext_model = fasttext.load_model(model_path)
        logger.info("FastText LangID model loaded")
        return _fasttext_model
    except Exception as exc:
        logger.warning("FastText LangID not available: %s", exc)
        _fasttext_model = _FASTTEXT_UNAVAILABLE
        return None


def detect_language_fasttext(text: str) -> Tuple[str, str, float]:
    original_clean = text.replace("\n", " ").strip() if text else ""
    if not original_clean:
        return "en", "eng_Latn", 0.0

    clean = turkish_lower(original_clean)
    model = _load_fasttext_langid()
    if model is None:
        return "unknown", "unknown", 0.0

    labels, scores = model.predict(clean)
    nllb_code = labels[0].replace("__label__", "")
    confidence = float(scores[0])
    iso_code = _NLLB_TO_ISO.get(nllb_code, "en")
    if confidence < 0.5:
        return "unknown", "unknown", confidence
    return iso_code, nllb_code, confidence


def _select_device(device: Optional[str]) -> str:
    requested = device if device and device != "auto" else DEFAULT_DEVICE
    if requested in (None, "", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return str(requested)


def _backend_state(backend: str) -> dict:
    normalized = _normalize_backend(backend)
    return _backend_states[normalized]


def initialize(device: str = None, backend: Optional[str] = None) -> bool:
    normalized = _normalize_backend(backend)
    state = _backend_state(normalized)
    if state["initialized"]:
        return True

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        state["device"] = _select_device(device)
        model_name = get_translation_backend_info(normalized)["model_name"]
        logger.info(
            "Loading translation model: backend=%s model=%s device=%s",
            normalized,
            model_name,
            state["device"].upper(),
        )

        if normalized == TRANSLATION_BACKEND_MADLAD:
            model_source = _resolve_madlad_model_source(model_name)
        else:
            model_source = _resolve_translation_model_source(model_name)

        state["tokenizer"] = AutoTokenizer.from_pretrained(model_source)
        state["model"] = AutoModelForSeq2SeqLM.from_pretrained(
            model_source,
            torch_dtype=torch.float16 if state["device"] == "cuda" else torch.float32,
        )
        state["model"].to(state["device"]).eval()
        state["initialized"] = True
        logger.info("Translation model ready: backend=%s", normalized)
        return True
    except ImportError:
        logger.error("Install: pip install transformers sentencepiece")
        return False
    except Exception as exc:
        logger.error("Translation init failed [%s]: %s", normalized, exc)
        return False


def is_ready(backend: Optional[str] = None) -> bool:
    state = _backend_state(_normalize_backend(backend))
    return state["initialized"] and state["model"] is not None


def unload(backend: Optional[str] = None):
    backends = [backend] if backend else list(SUPPORTED_TRANSLATION_BACKENDS)
    for item in backends:
        normalized = _normalize_backend(item)
        state = _backend_state(normalized)
        if state["model"] is not None:
            del state["model"]
            state["model"] = None
        if state["tokenizer"] is not None:
            del state["tokenizer"]
            state["tokenizer"] = None
        state["device"] = None
        state["initialized"] = False
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Translation model(s) unloaded")


def _normalize_model_input(text: str, backend: Optional[str] = None) -> str:
    text = text.strip()
    normalized = _normalize_backend(backend) if backend else TRANSLATION_BACKEND_NLLB
    if normalized == TRANSLATION_BACKEND_MADLAD:
        return text.lower() if text.isascii() else text
    return text.lower() if text.isascii() else text


def _has_token_overlap(source_text: str, translated_text: str) -> bool:
    source_tokens = set(turkish_lower(source_text).split())
    translated_tokens = set(turkish_lower(translated_text).split())
    return bool(source_tokens & translated_tokens)


def _normalize_prompt_meta_text(text: str) -> str:
    return turkish_lower(text or "").translate(_PROMPT_META_ASCII_MAP)


def _looks_like_prompt_meta_prefix(prefix: str) -> bool:
    normalized = _normalize_prompt_meta_text(prefix).strip()
    if not normalized:
        return False
    return bool(_TRANSLATION_CUE_RE.search(normalized))


def _strip_prompt_leak_prefixes(text: str) -> tuple[str, bool]:
    cleaned = text.strip() if text else ""
    changed = False
    while cleaned:
        match = _META_PREFIX_RE.match(cleaned)
        if not match:
            break
        prefix = match.group(1)
        if not _looks_like_prompt_meta_prefix(prefix):
            break
        cleaned = cleaned[match.end():].strip()
        changed = True
    return cleaned, changed


def _extract_digit_sequence(text: str) -> str:
    return "".join(char for char in (text or "") if char.isdigit())


def _has_digit_drift(source_text: str, candidate_text: str) -> bool:
    return _extract_digit_sequence(source_text) != _extract_digit_sequence(candidate_text)


def _postprocess_translation(translation: str, model_input: str) -> Optional[str]:
    text = translation.strip() if translation else ""
    if not text:
        return None
    text, _ = _strip_prompt_leak_prefixes(text)
    if not text:
        return None
    text = _TRAILING_PUNCT_RE.sub("", text).strip()
    if text.lower().endswith("color") and len(text) > 5:
        text = text[:-5].strip()
    words = text.split()
    if len(words) > 1 and len(set(turkish_lower(word) for word in words)) == 1:
        text = words[0]
    elif len(words) > 1 and len(model_input.split()) == 1:
        filtered = [word for word in words if turkish_lower(word) != turkish_lower(model_input)]
        if filtered:
            text = " ".join(filtered)
    if text and turkish_lower(text) != turkish_lower(model_input):
        return text
    return None


def has_prompt_leakage(text: Optional[str]) -> bool:
    if not text:
        return False
    _, changed = _strip_prompt_leak_prefixes(text)
    return changed


def _madlad_prompt(text: str, target: str) -> Optional[str]:
    target_code = MADLAD_LANG_MAP.get(target)
    if not target_code:
        return None
    return f"<2{target_code}> {text}"


def _madlad_guard_prompt(text: str, target: str) -> Optional[str]:
    target_code = MADLAD_LANG_MAP.get(target)
    if not target_code:
        return None
    return f"<2{target_code}> Translate to Turkish: {text}"


def _pick_madlad_candidate(
    original_text: str,
    model_input: str,
    primary_decoded: Optional[str],
    guard_decoded: Optional[str],
) -> Optional[str]:
    primary = _postprocess_translation(primary_decoded or "", model_input)
    guard = _postprocess_translation(guard_decoded or "", model_input)
    source_norm = original_text.strip().casefold()
    raw_guard, _ = _strip_prompt_leak_prefixes((guard_decoded or "").strip())
    raw_guard = _TRAILING_PUNCT_RE.sub("", raw_guard).strip()

    if raw_guard and raw_guard.casefold() == source_norm:
        return None
    if guard and guard.casefold() == source_norm:
        return None
    if primary and guard:
        primary_norm = turkish_lower(primary)
        guard_norm = turkish_lower(guard)
        if primary_norm == guard_norm:
            candidate = primary
        elif primary_norm.startswith(guard_norm):
            candidate = guard
        elif guard_norm.startswith(primary_norm):
            candidate = primary
        else:
            candidate = guard
    else:
        candidate = guard or primary
    if candidate and _has_digit_drift(original_text, candidate):
        return None
    return candidate


def _translate_nllb(text: str, source: str, target: str) -> Optional[str]:
    if not text or not text.strip() or source == target:
        return None
    src_nllb = NLLB_LANG_MAP.get(source)
    tgt_nllb = NLLB_LANG_MAP.get(target)
    if not src_nllb or not tgt_nllb:
        logger.debug("Unsupported NLLB pair %s -> %s", source, target)
        return None
    if not is_ready(TRANSLATION_BACKEND_NLLB):
        if not initialize(backend=TRANSLATION_BACKEND_NLLB):
            return None

    state = _backend_state(TRANSLATION_BACKEND_NLLB)
    tokenizer = state["tokenizer"]
    model = state["model"]
    device = state["device"]
    model_input = _normalize_model_input(text, TRANSLATION_BACKEND_NLLB)

    try:
        tokenizer.src_lang = src_nllb
        inputs = tokenizer(model_input, return_tensors="pt", padding=True, truncation=True, max_length=128)
        inputs = {key: value.to(device) for key, value in inputs.items()}
        tgt_token_id = tokenizer.convert_tokens_to_ids(tgt_nllb)
        input_word_count = len(model_input.split())
        max_tokens = min(8 + input_word_count * 4, 64)
        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                forced_bos_token_id=tgt_token_id,
                max_new_tokens=max_tokens,
                num_beams=2,
            )
        decoded = tokenizer.decode(outputs[0], skip_special_tokens=True)
        del inputs, outputs
        return _postprocess_translation(decoded, model_input)
    except Exception as exc:
        logger.debug("NLLB translation failed '%s': %s", text[:30], exc)
        return None


def _translate_madlad(text: str, target: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    model_input = _normalize_model_input(text, TRANSLATION_BACKEND_MADLAD)
    prompt = _madlad_prompt(model_input, target)
    guard_prompt = _madlad_guard_prompt(model_input, target)
    if not prompt or not guard_prompt:
        logger.debug("Unsupported MADLAD target %s", target)
        return None
    if not is_ready(TRANSLATION_BACKEND_MADLAD):
        if not initialize(backend=TRANSLATION_BACKEND_MADLAD):
            return None

    state = _backend_state(TRANSLATION_BACKEND_MADLAD)
    tokenizer = state["tokenizer"]
    model = state["model"]
    device = state["device"]
    try:
        inputs = tokenizer([prompt, guard_prompt], return_tensors="pt", padding=True, truncation=True, max_length=160)
        inputs = {key: value.to(device) for key, value in inputs.items()}
        input_word_count = len(model_input.split())
        max_tokens = min(8 + input_word_count * 4, 64)
        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                num_beams=4,
                repetition_penalty=1.2,
                no_repeat_ngram_size=2,
            )
        decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
        del inputs, outputs
        primary_decoded = decoded[0] if decoded else None
        guard_decoded = decoded[1] if len(decoded) > 1 else None
        return _pick_madlad_candidate(text, model_input, primary_decoded, guard_decoded)
    except Exception as exc:
        logger.debug("MADLAD translation failed '%s': %s", text[:30], exc)
        return None


@lru_cache(maxsize=MEMORY_CACHE_SIZE)
def translate(text: str, source: str, target: str, backend: Optional[str] = None) -> Optional[str]:
    normalized = _normalize_backend(backend)
    if normalized == TRANSLATION_BACKEND_MADLAD:
        return _translate_madlad(text, target)
    return _translate_nllb(text, source, target)


def _batch_translate_nllb(texts: List[str], source: str, target: str, batch_size: int = 256) -> List[Optional[str]]:
    if not texts:
        return []
    src_nllb = NLLB_LANG_MAP.get(source)
    tgt_nllb = NLLB_LANG_MAP.get(target)
    if not src_nllb or not tgt_nllb:
        return [None] * len(texts)
    if not is_ready(TRANSLATION_BACKEND_NLLB):
        if not initialize(backend=TRANSLATION_BACKEND_NLLB):
            return [None] * len(texts)

    state = _backend_state(TRANSLATION_BACKEND_NLLB)
    tokenizer = state["tokenizer"]
    model = state["model"]
    device = state["device"]
    tgt_token_id = tokenizer.convert_tokens_to_ids(tgt_nllb)
    results = [None] * len(texts)

    for batch_start in range(0, len(texts), batch_size):
        batch_texts = texts[batch_start:batch_start + batch_size]
        batch_indices = []
        batch_inputs = []
        for index, text in enumerate(batch_texts):
            absolute_index = batch_start + index
            if not text or not text.strip() or source == target:
                continue
            batch_indices.append(absolute_index)
            batch_inputs.append(_normalize_model_input(text))
        if not batch_inputs:
            continue
        try:
            tokenizer.src_lang = src_nllb
            encoded = tokenizer(batch_inputs, return_tensors="pt", padding=True, truncation=True, max_length=128)
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.inference_mode():
                outputs = model.generate(
                    **encoded,
                    forced_bos_token_id=tgt_token_id,
                    max_new_tokens=32,
                    num_beams=2,
                )
            decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
            del encoded, outputs
            for offset, (idx, model_input) in enumerate(zip(batch_indices, batch_inputs)):
                results[idx] = _postprocess_translation(decoded[offset], model_input)
        except Exception as exc:
            logger.warning("Batch NLLB translation failed (batch %s): %s", batch_start, exc)
    return results


def _batch_translate_madlad(texts: List[str], target: str, batch_size: Optional[int] = None) -> List[Optional[str]]:
    if not texts:
        return []
    target_code = MADLAD_LANG_MAP.get(target)
    if not target_code:
        return [None] * len(texts)
    if not is_ready(TRANSLATION_BACKEND_MADLAD):
        if not initialize(backend=TRANSLATION_BACKEND_MADLAD):
            return [None] * len(texts)

    state = _backend_state(TRANSLATION_BACKEND_MADLAD)
    tokenizer = state["tokenizer"]
    model = state["model"]
    device = state["device"]
    results = [None] * len(texts)
    effective_batch_size = get_madlad_translate_batch_size(batch_size)

    for batch_start in range(0, len(texts), effective_batch_size):
        batch_texts = texts[batch_start:batch_start + effective_batch_size]
        batch_indices = []
        batch_prompts = []
        batch_guard_prompts = []
        batch_inputs = []
        for index, text in enumerate(batch_texts):
            absolute_index = batch_start + index
            if not text or not text.strip():
                continue
            model_input = _normalize_model_input(text, TRANSLATION_BACKEND_MADLAD)
            batch_indices.append(absolute_index)
            batch_inputs.append(model_input)
            batch_prompts.append(f"<2{target_code}> {model_input}")
            batch_guard_prompts.append(f"<2{target_code}> Translate to Turkish: {model_input}")
        if not batch_prompts:
            continue
        try:
            encoded = tokenizer(
                batch_prompts + batch_guard_prompts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=160,
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.inference_mode():
                outputs = model.generate(
                    **encoded,
                    max_new_tokens=32,
                    num_beams=4,
                    repetition_penalty=1.2,
                    no_repeat_ngram_size=2,
                )
            decoded = tokenizer.batch_decode(outputs, skip_special_tokens=True)
            del encoded, outputs
            for offset, (idx, model_input) in enumerate(zip(batch_indices, batch_inputs)):
                guard_offset = offset + len(batch_inputs)
                primary_decoded = decoded[offset] if offset < len(decoded) else None
                guard_decoded = decoded[guard_offset] if guard_offset < len(decoded) else None
                results[idx] = _pick_madlad_candidate(texts[idx], model_input, primary_decoded, guard_decoded)
        except Exception as exc:
            logger.warning("Batch MADLAD translation failed (batch %s): %s", batch_start, exc)
    return results


def batch_translate(
    texts: List[str],
    source: str,
    target: str,
    batch_size: int = 256,
    backend: Optional[str] = None,
) -> List[Optional[str]]:
    normalized = _normalize_backend(backend)
    if normalized == TRANSLATION_BACKEND_MADLAD:
        return _batch_translate_madlad(texts, target=target, batch_size=min(batch_size, get_madlad_translate_batch_size()))
    return _batch_translate_nllb(texts, source=source, target=target, batch_size=batch_size)


def _batch_translate_to_turkish_nllb(texts: List[str]) -> List[Tuple[str, str]]:
    results = [("", "unknown")] * len(texts)
    detected_langs = []
    all_indices = []
    all_texts = []
    for index, text in enumerate(texts):
        if not text or not text.strip():
            detected_langs.append("unknown")
            continue
        iso_code, _, _ = detect_language_fasttext(text)
        lang = iso_code if iso_code in NLLB_LANG_MAP else "en"
        detected_langs.append(lang)
        results[index] = (turkish_lower(text), lang)
        all_indices.append(index)
        all_texts.append(text)

    if not all_texts:
        return results

    en_translations = batch_translate(all_texts, "en", "tr", backend=TRANSLATION_BACKEND_NLLB)
    for idx, orig_text, trans in zip(all_indices, all_texts, en_translations):
        lang = detected_langs[idx]
        if trans:
            name_tr = turkish_lower(trans)
            if name_tr != turkish_lower(orig_text):
                if lang == "tr" and not _has_token_overlap(orig_text, name_tr):
                    results[idx] = (turkish_lower(orig_text), lang)
                    continue
                results[idx] = (name_tr, lang)
                continue
        results[idx] = (turkish_lower(orig_text), lang)

    other_lang_items: Dict[str, List[Tuple[int, str]]] = {}
    for idx, orig_text in zip(all_indices, all_texts):
        lang = detected_langs[idx]
        if lang not in ("en", "tr", "unknown"):
            other_lang_items.setdefault(lang, []).append((idx, orig_text))

    for source_lang, items in other_lang_items.items():
        indices = [i for i, _ in items]
        source_texts = [text for _, text in items]
        translations = batch_translate(source_texts, source_lang, "tr", backend=TRANSLATION_BACKEND_NLLB)
        for idx, orig_text, trans in zip(indices, source_texts, translations):
            if not trans:
                continue
            name_tr = turkish_lower(trans)
            if not _has_token_overlap(orig_text, name_tr):
                continue
            current_tr = results[idx][0]
            if name_tr != current_tr and name_tr != turkish_lower(orig_text):
                results[idx] = (name_tr, detected_langs[idx])

    return results


def _batch_translate_to_turkish_madlad(
    texts: List[str],
    *,
    batch_size: Optional[int] = None,
) -> List[Tuple[str, str]]:
    results = [("", "unknown")] * len(texts)
    all_indices = []
    all_texts = []
    for index, text in enumerate(texts):
        if not text or not text.strip():
            continue
        iso_code, _, _ = detect_language_fasttext(text)
        lang = iso_code if iso_code in NLLB_LANG_MAP else "unknown"
        results[index] = (turkish_lower(text), lang)
        all_indices.append(index)
        all_texts.append(text)

    if not all_texts:
        return results

    translations = batch_translate(
        all_texts,
        "auto",
        "tr",
        batch_size=get_madlad_translate_batch_size(batch_size),
        backend=TRANSLATION_BACKEND_MADLAD,
    )
    for idx, orig_text, trans in zip(all_indices, all_texts, translations):
        lang = results[idx][1]
        if not trans:
            results[idx] = (turkish_lower(orig_text), lang)
            continue
        name_tr = turkish_lower(trans)
        if name_tr == turkish_lower(orig_text):
            results[idx] = (turkish_lower(orig_text), lang)
            continue
        results[idx] = (name_tr, lang)

    return results


def batch_translate_to_turkish(
    texts: List[str],
    backend: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> List[Tuple[str, str]]:
    normalized = _normalize_backend(backend)
    if normalized == TRANSLATION_BACKEND_MADLAD:
        return _batch_translate_to_turkish_madlad(texts, batch_size=batch_size)
    return _batch_translate_to_turkish_nllb(texts)


def get_translations(text: str, backend: Optional[str] = None) -> Dict[str, Optional[str]]:
    normalized = _normalize_backend(backend)
    if not text:
        return {"original": text, "detected_lang": "unknown", "tr": None}

    iso_code, _, _ = detect_language_fasttext(text)
    detected = iso_code if iso_code in NLLB_LANG_MAP else "unknown"
    result = {
        "original": text,
        "detected_lang": detected,
        "tr": None,
    }

    if normalized != TRANSLATION_BACKEND_MADLAD and detected == "tr":
        result["tr"] = turkish_lower(text)
        return result

    source = detected if detected in NLLB_LANG_MAP else "en"
    translation = translate(text, source, "tr", backend=normalized)
    if translation and normalized == TRANSLATION_BACKEND_NLLB and source != "en" and not _has_token_overlap(text, translation):
        translation = translate(text, "en", "tr", backend=normalized)
    if translation:
        result["tr"] = turkish_lower(translation)
    return result


def get_search_variants(query: str) -> List[str]:
    variants = set()
    if not query:
        return []
    variants.add(turkish_lower(query.strip()))
    translations = get_translations(query)
    for lang in TARGET_LANGUAGES:
        if translations.get(lang):
            variants.add(turkish_lower(translations[lang].strip()))
    return list(variants)


def get_cross_language_similarity(text1: str, text2: str, candidate_name_tr: str = None) -> Tuple[float, str]:
    score = calculate_translation_similarity(text1, text2, candidate_name_tr=candidate_name_tr)
    if score >= 1.0:
        return score, "translation_exact"
    if score >= 0.85:
        return score, "translation_high"
    if score >= 0.6:
        return score, "translation_partial"
    return score, "none"


def score_translated_pair(translated_query: str, translated_candidate_tr: str) -> dict:
    if not translated_query or not translated_candidate_tr:
        return {"translation_similarity": 0.0}

    q = turkish_lower(translated_query.strip())
    c = turkish_lower(translated_candidate_tr.strip())
    if q == c:
        return {"translation_similarity": 1.0, "translation_scoring_path": "EXACT_MATCH"}

    from difflib import SequenceMatcher as _SM

    from risk_engine import calculate_name_similarity
    from services.scoring_service import compute_idf_weighted_score
    from utils.phonetic import calculate_phonetic_similarity

    turkish_sim = calculate_name_similarity(q, c)
    seq_sim = _SM(None, q, c).ratio()
    text_sim = max(turkish_sim, seq_sim)
    phon = calculate_phonetic_similarity(q, c)
    idf_total, breakdown = compute_idf_weighted_score(
        query=translated_query,
        target=translated_candidate_tr,
        text_sim=text_sim,
        semantic_sim=0.0,
        phonetic_sim=phon,
        visual_sim=0.0,
    )
    return {
        "translation_similarity": idf_total,
        "translation_scoring_path": breakdown.get("scoring_path", ""),
        "translation_text_sim": text_sim,
        "translation_phonetic": phon,
    }


def calculate_translation_similarity(
    query_name: str,
    candidate_name: str,
    candidate_name_tr: str = None,
    backend: Optional[str] = None,
) -> float:
    if not query_name:
        return 0.0
    if not candidate_name_tr:
        return 0.0
    query_tr = translate_to_turkish(query_name, backend=backend)
    if not query_tr:
        return 0.0
    from utils.idf_scoring import normalize_turkish

    if normalize_turkish(query_tr) == normalize_turkish(query_name):
        return 0.0
    result = score_translated_pair(query_tr, candidate_name_tr)
    return result["translation_similarity"]


@lru_cache(maxsize=MEMORY_CACHE_SIZE)
def translate_to_turkish(text: str, backend: Optional[str] = None) -> str:
    if not text or not text.strip():
        return ""
    text = text.strip()
    normalized = _normalize_backend(backend)
    iso_code, _, _ = detect_language_fasttext(text)
    if normalized != TRANSLATION_BACKEND_MADLAD and iso_code == "tr":
        return turkish_lower(text)
    source = iso_code if iso_code in NLLB_LANG_MAP else "en"
    result = translate(text, source, "tr", backend=normalized)
    if result and normalized == TRANSLATION_BACKEND_NLLB and source != "en" and not _has_token_overlap(text, result):
        result = translate(text, "en", "tr", backend=normalized)
    return turkish_lower(result) if result else turkish_lower(text)


def auto_translate_to_turkish(text: str, backend: Optional[str] = None) -> Tuple[Optional[str], str]:
    if not text or not text.strip():
        return None, "unknown"
    text = text.strip()
    normalized = _normalize_backend(backend)
    iso_code, _, _ = detect_language_fasttext(text)
    detected = iso_code if iso_code in NLLB_LANG_MAP else "unknown"
    if normalized != TRANSLATION_BACKEND_MADLAD and iso_code == "tr":
        return None, "tr"
    source = iso_code if iso_code in NLLB_LANG_MAP else "en"
    result = translate(text, source, "tr", backend=normalized)
    if result and normalized == TRANSLATION_BACKEND_NLLB and source != "en" and not _has_token_overlap(text, result):
        result = translate(text, "en", "tr", backend=normalized)
        if result:
            return turkish_lower(result), "en"
        return None, detected
    return (turkish_lower(result), detected) if result else (None, detected)


def translate_to_english(text: str, backend: Optional[str] = None) -> Optional[str]:
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == "en":
        return text.lower()
    source = iso_code if iso_code in NLLB_LANG_MAP else "tr"
    return translate(text, source, "en", backend=backend)


def translate_to_kurdish(text: str, backend: Optional[str] = None) -> Optional[str]:
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == "ku":
        return turkish_lower(text)
    source = iso_code if iso_code in NLLB_LANG_MAP else "en"
    return translate(text, source, "ku", backend=backend)


def translate_to_farsi(text: str, backend: Optional[str] = None) -> Optional[str]:
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == "fa":
        return text.lower()
    source = iso_code if iso_code in NLLB_LANG_MAP else "en"
    return translate(text, source, "fa", backend=backend)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 70)
    print("Translation Module Test (FastText + backend-aware translation)")
    print("=" * 70)

    print("\nInitializing live translation backend...")
    if not initialize():
        print("Failed to load model")
        raise SystemExit(1)

    detection_tests = [
        ("APPLE", "en"),
        ("Ä°STANBUL", "tr"),
        ("SÃªv", "ku"),
        ("Ø³ÛŒØ¨", "fa"),
        ("ØªÙØ§Ø­", "ar"),
        ("Ã„pfel", "de"),
        ("Ð¯Ð±Ð»Ð¾ÐºÐ¾", "ru"),
        ("è‹¹æžœ", "zh"),
    ]

    print("\nLanguage Detection (FastText):")
    print("-" * 50)
    for text, expected in detection_tests:
        iso, nllb, conf = detect_language_fasttext(text)
        status = "OK" if iso == expected else "FAIL"
        print(f"  {status} '{text}' -> {iso} (expected: {expected}, conf={conf:.3f})")

    translation_tests = ["APPLE", "STAR", "GOLDEN", "LION", "RED", "WATER"]
    print("\nFull Turkish translations:")
    print("-" * 50)
    for text in translation_tests:
        result = get_translations(text)
        print(f"  {text:<15} -> {result.get('tr')}")

    print("\nDone!")
