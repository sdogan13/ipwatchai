"""Public cografi (GI) portfolio lookups (applicant / agent).

Two click-through paths from the dashboard cografi result card. Shape
matches the design + patent portfolio endpoints so the existing
dashboard portfolio modal renders cografi rows identically; only the
matching key differs per actor.

  * Applicant -> JOIN cografi_holders WHERE role='APPLICANT' against
                 the shared holders table (resolved by either TPE
                 client id or internal holders.id UUID via
                 services.design_search_service._resolve_holder_row).
  * Agent     -> exact match on normalize_designer_name(agent) against
                 the sparse text column cografi_records.agent. Backed
                 by idx_cog_agent_normalized.

Cografi watchlist API has no bulk-from-portfolio endpoint, so the
modal hides the bulk-add button for these entity types — see
templates/dashboard/partials/_search_panel.html.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from services.cografi_search_service import (
    HYDRATE_COLS,
    cografi_image_url,
)


COGRAFI_PORTFOLIO_PUBLIC_CAP = 10


def _isofmt(d: Any) -> Optional[str]:
    """Date -> ISO string. Cursor returns datetime.date for dates but
    pre-stringified for some columns, so tolerate both."""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def _portfolio_row(record: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a full hydrate-row down to the portfolio-modal shape -
    same fields the design + patent portfolios return so the modal
    renders any registry row without branching."""
    return {
        "id": record["record_id"],
        "registry_type": record.get("registry_type") or "cografi",
        "section_key": record.get("section_key"),
        "record_type": record.get("record_type"),
        "application_no": record.get("application_no"),
        "registration_no": record.get("registration_no"),
        "name": record.get("name"),
        "title": record.get("name"),
        "gi_type": record.get("gi_type"),
        "product_group": record.get("product_group"),
        "geographical_boundary": record.get("geographical_boundary"),
        "current_status": record.get("record_type"),
        "application_date": _isofmt(record.get("application_date")),
        "registration_date": _isofmt(record.get("registration_date")),
        "bulletin_no": record.get("bulletin_no"),
        "bulletin_date": _isofmt(record.get("bulletin_date")),
        "image_url": cografi_image_url(
            record.get("first_image_path"),
            record.get("bulletin_folder"),
        ),
    }


# ---------------------------------------------------------------------------
# JSON lookups
# ---------------------------------------------------------------------------


async def run_public_cografi_applicant_portfolio_lookup(
    *,
    holder_id: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 cografi records for an applicant, identified by
    either TPE client id or internal holders.id UUID. Resolver matches
    the design/patent variants."""
    from fastapi import HTTPException

    from database.crud import Database
    from services.design_search_service import _resolve_holder_row

    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")
    holder_id = str(holder_id).strip()
    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")

    with Database() as db:
        cur = db.cursor()
        h = _resolve_holder_row(cur, holder_id)
        if not h:
            raise HTTPException(status_code=404, detail="Applicant not found")
        applicant_name = h["name"] or holder_id

        cur.execute(
            """
            SELECT COUNT(DISTINCT r.id) AS cnt
            FROM cografi_records r
            JOIN cografi_holders ch ON ch.record_id = r.id
            WHERE ch.holder_id = %s
              AND ch.role = 'APPLICANT'
            """,
            (h["id"],),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)

        cur.execute(
            f"""
            SELECT DISTINCT {HYDRATE_COLS}
            FROM cografi_records r
            JOIN cografi_holders ch ON ch.record_id = r.id
            WHERE ch.holder_id = %s
              AND ch.role = 'APPLICANT'
            ORDER BY r.application_date DESC NULLS LAST, r.application_no DESC
            LIMIT %s
            """,
            (h["id"], COGRAFI_PORTFOLIO_PUBLIC_CAP),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "entity_type": "cografi-applicant",
        "entity_id": holder_id,
        "entity_name": applicant_name,
        "results": [_portfolio_row(r) for r in rows],
        "total_count": total_count,
    }


async def run_public_cografi_agent_portfolio_lookup(
    *,
    agent_name: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 cografi records whose agent column matches under
    conservative normalization. Backed by idx_cog_agent_normalized."""
    from fastapi import HTTPException

    from database.crud import Database

    if not agent_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(agent_name).strip()
    if not name_in:
        raise HTTPException(status_code=422, detail="name is required")

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM cografi_records r
            WHERE r.agent IS NOT NULL
              AND normalize_designer_name(r.agent) = normalize_designer_name(%s)
            """,
            (name_in,),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)
        if total_count == 0:
            raise HTTPException(status_code=404, detail="Agent not found")

        cur.execute(
            f"""
            SELECT {HYDRATE_COLS}
            FROM cografi_records r
            WHERE r.agent IS NOT NULL
              AND normalize_designer_name(r.agent) = normalize_designer_name(%s)
            ORDER BY r.application_date DESC NULLS LAST, r.application_no DESC
            LIMIT %s
            """,
            (name_in, COGRAFI_PORTFOLIO_PUBLIC_CAP),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "entity_type": "cografi-agent",
        "entity_id": name_in,
        "entity_name": name_in,
        "results": [_portfolio_row(r) for r in rows],
        "total_count": total_count,
    }


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

_CSV_HEADERS = [
    "Ad",
    "Basvuru No",
    "Tescil No",
    "Tur",
    "GI Turu",
    "Urun Grubu",
    "Cografi Sinir",
    "Basvuru Tarihi",
    "Tescil Tarihi",
    "Bulten No",
    "Basvuru Sahibi",
    "Vekil",
]


def _ensure_csv_plan(current_user):
    """Plan-gate the CSV endpoints (paid-plan only).
    401 if unauthenticated, 403 if free plan."""
    from fastapi import HTTPException

    from database.crud import Database
    from utils.subscription import get_plan_limit, get_user_plan

    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    with Database() as db:
        plan = get_user_plan(db, str(current_user.id))
        if not get_plan_limit(plan["plan_name"], "can_download_portfolio"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_required",
                    "message": "CSV export is available on paid plans.",
                    "current_plan": plan["plan_name"],
                    "upgrade_context": "portfolio_download",
                },
            )


def _build_csv_response(rows: List[Dict[str, Any]], *, safe_filename_stem: str):
    from fastapi.responses import StreamingResponse

    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADERS)
    for r in rows:
        record_type = r.get("record_type")
        if hasattr(record_type, "value"):
            record_type = record_type.value
        writer.writerow([
            r.get("name") or "",
            r.get("application_no") or "",
            r.get("registration_no") or "",
            record_type or "",
            r.get("gi_type") or "",
            r.get("product_group") or "",
            r.get("geographical_boundary") or "",
            r["application_date"].isoformat() if r.get("application_date") else "",
            r["registration_date"].isoformat() if r.get("registration_date") else "",
            r.get("bulletin_no") or "",
            r.get("applicant_name") or "",
            r.get("agent") or "",
        ])
    safe = "".join(
        c if c.isascii() and (c.isalnum() or c in " _-") else "_"
        for c in safe_filename_stem
    )[:50]
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="' + safe + '_portfolio.csv"',
        },
    )


_CSV_SELECT_BODY = """
    r.name, r.application_no, r.registration_no, r.record_type,
    r.gi_type, r.product_group, r.geographical_boundary,
    r.application_date, r.registration_date, r.bulletin_no, r.agent,
    (SELECT ch2.name FROM cografi_holders ch2
     WHERE ch2.record_id = r.id AND ch2.role = 'APPLICANT'
     ORDER BY ch2.seq ASC LIMIT 1) AS applicant_name
"""


async def build_public_cografi_applicant_portfolio_csv(
    *,
    holder_id: Optional[str] = None,
    logger=None,
    current_user=None,
):
    from fastapi import HTTPException

    from database.crud import Database
    from services.design_search_service import _resolve_holder_row

    if not holder_id:
        raise HTTPException(status_code=422, detail="holder_id is required")
    holder_id = str(holder_id).strip()
    _ensure_csv_plan(current_user)

    with Database() as db:
        cur = db.cursor()
        h = _resolve_holder_row(cur, holder_id)
        if not h:
            raise HTTPException(status_code=404, detail="Applicant not found")
        applicant_name = h["name"] or holder_id

        cur.execute(
            f"""
            SELECT DISTINCT {_CSV_SELECT_BODY}
            FROM cografi_records r
            JOIN cografi_holders ch ON ch.record_id = r.id
            WHERE ch.holder_id = %s
              AND ch.role = 'APPLICANT'
            ORDER BY r.application_date DESC NULLS LAST, r.application_no DESC
            """,
            (h["id"],),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="Applicant not found")
    return _build_csv_response(rows, safe_filename_stem=applicant_name)


async def build_public_cografi_agent_portfolio_csv(
    *,
    agent_name: Optional[str] = None,
    logger=None,
    current_user=None,
):
    from fastapi import HTTPException

    from database.crud import Database

    if not agent_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(agent_name).strip()
    _ensure_csv_plan(current_user)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT {_CSV_SELECT_BODY}
            FROM cografi_records r
            WHERE r.agent IS NOT NULL
              AND normalize_designer_name(r.agent) = normalize_designer_name(%s)
            ORDER BY r.application_date DESC NULLS LAST, r.application_no DESC
            """,
            (name_in,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _build_csv_response(rows, safe_filename_stem=name_in)
