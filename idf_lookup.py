# idf_lookup.py
"""
IDF Lookup Module - Fast word importance scoring.

This module loads pre-computed IDF scores from the database
and provides fast lookups for scoring trademark similarity.

Usage:
    from idf_lookup import IDFLookup

    # Get IDF score for a word (higher = more distinctive)
    score = IDFLookup.get_idf("dogan")  # Returns ~5.0 (distinctive)
    score = IDFLookup.get_idf("patent") # Returns ~1.6 (generic)

    # Check if word is generic
    IDFLookup.is_generic("patent")  # Returns True
    IDFLookup.is_generic("dogan")   # Returns False

    # Get weighted importance for a set of words
    weights = IDFLookup.get_word_weights({"dogan", "patent"})
    # Returns {"dogan": 0.76, "patent": 0.24}  (normalized to sum=1.0)
"""

import os
import sys
import re
import logging
from typing import Dict, Set, Tuple, Optional
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def normalize_turkish(text: str) -> str:
    """Normalize Turkish characters to ASCII equivalents."""
    if not text:
        return ""
    replacements = {
        'ğ': 'g', 'Ğ': 'g',
        'ı': 'i', 'İ': 'i', 'I': 'i',
        'ö': 'o', 'Ö': 'o',
        'ü': 'u', 'Ü': 'u',
        'ş': 's', 'Ş': 's',
        'ç': 'c', 'Ç': 'c',
    }
    for tr_char, en_char in replacements.items():
        text = text.replace(tr_char, en_char)
    return text.lower().strip()


class IDFLookup:
    """
    Singleton class for fast IDF lookups.

    Loads all word IDF scores into memory on first access.
    Subsequent lookups are O(1) dictionary access.
    """

    _instance = None
    _cache: Dict[str, dict] = {}
    _loaded: bool = False
    _total_docs: int = 0
    _default_idf: float = 5.0  # Default for unknown words (treat as distinctive)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def load(cls, force: bool = False) -> None:
        """
        Load IDF scores from database into memory.

        Args:
            force: If True, reload even if already loaded
        """
        if cls._loaded and not force:
            return

        try:
            import psycopg2
            import os

            # Direct connection for fast loading (avoids pool contention)
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "127.0.0.1"),
                port=int(os.getenv("DB_PORT", 5432)),
                database=os.getenv("DB_NAME", "trademark_db"),
                user=os.getenv("DB_USER", "turk_patent"),
                password=os.getenv("DB_PASSWORD")
            )

            try:
                cur = conn.cursor()

                # Get total document count for reference
                cur.execute("SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL")
                cls._total_docs = cur.fetchone()[0]

                # Load all IDF scores in one query
                cur.execute("""
                    SELECT word, idf_score, is_generic, document_frequency
                    FROM word_idf
                """)

                cls._cache = {}
                rows = cur.fetchall()
                for word, idf, is_generic, doc_freq in rows:
                    cls._cache[word] = {
                        'idf': idf,
                        'is_generic': is_generic,
                        'doc_freq': doc_freq
                    }

                cls._loaded = True
                logger.info(f"IDFLookup: Loaded {len(cls._cache):,} word scores into memory")

            finally:
                conn.close()

        except Exception as e:
            logger.warning(f"IDFLookup: Failed to load from database: {e}")
            logger.warning("IDFLookup: Will use default scores (all words treated as distinctive)")
            cls._loaded = True  # Mark as loaded to prevent repeated failures

    @classmethod
    def get_idf(cls, word: str) -> float:
        """
        Get IDF score for a word.

        Higher score = more distinctive/rare word.
        Lower score = more common/generic word.

        Args:
            word: Word to look up (will be normalized)

        Returns:
            IDF score (typically 1.0-8.0 range)
        """
        if not cls._loaded:
            cls.load()

        word_norm = normalize_turkish(word)
        entry = cls._cache.get(word_norm)

        if entry:
            return entry['idf']
        else:
            # Unknown word - treat as distinctive
            return cls._default_idf

    @classmethod
    def is_generic(cls, word: str) -> bool:
        """
        Check if a word is generic (common).

        Args:
            word: Word to check (will be normalized)

        Returns:
            True if word appears in >0.5% of trademarks
        """
        if not cls._loaded:
            cls.load()

        word_norm = normalize_turkish(word)
        entry = cls._cache.get(word_norm)

        if entry:
            return entry['is_generic']
        else:
            # Unknown word - treat as distinctive
            return False

    @classmethod
    def get_doc_frequency(cls, word: str) -> int:
        """
        Get document frequency for a word.

        Args:
            word: Word to look up (will be normalized)

        Returns:
            Number of trademarks containing this word
        """
        if not cls._loaded:
            cls.load()

        word_norm = normalize_turkish(word)
        entry = cls._cache.get(word_norm)

        return entry['doc_freq'] if entry else 0

    @classmethod
    def get_word_class(cls, word: str) -> str:
        """
        Get the 3-tier classification for a word.

        Returns one of: 'generic', 'semi_generic', 'distinctive'

        Classification based on IDF thresholds:
        - GENERIC: IDF < 5.3 (>0.5% of docs)
        - SEMI_GENERIC: 5.3 <= IDF < 6.9 (0.1%-0.5%)
        - DISTINCTIVE: IDF >= 6.9 (<0.1%)
        """
        idf = cls.get_idf(word)

        if idf < 5.3:
            return 'generic'
        elif idf < 6.9:
            return 'semi_generic'
        else:
            return 'distinctive'

    @classmethod
    def get_weight_multiplier(cls, word: str) -> float:
        """
        Get the weight multiplier for a word based on its class.

        Returns:
            1.0 for distinctive, 0.5 for semi_generic, 0.1 for generic
        """
        word_class = cls.get_word_class(word)

        if word_class == 'distinctive':
            return 1.0
        elif word_class == 'semi_generic':
            return 0.5
        else:
            return 0.1

    @classmethod
    def get_word_weights(cls, words: Set[str]) -> Dict[str, float]:
        """
        Get normalized importance weights for a set of words.

        Weights are normalized so they sum to 1.0.
        Distinctive words get higher weights.

        Args:
            words: Set of words to weight

        Returns:
            Dict mapping word -> weight (0.0 to 1.0, sums to 1.0)

        Example:
            get_word_weights({"dogan", "patent"})
            # Returns {"dogan": 0.76, "patent": 0.24}
        """
        if not cls._loaded:
            cls.load()

        if not words:
            return {}

        # Get IDF scores
        idf_scores = {}
        for word in words:
            word_norm = normalize_turkish(word)
            if len(word_norm) <= 1:  # Skip single characters
                continue
            idf_scores[word] = cls.get_idf(word_norm)

        if not idf_scores:
            return {}

        # Normalize to sum to 1.0
        total_idf = sum(idf_scores.values())

        if total_idf > 0:
            return {word: idf / total_idf for word, idf in idf_scores.items()}
        else:
            # Equal weights if all zeros
            equal_weight = 1.0 / len(idf_scores)
            return {word: equal_weight for word in idf_scores}

    @classmethod
    def analyze_query(cls, query: str) -> Dict:
        """
        Analyze a search query and return word importance breakdown.

        Args:
            query: Search query (e.g., "dogan patent")

        Returns:
            Analysis dict with word weights, 3-tier classification
        """
        if not cls._loaded:
            cls.load()

        # Tokenize
        normalized = normalize_turkish(query)
        words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
        words = {w for w in words if len(w) > 1}

        if not words:
            return {"query": query, "words": [], "total_weight": 0}

        # Analyze each word with 3-tier classification
        word_analysis = []
        total_weighted = 0.0

        for word in sorted(words):
            idf = cls.get_idf(word)
            word_class = cls.get_word_class(word)
            weight_mult = cls.get_weight_multiplier(word)
            doc_freq = cls.get_doc_frequency(word)

            word_analysis.append({
                "word": word,
                "idf": round(idf, 2),
                "word_class": word_class,
                "weight_multiplier": weight_mult,
                "doc_frequency": doc_freq
            })
            total_weighted += weight_mult

        # Calculate final weights (normalized to sum to 1.0)
        for wa in word_analysis:
            if total_weighted > 0:
                wa["final_weight"] = wa["weight_multiplier"] / total_weighted
            else:
                wa["final_weight"] = 0.0

        # Sort by final_weight descending
        word_analysis.sort(key=lambda x: x["final_weight"], reverse=True)

        # Summary stats by class
        distinctive_weight = sum(w["final_weight"] for w in word_analysis if w["word_class"] == "distinctive")
        semi_generic_weight = sum(w["final_weight"] for w in word_analysis if w["word_class"] == "semi_generic")
        generic_weight = sum(w["final_weight"] for w in word_analysis if w["word_class"] == "generic")

        return {
            "query": query,
            "normalized": normalized,
            "words": word_analysis,
            "distinctive_weight": round(distinctive_weight, 3),
            "semi_generic_weight": round(semi_generic_weight, 3),
            "generic_weight": round(generic_weight, 3),
            "distinctive_count": sum(1 for w in word_analysis if w["word_class"] == "distinctive"),
            "semi_generic_count": sum(1 for w in word_analysis if w["word_class"] == "semi_generic"),
            "generic_count": sum(1 for w in word_analysis if w["word_class"] == "generic")
        }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cache and force reload on next access."""
        cls._cache = {}
        cls._loaded = False
        logger.info("IDFLookup: Cache cleared")


# ============================================
# CLI for testing
# ============================================
def main():
    """Test the IDF lookup."""
    import json

    # Test queries
    test_queries = [
        "dogan patent",
        "nike",
        "coca cola",
        "kent patent",
        "apple technology",
        "istanbul ticaret"
    ]

    print("="*60)
    print("IDF LOOKUP TEST")
    print("="*60)

    for query in test_queries:
        print(f"\n[QUERY] '{query}'")
        analysis = IDFLookup.analyze_query(query)

        print(f"   Distinctive weight: {analysis['distinctive_weight']:.1%}")
        print(f"   Generic weight:     {analysis['generic_weight']:.1%}")
        print(f"\n   Word breakdown:")

        for w in analysis['words']:
            type_str = "GENERIC" if w['is_generic'] else "DISTINCTIVE"
            print(f"      {w['word']:<15} weight={w['weight']:.1%}  IDF={w['idf']:.2f}  freq={w['doc_frequency']:,}  [{type_str}]")

    print("\n" + "="*60)


if __name__ == "__main__":
    main()
