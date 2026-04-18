"""Report service helpers used by HTTP route modules."""

import os

from fastapi import HTTPException
from fastapi.responses import FileResponse
from psycopg2.extras import RealDictCursor

from database.crud import Database
from models.schemas import ReportType


REPORT_TYPE_MAP = {
    ReportType.WATCHLIST_SUMMARY: "watchlist_status",
    ReportType.ALERT_DIGEST: "weekly_digest",
    ReportType.RISK_ASSESSMENT: "monthly_summary",
    ReportType.COMPETITOR_ANALYSIS: "full_portfolio",
    ReportType.PORTFOLIO_STATUS: "watchlist_status",
    ReportType.CUSTOM: "monthly_summary",
}


def _serialize_report_row(row, include_detail_fields=False):
    """Convert a report database row to the API response shape."""
    payload = {
        "id": str(row["id"]),
        "organization_id": str(row["organization_id"]) if row["organization_id"] else None,
        "report_type": row["report_type"],
        "title": row["report_name"],
        "status": row["status"],
        "file_path": row["file_path"],
        "file_format": row["file_format"] or "pdf",
        "file_size_bytes": row["file_size_bytes"],
        "generated_at": row["generated_at"].isoformat() if row["generated_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if include_detail_fields:
        payload["download_count"] = row["download_count"] or 0
        payload["error_message"] = row["error_message"]
    return payload


async def generate_report_data(
    request,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    report_eligibility_checker=None,
    generator_factory=None,
    cursor_factory=RealDictCursor,
):
    """Generate a report and return the API payload."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if report_eligibility_checker is None:
        from utils.subscription import check_report_eligibility

        report_eligibility_checker = check_report_eligibility

    if generator_factory is None:
        from reports.generator import ReportGenerator

        def generator_factory(db):
            return ReportGenerator(db=db)

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))
        plan_name = plan["plan_name"]

        eligibility = report_eligibility_checker(
            db, plan_name, str(current_user.organization_id)
        )

        if not eligibility["eligible"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "limit_exceeded",
                    "message": eligibility["reason"],
                    "reports_used": eligibility["reports_used"],
                    "reports_limit": eligibility["reports_limit"],
                },
            )

        generator_type = REPORT_TYPE_MAP.get(request.report_type, "weekly_digest")

        parameters = {}
        if request.period_start:
            parameters["date_start"] = request.period_start.isoformat()
        if request.period_end:
            parameters["date_end"] = request.period_end.isoformat()
        if request.watchlist_ids and len(request.watchlist_ids) > 0:
            parameters["watchlist_id"] = str(request.watchlist_ids[0])

        try:
            generator = generator_factory(db)

            if request.file_format == "xlsx":
                result_path = generator.generate_excel_report(
                    user_id=current_user.id,
                    report_type=generator_type,
                    parameters=parameters,
                )
                result = {
                    "report_id": None,
                    "status": "completed",
                    "file_path": result_path,
                }
            else:
                result = generator.generate_report(
                    user_id=current_user.id,
                    report_type=generator_type,
                    parameters=parameters,
                )

            if request.title and result.get("report_id"):
                cur = db.cursor()
                cur.execute(
                    "UPDATE reports SET report_name = %s WHERE id = %s",
                    (request.title, result["report_id"]),
                )
                db.commit()

            report_id = result.get("report_id")
            if report_id:
                cur = db.cursor(cursor_factory=cursor_factory)
                cur.execute("SELECT * FROM reports WHERE id = %s", (str(report_id),))
                report_row = cur.fetchone()
                if report_row:
                    return _serialize_report_row(report_row)

            return {
                "report_id": result.get("report_id"),
                "status": result.get("status", "completed"),
                "file_path": result.get("file_path"),
                "message": "Rapor olusturuldu",
            }

        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"error": "generation_failed", "message": str(exc)},
            ) from exc


async def list_reports_data(
    page,
    page_size,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    report_eligibility_checker=None,
    cursor_factory=RealDictCursor,
):
    """List organization reports with usage metadata."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if report_eligibility_checker is None:
        from utils.subscription import check_report_eligibility

        report_eligibility_checker = check_report_eligibility

    with database_factory() as db:
        cur = db.cursor(cursor_factory=cursor_factory)
        org_id = str(current_user.organization_id)

        cur.execute(
            "SELECT COUNT(*) as cnt FROM reports WHERE organization_id = %s",
            (org_id,),
        )
        total = cur.fetchone()["cnt"]

        offset = (page - 1) * page_size
        cur.execute(
            """
            SELECT * FROM reports
            WHERE organization_id = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """,
            (org_id, page_size, offset),
        )

        rows = cur.fetchall()
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        plan = user_plan_getter(db, str(current_user.id))
        eligibility = report_eligibility_checker(db, plan["plan_name"], org_id)

        return {
            "reports": [_serialize_report_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "usage": {
                "reports_used": eligibility["reports_used"],
                "reports_limit": eligibility["reports_limit"],
                "can_export": eligibility["can_export"],
            },
        }


async def get_report_data(
    report_id,
    current_user,
    database_factory=Database,
    cursor_factory=RealDictCursor,
):
    """Fetch a single report payload for the current organization."""
    with database_factory() as db:
        cur = db.cursor(cursor_factory=cursor_factory)
        cur.execute("SELECT * FROM reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Rapor bulunamadi")

        if str(row["organization_id"]) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Bu rapora erisiminiz yok")

        return _serialize_report_row(row, include_detail_fields=True)


async def build_report_download_response(
    report_id,
    current_user,
    database_factory=Database,
    user_plan_getter=None,
    plan_limit_getter=None,
    file_exists=None,
    file_response_factory=FileResponse,
    cursor_factory=RealDictCursor,
):
    """Build the downloadable report response for the current organization."""
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    if file_exists is None:
        file_exists = os.path.isfile

    with database_factory() as db:
        cur = db.cursor(cursor_factory=cursor_factory)
        cur.execute("SELECT * FROM reports WHERE id = %s", (report_id,))
        row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Rapor bulunamadi")

        if str(row["organization_id"]) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Bu rapora erisiminiz yok")

        plan = user_plan_getter(db, str(current_user.id))
        can_export = plan_limit_getter(plan["plan_name"], "can_export_reports")
        if not can_export:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "export_not_allowed",
                    "message": "Rapor indirme icin planinizi yukseltin",
                },
            )

        if row["status"] != "completed":
            raise HTTPException(
                status_code=400,
                detail="Rapor henuz tamamlanmadi (durum: " + (row["status"] or "unknown") + ")",
            )

        file_path = row["file_path"]
        if not file_path or not file_exists(file_path):
            raise HTTPException(status_code=404, detail="Rapor dosyasi bulunamadi")

        cur.execute(
            """
            UPDATE reports
            SET download_count = COALESCE(download_count, 0) + 1,
                last_downloaded_at = NOW()
            WHERE id = %s
        """,
            (report_id,),
        )
        db.commit()

        file_format = row["file_format"] or "pdf"
        media_types = {
            "pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "csv": "text/csv",
        }
        media_type = media_types.get(file_format, "application/octet-stream")

        report_name = row["report_name"] or "rapor"
        filename = f"{report_name}.{file_format}"

        return file_response_factory(
            path=file_path,
            media_type=media_type,
            filename=filename,
        )
