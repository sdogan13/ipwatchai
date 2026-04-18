"""Nice-class service helpers used by HTTP route modules."""

import time

import psycopg2
import psycopg2.extras
from fastapi import HTTPException


async def run_nice_class_suggestion(
    description,
    top_k,
    lang,
    settings,
    logger=None,
    class_name_getter=None,
    text_embedding_getter=None,
    connect_fn=None,
    timer=None,
):
    """Suggest Nice classes from a goods/services description."""
    if class_name_getter is None:
        class_name_getter = lambda class_num, current_lang="tr": f"Class {class_num}"
    if text_embedding_getter is None:
        from ai import get_text_embedding_cached

        text_embedding_getter = get_text_embedding_cached
    if connect_fn is None:
        connect_fn = psycopg2.connect
    if timer is None:
        timer = time.time

    start_time = timer()
    conn = None
    cur = None

    try:
        query_embedding = text_embedding_getter(description)
        conn = connect_fn(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT
                class_number,
                description,
                1 - (description_embedding <=> %s::halfvec) as similarity
            FROM nice_classes_lookup
            WHERE description_embedding IS NOT NULL
            ORDER BY description_embedding <=> %s::halfvec
            LIMIT %s
            """,
            (query_embedding, query_embedding, top_k),
        )

        rows = cur.fetchall()
        suggestions = []
        for row in rows:
            suggestions.append(
                {
                    "class_number": row["class_number"],
                    "class_name": class_name_getter(row["class_number"], lang),
                    "similarity": round(float(row["similarity"]), 4),
                    "description": (
                        row["description"][:200] + "..."
                        if len(row["description"]) > 200
                        else row["description"]
                    ),
                }
            )

        processing_time = (timer() - start_time) * 1000
        return {
            "query": description[:100] + "..." if len(description) > 100 else description,
            "suggestions": suggestions,
            "processing_time_ms": round(processing_time, 2),
        }
    except Exception as exc:
        if logger:
            logger.error("Class suggestion error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
