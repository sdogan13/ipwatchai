"""Attorney portfolio service helpers used by HTTP route modules."""

import csv
import io

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from database.crud import Database
from utils.event_severity import classify_event_severity


async def get_attorney_trademarks_data(
    attorney_no,
    page,
    page_size,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Return paginated attorney portfolio data."""
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
                    "message": "Vekil portföyü görüntüleme PRO özelliğidir",
                    "upgrade_url": "/pricing",
                },
            )

        offset = (page - 1) * page_size
        cur = db.cursor()

        cur.execute(
            """
            SELECT DISTINCT attorney_name, attorney_no
            FROM trademarks
            WHERE attorney_no = %s
            LIMIT 1
        """,
            (attorney_no,),
        )
        attorney_row = cur.fetchone()

        if not attorney_row:
            raise HTTPException(status_code=404, detail="Attorney not found")

        attorney_name = attorney_row["attorney_name"]

        cur.execute(
            """
            SELECT COUNT(*) as cnt FROM trademarks
            WHERE attorney_no = %s
        """,
            (attorney_no,),
        )
        total_count = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT
                id, application_no, name, final_status,
                nice_class_numbers, application_date, registration_date,
                image_path, bulletin_no,
                holder_name, holder_tpe_client_id,
                holder_changed_at, last_event_type, last_event_date,
                has_restrictions, active_restriction_count,
                (extracted_goods IS NOT NULL
                    AND extracted_goods != '[]'::jsonb
                    AND extracted_goods != 'null'::jsonb) AS has_extracted_goods
            FROM trademarks
            WHERE attorney_no = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
            LIMIT %s OFFSET %s
        """,
            (attorney_no, page_size, offset),
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
                    "holder_name": tm.get("holder_name"),
                    "holder_tpe_client_id": tm.get("holder_tpe_client_id"),
                    "holder_changed_at": (
                        tm["holder_changed_at"].isoformat() if tm.get("holder_changed_at") else None
                    ),
                    "last_event_type": tm.get("last_event_type"),
                    "last_event_date": (
                        tm["last_event_date"].isoformat() if tm.get("last_event_date") else None
                    ),
                    "last_event_severity": classify_event_severity(tm.get("last_event_type")),
                    "has_restrictions": bool(tm.get("has_restrictions", False)),
                    "active_restriction_count": tm.get("active_restriction_count") or 0,
                }
            )

    total_pages = (total_count + page_size - 1) // page_size
    return {
        "attorney_name": attorney_name,
        "attorney_no": attorney_no,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "trademarks": trademarks,
    }


async def search_attorney_portfolio_data(
    query,
    limit,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Return attorney autocomplete data."""
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
            SELECT
                attorney_name,
                attorney_no,
                COUNT(*) as trademark_count
            FROM trademarks
            WHERE (attorney_name ILIKE %s OR attorney_no ILIKE %s)
              AND attorney_name IS NOT NULL
              AND attorney_no IS NOT NULL
            GROUP BY attorney_name, attorney_no
            ORDER BY trademark_count DESC
            LIMIT %s
        """,
            (f"%{query}%", f"%{query}%", limit),
        )

        return {
            "query": query,
            "results": [
                {
                    "attorney_name": row["attorney_name"],
                    "attorney_no": row["attorney_no"],
                    "trademark_count": row["trademark_count"],
                }
                for row in cur.fetchall()
            ],
        }


async def build_attorney_trademarks_csv_stream(
    attorney_no,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Build the attorney portfolio CSV export response."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))
        if not plan_limit_getter(plan["plan_name"], "can_download_portfolio"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": plan["plan_name"],
                    "upgrade_context": "portfolio_download",
                },
            )

        cur = db.cursor()
        cur.execute(
            """
            SELECT DISTINCT attorney_name FROM trademarks
            WHERE attorney_no = %s LIMIT 1
        """,
            (attorney_no,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Attorney not found")
        attorney_name = row["attorney_name"] or attorney_no

        cur.execute(
            """
            SELECT application_no, name, final_status,
                   nice_class_numbers, application_date, registration_date,
                   registration_no, holder_name, holder_tpe_client_id,
                   bulletin_no,
                   holder_changed_at, last_event_type, last_event_date,
                   has_restrictions, active_restriction_count
            FROM trademarks
            WHERE attorney_no = %s
            ORDER BY application_date DESC NULLS LAST, application_no DESC
        """,
            (attorney_no,),
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
            "Sahip",
            "Sahip TPE No",
            "Bulten No",
            "Sahip Degisim Tarihi",
            "Son Olay",
            "Son Olay Tarihi",
            "Aktif Kisitlama",
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
                tm.get("holder_name") or "",
                tm.get("holder_tpe_client_id") or "",
                tm.get("bulletin_no") or "",
                tm["holder_changed_at"].isoformat() if tm.get("holder_changed_at") else "",
                tm.get("last_event_type") or "",
                tm["last_event_date"].isoformat() if tm.get("last_event_date") else "",
                tm.get("active_restriction_count") or 0,
            ]
        )

    safe_name = "".join(
        c if c.isascii() and (c.isalnum() or c in " _-") else "_" for c in attorney_name
    )[:50]
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_portfolio.csv"'},
    )
