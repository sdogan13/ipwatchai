"""
Graduated Phonetic Scoring for Turkish Trademark Risk Assessment
================================================================

Replaces binary (0/1) dMetaphone matching with a 4-signal weighted score
that produces intermediate similarity values.

Signals:
    1. dMetaphone overlap     (0.25) — Jaccard of non-empty code pairs
    2. Phonetic code distance (0.35) — Normalized Levenshtein on primary codes
    3. Turkish voicing equiv  (0.25) — SequenceMatcher after d→t, b→p, g→k, v→f, z→s
    4. First syllable emphasis(0.15) — Separate comparison of initial syllable

Usage:
    from utils.phonetic import calculate_phonetic_similarity
    score = calculate_phonetic_similarity("NIKE", "NAIK")  # ~0.75
"""

from difflib import SequenceMatcher

# Turkish consonant voicing pairs — these are perceptually similar in Turkish
# d↔t, b↔p, g↔k (hard g), v↔f, z↔s
TURKISH_PHONETIC_MAP = {
    'd': 't', 'b': 'p', 'g': 'k', 'v': 'f', 'z': 's',
    # Also map the reverse direction to the same canonical form
    't': 't', 'p': 'p', 'k': 'k', 'f': 'f', 's': 's',
}

# Turkish-specific character folding (subset needed for phonetic comparison)
_TR_FOLD = {
    'ğ': 'g', 'ı': 'i', 'ö': 'o', 'ü': 'u', 'ş': 's', 'ç': 'c',
    'İ': 'i', 'I': 'i',
}

# Vowels for syllable extraction
_VOWELS = set('aeıioöuüAEIİOÖUÜ')


def _normalize_for_phonetic(text: str) -> str:
    """Lowercase + fold Turkish chars to ASCII for phonetic comparison."""
    if not text:
        return ""
    # Turkish-aware lowercasing
    text = text.replace('İ', 'i').replace('I', 'ı')
    text = text.lower()
    for tr_char, en_char in _TR_FOLD.items():
        text = text.replace(tr_char, en_char)
    # Keep only alphanumeric
    return ''.join(c for c in text if c.isalnum())


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,       # insert
                prev_row[j + 1] + 1,   # delete
                prev_row[j] + cost,    # substitute
            ))
        prev_row = curr_row

    return prev_row[-1]


def _get_metaphone_codes(text: str):
    """Return double-metaphone codes as a tuple (primary, secondary).
    Returns ('', '') if metaphone is unavailable."""
    try:
        import metaphone
        return metaphone.doublemetaphone(text)
    except ImportError:
        return ('', '')


def _metaphone_overlap(name_a: str, name_b: str) -> float:
    """Signal 1: Jaccard overlap of non-empty dMetaphone codes.

    Returns 0.0, 0.33, 0.5, or 1.0 depending on overlap.
    """
    codes_a = _get_metaphone_codes(name_a)
    codes_b = _get_metaphone_codes(name_b)

    set_a = {c for c in codes_a if c}
    set_b = {c for c in codes_b if c}

    if not set_a or not set_b:
        return 0.0

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)

    if union == 0:
        return 0.0

    return intersection / union


def _metaphone_code_distance(name_a: str, name_b: str) -> float:
    """Signal 2: Normalized Levenshtein similarity on primary dMetaphone codes.

    Returns 1.0 for identical codes, 0.0 for completely different.
    """
    codes_a = _get_metaphone_codes(name_a)
    codes_b = _get_metaphone_codes(name_b)

    primary_a = codes_a[0] if codes_a[0] else ''
    primary_b = codes_b[0] if codes_b[0] else ''

    if not primary_a and not primary_b:
        return 0.0
    if not primary_a or not primary_b:
        return 0.0

    max_len = max(len(primary_a), len(primary_b))
    if max_len == 0:
        return 0.0

    dist = _levenshtein(primary_a, primary_b)
    return 1.0 - (dist / max_len)


def _apply_voicing_map(text: str) -> str:
    """Collapse Turkish voicing pairs: d→t, b→p, g→k, v→f, z→s."""
    return ''.join(TURKISH_PHONETIC_MAP.get(c, c) for c in text)


def _turkish_voicing_similarity(name_a: str, name_b: str) -> float:
    """Signal 3: SequenceMatcher after collapsing Turkish consonant voicing pairs.

    This captures pairs like DOGAN/TOGAN (d↔t) and GUNES/KUNES (g↔k)
    that are perceptually similar in Turkish.
    """
    if not name_a or not name_b:
        return 0.0

    mapped_a = _apply_voicing_map(name_a)
    mapped_b = _apply_voicing_map(name_b)

    return SequenceMatcher(None, mapped_a, mapped_b).ratio()


def _extract_first_syllable(text: str) -> str:
    """Extract the onset + first vowel cluster of a word.

    For trademark law, the initial impression (first syllable) carries
    disproportionate weight in confusion analysis.

    Examples:
        "samsung" -> "sam"
        "nike" -> "ni"
        "apple" -> "ap"
        "google" -> "goo"
    """
    if not text:
        return ""

    # For multi-word, use first word only
    word = text.split()[0] if ' ' in text else text

    result = []
    found_vowel = False

    for c in word:
        is_vowel = c in 'aeiouy'  # ASCII-folded vowels
        if found_vowel and not is_vowel:
            # Hit consonant after vowel — include it and stop
            result.append(c)
            break
        result.append(c)
        if is_vowel:
            found_vowel = True

    return ''.join(result)


def _first_syllable_similarity(name_a: str, name_b: str) -> float:
    """Signal 4: Comparison of first syllable (initial impression).

    Trademark law gives extra weight to the beginning of a mark.
    """
    if not name_a or not name_b:
        return 0.0

    syl_a = _extract_first_syllable(name_a)
    syl_b = _extract_first_syllable(name_b)

    if not syl_a or not syl_b:
        return 0.0

    if syl_a == syl_b:
        return 1.0

    # Use SequenceMatcher for partial credit
    return SequenceMatcher(None, syl_a, syl_b).ratio()


def calculate_phonetic_similarity(name_a: str, name_b: str) -> float:
    """Calculate graduated phonetic similarity between two trademark names.

    Combines 4 signals with weights:
        - dMetaphone overlap:       0.25
        - Phonetic code distance:   0.35
        - Turkish voicing equiv:    0.25
        - First syllable emphasis:  0.15

    Args:
        name_a: First trademark name (raw, any case)
        name_b: Second trademark name (raw, any case)

    Returns:
        float between 0.0 and 1.0
    """
    if not name_a or not name_b:
        return 0.0

    # Normalize both names for consistent comparison
    norm_a = _normalize_for_phonetic(name_a)
    norm_b = _normalize_for_phonetic(name_b)

    if not norm_a or not norm_b:
        return 0.0

    # Exact match shortcut
    if norm_a == norm_b:
        return 1.0

    # Compute 4 signals
    sig_overlap = _metaphone_overlap(norm_a, norm_b)
    sig_distance = _metaphone_code_distance(norm_a, norm_b)
    sig_voicing = _turkish_voicing_similarity(norm_a, norm_b)
    sig_syllable = _first_syllable_similarity(norm_a, norm_b)

    # Weighted combination
    score = (
        0.25 * sig_overlap
        + 0.35 * sig_distance
        + 0.25 * sig_voicing
        + 0.15 * sig_syllable
    )

    return round(min(1.0, max(0.0, score)), 4)
