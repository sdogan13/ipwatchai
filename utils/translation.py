"""
Translation Layer for IP Watch AI
==================================
Supports: English, Turkish, Kurdish (Kurmanji), Farsi, German, French, Arabic, and 190+ more.

Uses Meta's NLLB-200-distilled-600M (runs on GPU or CPU, offline after first download).
"""

import os
import logging
from typing import Optional, Dict, Tuple, List
from functools import lru_cache

import torch

logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

# Try config, fallback to defaults
try:
    from config.settings import settings as _app_settings
    MODEL_NAME = _app_settings.ai.translation_model
except Exception:
    MODEL_NAME = "facebook/nllb-200-distilled-600M"

MEMORY_CACHE_SIZE = 100_000

# NLLB-200 language code mapping (ISO 639 -> NLLB flores-200 codes)
NLLB_LANG_MAP = {
    # Primary languages for Turkish market
    "tr": "tur_Latn",
    "en": "eng_Latn",
    "ku": "kmr_Latn",   # Kurmanji (Northern Kurdish, Latin script)
    "fa": "fas_Arab",   # Farsi / Persian
    "ar": "ara_Arab",
    # European languages
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
    # Asian languages
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    # Others common in trademark filings
    "he": "heb_Hebr",
    "hi": "hin_Deva",
    "ur": "urd_Arab",
}

# Human-readable names (kept for backwards compat & display)
LANG_NAMES = {
    'tr': 'Turkish', 'en': 'English', 'ku': 'Kurdish', 'fa': 'Persian',
    'ar': 'Arabic', 'de': 'German', 'fr': 'French', 'it': 'Italian',
    'es': 'Spanish', 'nl': 'Dutch', 'ru': 'Russian', 'zh': 'Chinese',
    'ja': 'Japanese', 'ko': 'Korean',
}

LANG_DISPLAY_NAMES = {
    'tr': 'Türkçe', 'en': 'İngilizce', 'ku': 'Kürtçe', 'fa': 'Farsça',
    'ar': 'Arapça', 'de': 'Almanca', 'fr': 'Fransızca', 'ru': 'Rusça',
    'zh': 'Çince',
}

# Target languages for trademark translation (only Turkish needed for risk scoring)
TARGET_LANGUAGES = ['tr']

# ============================================
# FASTTEXT LANGUAGE IDENTIFICATION (for NLLB)
# ============================================
# Facebook's FastText LangID model — designed to pair with NLLB.
# Returns NLLB-compatible language codes (eng_Latn, tur_Latn, etc.)
# Trained model (~1.2MB), NOT hardcoded character sets.
# Accurate even for 1-2 word queries. Runs on CPU in microseconds.

_FASTTEXT_UNAVAILABLE = object()  # sentinel: tried to load but failed
_fasttext_model = None

def _load_fasttext_langid():
    """Lazy-load the FastText language identification model.

    Uses a sentinel to distinguish 'never tried' (None) from 'tried and failed'
    (_FASTTEXT_UNAVAILABLE), so we only log the warning once.
    """
    global _fasttext_model
    if _fasttext_model is _FASTTEXT_UNAVAILABLE:
        return None
    if _fasttext_model is not None:
        return _fasttext_model
    try:
        import fasttext
        from huggingface_hub import hf_hub_download
        import warnings
        warnings.filterwarnings('ignore', category=UserWarning, module='fasttext')
        model_path = hf_hub_download(
            repo_id="facebook/fasttext-language-identification",
            filename="model.bin"
        )
        _fasttext_model = fasttext.load_model(model_path)
        logger.info("FastText LangID model loaded")
        return _fasttext_model
    except Exception as e:
        logger.warning(f"FastText LangID not available: {e}")
        _fasttext_model = _FASTTEXT_UNAVAILABLE
        return None


# Reverse map: NLLB code → ISO 639-1 code (for NLLB_LANG_MAP lookup)
_NLLB_TO_ISO = {v: k for k, v in NLLB_LANG_MAP.items()}


def detect_language_fasttext(text: str) -> Tuple[str, str, float]:
    """Detect language using FastText LangID model only (no hardcoded rules).

    1. FastText LangID model (217 languages, trained by Facebook)
    2. Low-confidence fallback → English (safe default for NLLB translation)

    Returns:
        Tuple of (iso_code, nllb_code, confidence).
        iso_code: ISO 639-1 code (e.g., 'en', 'tr', 'fr')
        nllb_code: NLLB flores code (e.g., 'eng_Latn', 'tur_Latn')
        confidence: Detection confidence 0-1
    """
    clean = text.replace('\n', ' ').strip() if text else ''
    if not clean:
        return 'en', 'eng_Latn', 0.0

    # Lowercase for FastText: trademark names are often ALL-CAPS ("APPLE",
    # "ŞEKER") which confuses FastText (trained on natural-case text).
    # e.g., "APPLE" → Korean(1.0) but "apple" → English(0.95).
    # Use turkish_lower() to handle İ→i, I→ı correctly.
    clean = turkish_lower(clean)

    # FastText model detection
    model = _load_fasttext_langid()
    if model is None:
        # Model not available → fall back to English
        return 'en', 'eng_Latn', 0.0

    labels, scores = model.predict(clean)
    nllb_code = labels[0].replace('__label__', '')
    confidence = float(scores[0])
    iso_code = _NLLB_TO_ISO.get(nllb_code, 'en')

    # Low confidence → fall back to English as default.
    # Short trademark names (1-3 words) can confuse FastText —
    # e.g., "cicek masali" → Polish(0.95), "samsung" → German(0.27).
    # English is a safe fallback: batch_translate_to_turkish() always
    # translates from English anyway, so the detected_lang is mainly
    # informational. NLLB echo detection returns None for names that
    # aren't actually English (Turkish, brand names, etc.)
    # English detections are trusted at any confidence since English is
    # our default and the translation pipeline handles it correctly.
    if iso_code == 'en':
        return iso_code, nllb_code, confidence
    if confidence < 0.5:
        return 'en', 'eng_Latn', confidence

    return iso_code, nllb_code, confidence


# ============================================
# TURKISH-AWARE TEXT HELPERS
# ============================================

# turkish_lower imported from canonical source
from utils.idf_scoring import turkish_lower


# ============================================
# MODEL MANAGEMENT (lazy-loaded NLLB-200)
# ============================================

_model = None
_tokenizer = None
_device = None
_initialized = False


def initialize(device: str = None) -> bool:
    """Initialize NLLB-200 translation model (lazy load)."""
    global _model, _tokenizer, _device, _initialized

    if _initialized:
        return True

    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        if device and device != "auto":
            _device = device
        else:
            _device = 'cuda' if torch.cuda.is_available() else 'cpu'

        logger.info(f"Loading NLLB-200 on {_device.upper()}...")

        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        _model = AutoModelForSeq2SeqLM.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.float16 if _device == 'cuda' else torch.float32,
        )
        _model.to(_device).eval()
        _initialized = True

        logger.info("NLLB-200 ready")
        return True

    except ImportError:
        logger.error("Install: pip install transformers sentencepiece")
        return False
    except Exception as e:
        logger.error(f"NLLB-200 init failed: {e}")
        return False


def is_ready() -> bool:
    """Check if translation model is loaded."""
    return _initialized and _model is not None


def unload():
    """Unload model to free GPU memory."""
    global _model, _tokenizer, _initialized

    if _model is not None:
        del _model
        _model = None
    if _tokenizer is not None:
        del _tokenizer
        _tokenizer = None

    _initialized = False

    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    logger.info("NLLB-200 unloaded")


# ============================================
# CORE TRANSLATION
# ============================================

@lru_cache(maxsize=MEMORY_CACHE_SIZE)
def translate(text: str, source: str, target: str) -> Optional[str]:
    """
    Translate text from source to target language using NLLB-200.

    Args:
        text: Text to translate
        source: Source language code (e.g. 'en', 'tr', 'ar')
        target: Target language code (e.g. 'tr', 'en')

    Returns:
        Translated text or None on failure
    """
    if not text or not text.strip():
        return None

    if source == target:
        return None

    # Map to NLLB language codes
    src_nllb = NLLB_LANG_MAP.get(source)
    tgt_nllb = NLLB_LANG_MAP.get(target)
    if not src_nllb or not tgt_nllb:
        logger.debug(f"Unsupported language pair: {source} -> {target}")
        return None

    if not is_ready():
        if not initialize():
            return None

    text = text.strip()

    # Lowercase Latin-script input so NLLB doesn't treat ALL-CAPS as proper nouns.
    # NLLB-200 often echoes uppercase words back unchanged (e.g. "APPLE" → "APPLE")
    # but translates lowercase correctly ("apple" → "elma").
    model_input = text.lower() if text.isascii() else text

    try:
        # Set source language on tokenizer
        _tokenizer.src_lang = src_nllb

        inputs = _tokenizer(model_input, return_tensors="pt", padding=True, truncation=True, max_length=128)
        inputs = {k: v.to(_device) for k, v in inputs.items()}

        # Get target language token id for forced_bos
        tgt_token_id = _tokenizer.convert_tokens_to_ids(tgt_nllb)

        # Limit output length based on input to reduce hallucinated repetitions.
        # Single words don't need 64 output tokens; 8 is more than enough.
        input_word_count = len(model_input.split())
        max_tokens = min(8 + input_word_count * 4, 64)

        with torch.no_grad():
            outputs = _model.generate(
                **inputs,
                forced_bos_token_id=tgt_token_id,
                max_new_tokens=max_tokens,
                num_beams=2,
            )

        translation = _tokenizer.decode(outputs[0], skip_special_tokens=True).strip()
        del inputs, outputs

        # Post-process NLLB-200-distilled quirks:
        # 1. Duplicated words: "apple" → "Elma elma" → "Elma"
        # 2. Input echo: "moon" → "ay moon" → "ay"
        if translation:
            words = translation.split()
            # Dedup: all tokens are the same word (case-insensitive, Turkish-aware)
            if len(words) > 1 and len(set(turkish_lower(w) for w in words)) == 1:
                translation = words[0]
            # Echo removal: for single-word input, strip the source word from output
            elif len(words) > 1 and len(model_input.split()) == 1:
                filtered = [w for w in words if turkish_lower(w) != turkish_lower(model_input)]
                if filtered:
                    translation = ' '.join(filtered)

        # Sanity: don't return if result is same as input (no translation happened)
        if translation and turkish_lower(translation) != turkish_lower(model_input):
            return translation

        return None

    except Exception as e:
        logger.debug(f"Translation failed '{text[:30]}': {e}")
        return None


def batch_translate(texts: List[str], source: str, target: str, batch_size: int = 256) -> List[Optional[str]]:
    """
    Translate a list of texts in batches using NLLB-200.
    Much faster than calling translate() per-text because GPU processes
    multiple sequences in one forward pass.

    Args:
        texts: List of texts to translate
        source: Source language code
        target: Target language code
        batch_size: Number of texts per GPU batch (default 64)

    Returns:
        List of translations (same length as texts, None for failures)
    """
    if not texts:
        return []

    src_nllb = NLLB_LANG_MAP.get(source)
    tgt_nllb = NLLB_LANG_MAP.get(target)
    if not src_nllb or not tgt_nllb:
        return [None] * len(texts)

    if not is_ready():
        if not initialize():
            return [None] * len(texts)

    tgt_token_id = _tokenizer.convert_tokens_to_ids(tgt_nllb)
    results = [None] * len(texts)

    for batch_start in range(0, len(texts), batch_size):
        batch_texts = texts[batch_start:batch_start + batch_size]
        batch_indices = []
        batch_inputs = []

        for i, text in enumerate(batch_texts):
            idx = batch_start + i
            if not text or not text.strip() or source == target:
                continue
            model_input = text.strip().lower() if text.strip().isascii() else text.strip()
            batch_indices.append(idx)
            batch_inputs.append(model_input)

        if not batch_inputs:
            continue

        try:
            _tokenizer.src_lang = src_nllb
            encoded = _tokenizer(
                batch_inputs, return_tensors="pt",
                padding=True, truncation=True, max_length=128
            )
            encoded = {k: v.to(_device) for k, v in encoded.items()}

            with torch.no_grad():
                outputs = _model.generate(
                    **encoded,
                    forced_bos_token_id=tgt_token_id,
                    max_new_tokens=32,
                    num_beams=2,
                )

            decoded = _tokenizer.batch_decode(outputs, skip_special_tokens=True)
            del encoded, outputs

            for j, (idx, model_input) in enumerate(zip(batch_indices, batch_inputs)):
                translation = decoded[j].strip() if j < len(decoded) else None
                if translation:
                    words = translation.split()
                    if len(words) > 1 and len(set(turkish_lower(w) for w in words)) == 1:
                        translation = words[0]
                    elif len(words) > 1 and len(model_input.split()) == 1:
                        filtered = [w for w in words if turkish_lower(w) != turkish_lower(model_input)]
                        if filtered:
                            translation = ' '.join(filtered)
                    if turkish_lower(translation) != turkish_lower(model_input):
                        results[idx] = translation

        except Exception as e:
            logger.warning(f"Batch translation failed (batch {batch_start}): {e}")

    return results


def batch_translate_to_turkish(texts: List[str]) -> List[Tuple[str, str]]:
    """
    Batch-translate a list of texts to Turkish.
    Returns list of (name_tr, detected_lang) tuples.

    Groups texts by detected language, batch-translates each group,
    then reassembles in original order.
    """
    if not texts:
        return []

    results = [("", "unknown")] * len(texts)

    # Phase 1: Detect languages using FastText (for detected_lang column)
    detected_langs = []
    all_indices = []      # indices of non-empty texts
    all_texts = []        # corresponding texts
    for i, text in enumerate(texts):
        if not text or not text.strip():
            detected_langs.append('unknown')
            continue
        iso_code, nllb_code, confidence = detect_language_fasttext(text)
        lang = iso_code if iso_code in NLLB_LANG_MAP else 'en'
        detected_langs.append(lang)
        results[i] = (turkish_lower(text), lang)  # default: lowercased original
        all_indices.append(i)
        all_texts.append(text)

    if not all_texts:
        return results

    # Phase 2: Always translate ALL names from English → Turkish.
    # NLLB handles mixed-language input well: it preserves Turkish proper
    # nouns and only translates the English parts. This ensures mixed names
    # like "DOĞAN electronics" → "DOĞAN elektronik" get their English
    # parts translated. For pure Turkish names, NLLB returns None (echo
    # detection) and we keep the original.
    en_translations = batch_translate(all_texts, 'en', 'tr')

    for idx, orig_text, trans in zip(all_indices, all_texts, en_translations):
        lang = detected_langs[idx]
        if trans:
            name_tr = turkish_lower(trans)
            # If en→tr produced something different from original, use it
            if name_tr != turkish_lower(orig_text):
                # Guard: for Turkish-detected names, NLLB can hallucinate when
                # forced through en→tr (e.g., "çikola çikola" → "okul").
                # Only accept if translation preserves at least one original token.
                # Mixed names like "DOĞAN electronics" → "doğan elektronik" pass
                # because "doğan" is preserved.
                if lang == 'tr':
                    orig_tokens = set(turkish_lower(orig_text).split())
                    trans_tokens = set(name_tr.split())
                    if not orig_tokens & trans_tokens:
                        # No overlap → NLLB hallucinated, keep original
                        results[idx] = (turkish_lower(orig_text), lang)
                        continue
                results[idx] = (name_tr, lang)
                continue
        # en→tr returned None or same text — keep original
        results[idx] = (turkish_lower(orig_text), lang)

    # Phase 3: For non-English/non-Turkish detected names, also try
    # translating from the detected language — might produce better results
    # than the English path (e.g., French "La belle vie" → "güzel hayat").
    other_lang_items: Dict[str, List[Tuple[int, str]]] = {}
    for idx, orig_text in zip(all_indices, all_texts):
        lang = detected_langs[idx]
        if lang not in ('en', 'tr', 'unknown'):
            other_lang_items.setdefault(lang, []).append((idx, orig_text))

    for source_lang, items in other_lang_items.items():
        indices = [i for i, _ in items]
        source_texts = [t for _, t in items]
        translations = batch_translate(source_texts, source_lang, 'tr')

        for idx, orig_text, trans in zip(indices, source_texts, translations):
            if not trans:
                continue
            name_tr = turkish_lower(trans)
            # Validate: translation must share at least one token with original
            orig_tokens = set(turkish_lower(orig_text).split())
            trans_tokens = set(name_tr.split())
            if not orig_tokens & trans_tokens:
                continue  # Bad translation from wrong source, keep en→tr result
            # Only replace if this translation is actually different and useful
            current_tr = results[idx][0]
            if name_tr != current_tr and name_tr != turkish_lower(orig_text):
                results[idx] = (name_tr, detected_langs[idx])

    return results


# ============================================
# HIGH-LEVEL API
# ============================================

def get_translations(text: str) -> Dict[str, Optional[str]]:
    """
    Get Turkish translation for a trademark name.

    Returns:
        {
            'original': 'APPLE',
            'detected_lang': 'en',
            'tr': 'elma',
        }
    """
    if not text:
        return {
            'original': text,
            'detected_lang': 'unknown',
            'tr': None,
        }

    iso_code, nllb_code, confidence = detect_language_fasttext(text)
    detected = iso_code

    result = {
        'original': text,
        'detected_lang': detected,
        'tr': None,
    }

    if detected == 'tr':
        result['tr'] = turkish_lower(text)
    else:
        source = detected if detected in NLLB_LANG_MAP else 'en'
        translation = translate(text, source, 'tr')
        # Validate: if non-English source produced translation with no
        # token overlap, retry from English (same logic as batch path)
        if translation and source != 'en':
            orig_tokens = set(turkish_lower(text).split())
            trans_tokens = set(turkish_lower(translation).split())
            if not orig_tokens & trans_tokens:
                translation = translate(text, 'en', 'tr')
        if translation:
            result['tr'] = turkish_lower(translation)

    return result


def get_search_variants(query: str) -> List[str]:
    """
    Get all search variants for a query.

    Example:
        get_search_variants("APPLE")
        -> ['apple', 'elma', 'sêv', 'سیب']
    """
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
    """
    Calculate similarity considering translations.
    Legacy wrapper.

    Returns:
        (score, match_type)
    """
    score = calculate_translation_similarity(text1, text2, candidate_name_tr=candidate_name_tr)
    if score >= 1.0:
        return score, 'translation_exact'
    elif score >= 0.85:
        return score, 'translation_high'
    elif score >= 0.6:
        return score, 'translation_partial'
    return score, 'none'


def score_translated_pair(translated_query: str, translated_candidate_tr: str) -> dict:
    """
    Score two Turkish strings through the IDF waterfall (Cases A-F).

    The query is translated via NLLB (cached), the candidate uses pre-stored
    name_tr from DB.  We reuse compute_idf_weighted_score() so containment,
    distinctive-word classification, and floor/ceiling logic all apply.

    semantic_sim and visual_sim are 0.0 because we have no embeddings for
    translated text — the waterfall handles this gracefully.
    """
    if not translated_query or not translated_candidate_tr:
        return {"translation_similarity": 0.0}

    q = turkish_lower(translated_query.strip())
    c = turkish_lower(translated_candidate_tr.strip())

    if q == c:
        return {"translation_similarity": 1.0, "translation_scoring_path": "EXACT_MATCH"}

    # --- text_sim: Turkish-normalised SequenceMatcher + containment + token overlap ---
    from risk_engine import calculate_name_similarity
    turkish_sim = calculate_name_similarity(q, c)

    from difflib import SequenceMatcher as _SM
    seq_sim = _SM(None, q, c).ratio()
    text_sim = max(turkish_sim, seq_sim)

    # --- graduated phonetic similarity on translated strings ---
    from utils.phonetic import calculate_phonetic_similarity
    phon = calculate_phonetic_similarity(q, c)

    # --- IDF waterfall (the same Cases A-F used for normal text scoring) ---
    from idf_scoring import compute_idf_weighted_score
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
) -> float:
    """
    Cross-language translation similarity.

    - Query: translated to Turkish once via NLLB (cached by @lru_cache).
    - Candidate: uses pre-stored name_tr from DB.  If NULL → returns 0.0
      (dynamic weighting gives zero-weight to dead signals).
    - Scoring: IDF waterfall via score_translated_pair().

    This means 1 NLLB call per search, 0 calls per candidate.
    """
    if not query_name:
        return 0.0

    # Candidate must have a pre-stored Turkish translation
    if not candidate_name_tr:
        return 0.0

    # Translate only the query (cached)
    query_tr = translate_to_turkish(query_name)
    if not query_tr:
        return 0.0

    # Deduplication: if translated query is the same as the original query
    # (normalized), the translation didn't actually translate anything.
    # e.g., "NAIK" → "naik" — not a real translation, just lowercasing.
    # Returning 0.0 prevents the translation signal from redundantly
    # inflating scores that text_sim already covers.
    from utils.idf_scoring import normalize_turkish
    if normalize_turkish(query_tr) == normalize_turkish(query_name):
        return 0.0

    result = score_translated_pair(query_tr, candidate_name_tr)
    return result["translation_similarity"]


# ============================================
# LANGUAGE-SPECIFIC HELPERS
# ============================================

@lru_cache(maxsize=MEMORY_CACHE_SIZE)
def translate_to_turkish(text: str) -> str:
    """Translate any text to Turkish. Returns Turkish-aware lowercase.

    If text is already Turkish, returns as-is (no model call).
    """
    if not text or not text.strip():
        return ""
    text = text.strip()
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == 'tr':
        return turkish_lower(text)
    source = iso_code if iso_code in NLLB_LANG_MAP else 'en'
    result = translate(text, source, 'tr')
    # Validate non-English translations (same logic as batch path)
    if result and source != 'en':
        orig_tokens = set(turkish_lower(text).split())
        trans_tokens = set(turkish_lower(result).split())
        if not orig_tokens & trans_tokens:
            result = translate(text, 'en', 'tr')
    return turkish_lower(result) if result else turkish_lower(text)


def auto_translate_to_turkish(text: str) -> Tuple[Optional[str], str]:
    """Translate any text to Turkish using model-based language detection.

    Uses Facebook's FastText LangID (trained model, 217 languages) to detect
    the source language, then NLLB-200 to translate. No hardcoded character
    sets or word lists — both detection and translation are model-based.

    Returns:
        Tuple of (translated_text, detected_language_code).
        translated_text is None if the text is already Turkish or
        if translation produced the same result as input.
        detected_language_code is the ISO 639-1 code (e.g., 'en', 'tr').
    """
    if not text or not text.strip():
        return None, 'unknown'

    text = text.strip()

    # FastText LangID: model-based detection, accurate even for 1-2 words
    iso_code, nllb_code, confidence = detect_language_fasttext(text)

    # Turkish detected → no translation needed
    if iso_code == 'tr':
        return None, 'tr'

    # Map to NLLB source code for translation.
    # FastText returns NLLB-compatible codes directly.
    # If the detected language isn't in our NLLB_LANG_MAP, fall back to
    # English — NLLB still produces reasonable Turkish output.
    source = iso_code if iso_code in NLLB_LANG_MAP else 'en'

    # Translate to Turkish via NLLB
    result = translate(text, source, 'tr')

    if result:
        # Validate: if source was not English and translation shares no
        # tokens with original, it may be from a wrong source language.
        # Retry from English (NLLB echo detection safely handles this).
        if source != 'en':
            orig_tokens = set(turkish_lower(text).split())
            trans_tokens = set(turkish_lower(result).split())
            if not orig_tokens & trans_tokens:
                result = translate(text, 'en', 'tr')
                if result:
                    return turkish_lower(result), 'en'
                return None, iso_code
        return turkish_lower(result), iso_code
    return None, iso_code


def translate_to_english(text: str) -> Optional[str]:
    """Translate any text to English."""
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == 'en':
        return text.lower()
    source = iso_code if iso_code in NLLB_LANG_MAP else 'tr'
    return translate(text, source, 'en')


def translate_to_kurdish(text: str) -> Optional[str]:
    """Translate any text to Kurdish (Kurmanji)."""
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == 'ku':
        return turkish_lower(text)
    source = iso_code if iso_code in NLLB_LANG_MAP else 'en'
    return translate(text, source, 'ku')


def translate_to_farsi(text: str) -> Optional[str]:
    """Translate any text to Farsi (Persian)."""
    iso_code, _, _ = detect_language_fasttext(text)
    if iso_code == 'fa':
        return text.lower()
    source = iso_code if iso_code in NLLB_LANG_MAP else 'en'
    return translate(text, source, 'fa')


# ============================================
# CLI TEST
# ============================================

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("=" * 70)
    print("Translation Module Test (NLLB-200: TR, EN, KU, FA)")
    print("=" * 70)

    print("\nInitializing NLLB-200...")
    if not initialize():
        print("Failed to load model")
        exit(1)

    # Test language detection
    detection_tests = [
        ("APPLE", "en"),
        ("İSTANBUL", "tr"),
        ("Sêv", "ku"),
        ("سیب", "fa"),
        ("تفاح", "ar"),
        ("Äpfel", "de"),
        ("Яблоко", "ru"),
        ("苹果", "zh"),
    ]

    print("\nLanguage Detection (FastText):")
    print("-" * 50)
    for text, expected in detection_tests:
        iso, nllb, conf = detect_language_fasttext(text)
        status = "OK" if iso == expected else "FAIL"
        print(f"  {status} '{text}' -> {iso} (expected: {expected}, conf={conf:.3f})")

    # Test translations
    translation_tests = ["APPLE", "STAR", "GOLDEN", "LION", "RED", "WATER"]

    print(f"\n\nFull Translations:")
    print("-" * 70)
    print(f"  {'Original':<15} {'TR':<15} {'EN':<15} {'KU':<15} {'FA':<15}")
    print("-" * 70)

    for text in translation_tests:
        result = get_translations(text)
        tr = result.get('tr', '-') or '-'
        en = result.get('en', '-') or '-'
        ku = result.get('ku', '-') or '-'
        fa = result.get('fa', '-') or '-'
        print(f"  {text:<15} {tr:<15} {en:<15} {ku:<15} {fa:<15}")

    # Test cross-language similarity
    print(f"\n\nCross-Language Similarity:")
    print("-" * 50)
    pairs = [
        ("APPLE", "ELMA"),
        ("STAR", "YILDIZ"),
        ("NIKE", "NIKE"),
        ("APPLE", "BANANA"),
    ]

    for t1, t2 in pairs:
        score, match_type = get_cross_language_similarity(t1, t2)
        print(f"  '{t1}' vs '{t2}': {score:.0%} ({match_type})")

    print("\nDone!")
