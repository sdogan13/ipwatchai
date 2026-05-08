"""Service helpers for lead and renewal lead flows."""

import csv
import io
from datetime import datetime

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from database.crud import Database
from utils.subscription import get_plan_limit, get_user_plan


LEADS_PER_PAGE = 20
MAX_EXPORT_LEADS = 500
RENEWAL_ACTIVE_STATUSES = ("Tescil Edildi", "Devredildi")


def _as_day_count(value):
    """Normalize database day-difference values to integers."""
    if value is None:
        return None
    if hasattr(value, "days"):
        return value.days
    return int(value)


def _get_lead_access(
    db,
    user_id: str,
    *,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
) -> dict:
    """Check if a user can access leads and how many views remain today."""
    plan = user_plan_getter(db, user_id)
    plan_name = plan["plan_name"]
    daily_limit = plan_limit_getter(plan_name, "daily_lead_views")

    if daily_limit == 0:
        return {
            "can_access": False,
            "plan": plan_name,
            "daily_limit": 0,
            "used_today": 0,
            "remaining": 0,
        }

    cur = db.cursor()
    cur.execute(
        """
        SELECT COUNT(*) as cnt
        FROM lead_access_log
        WHERE user_id = %s
          AND action = 'viewed'
          AND created_at::date = CURRENT_DATE
    """,
        (user_id,),
    )
    used_today = cur.fetchone()["cnt"]

    if daily_limit == -1:
        remaining = -1
    else:
        remaining = max(0, daily_limit - used_today)

    return {
        "can_access": True,
        "plan": plan_name,
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining": remaining,
    }


def _require_lead_access(
    db,
    user_id: str,
    *,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
) -> dict:
    """Raise when lead access is not allowed for the current user."""
    access = _get_lead_access(
        db,
        user_id,
        user_plan_getter=user_plan_getter,
        plan_limit_getter=plan_limit_getter,
    )

    if not access["can_access"]:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "upgrade_required",
                "message": "Lead erisimi icin Professional veya Enterprise plan gereklidir.",
                "current_plan": access["plan"],
            },
        )

    if access["remaining"] == 0 and access["daily_limit"] != -1:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_limit_exceeded",
                "message": f"Gunluk {access['daily_limit']} lead limitinize ulastiniz.",
                "daily_limit": access["daily_limit"],
                "used_today": access["used_today"],
            },
        )

    return access


def _log_lead_access(db, user_id: str, org_id: str, conflict_id: str, action: str):
    """Log a lead-related action for auditing and usage tracking."""
    cur = db.cursor()
    cur.execute(
        """
        INSERT INTO lead_access_log (user_id, organization_id, conflict_id, action)
        VALUES (%s, %s, %s::uuid, %s)
    """,
        (str(user_id), str(org_id) if org_id else None, conflict_id, action),
    )
    db.commit()


def _urgency_case_sql():
    """SQL CASE expression for opposition urgency."""
    return """
        CASE
            WHEN (uc.opposition_deadline - CURRENT_DATE) <= 7 THEN 'critical'
            WHEN (uc.opposition_deadline - CURRENT_DATE) <= 14 THEN 'urgent'
            WHEN (uc.opposition_deadline - CURRENT_DATE) <= 30 THEN 'soon'
            ELSE 'normal'
        END
    """


def _shape_only_name_sql(column_sql: str) -> str:
    """Return SQL that treats stale sekil-only labels as logo-only/no-name marks."""
    return (
        "NULLIF("
        "regexp_replace("
        f"regexp_replace(lower(coalesce({column_sql}, '')), '(şekil|sekil)', '', 'g'), "
        "'[^[:alnum:]]+', '', 'g'"
        "), "
        "''"
        ") IS NULL"
    )


def _shape_only_conflict_exclusion_sql(alias: str = "uc") -> str:
    """Exclude derived Opposition Radar rows created from logo-only placeholder names."""
    return f"""
              AND NOT (
                  {_shape_only_name_sql(f"{alias}.new_mark_name")}
                  OR {_shape_only_name_sql(f"{alias}.existing_mark_name")}
              )
    """


def _renewal_urgency_sql():
    """SQL CASE expression for renewal urgency."""
    return """
        CASE
            WHEN t.expiry_date < CURRENT_DATE THEN 'grace_period'
            WHEN (t.expiry_date - CURRENT_DATE) <= 90 THEN 'critical'
            WHEN (t.expiry_date - CURRENT_DATE) <= 180 THEN 'urgent'
            WHEN (t.expiry_date - CURRENT_DATE) <= 365 THEN 'upcoming'
            ELSE 'normal'
        END
    """


def _serialize_lead_row(row):
    """Map a raw lead row to the response payload."""
    return {
        "id": str(row["id"]),
        "new_mark_name": row["new_mark_name"],
        "new_mark_app_no": row["new_mark_app_no"],
        "new_mark_holder_name": row["new_mark_holder_name"],
        "new_mark_nice_classes": row["new_mark_nice_classes"],
        "new_mark_image": row.get("new_mark_image"),
        "existing_mark_name": row["existing_mark_name"],
        "existing_mark_app_no": row["existing_mark_app_no"],
        "existing_mark_holder_name": row["existing_mark_holder_name"],
        "existing_mark_nice_classes": row["existing_mark_nice_classes"],
        "existing_mark_image": row.get("existing_mark_image"),
        "similarity_score": row["similarity_score"],
        "text_similarity": row.get("text_similarity"),
        "semantic_similarity": row.get("semantic_similarity"),
        "visual_similarity": row.get("visual_similarity"),
        "translation_similarity": row.get("translation_similarity"),
        "risk_level": row["risk_level"],
        "conflict_type": row["conflict_type"],
        "overlapping_classes": row["overlapping_classes"],
        "conflict_reasons": row["conflict_reasons"],
        "bulletin_no": row["bulletin_no"],
        "bulletin_date": row["bulletin_date"],
        "opposition_deadline": row["opposition_deadline"],
        "days_until_deadline": _as_day_count(row["days_until_deadline"]),
        "urgency_level": row["urgency_level"],
        "new_mark_application_date": row.get("new_mark_application_date"),
        "existing_mark_application_date": row.get("existing_mark_application_date"),
        "new_mark_has_extracted_goods": bool(row.get("new_mark_has_extracted_goods", False)),
        "existing_mark_has_extracted_goods": bool(
            row.get("existing_mark_has_extracted_goods", False)
        ),
        "lead_status": row["lead_status"],
        "created_at": row["created_at"],
    }


def _serialize_renewal_row(row):
    """Map a raw renewal row to the response payload."""
    days_until_expiry = _as_day_count(row["days_until_expiry"])
    grace_days_remaining = None
    if days_until_expiry is not None and days_until_expiry < 0:
        grace_days_remaining = 183 + days_until_expiry

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "application_no": row["application_no"],
        "nice_classes": row["nice_class_numbers"],
        "image_path": row.get("image_path"),
        "status": row["final_status"],
        "expiry_date": str(row["expiry_date"]) if row["expiry_date"] else None,
        "days_until_expiry": days_until_expiry,
        "application_date": (
            str(row["application_date"]) if row.get("application_date") else None
        ),
        "registration_no": row.get("registration_no"),
        "holder_name": row.get("holder_name"),
        "attorney_name": row.get("attorney_name"),
        "attorney_no": row.get("attorney_no"),
        "holder_tpe_client_id": row.get("holder_tpe_client_id"),
        "urgency_level": row["urgency_level"],
        "grace_days_remaining": grace_days_remaining,
    }


async def get_lead_feed_data(
    *,
    urgency,
    nice_class,
    min_score,
    status,
    search,
    page,
    limit,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return paginated opposition leads."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        urgency_sql = _urgency_case_sql()
        query = f"""
            SELECT
                uc.id,
                uc.new_mark_name, uc.new_mark_app_no,
                uc.new_mark_holder_name, uc.new_mark_nice_classes,
                uc.existing_mark_name, uc.existing_mark_app_no,
                uc.existing_mark_holder_name, uc.existing_mark_nice_classes,
                uc.similarity_score,
                uc.text_similarity, uc.semantic_similarity, uc.visual_similarity, uc.translation_similarity,
                uc.risk_level, uc.conflict_type,
                uc.overlapping_classes, uc.conflict_reasons,
                uc.bulletin_no, uc.bulletin_date,
                uc.opposition_deadline, (uc.opposition_deadline - CURRENT_DATE) as days_until_deadline,
                uc.lead_status, uc.created_at,
                new_tm.image_path as new_mark_image,
                exist_tm.image_path as existing_mark_image,
                new_tm.application_date as new_mark_application_date,
                exist_tm.application_date as existing_mark_application_date,
                {urgency_sql} as urgency_level,
                (new_tm.extracted_goods IS NOT NULL
                    AND new_tm.extracted_goods != '[]'::jsonb
                    AND new_tm.extracted_goods != 'null'::jsonb) AS new_mark_has_extracted_goods,
                (exist_tm.extracted_goods IS NOT NULL
                    AND exist_tm.extracted_goods != '[]'::jsonb
                    AND exist_tm.extracted_goods != 'null'::jsonb) AS existing_mark_has_extracted_goods
            FROM universal_conflicts uc
            LEFT JOIN trademarks new_tm ON uc.new_mark_id = new_tm.id
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.opposition_deadline >= CURRENT_DATE
              AND uc.similarity_score >= %s
              AND uc.overlapping_classes IS NOT NULL
              AND array_length(uc.overlapping_classes, 1) > 0
              AND (exist_tm.expiry_date IS NULL
                   OR exist_tm.expiry_date >= CURRENT_DATE - INTERVAL '6 months')
              {_shape_only_conflict_exclusion_sql()}
        """
        params = [min_score]

        query += """
              AND NOT EXISTS (
                  SELECT 1 FROM organizations org
                  WHERE org.id = uc.existing_mark_holder_id
              )
        """
        query += """
              AND NOT EXISTS (
                  SELECT 1 FROM watchlist_mt wl
                  WHERE wl.organization_id = %s
                    AND wl.customer_application_no = uc.existing_mark_app_no
                    AND wl.is_active = true
              )
        """
        params.append(str(current_user.organization_id))

        if urgency and urgency != "all":
            if urgency == "critical":
                query += " AND (uc.opposition_deadline - CURRENT_DATE) <= 7"
            elif urgency == "urgent":
                query += " AND (uc.opposition_deadline - CURRENT_DATE) <= 14"
            elif urgency == "soon":
                query += " AND (uc.opposition_deadline - CURRENT_DATE) <= 30"

        if nice_class is not None:
            query += " AND %s = ANY(uc.overlapping_classes)"
            params.append(nice_class)

        if status and status != "all":
            query += " AND uc.lead_status = %s"
            params.append(status)

        if search and search.strip():
            safe_search = (
                search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query += """ AND (
                uc.new_mark_name ILIKE %s ESCAPE '\\' OR
                uc.existing_mark_name ILIKE %s ESCAPE '\\' OR
                uc.new_mark_holder_name ILIKE %s ESCAPE '\\' OR
                uc.existing_mark_holder_name ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern, like_pattern, like_pattern])

        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()["cnt"]

        query += " ORDER BY uc.opposition_deadline ASC, uc.similarity_score DESC"
        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [_serialize_lead_row(row) for row in rows],
        }


async def get_lead_stats_data(
    *,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return lead dashboard statistics."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        cur.execute(
            f"""
            SELECT
                COUNT(*) as total_leads,
                COUNT(*) FILTER (WHERE (uc.opposition_deadline - CURRENT_DATE) <= 7) as critical_leads,
                COUNT(*) FILTER (WHERE (uc.opposition_deadline - CURRENT_DATE) > 7 AND (uc.opposition_deadline - CURRENT_DATE) <= 14) as urgent_leads,
                COUNT(*) FILTER (WHERE (uc.opposition_deadline - CURRENT_DATE) > 14 AND (uc.opposition_deadline - CURRENT_DATE) <= 30) as upcoming_leads,
                COUNT(*) FILTER (WHERE uc.lead_status = 'new') as new_leads,
                COUNT(*) FILTER (WHERE uc.lead_status = 'viewed') as viewed_leads,
                COUNT(*) FILTER (WHERE uc.lead_status = 'contacted') as contacted_leads,
                COUNT(*) FILTER (WHERE uc.lead_status = 'converted') as converted_leads,
                ROUND(AVG(uc.similarity_score)::numeric, 3) as avg_similarity,
                MAX(uc.created_at) as last_scan_at
            FROM universal_conflicts uc
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.opposition_deadline >= CURRENT_DATE
              AND uc.overlapping_classes IS NOT NULL
              AND array_length(uc.overlapping_classes, 1) > 0
              AND (exist_tm.expiry_date IS NULL
                   OR exist_tm.expiry_date >= CURRENT_DATE - INTERVAL '6 months')
              {_shape_only_conflict_exclusion_sql()}
        """
        )
        stats = cur.fetchone()
        return {
            "total_leads": stats["total_leads"] or 0,
            "critical_leads": stats["critical_leads"] or 0,
            "urgent_leads": stats["urgent_leads"] or 0,
            "upcoming_leads": stats["upcoming_leads"] or 0,
            "new_leads": stats["new_leads"] or 0,
            "viewed_leads": stats["viewed_leads"] or 0,
            "contacted_leads": stats["contacted_leads"] or 0,
            "converted_leads": stats["converted_leads"] or 0,
            "avg_similarity": float(stats["avg_similarity"]) if stats["avg_similarity"] else None,
            "last_scan_at": stats["last_scan_at"],
        }


async def get_lead_credits_data(
    *,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return the authenticated user's lead-access allowance."""
    with db_factory() as db:
        access = _get_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )
        return {
            "can_access": access["can_access"],
            "plan": access["plan"],
            "daily_limit": access["daily_limit"],
            "used_today": access["used_today"],
            "remaining": access["remaining"] if access["daily_limit"] != -1 else "unlimited",
        }


async def export_leads_csv_data(
    *,
    urgency,
    nice_class,
    min_score,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
    now_getter=datetime.now,
    streaming_response_factory=StreamingResponse,
):
    """Export opposition leads as CSV."""
    with db_factory() as db:
        access = _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        if not plan_limit_getter(access["plan"], "can_export_csv_leads"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": access["plan"],
                    "upgrade_context": "csv_export",
                },
            )

        cur = db.cursor()
        query = f"""
            SELECT
                uc.new_mark_name, uc.new_mark_app_no, uc.new_mark_holder_name,
                uc.existing_mark_name, uc.existing_mark_app_no, uc.existing_mark_holder_name,
                uc.similarity_score, uc.risk_level, uc.conflict_type,
                uc.opposition_deadline, (uc.opposition_deadline - CURRENT_DATE) as days_until_deadline
            FROM universal_conflicts uc
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.opposition_deadline >= CURRENT_DATE
              AND uc.similarity_score >= %s
              AND uc.lead_status NOT IN ('dismissed', 'converted')
              AND uc.overlapping_classes IS NOT NULL
              AND array_length(uc.overlapping_classes, 1) > 0
              AND (exist_tm.expiry_date IS NULL
                   OR exist_tm.expiry_date >= CURRENT_DATE - INTERVAL '6 months')
              {_shape_only_conflict_exclusion_sql()}
        """
        params = [min_score]

        if urgency == "critical":
            query += " AND (uc.opposition_deadline - CURRENT_DATE) <= 7"
        elif urgency == "urgent":
            query += " AND (uc.opposition_deadline - CURRENT_DATE) <= 14"

        if nice_class is not None:
            query += " AND %s = ANY(uc.overlapping_classes)"
            params.append(nice_class)

        query += " ORDER BY uc.opposition_deadline ASC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        rows = cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Yeni Marka",
                "Yeni Basvuru No",
                "Yeni Basvuru Sahibi",
                "Mevcut Marka",
                "Mevcut Basvuru No",
                "Potansiyel Musteri",
                "Benzerlik",
                "Risk",
                "Tip",
                "Itiraz Suresi",
                "Kalan Gun",
            ]
        )

        for row in rows:
            writer.writerow(
                [
                    row["new_mark_name"],
                    row["new_mark_app_no"],
                    row["new_mark_holder_name"],
                    row["existing_mark_name"],
                    row["existing_mark_app_no"],
                    row["existing_mark_holder_name"],
                    f"{row['similarity_score']:.1%}",
                    row["risk_level"],
                    row["conflict_type"],
                    row["opposition_deadline"],
                    _as_day_count(row["days_until_deadline"]),
                ]
            )

        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            "00000000-0000-0000-0000-000000000000",
            "exported",
        )

        output.seek(0)
        filename = f"leads_{now_getter().strftime('%Y%m%d')}.csv"
        return streaming_response_factory(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


async def get_lead_detail_data(
    *,
    lead_id,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return a single lead payload and mark new leads as viewed."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        urgency_sql = _urgency_case_sql()
        cur.execute(
            f"""
            SELECT
                uc.id, uc.new_mark_id, uc.new_mark_name, uc.new_mark_app_no,
                uc.new_mark_holder_name, uc.new_mark_nice_classes,
                uc.existing_mark_id, uc.existing_mark_name, uc.existing_mark_app_no,
                uc.existing_mark_holder_id, uc.existing_mark_holder_name, uc.existing_mark_nice_classes,
                uc.similarity_score, uc.text_similarity, uc.semantic_similarity,
                uc.visual_similarity, uc.translation_similarity,
                uc.risk_level, uc.conflict_type,
                uc.overlapping_classes, uc.conflict_reasons,
                uc.bulletin_no, uc.bulletin_date,
                uc.opposition_deadline,
                (uc.opposition_deadline - CURRENT_DATE) as days_until_deadline,
                uc.lead_status, uc.viewed_by, uc.contacted_at, uc.notes,
                uc.created_at, uc.updated_at,
                new_tm.image_path as new_mark_image,
                exist_tm.image_path as existing_mark_image,
                new_tm.application_date as new_mark_application_date,
                exist_tm.application_date as existing_mark_application_date,
                {urgency_sql} as urgency_level,
                (new_tm.extracted_goods IS NOT NULL
                    AND new_tm.extracted_goods != '[]'::jsonb
                    AND new_tm.extracted_goods != 'null'::jsonb) AS new_mark_has_extracted_goods,
                (exist_tm.extracted_goods IS NOT NULL
                    AND exist_tm.extracted_goods != '[]'::jsonb
                    AND exist_tm.extracted_goods != 'null'::jsonb) AS existing_mark_has_extracted_goods
            FROM universal_conflicts uc
            LEFT JOIN trademarks new_tm ON uc.new_mark_id = new_tm.id
            LEFT JOIN trademarks exist_tm ON uc.existing_mark_id = exist_tm.id
            WHERE uc.id = %s::uuid
              {_shape_only_conflict_exclusion_sql()}
        """,
            (lead_id,),
        )

        lead = cur.fetchone()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        if lead["lead_status"] == "new":
            cur.execute(
                """
                UPDATE universal_conflicts
                SET lead_status = 'viewed',
                    viewed_by = array_append(COALESCE(viewed_by, '{}'), %s::uuid)
                WHERE id = %s::uuid
            """,
                (str(current_user.id), lead_id),
            )
            db.commit()

        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            lead_id,
            "viewed",
        )
        return dict(lead)


async def mark_lead_contacted_data(
    *,
    lead_id,
    notes,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Mark a lead as contacted."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )
        cur = db.cursor()
        cur.execute(
            """
            UPDATE universal_conflicts
            SET lead_status = 'contacted',
                contacted_at = NOW(),
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """,
            (notes or "", lead_id),
        )

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()
        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            lead_id,
            "contacted",
        )
        return {
            "success": True,
            "message": "Lead 'iletisime gecildi' olarak isaretlendi.",
            "lead_id": lead_id,
            "new_status": "contacted",
        }


async def mark_lead_converted_data(
    *,
    lead_id,
    notes,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Mark a lead as converted."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )
        cur = db.cursor()
        cur.execute(
            """
            UPDATE universal_conflicts
            SET lead_status = 'converted',
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """,
            (notes or "", lead_id),
        )

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()
        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            lead_id,
            "converted",
        )
        return {
            "success": True,
            "message": "Lead 'musteri oldu' olarak isaretlendi.",
            "lead_id": lead_id,
            "new_status": "converted",
        }


async def dismiss_lead_data(
    *,
    lead_id,
    reason,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Dismiss a lead."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )
        cur = db.cursor()
        cur.execute(
            """
            UPDATE universal_conflicts
            SET lead_status = 'dismissed',
                notes = COALESCE(notes || E'\\n', '') || %s
            WHERE id = %s::uuid
            RETURNING id
        """,
            (f"Dismissed: {reason}" if reason else "Dismissed", lead_id),
        )

        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Lead bulunamadi.")

        db.commit()
        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            lead_id,
            "dismissed",
        )
        return {
            "success": True,
            "message": "Lead reddedildi.",
            "lead_id": lead_id,
            "new_status": "dismissed",
        }


async def get_renewal_stats_data(
    *,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return renewal lead statistics."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE t.expiry_date < CURRENT_DATE) as grace_period,
                COUNT(*) FILTER (WHERE t.expiry_date >= CURRENT_DATE
                    AND (t.expiry_date - CURRENT_DATE) <= 90) as critical,
                COUNT(*) FILTER (WHERE (t.expiry_date - CURRENT_DATE) > 90
                    AND (t.expiry_date - CURRENT_DATE) <= 180) as urgent,
                COUNT(*) FILTER (WHERE (t.expiry_date - CURRENT_DATE) > 180
                    AND (t.expiry_date - CURRENT_DATE) <= 365) as upcoming
            FROM trademarks t
            WHERE t.final_status IN %s
              AND t.expiry_date IS NOT NULL
              AND t.expiry_date BETWEEN CURRENT_DATE - INTERVAL '6 months'
                                     AND CURRENT_DATE + INTERVAL '12 months'
        """,
            (RENEWAL_ACTIVE_STATUSES,),
        )
        stats = cur.fetchone()
        return {
            "total": stats["total"] or 0,
            "grace_period": stats["grace_period"] or 0,
            "critical": stats["critical"] or 0,
            "urgent": stats["urgent"] or 0,
            "upcoming": stats["upcoming"] or 0,
        }


async def get_renewal_feed_data(
    *,
    urgency,
    nice_class,
    search,
    page,
    limit,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return paginated renewal leads."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        urgency_sql = _renewal_urgency_sql()
        query = f"""
            SELECT
                t.id,
                t.name,
                t.application_no,
                t.nice_class_numbers,
                t.image_path,
                t.final_status,
                t.expiry_date,
                (t.expiry_date - CURRENT_DATE) as days_until_expiry,
                t.application_date,
                t.registration_no,
                h.name as holder_name,
                t.holder_tpe_client_id,
                t.attorney_name,
                t.attorney_no,
                {urgency_sql} as urgency_level,
                CASE
                    WHEN t.expiry_date < CURRENT_DATE
                    THEN CURRENT_DATE + INTERVAL '6 months' - t.expiry_date
                    ELSE NULL
                END as grace_days_remaining
            FROM trademarks t
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE t.final_status IN %s
              AND t.expiry_date IS NOT NULL
              AND t.expiry_date BETWEEN CURRENT_DATE - INTERVAL '6 months'
                                     AND CURRENT_DATE + INTERVAL '12 months'
        """
        params = [RENEWAL_ACTIVE_STATUSES]

        if urgency and urgency != "all":
            if urgency == "grace_period":
                query += " AND t.expiry_date < CURRENT_DATE"
            elif urgency == "critical":
                query += " AND t.expiry_date >= CURRENT_DATE AND (t.expiry_date - CURRENT_DATE) <= 90"
            elif urgency == "urgent":
                query += " AND (t.expiry_date - CURRENT_DATE) > 90 AND (t.expiry_date - CURRENT_DATE) <= 180"
            elif urgency == "upcoming":
                query += " AND (t.expiry_date - CURRENT_DATE) > 180 AND (t.expiry_date - CURRENT_DATE) <= 365"

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        if search and search.strip():
            safe_search = (
                search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query += """ AND (
                t.name ILIKE %s ESCAPE '\\' OR
                h.name ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern])

        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()["cnt"]

        query += " ORDER BY t.expiry_date ASC"
        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [_serialize_renewal_row(row) for row in rows],
        }


async def export_renewals_csv_data(
    *,
    urgency,
    nice_class,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
    now_getter=datetime.now,
    streaming_response_factory=StreamingResponse,
):
    """Export renewal leads as CSV."""
    with db_factory() as db:
        access = _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        if not plan_limit_getter(access["plan"], "can_export_csv_leads"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": access["plan"],
                    "upgrade_context": "csv_export",
                },
            )

        cur = db.cursor()
        query = """
            SELECT
                t.name, t.application_no, t.registration_no,
                h.name as holder_name, t.attorney_name,
                t.nice_class_numbers, t.final_status,
                t.expiry_date, (t.expiry_date - CURRENT_DATE) as days_until_expiry
            FROM trademarks t
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE t.final_status IN %s
              AND t.expiry_date IS NOT NULL
              AND t.expiry_date BETWEEN CURRENT_DATE - INTERVAL '6 months'
                                     AND CURRENT_DATE + INTERVAL '12 months'
        """
        params = [RENEWAL_ACTIVE_STATUSES]

        if urgency == "grace_period":
            query += " AND t.expiry_date < CURRENT_DATE"
        elif urgency == "critical":
            query += " AND t.expiry_date >= CURRENT_DATE AND (t.expiry_date - CURRENT_DATE) <= 90"
        elif urgency == "urgent":
            query += " AND (t.expiry_date - CURRENT_DATE) > 90 AND (t.expiry_date - CURRENT_DATE) <= 180"

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        query += " ORDER BY t.expiry_date ASC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        rows = cur.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Marka",
                "Basvuru No",
                "Tescil No",
                "Sahip",
                "Vekil",
                "Siniflar",
                "Durum",
                "Bitis Tarihi",
                "Kalan Gun",
            ]
        )

        for row in rows:
            classes_str = ",".join(str(item) for item in (row["nice_class_numbers"] or []))
            writer.writerow(
                [
                    row["name"],
                    row["application_no"],
                    row["registration_no"],
                    row["holder_name"],
                    row["attorney_name"],
                    classes_str,
                    row["final_status"],
                    row["expiry_date"],
                    _as_day_count(row["days_until_expiry"]),
                ]
            )

        _log_lead_access(
            db,
            str(current_user.id),
            str(current_user.organization_id),
            "00000000-0000-0000-0000-000000000000",
            "renewal_exported",
        )

        output.seek(0)
        filename = f"renewals_{now_getter().strftime('%Y%m%d')}.csv"
        return streaming_response_factory(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


CANCELLATION_RECENT_MONTHS_DEFAULT = 12


def _serialize_cancellation_row(row):
    """Map a raw cancellation event row to the response payload."""
    cancellation_date = row.get("cancellation_date")
    days_since = _as_day_count(row.get("days_since_cancellation"))
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "application_no": row["application_no"],
        "registration_no": row.get("registration_no"),
        "nice_classes": row.get("nice_class_numbers") or [],
        "image_path": row.get("image_path"),
        "status": row.get("final_status"),
        "application_date": (
            str(row["application_date"]) if row.get("application_date") else None
        ),
        "cancellation_date": str(cancellation_date) if cancellation_date else None,
        "cancellation_bulletin_no": row.get("cancellation_bulletin_no"),
        "cancellation_subtype": row.get("cancellation_subtype"),
        "days_since_cancellation": days_since,
        "holder_name": row.get("holder_name"),
        "holder_tpe_client_id": row.get("holder_tpe_client_id"),
        "attorney_name": row.get("attorney_name"),
        "attorney_no": row.get("attorney_no"),
    }


async def get_cancellation_feed_data(
    *,
    nice_class,
    search,
    page,
    limit,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return paginated cancellation leads (recently-cancelled marks → reapply candidates)."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        query = f"""
            SELECT
                t.id,
                t.name,
                t.application_no,
                t.registration_no,
                t.nice_class_numbers,
                t.image_path,
                t.final_status,
                t.application_date,
                te.bulletin_no AS cancellation_bulletin_no,
                te.bulletin_date AS cancellation_date,
                te.event_subtype AS cancellation_subtype,
                (CURRENT_DATE - te.bulletin_date) AS days_since_cancellation,
                h.name AS holder_name,
                t.holder_tpe_client_id,
                t.attorney_name,
                t.attorney_no
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = 'cancellation'
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{CANCELLATION_RECENT_MONTHS_DEFAULT} months'
        """
        params = []

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        if search and search.strip():
            safe_search = (
                search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query += """ AND (
                t.name ILIKE %s ESCAPE '\\' OR
                h.name ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern])

        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()["cnt"]

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC"
        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [_serialize_cancellation_row(row) for row in rows],
        }


async def export_cancellations_csv_data(
    *,
    nice_class,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
    now_getter=datetime.now,
    streaming_response_factory=StreamingResponse,
):
    """Export cancellation leads as CSV."""
    with db_factory() as db:
        access = _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        if not plan_limit_getter(access["plan"], "can_export_csv_leads"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": access["plan"],
                    "upgrade_context": "csv_export",
                },
            )

        cur = db.cursor()
        query = f"""
            SELECT
                t.name, t.application_no, t.registration_no,
                h.name AS holder_name, t.attorney_name, t.attorney_no,
                t.nice_class_numbers, t.final_status,
                te.bulletin_no AS cancellation_bulletin_no,
                te.bulletin_date AS cancellation_date,
                te.event_subtype AS cancellation_subtype,
                (CURRENT_DATE - te.bulletin_date) AS days_since_cancellation
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = 'cancellation'
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{CANCELLATION_RECENT_MONTHS_DEFAULT} months'
        """
        params = []

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        rows = cur.fetchall()

        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Marka",
                "Basvuru No",
                "Tescil No",
                "Sahip",
                "Vekil",
                "Vekil No",
                "Siniflar",
                "Durum",
                "Iptal Tarihi",
                "Iptal Bulten No",
                "Iptal Alt Tipi",
                "Iptalden Sonra Gun",
            ]
        )

        for row in rows:
            classes_str = ",".join(str(item) for item in (row["nice_class_numbers"] or []))
            writer.writerow(
                [
                    row.get("name") or "",
                    row.get("application_no") or "",
                    row.get("registration_no") or "",
                    row.get("holder_name") or "",
                    row.get("attorney_name") or "",
                    row.get("attorney_no") or "",
                    classes_str,
                    row.get("final_status") or "",
                    row.get("cancellation_date") or "",
                    row.get("cancellation_bulletin_no") or "",
                    row.get("cancellation_subtype") or "",
                    _as_day_count(row.get("days_since_cancellation")) or "",
                ]
            )

        # NOTE: skipping _log_lead_access here — that helper requires a real
        # universal_conflicts.id and there isn't one for an event-driven export.
        # The shared placeholder UUID ('00000000-...') the renewal export uses
        # violates the lead_access_log_conflict_id_fkey constraint and 500s.

        output.seek(0)
        filename = f"cancellations_{now_getter().strftime('%Y%m%d')}.csv"
        return streaming_response_factory(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


TRANSFER_RECENT_MONTHS_DEFAULT = 12
TRANSFER_EVENT_TYPES = ("transfer", "merger", "partial_transfer")


def _serialize_transfer_row(row):
    """Map a raw transfer/merger event row to the response payload."""
    transfer_date = row.get("transfer_date")
    days_since = _as_day_count(row.get("days_since_transfer"))
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "application_no": row["application_no"],
        "registration_no": row.get("registration_no"),
        "nice_classes": row.get("nice_class_numbers") or [],
        "image_path": row.get("image_path"),
        "status": row.get("final_status"),
        "application_date": (
            str(row["application_date"]) if row.get("application_date") else None
        ),
        "transfer_date": str(transfer_date) if transfer_date else None,
        "transfer_bulletin_no": row.get("transfer_bulletin_no"),
        "transfer_event_type": row.get("event_type"),
        "previous_holder_name": row.get("previous_holder_name"),
        "new_holder_name": row.get("new_holder_name") or row.get("holder_name"),
        "days_since_transfer": days_since,
        "holder_name": row.get("holder_name"),
        "holder_tpe_client_id": row.get("holder_tpe_client_id"),
        "attorney_name": row.get("attorney_name"),
        "attorney_no": row.get("attorney_no"),
    }


async def get_transfer_feed_data(
    *,
    event_type,
    nice_class,
    search,
    page,
    limit,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return paginated M&A transfer leads (recent transfer / merger / partial_transfer events)."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        query = f"""
            SELECT
                t.id,
                t.name,
                t.application_no,
                t.registration_no,
                t.nice_class_numbers,
                t.image_path,
                t.final_status,
                t.application_date,
                te.event_type,
                te.bulletin_no AS transfer_bulletin_no,
                te.bulletin_date AS transfer_date,
                te.old_value AS previous_holder_name,
                te.new_value AS new_holder_name,
                (CURRENT_DATE - te.bulletin_date) AS days_since_transfer,
                h.name AS holder_name,
                t.holder_tpe_client_id,
                t.attorney_name,
                t.attorney_no
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = ANY(%s)
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{TRANSFER_RECENT_MONTHS_DEFAULT} months'
        """
        if event_type and event_type in TRANSFER_EVENT_TYPES:
            params = [[event_type]]
        else:
            params = [list(TRANSFER_EVENT_TYPES)]

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        if search and search.strip():
            safe_search = (
                search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query += """ AND (
                t.name ILIKE %s ESCAPE '\\' OR
                h.name ILIKE %s ESCAPE '\\' OR
                te.old_value ILIKE %s ESCAPE '\\' OR
                te.new_value ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern, like_pattern, like_pattern])

        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()["cnt"]

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC"
        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [_serialize_transfer_row(row) for row in rows],
        }


async def export_transfers_csv_data(
    *,
    event_type,
    nice_class,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
    now_getter=datetime.now,
    streaming_response_factory=StreamingResponse,
):
    """Export transfer leads as CSV."""
    with db_factory() as db:
        access = _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        if not plan_limit_getter(access["plan"], "can_export_csv_leads"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": access["plan"],
                    "upgrade_context": "csv_export",
                },
            )

        cur = db.cursor()
        query = f"""
            SELECT
                t.name, t.application_no, t.registration_no,
                h.name AS holder_name, t.attorney_name, t.attorney_no,
                t.nice_class_numbers, t.final_status,
                te.event_type,
                te.bulletin_no AS transfer_bulletin_no,
                te.bulletin_date AS transfer_date,
                te.old_value AS previous_holder_name,
                te.new_value AS new_holder_name,
                (CURRENT_DATE - te.bulletin_date) AS days_since_transfer
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = ANY(%s)
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{TRANSFER_RECENT_MONTHS_DEFAULT} months'
        """
        if event_type and event_type in TRANSFER_EVENT_TYPES:
            params = [[event_type]]
        else:
            params = [list(TRANSFER_EVENT_TYPES)]

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        rows = cur.fetchall()

        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Marka",
                "Basvuru No",
                "Tescil No",
                "Olay Tipi",
                "Onceki Sahip",
                "Yeni Sahip",
                "Mevcut Sahip",
                "Vekil",
                "Vekil No",
                "Siniflar",
                "Durum",
                "Devir Tarihi",
                "Devir Bulten No",
                "Devirden Sonra Gun",
            ]
        )

        for row in rows:
            classes_str = ",".join(str(item) for item in (row["nice_class_numbers"] or []))
            writer.writerow(
                [
                    row.get("name") or "",
                    row.get("application_no") or "",
                    row.get("registration_no") or "",
                    row.get("event_type") or "",
                    row.get("previous_holder_name") or "",
                    row.get("new_holder_name") or "",
                    row.get("holder_name") or "",
                    row.get("attorney_name") or "",
                    row.get("attorney_no") or "",
                    classes_str,
                    row.get("final_status") or "",
                    row.get("transfer_date") or "",
                    row.get("transfer_bulletin_no") or "",
                    _as_day_count(row.get("days_since_transfer")) or "",
                ]
            )

        # NOTE: same FK caveat as cancellation export — _log_lead_access
        # not invoked because its conflict_id FK can't be satisfied here.

        output.seek(0)
        filename = f"transfers_{now_getter().strftime('%Y%m%d')}.csv"
        return streaming_response_factory(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


BANKRUPTCY_RECENT_MONTHS_DEFAULT = 24


def _serialize_bankruptcy_row(row):
    """Map a raw bankruptcy event row to the response payload."""
    bankruptcy_date = row.get("bankruptcy_date")
    days_since = _as_day_count(row.get("days_since_bankruptcy"))
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "application_no": row["application_no"],
        "registration_no": row.get("registration_no"),
        "nice_classes": row.get("nice_class_numbers") or [],
        "image_path": row.get("image_path"),
        "status": row.get("final_status"),
        "application_date": (
            str(row["application_date"]) if row.get("application_date") else None
        ),
        "bankruptcy_date": str(bankruptcy_date) if bankruptcy_date else None,
        "bankruptcy_bulletin_no": row.get("bankruptcy_bulletin_no"),
        "bankruptcy_details": row.get("bankruptcy_details"),
        "days_since_bankruptcy": days_since,
        "holder_name": row.get("holder_name"),
        "holder_tpe_client_id": row.get("holder_tpe_client_id"),
        "attorney_name": row.get("attorney_name"),
        "attorney_no": row.get("attorney_no"),
    }


async def get_bankruptcy_feed_data(
    *,
    nice_class,
    search,
    page,
    limit,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
):
    """Return paginated bankruptcy leads (rare, high-LTV signal — full holder portfolio may be acquirable)."""
    with db_factory() as db:
        _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        cur = db.cursor()
        query = f"""
            SELECT
                t.id,
                t.name,
                t.application_no,
                t.registration_no,
                t.nice_class_numbers,
                t.image_path,
                t.final_status,
                t.application_date,
                te.bulletin_no AS bankruptcy_bulletin_no,
                te.bulletin_date AS bankruptcy_date,
                te.new_value AS bankruptcy_details,
                (CURRENT_DATE - te.bulletin_date) AS days_since_bankruptcy,
                h.name AS holder_name,
                t.holder_tpe_client_id,
                t.attorney_name,
                t.attorney_no
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = 'bankruptcy'
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{BANKRUPTCY_RECENT_MONTHS_DEFAULT} months'
        """
        params = []

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        if search and search.strip():
            safe_search = (
                search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query += """ AND (
                t.name ILIKE %s ESCAPE '\\' OR
                h.name ILIKE %s ESCAPE '\\'
            )"""
            like_pattern = f"%{safe_search}%"
            params.extend([like_pattern, like_pattern])

        count_query = "SELECT COUNT(*) as cnt FROM (" + query + ") sub"
        cur.execute(count_query, params)
        total_count = cur.fetchone()["cnt"]

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC"
        offset = (page - 1) * limit
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(query, params)
        rows = cur.fetchall()
        return {
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "items": [_serialize_bankruptcy_row(row) for row in rows],
        }


async def export_bankruptcies_csv_data(
    *,
    nice_class,
    current_user,
    db_factory=Database,
    user_plan_getter=get_user_plan,
    plan_limit_getter=get_plan_limit,
    now_getter=datetime.now,
    streaming_response_factory=StreamingResponse,
):
    """Export bankruptcy leads as CSV."""
    with db_factory() as db:
        access = _require_lead_access(
            db,
            str(current_user.id),
            user_plan_getter=user_plan_getter,
            plan_limit_getter=plan_limit_getter,
        )

        if not plan_limit_getter(access["plan"], "can_export_csv_leads"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": access["plan"],
                    "upgrade_context": "csv_export",
                },
            )

        cur = db.cursor()
        query = f"""
            SELECT
                t.name, t.application_no, t.registration_no,
                h.name AS holder_name, t.holder_tpe_client_id,
                t.attorney_name, t.attorney_no,
                t.nice_class_numbers, t.final_status,
                te.bulletin_no AS bankruptcy_bulletin_no,
                te.bulletin_date AS bankruptcy_date,
                te.new_value AS bankruptcy_details,
                (CURRENT_DATE - te.bulletin_date) AS days_since_bankruptcy
            FROM trademark_events te
            INNER JOIN trademarks t ON t.id = te.trademark_id
            LEFT JOIN holders h ON t.holder_id = h.id
            WHERE te.event_type = 'bankruptcy'
              AND te.bulletin_date IS NOT NULL
              AND te.bulletin_date >= CURRENT_DATE - INTERVAL '{BANKRUPTCY_RECENT_MONTHS_DEFAULT} months'
        """
        params = []

        if nice_class is not None:
            query += " AND %s = ANY(t.nice_class_numbers)"
            params.append(nice_class)

        query += " ORDER BY te.bulletin_date DESC NULLS LAST, te.id DESC LIMIT %s"
        params.append(MAX_EXPORT_LEADS)

        cur.execute(query, params)
        rows = cur.fetchall()

        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Marka",
                "Basvuru No",
                "Tescil No",
                "Iflas Sahibi",
                "Sahip TPE No",
                "Vekil",
                "Vekil No",
                "Siniflar",
                "Durum",
                "Iflas Tarihi",
                "Iflas Bulten No",
                "Iflas Detayi",
                "Iflastan Sonra Gun",
            ]
        )

        for row in rows:
            classes_str = ",".join(str(item) for item in (row["nice_class_numbers"] or []))
            writer.writerow(
                [
                    row.get("name") or "",
                    row.get("application_no") or "",
                    row.get("registration_no") or "",
                    row.get("holder_name") or "",
                    row.get("holder_tpe_client_id") or "",
                    row.get("attorney_name") or "",
                    row.get("attorney_no") or "",
                    classes_str,
                    row.get("final_status") or "",
                    row.get("bankruptcy_date") or "",
                    row.get("bankruptcy_bulletin_no") or "",
                    (row.get("bankruptcy_details") or "")[:200],
                    _as_day_count(row.get("days_since_bankruptcy")) or "",
                ]
            )

        # NOTE: same FK caveat as cancellation/transfer exports.

        output.seek(0)
        filename = f"bankruptcies_{now_getter().strftime('%Y%m%d')}.csv"
        return streaming_response_factory(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
