"""Shared helpers for ingest pipelines that upsert into ``holders``.

Three ingest pipelines (designs, patents, cografi) all dedupe a no-TPE-ID
holder by name before inserting. Historically each used a plain
``LOWER(name) = LOWER(%s)`` comparison, which let trivial punctuation +
whitespace variants ("CO.  LTD." vs "CO. LTD.") create distinct holders
rows for the same real-world entity. The one-shot consolidation
migration ``holders_consolidate_dups_no_tpe.sql`` fixed the existing
data; this module keeps the ingest paths from drifting back by using the
same conservative normalization.

Normalization rule (mirrors the migration's expression):
  LOWER + collapse runs of whitespace + strip ASCII punctuation.
  All letters are preserved, including Turkish diacritics (İ, Ş, Ğ, Ü,
  Ö, Ç), so "ÜMİT ÜNAL" and "ÜMÜT İNAL" stay separate.

The matching functional index lives in
``migrations/holders_normalized_name_index.sql``; without it the
dedup SELECT degrades to a seq scan on the 143k-row holders table.
"""

from __future__ import annotations


# The SQL fragment that normalizes a name expression. The argument must
# be a column reference or a literal — psycopg2 parameter substitution
# is handled by passing %s alongside the value. Keep this in sync with
# the migration's normalization rule.
_NORMALIZE_NAME_SQL_TMPL = (
    "LOWER(REGEXP_REPLACE("
    "REGEXP_REPLACE({expr}, '[[:space:]]+', ' ', 'g'), "
    "'[[:punct:]]', '', 'g'"
    "))"
)


def find_holder_id_by_normalized_name(cur, name: str) -> str | None:
    """Return ``holders.id`` for any existing no-TPE-ID row whose name
    normalizes to the same canonical form, else ``None``.

    Restricted to ``tpe_client_id IS NULL`` so a TPE-issued holder is
    never returned by accident for an unrelated foreign-entity name
    match (TPE rows live under their own canonical key).
    """
    if not name:
        return None
    cur.execute(
        f"""
        SELECT id FROM holders
        WHERE {_NORMALIZE_NAME_SQL_TMPL.format(expr="name")}
            = {_NORMALIZE_NAME_SQL_TMPL.format(expr="%s")}
          AND tpe_client_id IS NULL
        LIMIT 1
        """,
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None
