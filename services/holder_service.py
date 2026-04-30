"""Holder portfolio service helpers used by HTTP route modules."""

import csv
import io

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from database.crud import Database


async def get_holder_trademarks_data(
    tpe_client_id,
    page,
    page_size,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Return paginated holder portfolio data."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))
        if not plan_limit_getter(plan["plan_name"], "can_view_holder_portfolio"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "pro_feature",
                    "message": "Sahip portföyü görüntüleme PRO özelliğidir",
                    "upgrade_url": "/pricing",
                },
            )

        offset = (page - 1) * page_size
        cur = db.cursor()

        cur.execute(
            """
            SELECT DISTINCT holder_name, holder_tpe_client_id
            FROM trademarks
            WHERE holder_tpe_client_id = %s
            LIMIT 1
        """,
            (tpe_client_id,),
        )
        holder_row = cur.fetchone()
        if not holder_row:
            raise HTTPException(status_code=404, detail="Holder not found")

        holder_name = holder_row["holder_name"]

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM trademarks
            WHERE holder_tpe_client_id = %s
        """,
            (tpe_client_id,),
        )
        total_count = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT
                id, application_no, name, final_status,
                nice_class_numbers, application_date, registration_date,
                image_path, bulletin_no, gazette_no,
                attorney_name, attorney_no, registration_no,
                (extracted_goods IS NOT NULL
                    AND extracted_goods != '[]'::jsonb
                    AND extracted_goods != 'null'::jsonb) AS has_extracted_goods
            FROM trademarks
            WHERE holder_tpe_client_id = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
            LIMIT %s OFFSET %s
        """,
            (tpe_client_id, page_size, offset),
        )

        trademarks = []
        for tm in cur.fetchall():
            trademarks.append(
                {
                    "id": str(tm["id"]),
                    "application_no": tm["application_no"],
                    "name": tm["name"],
                    "status": tm["final_status"],
                    "classes": tm["nice_class_numbers"] or [],
                    "application_date": (
                        tm["application_date"].isoformat() if tm["application_date"] else None
                    ),
                    "registration_date": (
                        tm["registration_date"].isoformat() if tm["registration_date"] else None
                    ),
                    "image_path": tm["image_path"],
                    "has_extracted_goods": bool(tm.get("has_extracted_goods", False)),
                    "attorney_name": tm.get("attorney_name"),
                    "attorney_no": tm.get("attorney_no"),
                    "registration_no": tm.get("registration_no"),
                    "bulletin_no": tm.get("bulletin_no"),
                }
            )

    total_pages = (total_count + page_size - 1) // page_size
    return {
        "holder_name": holder_name,
        "holder_tpe_client_id": tpe_client_id,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "trademarks": trademarks,
    }


async def search_holder_portfolio_data(
    query,
    limit,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Return holder autocomplete data."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))
        if not plan_limit_getter(plan["plan_name"], "can_view_holder_portfolio"):
            raise HTTPException(status_code=403, detail="PRO feature")

        cur = db.cursor()
        safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cur.execute(
            """
            SELECT
                holder_name,
                holder_tpe_client_id,
                COUNT(*) as trademark_count
            FROM trademarks
            WHERE holder_name ILIKE %s ESCAPE '\\'
              AND holder_name IS NOT NULL
              AND holder_tpe_client_id IS NOT NULL
            GROUP BY holder_name, holder_tpe_client_id
            ORDER BY trademark_count DESC
            LIMIT %s
        """,
            (f"%{safe_query}%", limit),
        )

        return {
            "query": query,
            "results": [
                {
                    "holder_name": row["holder_name"],
                    "holder_tpe_client_id": row["holder_tpe_client_id"],
                    "trademark_count": row["trademark_count"],
                }
                for row in cur.fetchall()
            ],
        }


async def build_holder_trademarks_csv_stream(
    tpe_client_id,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Build the holder portfolio CSV export response."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))
        if not plan_limit_getter(plan["plan_name"], "can_view_holder_portfolio"):
            raise HTTPException(status_code=403, detail="PRO feature")

        cur = db.cursor()
        cur.execute(
            """
            SELECT DISTINCT holder_name FROM trademarks
            WHERE holder_tpe_client_id = %s LIMIT 1
        """,
            (tpe_client_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Holder not found")
        holder_name = row["holder_name"] or tpe_client_id

        cur.execute(
            """
            SELECT application_no, name, final_status,
                   nice_class_numbers, application_date, registration_date,
                   registration_no, attorney_name, attorney_no,
                   bulletin_no, gazette_no
            FROM trademarks
            WHERE holder_tpe_client_id = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
        """,
            (tpe_client_id,),
        )
        rows = cur.fetchall()

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Marka Adi",
            "Basvuru No",
            "Durum",
            "Siniflar",
            "Basvuru Tarihi",
            "Tescil Tarihi",
            "Tescil No",
            "Vekil",
            "Vekil No",
            "Bulten No",
            "Gazete No",
        ]
    )
    for tm in rows:
        writer.writerow(
            [
                tm.get("name") or "",
                tm.get("application_no") or "",
                tm.get("final_status") or "",
                "; ".join(str(c) for c in (tm.get("nice_class_numbers") or [])),
                tm["application_date"].isoformat() if tm.get("application_date") else "",
                tm["registration_date"].isoformat() if tm.get("registration_date") else "",
                tm.get("registration_no") or "",
                tm.get("attorney_name") or "",
                tm.get("attorney_no") or "",
                tm.get("bulletin_no") or "",
                tm.get("gazette_no") or "",
            ]
        )

    safe_name = "".join(
        c if c.isascii() and (c.isalnum() or c in " _-") else "_" for c in holder_name
    )[:50]
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_portfolio.csv"'},
    )
