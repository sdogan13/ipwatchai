"""Service helpers for trademark routes."""

from fastapi import HTTPException

from database.crud import Database


# ---------------------------------------------------------------------------
# Turkish labels for event types and statuses
# ---------------------------------------------------------------------------
EVENT_TYPE_LABELS = {
    "transfer": "Devir",
    "merger": "BirleÅŸme",
    "partial_transfer": "KÄ±smi Devir",
    "cancellation": "Ä°ptal",
    "withdrawal": "Geri Ã‡ekme",
    "renewal": "Yenileme",
    "seizure": "Haciz",
    "precautionary_seizure": "Ä°htiyati Haciz",
    "injunction": "Ä°htiyati Tedbir",
    "precautionary_injunction": "Ä°htiyati Tedbir",
    "seizure_lift": "Haciz KaldÄ±rma",
    "injunction_lift": "Tedbir KaldÄ±rma",
    "restriction_lift": "KÄ±sÄ±tlama KaldÄ±rma",
    "license": "Lisans",
    "bankruptcy": "Ä°flas",
    "correction": "DÃ¼zeltme",
    "madrid_registration": "Madrid Tescil",
    "madrid_renewal": "Madrid Yenileme",
    "address_change": "Adres DeÄŸiÅŸikliÄŸi",
    "name_change": "Unvan DeÄŸiÅŸikliÄŸi",
    "class_change": "SÄ±nÄ±f DeÄŸiÅŸikliÄŸi",
}

# Health card severity: critical > warning > info
EVENT_SEVERITY = {
    "cancellation": "critical",
    "seizure": "critical",
    "precautionary_seizure": "critical",
    "injunction": "warning",
    "precautionary_injunction": "warning",
    "bankruptcy": "critical",
    "transfer": "warning",
    "merger": "warning",
    "partial_transfer": "warning",
    "withdrawal": "warning",
    "renewal": "info",
    "license": "info",
    "seizure_lift": "info",
    "injunction_lift": "info",
    "restriction_lift": "info",
    "correction": "info",
    "address_change": "info",
    "name_change": "info",
    "class_change": "info",
    "madrid_registration": "info",
    "madrid_renewal": "info",
}


async def get_trademark_events_data(
    *,
    application_no: str,
    page: int,
    per_page: int,
    event_type: str | None,
    current_user=None,
    database_factory=Database,
):
    """Return event timeline and health summary for a trademark."""
    with database_factory() as db:
        cur = db.cursor()

        # 1. Fetch trademark + event-derived columns
        cur.execute(
            """
            SELECT t.id, t.application_no, t.name, t.final_status,
                   t.effective_status, t.active_restriction_count,
                   t.current_holder_name, t.holder_changed_at,
                   t.renewal_expiry, t.last_event_type, t.last_event_date,
                   t.has_restrictions, t.event_flags, t.total_event_count,
                   t.expiry_date, t.registration_date,
                   h.name AS original_holder_name
            FROM trademarks t
            LEFT JOIN holders h ON h.id = t.holder_id
            WHERE t.application_no = %s
        """,
            (application_no,),
        )
        tm = cur.fetchone()

        if not tm:
            raise HTTPException(status_code=404, detail="Marka bulunamadÄ±")

        # 2. Build health card
        event_flags = tm.get("event_flags") or {}
        health_card = {
            "effective_status": tm["effective_status"],
            "final_status": tm["final_status"],
            "active_restriction_count": tm["active_restriction_count"] or 0,
            "has_restrictions": tm["has_restrictions"] or False,
            "current_holder_name": tm["current_holder_name"],
            "original_holder_name": tm["original_holder_name"],
            "holder_changed": tm["current_holder_name"] is not None,
            "holder_changed_at": str(tm["holder_changed_at"]) if tm["holder_changed_at"] else None,
            "renewal_expiry": str(tm["renewal_expiry"]) if tm["renewal_expiry"] else None,
            "expiry_date": str(tm["expiry_date"]) if tm["expiry_date"] else None,
            "last_event_type": tm["last_event_type"],
            "last_event_type_label": EVENT_TYPE_LABELS.get(
                tm["last_event_type"],
                tm["last_event_type"],
            ),
            "last_event_date": str(tm["last_event_date"]) if tm["last_event_date"] else None,
            "total_event_count": tm["total_event_count"] or 0,
            "flags": event_flags,
        }

        # Compute health severity
        severity = "healthy"
        if tm["active_restriction_count"] and tm["active_restriction_count"] > 0:
            severity = "critical"
        elif tm["effective_status"] in ("Ä°ptal Edildi",):
            severity = "critical"
        elif tm["effective_status"] in ("Geri Ã‡ekildi",):
            severity = "warning"
        elif tm["effective_status"] in ("Devredildi",):
            severity = "warning"
        elif event_flags.get("has_bankruptcy"):
            severity = "critical"
        health_card["severity"] = severity

        # 3. Fetch paginated events
        offset = (page - 1) * per_page
        params = [application_no]
        type_filter = ""
        if event_type:
            type_filter = "AND event_type = %s"
            params.append(event_type)

        cur.execute(
            f"""
            SELECT id, event_type, event_subtype, source_type, bulletin_no,
                   bulletin_date, page_number, old_value, new_value, details, raw_text,
                   created_at
            FROM trademark_events
            WHERE application_no = %s {type_filter}
            ORDER BY bulletin_date DESC NULLS LAST, created_at DESC
            LIMIT %s OFFSET %s
        """,
            params + [per_page, offset],
        )
        rows = cur.fetchall()

        # Count total
        cur.execute(
            f"""
            SELECT COUNT(*) as cnt
            FROM trademark_events
            WHERE application_no = %s {type_filter}
        """,
            params,
        )
        total = cur.fetchone()["cnt"]

        # 4. Format events
        events = []
        for r in rows:
            etype = r["event_type"]
            events.append(
                {
                    "id": str(r["id"]),
                    "event_type": etype,
                    "event_type_label": EVENT_TYPE_LABELS.get(etype, etype),
                    "event_subtype": r["event_subtype"],
                    "severity": EVENT_SEVERITY.get(etype, "info"),
                    "source_type": r["source_type"],
                    "bulletin_no": r["bulletin_no"],
                    "bulletin_date": str(r["bulletin_date"]) if r["bulletin_date"] else None,
                    "page_number": r["page_number"],
                    "old_value": r["old_value"],
                    "new_value": r["new_value"],
                    "details": r["details"] or {},
                    "raw_text": r["raw_text"],
                }
            )

    return {
        "application_no": application_no,
        "name": tm["name"],
        "health_card": health_card,
        "events": events,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


async def get_extracted_goods_data(
    *,
    application_no: str,
    current_user=None,
    database_factory=Database,
):
    """Return extracted goods payload for a trademark."""
    with database_factory() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT application_no, name, extracted_goods, nice_class_numbers
            FROM trademarks
            WHERE application_no = %s
        """,
            (application_no,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Marka bulunamadi")

    extracted = row.get("extracted_goods")
    if not extracted or extracted == [] or extracted is None:
        return {
            "application_no": application_no,
            "has_extracted_goods": False,
            "extracted_goods": [],
            "total_items": 0,
        }

    return {
        "application_no": application_no,
        "name": row.get("name"),
        "has_extracted_goods": True,
        "extracted_goods": extracted,
        "nice_classes": row.get("nice_class_numbers"),
        "total_items": len(extracted) if isinstance(extracted, list) else 0,
    }
