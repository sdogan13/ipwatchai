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

# Target languages for trademark translation
TARGET_LANGUAGES = ['tr', 'en', 'ku', 'fa']

# ============================================
# CHARACTER SETS FOR DETECTION
# ============================================

TURKISH_CHARS = set('çğıöşüÇĞİÖŞÜ')
KURDISH_LATIN_CHARS = set('êîûÊÎÛ')
KURDISH_EXTRA = set('ẍẌ')
# Fix: Added ی (U+06CC, Farsi Yeh) and ھ (U+06BE, Heh Doachashmee) to Farsi detection
FARSI_CHARS = set('پچژگکیھ')
ARABIC_CHARS = set('ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوي')
SORANI_CHARS = set('ڕڵۆێ')
GERMAN_CHARS = set('äöüßÄÖÜ')
CYRILLIC_RANGE = ('\u0400', '\u04FF')
CJK_RANGE = ('\u4e00', '\u9fff')

# Common Turkish words (ASCII-only) used as fallback detection
# when no special characters are present. Covers high-frequency
# trademark-relevant nouns, adjectives, verbs so that e.g. "ELMA"
# is correctly identified as Turkish instead of defaulting to English.
COMMON_TURKISH_WORDS = {
    # Fruits / food
    'elma', 'armut', 'portakal', 'kiraz', 'erik', 'kavun', 'karpuz',
    'limon', 'muz', 'nar', 'incir', 'kayisi', 'visne', 'zeytin',
    'bal', 'peynir', 'ekmek', 'su', 'sut', 'yumurta', 'tavuk',
    # Animals
    'aslan', 'kartal', 'kurt', 'ayı', 'at', 'kedi', 'kopek', 'kus',
    'balik', 'arı', 'kaplan', 'tilki', 'tavsan', 'kurt', 'yunus',
    # Nature
    'deniz', 'dag', 'nehir', 'bulut', 'ruzgar', 'toprak', 'ates',
    'gunes', 'ay', 'yildiz', 'orman', 'bahce', 'cicek', 'yaprak',
    # Colors
    'beyaz', 'siyah', 'kirmizi', 'mavi', 'yesil', 'sari', 'turuncu',
    'mor', 'pembe', 'gri', 'kahverengi', 'altin', 'gumus',
    # Common adjectives / nouns in trademarks
    'buyuk', 'kucuk', 'yeni', 'eski', 'guzel', 'iyi', 'temiz',
    'hizli', 'parlak', 'taze', 'dogal', 'saf', 'zengin',
    'ev', 'yol', 'kapi', 'pencere', 'masa', 'sandalye',
    'anahtar', 'kale', 'kule', 'kopru', 'liman', 'ada',
    'tas', 'demir', 'bakir', 'celik', 'mermer',
    'pinar', 'dere', 'vadi', 'tepe', 'ova',
    # Business / commerce
    'ticaret', 'pazar', 'magaza', 'fabrika', 'imalat',
    'kalite', 'marka', 'sanayi', 'ihracat', 'ithalat',
    # People / body
    'anne', 'baba', 'kardes', 'dost', 'usta', 'reis',
    'el', 'goz', 'kalp', 'akil', 'can',
    # Time
    'sabah', 'aksam', 'gece', 'bahar', 'yaz', 'sonbahar', 'kis',
    # Food / drink brands
    'cay', 'kahve', 'ayran', 'boza', 'simit', 'pide', 'kebap',
    'helva', 'lokum', 'baklava', 'lahmacun', 'dondurma',
}


# ============================================
# LANGUAGE DETECTION
# ============================================

def detect_language(text: str) -> str:
    """
    Detect language from text using character analysis + Turkish word lookup.

    Priority order for overlapping scripts:
    1. Turkish (distinctive chars: ç, ğ, ı, ö, ş, ü, İ)
    2. Kurdish Kurmanji (Latin with ê, î, û)
    3. Farsi (پ چ ژ گ ک ی)
    4. Sorani Kurdish (ڕ ڵ ۆ ێ)
    5. Arabic
    6. German (ä, ö, ü, ß)
    7. Russian (Cyrillic)
    8. Chinese (CJK)
    9. Turkish (common word lookup — catches ASCII Turkish like "ELMA")
    10. English (default for Latin)

    Note: ASCII-only Turkish words without special characters (e.g. "ELMA",
    "ASLAN") are detected via a common-word lookup. Words not in the set
    will still default to English, which is acceptable — translate_to_turkish()
    will fall back to text.lower() if NLLB returns no change.
    """
    if not text:
        return 'unknown'

    if any(c in text for c in TURKISH_CHARS):
        return 'tr'
    if any(c in text for c in KURDISH_LATIN_CHARS):
        return 'ku'
    if any(c in text for c in FARSI_CHARS):
        return 'fa'
    if any(c in text for c in SORANI_CHARS):
        return 'ku'
    if any(c in text for c in ARABIC_CHARS):
        return 'ar'
    if any(c in text for c in GERMAN_CHARS):
        return 'de'
    if any(CYRILLIC_RANGE[0] <= c <= CYRILLIC_RANGE[1] for c in text):
        return 'ru'
    if any(CJK_RANGE[0] <= c <= CJK_RANGE[1] for c in text):
        return 'zh'

    # Fallback: check if any token is a known Turkish word
    tokens = text.lower().split()
    if any(t in COMMON_TURKISH_WORDS for t in tokens):
        return 'tr'

    return 'en'


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
            dtype=torch.float16 if _device == 'cuda' else torch.float32,
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

        # Post-process NLLB-200-distilled quirks:
        # 1. Duplicated words: "apple" → "Elma elma" → "Elma"
        # 2. Input echo: "moon" → "ay moon" → "ay"
        if translation:
            words = translation.split()
            # Dedup: all tokens are the same word (case-insensitive)
            if len(words) > 1 and len(set(w.lower() for w in words)) == 1:
                translation = words[0]
            # Echo removal: for single-word input, strip the source word from output
            elif len(words) > 1 and len(model_input.split()) == 1:
                filtered = [w for w in words if w.lower() != model_input.lower()]
                if filtered:
                    translation = ' '.join(filtered)

        # Sanity: don't return if result is same as input (no translation happened)
        if translation and translation.lower() != model_input.lower():
            return translation

        return None

    except Exception as e:
        logger.debug(f"Translation failed '{text[:30]}': {e}")
        return None


# ============================================
# HIGH-LEVEL API
# ============================================

def get_translations(text: str) -> Dict[str, Optional[str]]:
    """
    Get all translations for a trademark name.

    Translates to: Turkish, English, Kurdish, Farsi

    Returns:
        {
            'original': 'APPLE',
            'detected_lang': 'en',
            'tr': 'elma',
            'en': 'apple',
            'ku': 'sêv',
            'fa': 'سیب'
        }
    """
    if not text:
        return {
            'original': text,
            'detected_lang': 'unknown',
            'tr': None,
            'en': None,
            'ku': None,
            'fa': None,
        }

    detected = detect_language(text)

    result = {
        'original': text,
        'detected_lang': detected,
        'tr': None,
        'en': None,
        'ku': None,
        'fa': None,
    }

    for target in TARGET_LANGUAGES:
        if detected == target:
            result[target] = text.lower()
        else:
            translation = translate(text, detected, target)
            if translation:
                result[target] = translation.lower()

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

    variants.add(query.lower().strip())

    translations = get_translations(query)
    for lang in TARGET_LANGUAGES:
        if translations.get(lang):
            variants.add(translations[lang].lower().strip())

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

    q = translated_query.strip().lower()
    c = translated_candidate_tr.strip().lower()

    if q == c:
        return {"translation_similarity": 1.0, "translation_scoring_path": "EXACT_MATCH"}

    # --- text_sim: Turkish-normalised SequenceMatcher + containment + token overlap ---
    from risk_engine import calculate_turkish_similarity
    turkish_sim = calculate_turkish_similarity(q, c)

    from difflib import SequenceMatcher as _SM
    seq_sim = _SM(None, q, c).ratio()
    text_sim = max(turkish_sim, seq_sim)

    # --- phonetic match on translated strings (double-metaphone) ---
    phon = 0.0
    try:
        import metaphone
        m1 = metaphone.doublemetaphone(q)
        m2 = metaphone.doublemetaphone(c)
        codes1 = {code for code in m1 if code}
        codes2 = {code for code in m2 if code}
        if codes1 & codes2:
            phon = 1.0
    except ImportError:
        pass

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

    result = score_translated_pair(query_tr, candidate_name_tr)
    return result["translation_similarity"]


# ============================================
# LANGUAGE-SPECIFIC HELPERS
# ============================================

@lru_cache(maxsize=MEMORY_CACHE_SIZE)
def translate_to_turkish(text: str) -> str:
    """Translate any text to Turkish. Returns lowercase.

    If text is already Turkish, returns as-is (no model call).
    """
    if not text or not text.strip():
        return ""
    text = text.strip()
    source = detect_language(text)
    if source == 'tr':
        return text.lower()
    result = translate(text, source, 'tr')
    return result.lower() if result else text.lower()


def translate_to_english(text: str) -> Optional[str]:
    """Translate any text to English."""
    source = detect_language(text)
    if source == 'en':
        return text.lower()
    return translate(text, source, 'en')


def translate_to_kurdish(text: str) -> Optional[str]:
    """Translate any text to Kurdish (Kurmanji)."""
    source = detect_language(text)
    if source == 'ku':
        return text.lower()
    return translate(text, source, 'ku')


def translate_to_farsi(text: str) -> Optional[str]:
    """Translate any text to Farsi (Persian)."""
    source = detect_language(text)
    if source == 'fa':
        return text.lower()
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

    print("\nLanguage Detection:")
    print("-" * 50)
    for text, expected in detection_tests:
        detected = detect_language(text)
        status = "OK" if detected == expected else "FAIL"
        print(f"  {status} '{text}' -> {detected} (expected: {expected})")

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
