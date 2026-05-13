"""Public patent portfolio lookups (holder / inventor / attorney).

Three click-through paths from the dashboard patent result card. All
three share the same response shape as the design portfolio endpoints
so the existing portfolio modal renders them identically; only the
matching key differs per actor.

  * Holder    -> routes through services.design_search_service.
                 _resolve_holder_row (UUID OR tpe_client_id) against
                 the shared `holders` table.
  * Inventor  -> conservative name normalization, backed by
                 idx_pinv_normalized_name on patent_inventors(name).
  * Attorney  -> (name, firm) pair under conservative normalization,
                 backed by idx_patt_normalized_pair.

Patent watchlist API does not support per-patent watches (only holder-
based or similarity-query), so the modal hides the bulk-add button
for these entity types — see static/js/dashboard/app.js.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

from services.patent_search_service import (
    HYDRATE_COLS,
    patent_image_url,
)


PATENT_PORTFOLIO_PUBLIC_CAP = 10


def _isofmt(d: Any) -> Optional[str]:
    """Date -> ISO string. The shared cursor returns dates as
    datetime.date for normal columns but as ISO strings in some
    contexts (RealDictCursor with JSON-typed columns), so handle
    both shapes."""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def _portfolio_row(record: Dict[str, Any]) -> Dict[str, Any]:
    """Trim the full hydrate-row down to the portfolio-modal shape -
    same fields the design portfolio returns so the existing modal
    renders patents and designs side by side without branching."""
    record_type = record.get("record_type")
    if hasattr(record_type, "value"):
        record_type = record_type.value
    return {
        "id": record["patent_id"],
        "registry_type": record.get("registry_type") or "patent",
        "application_no": record.get("application_no"),
        "publication_no": record.get("publication_no"),
        "kind_code": record.get("kind_code"),
        "record_type": record_type,
        "name": record.get("title"),
        "title": record.get("title"),
        "ipc_classes": list(record.get("ipc_classes") or []),
        "current_status": record_type,
        "application_date": _isofmt(record.get("application_date")),
        "publication_date": _isofmt(record.get("publication_date")),
        "bulletin_no": record.get("bulletin_no"),
        "bulletin_date": _isofmt(record.get("bulletin_date")),
        "image_url": patent_image_url(
            record.get("first_image_path"),
            record.get("bulletin_folder"),
        ),
    }


# ---------------------------------------------------------------------------
# JSON lookups - return up to 10 patents + total count
# ---------------------------------------------------------------------------


async def run_public_patent_portfolio_lookup(
    *,
    holder_id: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 patents for a given holder, identified by either
    TPE client id or the internal holders.id UUID (matches the design
    portfolio resolver)."""
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
            raise HTTPException(status_code=404, detail="Holder not found")
        holder_name = h["name"] or holder_id

        cur.execute(
            """
            SELECT COUNT(DISTINCT p.id) AS cnt
            FROM patents p
            JOIN patent_holders ph ON ph.patent_id = p.id
            WHERE ph.holder_id = %s
            """,
            (h["id"],),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)

        cur.execute(
            f"""
            SELECT DISTINCT {HYDRATE_COLS}
            FROM patents p
            JOIN patent_holders ph ON ph.patent_id = p.id
            WHERE ph.holder_id = %s
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            LIMIT %s
            """,
            (h["id"], PATENT_PORTFOLIO_PUBLIC_CAP),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "entity_type": "patent-holder",
        "entity_id": holder_id,
        "entity_name": holder_name,
        "results": [_portfolio_row(r) for r in rows],
        "total_count": total_count,
    }


async def run_public_inventor_portfolio_lookup(
    *,
    inventor_name: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 patents whose patent_inventors.name matches under
    conservative normalization. Backed by idx_pinv_normalized_name."""
    from fastapi import HTTPException

    from database.crud import Database

    if not inventor_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(inventor_name).strip()
    if not name_in:
        raise HTTPException(status_code=422, detail="name is required")

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT p.id) AS cnt
            FROM patents p
            JOIN patent_inventors pi ON pi.patent_id = p.id
            WHERE normalize_designer_name(pi.name) = normalize_designer_name(%s)
            """,
            (name_in,),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)
        if total_count == 0:
            raise HTTPException(status_code=404, detail="Inventor not found")

        cur.execute(
            f"""
            SELECT DISTINCT {HYDRATE_COLS}
            FROM patents p
            JOIN patent_inventors pi ON pi.patent_id = p.id
            WHERE normalize_designer_name(pi.name) = normalize_designer_name(%s)
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            LIMIT %s
            """,
            (name_in, PATENT_PORTFOLIO_PUBLIC_CAP),
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "entity_type": "patent-inventor",
        "entity_id": name_in,
        "entity_name": name_in,
        "results": [_portfolio_row(r) for r in rows],
        "total_count": total_count,
    }


async def run_public_patent_attorney_portfolio_lookup(
    *,
    attorney_name: Optional[str] = None,
    attorney_firm: Optional[str] = None,
    logger=None,
) -> Dict[str, Any]:
    """Return up to 10 patents whose patent_attorneys.(name, firm) pair
    matches under conservative normalization. Empty firm matches NULL-
    firm rows via COALESCE(...,''). Backed by idx_patt_normalized_pair."""
    from fastapi import HTTPException

    from database.crud import Database

    if not attorney_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(attorney_name).strip()
    if not name_in:
        raise HTTPException(status_code=422, detail="name is required")
    firm_in = (attorney_firm or "").strip()

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT COUNT(DISTINCT p.id) AS cnt
            FROM patents p
            JOIN patent_attorneys pa ON pa.patent_id = p.id
            WHERE normalize_designer_name(pa.name) = normalize_designer_name(%s)
              AND COALESCE(normalize_designer_name(pa.firm), '')
                  = COALESCE(normalize_designer_name(%s), '')
            """,
            (name_in, firm_in or None),
        )
        total_count = int(cur.fetchone()["cnt"] or 0)
        if total_count == 0:
            raise HTTPException(status_code=404, detail="Attorney not found")

        cur.execute(
            f"""
            SELECT DISTINCT {HYDRATE_COLS}
            FROM patents p
            JOIN patent_attorneys pa ON pa.patent_id = p.id
            WHERE normalize_designer_name(pa.name) = normalize_designer_name(%s)
              AND COALESCE(normalize_designer_name(pa.firm), '')
                  = COALESCE(normalize_designer_name(%s), '')
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            LIMIT %s
            """,
            (name_in, firm_in or None, PATENT_PORTFOLIO_PUBLIC_CAP),
        )
        rows = [dict(r) for r in cur.fetchall()]

    firm_in_name = bool(firm_in) and firm_in.lower() in name_in.lower()
    display_name = (
        name_in + " - " + firm_in if firm_in and not firm_in_name else name_in
    )

    return {
        "entity_type": "patent-attorney",
        "entity_id": json.dumps(
            {"name": name_in, "firm": firm_in or ""}, ensure_ascii=False,
        ),
        "entity_name": display_name,
        "results": [_portfolio_row(r) for r in rows],
        "total_count": total_count,
    }


# ---------------------------------------------------------------------------
# CSV writers - plan-gated, mirror the design + trademark variants
# ---------------------------------------------------------------------------

_CSV_HEADERS = [
    "Bulus Basligi",
    "Basvuru No",
    "Yayin No",
    "Tur",
    "IPC Siniflari",
    "Basvuru Tarihi",
    "Yayin Tarihi",
    "Bulten No",
    "Sahip",
    "Bulus Sahipleri",
    "Vekil",
    "Vekil Firmasi",
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
            r.get("title") or "",
            r.get("application_no") or "",
            r.get("publication_no") or "",
            record_type or "",
            "; ".join(str(c) for c in (r.get("ipc_classes") or [])),
            r["application_date"].isoformat() if r.get("application_date") else "",
            r["publication_date"].isoformat() if r.get("publication_date") else "",
            r.get("bulletin_no") or "",
            r.get("holder_name") or "",
            "; ".join(str(n) for n in (r.get("inventors") or [])),
            r.get("attorney_name") or "",
            r.get("attorney_firm") or "",
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
    p.title, p.application_no, p.publication_no,
    p.record_type, p.ipc_classes,
    p.application_date, p.publication_date, p.bulletin_no,
    (SELECT ph2.name FROM patent_holders ph2
     WHERE ph2.patent_id = p.id ORDER BY ph2.seq ASC LIMIT 1) AS holder_name,
    (SELECT ARRAY_AGG(pi2.name ORDER BY pi2.seq) FROM patent_inventors pi2
     WHERE pi2.patent_id = p.id) AS inventors,
    (SELECT pa2.name FROM patent_attorneys pa2
     WHERE pa2.patent_id = p.id ORDER BY pa2.seq ASC LIMIT 1) AS attorney_name,
    (SELECT pa2.firm FROM patent_attorneys pa2
     WHERE pa2.patent_id = p.id ORDER BY pa2.seq ASC LIMIT 1) AS attorney_firm
"""


async def build_public_patent_portfolio_csv(
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
            raise HTTPException(status_code=404, detail="Holder not found")
        holder_name = h["name"] or holder_id

        cur.execute(
            f"""
            SELECT DISTINCT {_CSV_SELECT_BODY}
            FROM patents p
            JOIN patent_holders ph ON ph.patent_id = p.id
            WHERE ph.holder_id = %s
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            """,
            (h["id"],),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="Holder not found")
    return _build_csv_response(rows, safe_filename_stem=holder_name)


async def build_public_inventor_portfolio_csv(
    *,
    inventor_name: Optional[str] = None,
    logger=None,
    current_user=None,
):
    from fastapi import HTTPException

    from database.crud import Database

    if not inventor_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(inventor_name).strip()
    _ensure_csv_plan(current_user)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT {_CSV_SELECT_BODY}
            FROM patents p
            JOIN patent_inventors pi ON pi.patent_id = p.id
            WHERE normalize_designer_name(pi.name) = normalize_designer_name(%s)
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            """,
            (name_in,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="Inventor not found")
    return _build_csv_response(rows, safe_filename_stem=name_in)


async def build_public_patent_attorney_portfolio_csv(
    *,
    attorney_name: Optional[str] = None,
    attorney_firm: Optional[str] = None,
    logger=None,
    current_user=None,
):
    from fastapi import HTTPException

    from database.crud import Database

    if not attorney_name:
        raise HTTPException(status_code=422, detail="name is required")
    name_in = str(attorney_name).strip()
    firm_in = (attorney_firm or "").strip()
    _ensure_csv_plan(current_user)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT DISTINCT {_CSV_SELECT_BODY}
            FROM patents p
            JOIN patent_attorneys pa ON pa.patent_id = p.id
            WHERE normalize_designer_name(pa.name) = normalize_designer_name(%s)
              AND COALESCE(normalize_designer_name(pa.firm), '')
                  = COALESCE(normalize_designer_name(%s), '')
            ORDER BY p.application_date DESC NULLS LAST, p.application_no DESC
            """,
            (name_in, firm_in or None),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise HTTPException(status_code=404, detail="Attorney not found")
    return _build_csv_response(rows, safe_filename_stem=name_in)
