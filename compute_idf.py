# compute_idf.py
"""
Compute IDF (Inverse Document Frequency) scores for all words in trademark database.

This identifies which words are DISTINCTIVE (rare) vs GENERIC (common).
- "patent" appears in 50,000 trademarks → LOW IDF (generic)
- "dogan" appears in 127 trademarks → HIGH IDF (distinctive)

Run once to populate the word_idf table:
    python compute_idf.py

Re-run periodically (monthly) to update with new trademarks:
    python compute_idf.py --update
"""

import os
import sys
import math
import re
import logging
import argparse
from collections import Counter
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [IDF] - %(levelname)s - %(message)s'
)
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


def tokenize(text: str) -> set:
    """Extract unique words from text."""
    normalized = normalize_turkish(text)
    # Extract words (alphanumeric sequences)
    words = set(re.findall(r'\b[a-z0-9]+\b', normalized))
    # Filter: keep words with length > 1
    words = {w for w in words if len(w) > 1}
    return words


def compute_idf_scores(update_mode: bool = False):
    """
    Compute IDF scores for all words in the trademark database.

    Uses 3-tier classification:
    - GENERIC: >0.5% of docs (e.g., "ve", "com", "ltd")
    - SEMI_GENERIC: 0.1%-0.5% of docs (e.g., "patent", "marka", "grup")
    - DISTINCTIVE: <0.1% of docs (e.g., "dogan", "nike", "apple")

    Args:
        update_mode: If True, only process new trademarks since last run
    """
    import psycopg2
    from psycopg2.extras import execute_values

    # Database connection
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=os.getenv("DB_PORT", "5433"),
        database=os.getenv("DB_NAME", "trademark_db"),
        user=os.getenv("DB_USER", "turk_patent"),
        password=os.getenv("DB_PASSWORD")
    )
    cur = conn.cursor()

    start_time = datetime.now()
    logger.info("="*60)
    logger.info("IDF COMPUTATION STARTED (3-Tier Classification)")
    logger.info("="*60)

    # Step 1: Get total document count
    cur.execute("SELECT COUNT(*) FROM trademarks WHERE name IS NOT NULL AND length(name) > 0")
    total_docs = cur.fetchone()[0]
    logger.info(f"Total trademarks with names: {total_docs:,}")

    if total_docs == 0:
        logger.error("No trademarks found in database!")
        return

    # Step 2: Count word frequencies
    logger.info("\nStep 1/3: Counting word frequencies...")

    word_doc_count = Counter()
    processed = 0
    batch_size = 50000

    cur.execute("""
        SELECT name FROM trademarks
        WHERE name IS NOT NULL AND length(name) > 0
    """)

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break

        for (name,) in rows:
            words = tokenize(name)
            for word in words:
                word_doc_count[word] += 1

        processed += len(rows)
        if processed % 500000 == 0:
            logger.info(f"   Processed {processed:,} / {total_docs:,} trademarks ({processed*100//total_docs}%)")

    logger.info(f"   [OK] Processed {processed:,} trademarks")
    logger.info(f"   [OK] Found {len(word_doc_count):,} unique words")

    # Step 3: Compute IDF scores with 3-tier classification
    logger.info("\nStep 2/3: Computing IDF scores with 3-tier classification...")

    # Thresholds for classification
    # GENERIC: >0.5% of documents (>11,000 occurrences)
    # SEMI_GENERIC: 0.1%-0.5% of documents (2,200-11,000 occurrences)
    # DISTINCTIVE: <0.1% of documents (<2,200 occurrences)
    GENERIC_THRESHOLD = total_docs * 0.005       # 0.5%
    SEMI_GENERIC_THRESHOLD = total_docs * 0.001  # 0.1%

    logger.info(f"   Thresholds:")
    logger.info(f"     GENERIC:      > {GENERIC_THRESHOLD:,.0f} occurrences (>0.5%)")
    logger.info(f"     SEMI_GENERIC: {SEMI_GENERIC_THRESHOLD:,.0f} - {GENERIC_THRESHOLD:,.0f} (0.1%-0.5%)")
    logger.info(f"     DISTINCTIVE:  < {SEMI_GENERIC_THRESHOLD:,.0f} (<0.1%)")

    idf_data = []
    generic_count = 0
    semi_generic_count = 0
    distinctive_count = 0

    # Weight multipliers for scoring
    WEIGHT_GENERIC = 0.1       # Heavily penalized
    WEIGHT_SEMI_GENERIC = 0.5  # Moderately penalized
    WEIGHT_DISTINCTIVE = 1.0   # Full weight

    for word, doc_freq in word_doc_count.items():
        # IDF formula: log(N / df)
        idf = math.log(total_docs / doc_freq)

        # 3-tier classification
        if doc_freq > GENERIC_THRESHOLD:
            word_class = 'generic'
            is_generic = True
            weight_mult = WEIGHT_GENERIC
            generic_count += 1
        elif doc_freq > SEMI_GENERIC_THRESHOLD:
            word_class = 'semi_generic'
            is_generic = False  # Keep legacy flag for backwards compat
            weight_mult = WEIGHT_SEMI_GENERIC
            semi_generic_count += 1
        else:
            word_class = 'distinctive'
            is_generic = False
            weight_mult = WEIGHT_DISTINCTIVE
            distinctive_count += 1

        idf_data.append((
            word,
            doc_freq,
            round(idf, 4),
            is_generic,
            total_docs,
            word_class,
            weight_mult
        ))

    logger.info(f"\n   Classification Results:")
    logger.info(f"     GENERIC:      {generic_count:,} words")
    logger.info(f"     SEMI_GENERIC: {semi_generic_count:,} words")
    logger.info(f"     DISTINCTIVE:  {distinctive_count:,} words")

    # Step 4: Insert into database
    logger.info("\nStep 3/3: Saving to database...")

    # Clear existing data
    cur.execute("TRUNCATE TABLE word_idf")

    # Batch insert with new columns
    execute_values(
        cur,
        """
        INSERT INTO word_idf (word, document_frequency, idf_score, is_generic,
                              total_documents, word_class, weight_multiplier, updated_at)
        VALUES %s
        """,
        [(w, df, idf, gen, total, wclass, wmult, datetime.now())
         for w, df, idf, gen, total, wclass, wmult in idf_data],
        page_size=10000
    )

    conn.commit()
    logger.info(f"   [OK] Inserted {len(idf_data):,} word records")

    # Step 5: Print statistics
    elapsed = (datetime.now() - start_time).total_seconds()

    logger.info("\n" + "="*60)
    logger.info("IDF COMPUTATION COMPLETE")
    logger.info("="*60)
    logger.info(f"Total documents:    {total_docs:,}")
    logger.info(f"Unique words:       {len(word_doc_count):,}")
    logger.info(f"GENERIC words:      {generic_count:,} (>0.5% of docs, weight=0.1)")
    logger.info(f"SEMI_GENERIC words: {semi_generic_count:,} (0.1%-0.5%, weight=0.5)")
    logger.info(f"DISTINCTIVE words:  {distinctive_count:,} (<0.1%, weight=1.0)")
    logger.info(f"Time elapsed:       {elapsed:.1f} seconds")

    # Show GENERIC words
    cur.execute("""
        SELECT word, document_frequency, idf_score, weight_multiplier
        FROM word_idf
        WHERE word_class = 'generic'
        ORDER BY document_frequency DESC
        LIMIT 15
    """)

    logger.info("\nGENERIC WORDS (weight=0.1, will be heavily de-emphasized):")
    logger.info(f"{'Word':<20} {'Frequency':>12} {'IDF':>8} {'Weight':>8}")
    logger.info("-" * 50)
    for word, freq, idf, wm in cur.fetchall():
        logger.info(f"{word:<20} {freq:>12,} {idf:>8.2f} {wm:>8.1f}")

    # Show SEMI_GENERIC words (THIS IS THE KEY GROUP!)
    cur.execute("""
        SELECT word, document_frequency, idf_score, weight_multiplier
        FROM word_idf
        WHERE word_class = 'semi_generic'
        ORDER BY document_frequency DESC
        LIMIT 25
    """)

    logger.info("\nSEMI_GENERIC WORDS (weight=0.5, moderately de-emphasized):")
    logger.info("These are common trademark suffixes like 'patent', 'marka', 'grup'")
    logger.info(f"{'Word':<20} {'Frequency':>12} {'IDF':>8} {'Weight':>8}")
    logger.info("-" * 50)
    for word, freq, idf, wm in cur.fetchall():
        logger.info(f"{word:<20} {freq:>12,} {idf:>8.2f} {wm:>8.1f}")

    # Show sample DISTINCTIVE words
    cur.execute("""
        SELECT word, document_frequency, idf_score, weight_multiplier
        FROM word_idf
        WHERE word_class = 'distinctive'
          AND document_frequency >= 50
        ORDER BY document_frequency DESC
        LIMIT 15
    """)

    logger.info("\nDISTINCTIVE WORDS (weight=1.0, full importance):")
    logger.info(f"{'Word':<20} {'Frequency':>12} {'IDF':>8} {'Weight':>8}")
    logger.info("-" * 50)
    for word, freq, idf, wm in cur.fetchall():
        logger.info(f"{word:<20} {freq:>12,} {idf:>8.2f} {wm:>8.1f}")

    # Test specific words with full classification
    test_words = ['patent', 'marka', 'dogan', 'nike', 'coca', 'cola', 'apple', 'grup', 'holding', 'insaat', 'ticaret']
    logger.info("\nTEST WORDS CLASSIFICATION:")
    logger.info(f"{'Word':<15} {'Frequency':>10} {'IDF':>7} {'Class':<15} {'Weight':>7}")
    logger.info("-" * 60)

    for word in test_words:
        cur.execute("""
            SELECT word, document_frequency, idf_score, word_class, weight_multiplier
            FROM word_idf WHERE word = %s
        """, (word,))
        row = cur.fetchone()
        if row:
            w, freq, idf, wclass, wmult = row
            logger.info(f"{w:<15} {freq:>10,} {idf:>7.2f} {wclass:<15} {wmult:>7.1f}")
        else:
            logger.info(f"{word:<15} {'(not found)':>10}")

    conn.close()
    logger.info("\n[OK] Done! word_idf table is ready for use.")


def main():
    parser = argparse.ArgumentParser(description="Compute IDF scores for trademark words")
    parser.add_argument("--update", action="store_true", help="Update mode (process new trademarks only)")
    args = parser.parse_args()

    compute_idf_scores(update_mode=args.update)


if __name__ == "__main__":
    main()
