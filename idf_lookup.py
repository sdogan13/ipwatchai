# idf_lookup.py
"""
Fast IDF and descriptor-evidence lookup for trademark scoring.

The lookup keeps backward compatibility with older IDF tables while exposing
descriptor-like evidence when the descriptor columns have been migrated and
populated by compute_idf.py.
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

from utils.idf_scoring import normalize_turkish  # canonical source


class IDFLookup:
    """
    Singleton class for fast IDF lookups.

    Loads all word IDF scores into memory on first access. Subsequent lookups
    are O(1) dictionary access with a conservative suffix fallback for Turkish
    inflections.
    """

    _instance = None
    _cache: Dict[str, dict] = {}
    _cache_tr: Dict[str, dict] = {}
    _loaded: bool = False
    _loaded_tr: bool = False
    _total_docs: int = 0
    _total_docs_tr: int = 0
    _default_idf: float = 9.0
    _descriptor_suffixes: Optional[Tuple[str, ...]] = None
    _descriptor_suffixes_tr: Optional[Tuple[str, ...]] = None
    _MORPHOLOGICAL_SUFFIXES = (
        "leri",
        "lari",
        "ler",
        "lar",
        "si",
        "i",
        "in",
        "nin",
        "ye",
        "ya",
        "e",
        "a",
    )

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def _normalize_descriptor_stats(value) -> Dict:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _entry(
        cls,
        *,
        idf: float,
        is_generic: bool,
        doc_freq: int,
        word_class: str,
        descriptor_like: bool = False,
        descriptor_score: float = 0.0,
        descriptor_stats=None,
    ) -> Dict:
        return {
            "idf": float(idf or 0.0),
            "is_generic": bool(is_generic),
            "doc_freq": int(doc_freq or 0),
            "word_class": word_class or "distinctive",
            "descriptor_like": bool(descriptor_like),
            "descriptor_score": float(descriptor_score or 0.0),
            "descriptor_stats": cls._normalize_descriptor_stats(descriptor_stats),
        }

    @classmethod
    def _connect(cls):
        import psycopg2

        return psycopg2.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME", "trademark_db"),
            user=os.getenv("DB_USER", "turk_patent"),
            password=os.getenv("DB_PASSWORD"),
        )

    @classmethod
    def _descriptor_columns_available(cls, cur, table_name: str) -> bool:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_name = %s
              AND column_name IN ('descriptor_like', 'descriptor_score', 'descriptor_stats')
            """,
            (table_name,),
        )
        return cur.fetchone()[0] == 3

    @classmethod
    def _table_exists(cls, cur, table_name: str) -> bool:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = %s
            )
            """,
            (table_name,),
        )
        return bool(cur.fetchone()[0])

    @classmethod
    def _load_table(cls, cur, table_name: str) -> Dict[str, dict]:
        descriptor_columns = cls._descriptor_columns_available(cur, table_name)
        if descriptor_columns:
            cur.execute(
                f"""
                SELECT
                    word,
                    idf_score,
                    is_generic,
                    document_frequency,
                    word_class,
                    COALESCE(descriptor_like, FALSE),
                    COALESCE(descriptor_score, 0),
                    COALESCE(descriptor_stats, '{{}}'::jsonb)
                FROM {table_name}
                """
            )
        else:
            cur.execute(
                f"""
                SELECT word, idf_score, is_generic, document_frequency, word_class
                FROM {table_name}
                """
            )

        cache: Dict[str, dict] = {}
        for row in cur.fetchall():
            if descriptor_columns:
                (
                    word,
                    idf,
                    is_generic,
                    doc_freq,
                    word_class,
                    descriptor_like,
                    descriptor_score,
                    descriptor_stats,
                ) = row
            else:
                word, idf, is_generic, doc_freq, word_class = row
                descriptor_like = False
                descriptor_score = 0.0
                descriptor_stats = {}

            cache[word] = cls._entry(
                idf=idf,
                is_generic=is_generic,
                doc_freq=doc_freq,
                word_class=word_class,
                descriptor_like=descriptor_like,
                descriptor_score=descriptor_score,
                descriptor_stats=descriptor_stats,
            )
        return cache

    @classmethod
    def _apply_foreign_generics_override(cls, cache: Dict[str, dict]) -> int:
        from foreign_generics import FOREIGN_GENERICS_OVERRIDE

        overridden = 0
        for word in FOREIGN_GENERICS_OVERRIDE:
            if word in cache:
                cache[word]["idf"] = 2.0
                cache[word]["is_generic"] = True
                cache[word]["word_class"] = "generic"
            else:
                cache[word] = cls._entry(
                    idf=2.0,
                    is_generic=True,
                    doc_freq=0,
                    word_class="generic",
                )
            overridden += 1
        return overridden

    @classmethod
    def load(cls, force: bool = False) -> None:
        """Load original-name IDF scores from word_idf."""
        if cls._loaded and not force:
            return

        try:
            conn = cls._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL")
                cls._total_docs = cur.fetchone()[0]

                cls._cache = cls._load_table(cur, "word_idf")
                overridden = cls._apply_foreign_generics_override(cls._cache)

                cls._loaded = True
                cls._descriptor_suffixes = None
                logger.info(
                    "IDFLookup: Loaded %s word scores into memory (overrode %s generic terms)",
                    f"{len(cls._cache):,}",
                    overridden,
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("IDFLookup: Failed to load from database: %s", exc)
            logger.warning("IDFLookup: Will use default scores (all words treated as distinctive)")
            cls._loaded = True

    @classmethod
    def load_translated(cls, force: bool = False) -> None:
        """Load translated-name IDF scores from word_idf_tr."""
        if cls._loaded_tr and not force:
            return

        try:
            conn = cls._connect()
            try:
                cur = conn.cursor()
                if not cls._table_exists(cur, "word_idf_tr"):
                    logger.warning("word_idf_tr table not found - run compute_idf.py --source both")
                    cls._loaded_tr = True
                    return

                cur.execute("SELECT COUNT(*) FROM trademarks WHERE name_tr IS NOT NULL")
                cls._total_docs_tr = cur.fetchone()[0]

                cls._cache_tr = cls._load_table(cur, "word_idf_tr")
                overridden = cls._apply_foreign_generics_override(cls._cache_tr)

                cls._loaded_tr = True
                cls._descriptor_suffixes_tr = None
                logger.info(
                    "IDFLookup: Loaded %s translated word scores (overrode %s generic terms)",
                    f"{len(cls._cache_tr):,}",
                    overridden,
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("IDFLookup: Failed to load translated IDF from database: %s", exc)
            logger.warning("IDFLookup: Path B will use default scores")
            cls._loaded_tr = True

    @classmethod
    def _get_entry_with_fallback_from_cache(
        cls,
        cache: Dict[str, dict],
        word_norm: str,
    ) -> Optional[dict]:
        entry = cache.get(word_norm)
        if entry:
            return entry

        for suffix in cls._MORPHOLOGICAL_SUFFIXES:
            if word_norm.endswith(suffix):
                stem = word_norm[: -len(suffix)]
                if len(stem) >= 3:
                    stem_entry = cache.get(stem)
                    if stem_entry is not None:
                        return stem_entry
        return None

    @classmethod
    def _get_entry_with_fallback(cls, word_norm: str) -> Optional[dict]:
        return cls._get_entry_with_fallback_from_cache(cls._cache, word_norm)

    @classmethod
    def get_idf(cls, word: str) -> float:
        """Get IDF score for a word from the original-name corpus."""
        if not cls._loaded:
            cls.load()

        entry = cls._get_entry_with_fallback(normalize_turkish(word))
        return entry["idf"] if entry else cls._default_idf

    @classmethod
    def is_generic(cls, word: str) -> bool:
        """Check if a word is generic in the original-name corpus."""
        if not cls._loaded:
            cls.load()

        entry = cls._get_entry_with_fallback(normalize_turkish(word))
        return bool(entry and entry.get("is_generic", False))

    @classmethod
    def get_doc_frequency(cls, word: str) -> int:
        """Get original-name document frequency for a word."""
        if not cls._loaded:
            cls.load()

        entry = cls._get_entry_with_fallback(normalize_turkish(word))
        return entry.get("doc_freq", 0) if entry else 0

    @classmethod
    def get_word_class(cls, word: str) -> str:
        """Get the 3-tier original-name classification for a word."""
        if not cls._loaded:
            cls.load()

        entry = cls._get_entry_with_fallback(normalize_turkish(word))
        if entry:
            if entry.get("descriptor_like"):
                return "generic"
            word_class = entry.get("word_class")
            if word_class in ("generic", "semi_generic", "distinctive"):
                return word_class
            idf = entry.get("idf", cls._default_idf)
            if idf < 6.0:
                return "generic"
            if idf < 8.5:
                return "semi_generic"
        return "distinctive"

    @classmethod
    def get_idf_tr(cls, word: str) -> float:
        """Get IDF score for a word from the translated-name corpus."""
        if not cls._loaded_tr:
            cls.load_translated()

        entry = cls._get_entry_with_fallback_from_cache(
            cls._cache_tr,
            normalize_turkish(word),
        )
        return entry["idf"] if entry else cls._default_idf

    @classmethod
    def get_word_class_tr(cls, word: str) -> str:
        """Get the 3-tier translated-name classification for a word."""
        if not cls._loaded_tr:
            cls.load_translated()

        entry = cls._get_entry_with_fallback_from_cache(
            cls._cache_tr,
            normalize_turkish(word),
        )
        if entry:
            if entry.get("descriptor_like"):
                return "generic"
            word_class = entry.get("word_class")
            if word_class in ("generic", "semi_generic", "distinctive"):
                return word_class
            idf = entry.get("idf", cls._default_idf)
            if idf < 6.0:
                return "generic"
            if idf < 8.5:
                return "semi_generic"
        return "distinctive"

    @classmethod
    def is_generic_tr(cls, word: str) -> bool:
        """Check if a word is generic or semi-generic in the translated corpus."""
        return cls.get_word_class_tr(word) in ("generic", "semi_generic")

    @classmethod
    def _descriptor_entry(
        cls,
        word: str,
        use_translated_idf: bool = False,
    ) -> Optional[dict]:
        if use_translated_idf:
            if not cls._loaded_tr:
                cls.load_translated()
            return cls._get_entry_with_fallback_from_cache(
                cls._cache_tr,
                normalize_turkish(word),
            )

        if not cls._loaded:
            cls.load()
        return cls._get_entry_with_fallback(normalize_turkish(word))

    @classmethod
    def is_descriptor_like(cls, word: str, use_translated_idf: bool = False) -> bool:
        """Return True when the IDF corpus marked the word as descriptor-like."""
        entry = cls._descriptor_entry(word, use_translated_idf=use_translated_idf)
        return bool(entry and entry.get("descriptor_like"))

    @classmethod
    def get_descriptor_stats(
        cls,
        word: str,
        use_translated_idf: bool = False,
    ) -> Dict:
        """Return stored descriptor evidence for a word, if available."""
        entry = cls._descriptor_entry(word, use_translated_idf=use_translated_idf)
        if not entry:
            return {}
        return dict(entry.get("descriptor_stats") or {})

    @classmethod
    def get_descriptor_score(cls, word: str, use_translated_idf: bool = False) -> float:
        """Return the stored descriptor score for a word, if available."""
        entry = cls._descriptor_entry(word, use_translated_idf=use_translated_idf)
        return float(entry.get("descriptor_score", 0.0)) if entry else 0.0

    @classmethod
    def get_descriptor_suffixes(
        cls,
        use_translated_idf: bool = False,
        min_length: int = 3,
    ) -> Tuple[str, ...]:
        """Return descriptor-like tokens that can act as compact suffixes."""
        if use_translated_idf:
            if not cls._loaded_tr:
                cls.load_translated()
            cache = cls._cache_tr
            cached = cls._descriptor_suffixes_tr
        else:
            if not cls._loaded:
                cls.load()
            cache = cls._cache
            cached = cls._descriptor_suffixes

        if min_length == 3 and cached is not None:
            return cached

        suffixes = tuple(
            sorted(
                (
                    word
                    for word, entry in cache.items()
                    if entry.get("descriptor_like")
                    and len(word) >= min_length
                    and word.isalnum()
                ),
                key=len,
                reverse=True,
            )
        )

        if min_length == 3:
            if use_translated_idf:
                cls._descriptor_suffixes_tr = suffixes
            else:
                cls._descriptor_suffixes = suffixes
        return suffixes

    @classmethod
    def get_weight_multiplier(cls, word: str) -> float:
        """Get the weight multiplier for a word based on its class."""
        word_class = cls.get_word_class(word)
        if word_class == "distinctive":
            return 1.0
        if word_class == "semi_generic":
            return 0.5
        return 0.1

    @classmethod
    def get_word_weights(cls, words: Set[str]) -> Dict[str, float]:
        """Get normalized IDF weights for a set of words."""
        if not cls._loaded:
            cls.load()

        idf_scores = {}
        for word in words:
            word_norm = normalize_turkish(word)
            if len(word_norm) <= 1:
                continue
            idf_scores[word] = cls.get_idf(word_norm)

        if not idf_scores:
            return {}

        total_idf = sum(idf_scores.values())
        if total_idf > 0:
            return {word: idf / total_idf for word, idf in idf_scores.items()}

        equal_weight = 1.0 / len(idf_scores)
        return {word: equal_weight for word in idf_scores}

    @classmethod
    def analyze_query(cls, query: str) -> Dict:
        """Analyze a search query and return word-importance diagnostics."""
        if not cls._loaded:
            cls.load()

        normalized = normalize_turkish(query)
        words = set(re.findall(r"\b[a-z0-9]+\b", normalized))
        words = {word for word in words if len(word) > 1}

        if not words:
            return {"query": query, "words": [], "total_weight": 0}

        word_analysis = []
        total_weighted = 0.0

        for word in sorted(words):
            idf = cls.get_idf(word)
            word_class = cls.get_word_class(word)
            weight_mult = cls.get_weight_multiplier(word)
            doc_freq = cls.get_doc_frequency(word)
            descriptor_stats = cls.get_descriptor_stats(word)

            word_analysis.append(
                {
                    "word": word,
                    "idf": round(idf, 2),
                    "word_class": word_class,
                    "is_generic": word_class == "generic",
                    "weight_multiplier": weight_mult,
                    "doc_frequency": doc_freq,
                    "descriptor_like": cls.is_descriptor_like(word),
                    "descriptor_score": round(cls.get_descriptor_score(word), 4),
                    "descriptor_stats": descriptor_stats,
                }
            )
            total_weighted += weight_mult

        for item in word_analysis:
            item["final_weight"] = (
                item["weight_multiplier"] / total_weighted
                if total_weighted > 0
                else 0.0
            )
            item["weight"] = item["final_weight"]

        word_analysis.sort(key=lambda item: item["final_weight"], reverse=True)

        distinctive_weight = sum(
            item["final_weight"]
            for item in word_analysis
            if item["word_class"] == "distinctive"
        )
        semi_generic_weight = sum(
            item["final_weight"]
            for item in word_analysis
            if item["word_class"] == "semi_generic"
        )
        generic_weight = sum(
            item["final_weight"]
            for item in word_analysis
            if item["word_class"] == "generic"
        )

        return {
            "query": query,
            "normalized": normalized,
            "words": word_analysis,
            "distinctive_weight": round(distinctive_weight, 3),
            "semi_generic_weight": round(semi_generic_weight, 3),
            "generic_weight": round(generic_weight, 3),
            "distinctive_count": sum(
                1 for item in word_analysis if item["word_class"] == "distinctive"
            ),
            "semi_generic_count": sum(
                1 for item in word_analysis if item["word_class"] == "semi_generic"
            ),
            "generic_count": sum(
                1 for item in word_analysis if item["word_class"] == "generic"
            ),
        }

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cache and force reload on next access."""
        cls._cache = {}
        cls._cache_tr = {}
        cls._loaded = False
        cls._loaded_tr = False
        cls._descriptor_suffixes = None
        cls._descriptor_suffixes_tr = None
        logger.info("IDFLookup: Cache cleared (original + translated)")


def main():
    """Small CLI smoke test for the IDF lookup."""
    test_queries = [
        "dogan patent",
        "nike",
        "coca cola",
        "kent patent",
        "apple technology",
        "istanbul ticaret",
    ]

    print("=" * 60)
    print("IDF LOOKUP TEST")
    print("=" * 60)

    for query in test_queries:
        print(f"\n[QUERY] '{query}'")
        analysis = IDFLookup.analyze_query(query)

        print(f"   Distinctive weight: {analysis['distinctive_weight']:.1%}")
        print(f"   Generic weight:     {analysis['generic_weight']:.1%}")
        print("\n   Word breakdown:")

        for item in analysis["words"]:
            type_str = "GENERIC" if item["is_generic"] else item["word_class"].upper()
            descriptor = " descriptor" if item["descriptor_like"] else ""
            print(
                f"      {item['word']:<15} weight={item['weight']:.1%}  "
                f"IDF={item['idf']:.2f}  freq={item['doc_frequency']:,}  "
                f"[{type_str}{descriptor}]"
            )

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
