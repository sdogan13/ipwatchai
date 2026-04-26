# compute_idf.py
"""
Compute IDF scores and descriptor-like token behavior for trademark names.

Run once to populate the word_idf table:
    python compute_idf.py

Re-run periodically to update with new trademarks:
    python compute_idf.py --source both
"""

import argparse
import logging
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [IDF] - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

from utils.idf_scoring import normalize_turkish, tokenize  # canonical source


SOURCE_CONFIG = {
    "name": {
        "field": "name",
        "table": "word_idf",
        "label": "original names",
    },
    "name_tr": {
        "field": "name_tr",
        "table": "word_idf_tr",
        "label": "translated names",
    },
}

WEIGHT_GENERIC = 0.1
WEIGHT_SEMI_GENERIC = 0.5
WEIGHT_DISTINCTIVE = 1.0

DESCRIPTOR_MIN_DOC_RATIO = 0.00035
DESCRIPTOR_SCORE_THRESHOLD = 0.72
DESCRIPTOR_MAX_FIRST_RATE = 0.25
DESCRIPTOR_MAX_SINGLE_RATE = 0.05
DESCRIPTOR_STRONG_SUFFIX_RATE = 0.45
DESCRIPTOR_MODERATE_SUFFIX_RATE = 0.15
COMPACT_PREFIX_MIN_LENGTH = 4
COMPACT_SUFFIX_MIN_LENGTH = 3


def _ordered_tokens(text: str) -> List[str]:
    normalized = normalize_turkish(text or "")
    return [word for word in re.findall(r"\b[a-z0-9]+\b", normalized) if len(word) > 1]


def _normalize_holder(holder_name: Optional[str]) -> str:
    return normalize_turkish(holder_name or "")[:160]


def _normalize_classes(classes: Optional[Iterable]) -> Set[int]:
    normalized = set()
    if not classes:
        return normalized
    for value in classes:
        try:
            normalized.add(int(value))
        except (TypeError, ValueError):
            continue
    return normalized


def _frequency_class(
    doc_freq: int,
    generic_threshold: float,
    semi_generic_threshold: float,
) -> Tuple[str, bool, float]:
    if doc_freq > generic_threshold:
        return "generic", True, WEIGHT_GENERIC
    if doc_freq > semi_generic_threshold:
        return "semi_generic", False, WEIGHT_SEMI_GENERIC
    return "distinctive", False, WEIGHT_DISTINCTIVE


def _descriptor_candidate_min_docs(total_docs: int) -> int:
    return max(250, int(total_docs * DESCRIPTOR_MIN_DOC_RATIO))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _descriptor_profile(
    *,
    word: str,
    doc_freq: int,
    total_docs: int,
    first_count: int,
    last_count: int,
    single_count: int,
    unique_partner_count: int,
    unique_holder_count: int,
    unique_class_count: int,
    compact_suffix_hits: int,
    original_word_class: str,
) -> Tuple[bool, float, Dict]:
    min_docs = _descriptor_candidate_min_docs(total_docs)
    first_rate = _ratio(first_count, doc_freq)
    last_rate = _ratio(last_count, doc_freq)
    single_rate = _ratio(single_count, doc_freq)

    high_partner_dispersion = unique_partner_count >= max(80, int(doc_freq * 0.45))
    high_holder_dispersion = unique_holder_count >= max(50, int(doc_freq * 0.30))
    class_dispersion = unique_class_count >= 8
    compound_suffix = compact_suffix_hits >= max(15, int(doc_freq * 0.02))
    mostly_suffix = last_rate >= DESCRIPTOR_STRONG_SUFFIX_RATE
    moderate_suffix_with_dispersion = (
        last_rate >= DESCRIPTOR_MODERATE_SUFFIX_RATE
        and high_partner_dispersion
        and high_holder_dispersion
        and class_dispersion
    )

    score = 0.0
    score += min(1.0, last_rate / DESCRIPTOR_STRONG_SUFFIX_RATE) * 0.30
    score += (1.0 - min(1.0, first_rate / DESCRIPTOR_MAX_FIRST_RATE)) * 0.16
    score += (1.0 - min(1.0, single_rate / DESCRIPTOR_MAX_SINGLE_RATE)) * 0.10
    score += min(1.0, unique_partner_count / max(doc_freq * 0.50, 1)) * 0.14
    score += min(1.0, unique_holder_count / max(doc_freq * 0.35, 1)) * 0.12
    score += min(1.0, unique_class_count / 8) * 0.13
    score += min(1.0, compact_suffix_hits / max(doc_freq * 0.04, 10)) * 0.15
    score = round(min(1.0, score), 4)

    reason_flags = []
    if mostly_suffix:
        reason_flags.append("mostly_suffix")
    if first_rate <= DESCRIPTOR_MAX_FIRST_RATE:
        reason_flags.append("low_initial_use")
    if single_rate <= DESCRIPTOR_MAX_SINGLE_RATE:
        reason_flags.append("low_single_use")
    if high_partner_dispersion:
        reason_flags.append("high_partner_dispersion")
    if high_holder_dispersion:
        reason_flags.append("high_holder_dispersion")
    if class_dispersion:
        reason_flags.append("class_dispersion")
    if compound_suffix:
        reason_flags.append("compound_suffix")
    if moderate_suffix_with_dispersion:
        reason_flags.append("moderate_suffix_with_dispersion")

    descriptor_like = (
        doc_freq >= min_docs
        and score >= DESCRIPTOR_SCORE_THRESHOLD
        and first_rate <= DESCRIPTOR_MAX_FIRST_RATE
        and single_rate <= DESCRIPTOR_MAX_SINGLE_RATE
        and (
            mostly_suffix
            or moderate_suffix_with_dispersion
            or compound_suffix
        )
    )

    stats = {
        "word": word,
        "doc_frequency": doc_freq,
        "candidate_min_docs": min_docs,
        "first_count": first_count,
        "last_count": last_count,
        "single_count": single_count,
        "first_rate": round(first_rate, 4),
        "last_rate": round(last_rate, 4),
        "single_rate": round(single_rate, 4),
        "unique_partner_count": unique_partner_count,
        "unique_holder_count": unique_holder_count,
        "unique_class_count": unique_class_count,
        "compact_suffix_hits": compact_suffix_hits,
        "original_word_class": original_word_class,
        "reason_flags": reason_flags,
    }
    return descriptor_like, score, stats


def _collect_base_counts(cur, source_field: str, total_docs: int, batch_size: int):
    word_doc_count = Counter()
    first_count = Counter()
    last_count = Counter()
    single_count = Counter()
    processed = 0

    cur.execute(
        f"""
        SELECT {source_field}
        FROM trademarks
        WHERE {source_field} IS NOT NULL AND length({source_field}) > 0
        """
    )

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break

        for (text_value,) in rows:
            ordered = _ordered_tokens(text_value)
            if not ordered:
                continue
            words = set(ordered)
            for word in words:
                word_doc_count[word] += 1
            first_count[ordered[0]] += 1
            last_count[ordered[-1]] += 1
            if len(ordered) == 1:
                single_count[ordered[0]] += 1

        processed += len(rows)
        if processed % 500000 == 0:
            logger.info(
                "   Processed %s / %s trademarks (%s%%)",
                f"{processed:,}",
                f"{total_docs:,}",
                processed * 100 // total_docs,
            )

    return word_doc_count, first_count, last_count, single_count


def _collect_descriptor_dispersion(
    cur,
    source_field: str,
    candidate_words: Set[str],
    total_docs: int,
    batch_size: int,
):
    partner_sets = defaultdict(set)
    holder_sets = defaultdict(set)
    class_sets = defaultdict(set)
    compact_suffix_hits = Counter()
    max_suffix_length = max((len(word) for word in candidate_words), default=0)
    processed = 0

    cur.execute(
        f"""
        SELECT {source_field}, holder_name, nice_class_numbers
        FROM trademarks
        WHERE {source_field} IS NOT NULL AND length({source_field}) > 0
        """
    )

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break

        for text_value, holder_name, nice_classes in rows:
            words = tokenize(text_value)
            if not words:
                continue

            matching_words = words.intersection(candidate_words)
            if matching_words:
                holder_norm = _normalize_holder(holder_name)
                class_values = _normalize_classes(nice_classes)
                for word in matching_words:
                    partner_sets[word].update(words - {word})
                    if holder_norm:
                        holder_sets[word].add(holder_norm)
                    if class_values:
                        class_sets[word].update(class_values)

            for token in words:
                if len(token) < COMPACT_PREFIX_MIN_LENGTH + COMPACT_SUFFIX_MIN_LENGTH:
                    continue
                start_min = max(
                    COMPACT_PREFIX_MIN_LENGTH,
                    len(token) - max_suffix_length,
                )
                for suffix_start in range(start_min, len(token) - COMPACT_SUFFIX_MIN_LENGTH + 1):
                    suffix = token[suffix_start:]
                    if suffix not in candidate_words:
                        continue
                    root = token[:suffix_start]
                    if len(root) >= COMPACT_PREFIX_MIN_LENGTH and root.isalnum():
                        compact_suffix_hits[suffix] += 1

        processed += len(rows)
        if processed % 500000 == 0:
            logger.info(
                "   Descriptor pass processed %s / %s trademarks (%s%%)",
                f"{processed:,}",
                f"{total_docs:,}",
                processed * 100 // total_docs,
            )

    return partner_sets, holder_sets, class_sets, compact_suffix_hits


def _ensure_descriptor_columns(cur, table_name: str) -> None:
    cur.execute(
        f"""
        ALTER TABLE {table_name}
            ADD COLUMN IF NOT EXISTS total_documents INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS weight_multiplier FLOAT DEFAULT 1.0,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP,
            ADD COLUMN IF NOT EXISTS descriptor_like BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS descriptor_score DOUBLE PRECISION DEFAULT 0,
            ADD COLUMN IF NOT EXISTS descriptor_stats JSONB DEFAULT '{{}}'::jsonb
        """
    )


def _compute_idf_for_source(conn, source_name: str, batch_size: int = 50000) -> None:
    from psycopg2.extras import Json, execute_values

    config = SOURCE_CONFIG[source_name]
    source_field = config["field"]
    target_table = config["table"]
    label = config["label"]
    cur = conn.cursor()

    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("IDF COMPUTATION STARTED for %s", label)
    logger.info("=" * 60)

    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM trademarks
        WHERE {source_field} IS NOT NULL AND length({source_field}) > 0
        """
    )
    total_docs = cur.fetchone()[0]
    logger.info("Total trademarks with %s: %s", source_field, f"{total_docs:,}")

    if total_docs == 0:
        logger.warning("No trademarks found for %s; skipping", source_field)
        cur.close()
        return

    _ensure_descriptor_columns(cur, target_table)
    conn.commit()

    logger.info("Step 1/4: Counting word frequencies and token positions...")
    word_doc_count, first_count, last_count, single_count = _collect_base_counts(
        cur,
        source_field,
        total_docs,
        batch_size,
    )
    logger.info("   [OK] Found %s unique words", f"{len(word_doc_count):,}")

    descriptor_min_docs = _descriptor_candidate_min_docs(total_docs)
    candidate_words = {
        word for word, doc_freq in word_doc_count.items()
        if doc_freq >= descriptor_min_docs
    }
    logger.info(
        "Step 2/4: Computing descriptor dispersion for %s candidate words...",
        f"{len(candidate_words):,}",
    )
    partner_sets, holder_sets, class_sets, compact_suffix_hits = _collect_descriptor_dispersion(
        cur,
        source_field,
        candidate_words,
        total_docs,
        batch_size,
    )

    logger.info("Step 3/4: Computing IDF and descriptor classifications...")
    generic_threshold = total_docs * 0.005
    semi_generic_threshold = total_docs * 0.001
    logger.info("   Generic threshold: > %s occurrences", f"{generic_threshold:,.0f}")
    logger.info(
        "   Semi-generic threshold: %s - %s occurrences",
        f"{semi_generic_threshold:,.0f}",
        f"{generic_threshold:,.0f}",
    )
    logger.info("   Descriptor min docs: %s", f"{descriptor_min_docs:,}")

    idf_data = []
    class_counts = Counter()
    descriptor_count = 0

    for word, doc_freq in word_doc_count.items():
        idf = math.log(total_docs / doc_freq)
        original_word_class, original_is_generic, original_weight = _frequency_class(
            doc_freq,
            generic_threshold,
            semi_generic_threshold,
        )
        descriptor_like, descriptor_score, descriptor_stats = _descriptor_profile(
            word=word,
            doc_freq=doc_freq,
            total_docs=total_docs,
            first_count=first_count[word],
            last_count=last_count[word],
            single_count=single_count[word],
            unique_partner_count=len(partner_sets.get(word, ())),
            unique_holder_count=len(holder_sets.get(word, ())),
            unique_class_count=len(class_sets.get(word, ())),
            compact_suffix_hits=compact_suffix_hits[word],
            original_word_class=original_word_class,
        )

        if descriptor_like:
            word_class = "generic"
            is_generic = True
            weight_mult = WEIGHT_GENERIC
            descriptor_count += 1
        else:
            word_class = original_word_class
            is_generic = original_is_generic
            weight_mult = original_weight

        class_counts[word_class] += 1
        descriptor_stats["final_word_class"] = word_class
        descriptor_stats["final_is_generic"] = is_generic

        idf_data.append(
            (
                word,
                doc_freq,
                round(idf, 4),
                is_generic,
                total_docs,
                word_class,
                weight_mult,
                descriptor_like,
                descriptor_score,
                Json(descriptor_stats),
            )
        )

    logger.info("Step 4/4: Saving to %s...", target_table)
    cur.execute(f"TRUNCATE TABLE {target_table}")
    execute_values(
        cur,
        f"""
        INSERT INTO {target_table} (
            word, document_frequency, idf_score, is_generic,
            total_documents, word_class, weight_multiplier, updated_at,
            descriptor_like, descriptor_score, descriptor_stats
        )
        VALUES %s
        """,
        [
            (w, df, idf, gen, total, wclass, wmult, datetime.now(), dlike, dscore, dstats)
            for w, df, idf, gen, total, wclass, wmult, dlike, dscore, dstats in idf_data
        ],
        page_size=10000,
    )

    conn.commit()
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("Inserted %s records into %s", f"{len(idf_data):,}", target_table)
    logger.info("GENERIC words: %s", f"{class_counts['generic']:,}")
    logger.info("SEMI_GENERIC words: %s", f"{class_counts['semi_generic']:,}")
    logger.info("DISTINCTIVE words: %s", f"{class_counts['distinctive']:,}")
    logger.info("Descriptor-like words: %s", f"{descriptor_count:,}")
    logger.info("Time elapsed: %.1f seconds", elapsed)

    cur.execute(
        f"""
        SELECT word, document_frequency, idf_score, descriptor_score, descriptor_stats
        FROM {target_table}
        WHERE descriptor_like = TRUE
        ORDER BY descriptor_score DESC, document_frequency DESC
        LIMIT 20
        """
    )
    logger.info("Top descriptor-like words for %s:", label)
    for word, freq, idf, score, stats in cur.fetchall():
        flags = ",".join((stats or {}).get("reason_flags", []))
        logger.info(
            "   %-18s freq=%8s idf=%5.2f descriptor=%4.2f flags=%s",
            word,
            f"{freq:,}",
            idf,
            score,
            flags,
        )

    cur.close()


def _selected_sources(source: str) -> List[str]:
    if source == "both":
        return ["name", "name_tr"]
    return [source]


def compute_idf_scores(update_mode: bool = False, source: str = "name"):
    """
    Compute IDF and descriptor-like stats for original and/or translated names.

    Args:
        update_mode: Kept for CLI compatibility. The current implementation
            recomputes the selected source tables fully.
        source: One of "name", "name_tr", or "both".
    """
    del update_mode
    import psycopg2

    if source not in {"name", "name_tr", "both"}:
        raise ValueError("source must be one of: name, name_tr, both")

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5433"),
        database=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD"),
    )

    try:
        for selected_source in _selected_sources(source):
            _compute_idf_for_source(conn, selected_source)
    finally:
        conn.close()
        logger.info("[OK] Done! IDF tables are ready for use.")


def main():
    parser = argparse.ArgumentParser(description="Compute trademark IDF and descriptor scores")
    parser.add_argument("--update", action="store_true", help="Compatibility flag; selected sources are fully recomputed")
    parser.add_argument(
        "--source",
        choices=["name", "name_tr", "both"],
        default="name",
        help="Which corpus/table to compute",
    )
    args = parser.parse_args()

    compute_idf_scores(update_mode=args.update, source=args.source)


if __name__ == "__main__":
    main()
