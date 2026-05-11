"""Coğrafi İşaret watchlist + alert routes.

Watchlist endpoints:
  * POST   /api/v1/cografi-watchlist             create (any of 4 watch_types)
  * GET    /api/v1/cografi-watchlist             list
  * GET    /api/v1/cografi-watchlist/{id}        fetch one
  * PATCH  /api/v1/cografi-watchlist/{id}        update mutable fields
  * DELETE /api/v1/cografi-watchlist/{id}        delete (cascades alerts)
  * POST   /api/v1/cografi-watchlist/{id}/scan   on-demand scan

Alert endpoints:
  * GET    /api/v1/cografi-alerts                list with filters
  * GET    /api/v1/cografi-alerts/{id}           fetch one
  * PATCH  /api/v1/cografi-alerts/{id}           acknowledge / dismiss / resolve
  * DELETE /api/v1/cografi-alerts/{id}           hard delete

Mirrors the patent + design watchlist + alert route conventions.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request


logger = logging.getLogger("turkpatent.cografi_watchlist_routes")


def register_cografi_watchlist_routes(app, limiter):
    from auth.authentication import get_current_user
    from services.cografi_watchlist_service import (
        create_cografi_watchlist_item,
        delete_cografi_watchlist_item,
        get_cografi_watchlist_item,
        list_cografi_watchlist_items,
        update_cografi_watchlist_item,
    )

    # ---- Watchlist CRUD ---------------------------------------------------

    @app.post("/api/v1/cografi-watchlist", tags=["Cografi Watchlist"])
    @limiter.limit("30/minute")
    async def create_cografi_watchlist(
        request: Request,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        # Reference watches with reference_query but no reference_record_id
        # need a query embedding so the scanner has something to cosine
        # against. Embed in the route layer (where the e5 loader lives).
        watch_type = (data.get("watch_type") or "").strip().lower()
        if (
            watch_type == "reference"
            and data.get("reference_query")
            and not data.get("reference_record_id")
            and not data.get("reference_embedding")
        ):
            from app_cografi_search_routes import _embed_query_text
            embedding = _embed_query_text(data["reference_query"])
            if embedding:
                data["reference_embedding"] = embedding

        return create_cografi_watchlist_item(data=data, current_user=current_user)

    @app.get("/api/v1/cografi-watchlist", tags=["Cografi Watchlist"])
    @limiter.limit("60/minute")
    async def list_cografi_watchlist(
        request: Request,
        watch_type: Optional[str] = Query(None),
        is_active: Optional[bool] = Query(True),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return list_cografi_watchlist_items(
            current_user=current_user, watch_type=watch_type, is_active=is_active,
        )

    @app.get("/api/v1/cografi-watchlist/{item_id}", tags=["Cografi Watchlist"])
    @limiter.limit("60/minute")
    async def get_cografi_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return get_cografi_watchlist_item(item_id=item_id, current_user=current_user)

    @app.patch("/api/v1/cografi-watchlist/{item_id}", tags=["Cografi Watchlist"])
    @limiter.limit("30/minute")
    async def update_cografi_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        return update_cografi_watchlist_item(
            item_id=item_id, data=data, current_user=current_user,
        )

    @app.delete("/api/v1/cografi-watchlist/{item_id}", tags=["Cografi Watchlist"])
    @limiter.limit("30/minute")
    async def delete_cografi_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return delete_cografi_watchlist_item(item_id=item_id, current_user=current_user)

    @app.post("/api/v1/cografi-watchlist/{item_id}/scan", tags=["Cografi Watchlist"])
    @limiter.limit("10/minute")
    async def scan_cografi_watchlist(
        request: Request,
        item_id: UUID,
        current_user=Depends(get_current_user),
    ):
        """Manually trigger a scan for one watchlist item. Synchronous;
        scans are fast (sub-second to a few seconds at the current corpus
        size). Returns the scan summary with alerts_created count."""
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")

        from database.crud import Database
        from services.cografi_scanner_service import scan_and_store

        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, organization_id, user_id, watch_type, label,
                       holder_name, holder_id, holder_tpe_client_id,
                       reference_record_id, reference_query,
                       reference_embedding::text AS reference_embedding_text,
                       region_query, region_terms,
                       lifecycle_registration_no,
                       section_keys, record_types, gi_type,
                       customer_application_no, customer_registration_no,
                       similarity_threshold,
                       alert_email, alert_webhook, webhook_url
                FROM cografi_watchlist_mt
                WHERE id = %s AND organization_id = %s
                """,
                (str(item_id), str(current_user.organization_id)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Watchlist item not found")
            item = dict(row)
            return scan_and_store(db, item)

    # ---- Alerts -----------------------------------------------------------

    @app.get("/api/v1/cografi-alerts", tags=["Cografi Alerts"])
    @limiter.limit("60/minute")
    async def list_cografi_alerts(
        request: Request,
        watchlist_item_id: Optional[UUID] = Query(None),
        status: Optional[str] = Query(None),
        severity: Optional[str] = Query(None),
        match_type: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        from database.crud import Database

        parts = ["organization_id = %s"]
        params = [str(current_user.organization_id)]
        if watchlist_item_id:
            parts.append("watchlist_item_id = %s")
            params.append(str(watchlist_item_id))
        if status:
            parts.append("status = %s")
            params.append(status)
        if severity:
            parts.append("severity = %s")
            params.append(severity)
        if match_type:
            parts.append("match_type = %s")
            params.append(match_type)
        where = " AND ".join(parts)
        params.extend([limit, offset])
        sql = f"""
            SELECT id, watchlist_item_id, conflicting_record_id,
                   conflicting_section_key, conflicting_record_type,
                   conflicting_application_no, conflicting_registration_no,
                   conflicting_existing_registration_no, conflicting_name,
                   conflicting_gi_type, conflicting_geographical_boundary,
                   conflicting_bulletin_no, conflicting_bulletin_date,
                   match_type, overall_similarity_score,
                   text_similarity_score, embedding_similarity_score, region_similarity_score,
                   severity, status, alert_type,
                   email_sent, email_sent_at, webhook_sent, webhook_sent_at,
                   acknowledged_at, resolved_at,
                   created_at, updated_at
            FROM cografi_alerts_mt
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        with Database() as db:
            cur = db.cursor()
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"SELECT COUNT(*) AS n FROM cografi_alerts_mt WHERE {where}",
                params[:-2],
            )
            total = cur.fetchone()
            total_n = int(total["n"] if isinstance(total, dict) else total[0])
        return {"items": rows, "total": total_n, "limit": limit, "offset": offset}

    @app.get("/api/v1/cografi-alerts/{alert_id}", tags=["Cografi Alerts"])
    @limiter.limit("60/minute")
    async def get_cografi_alert(
        request: Request,
        alert_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        from database.crud import Database

        with Database() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT *
                FROM cografi_alerts_mt
                WHERE id = %s AND organization_id = %s
                """,
                (str(alert_id), str(current_user.organization_id)),
            )
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        return dict(row)

    @app.patch("/api/v1/cografi-alerts/{alert_id}", tags=["Cografi Alerts"])
    @limiter.limit("30/minute")
    async def update_cografi_alert(
        request: Request,
        alert_id: UUID,
        current_user=Depends(get_current_user),
    ):
        """Update status / severity / resolution_notes. Status transitions:
        new -> seen -> acknowledged -> resolved | dismissed."""
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        sets, vals = [], []
        if "status" in data:
            new_status = (data.get("status") or "").strip().lower()
            if new_status not in ("new", "seen", "acknowledged", "resolved", "dismissed"):
                raise HTTPException(status_code=400, detail="invalid status")
            sets.append("status = %s")
            vals.append(new_status)
            if new_status == "acknowledged":
                sets.append("acknowledged_at = NOW()")
                sets.append("acknowledged_by = %s")
                vals.append(str(current_user.id))
            if new_status == "resolved":
                sets.append("resolved_at = NOW()")
                sets.append("resolved_by = %s")
                vals.append(str(current_user.id))
        if "severity" in data:
            sev = (data.get("severity") or "").strip().lower()
            if sev not in ("low", "medium", "high", "critical"):
                raise HTTPException(status_code=400, detail="invalid severity")
            sets.append("severity = %s")
            vals.append(sev)
        if "resolution_notes" in data:
            sets.append("resolution_notes = %s")
            vals.append(data.get("resolution_notes"))
        if not sets:
            raise HTTPException(status_code=400, detail="no updatable fields")
        sets.append("updated_at = NOW()")
        vals.extend([str(alert_id), str(current_user.organization_id)])

        from database.crud import Database

        sql = f"UPDATE cografi_alerts_mt SET {', '.join(sets)} WHERE id = %s AND organization_id = %s RETURNING id"
        with Database() as db:
            cur = db.cursor()
            cur.execute(sql, vals)
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Alert not found")
            db.commit()
        return {"updated": True, "id": str(alert_id)}

    @app.delete("/api/v1/cografi-alerts/{alert_id}", tags=["Cografi Alerts"])
    @limiter.limit("30/minute")
    async def delete_cografi_alert(
        request: Request,
        alert_id: UUID,
        current_user=Depends(get_current_user),
    ):
        if current_user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        from database.crud import Database

        with Database() as db:
            cur = db.cursor()
            cur.execute(
                "DELETE FROM cografi_alerts_mt WHERE id = %s AND organization_id = %s RETURNING id",
                (str(alert_id), str(current_user.organization_id)),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Alert not found")
            db.commit()
        return {"deleted": True, "id": str(alert_id)}
