"""Watchlist routes extracted from the legacy api.routes module."""

import io
import logging
import re
import threading
from typing import List, Optional
from uuid import UUID, uuid4

import pandas as pd
import psycopg2
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel as PydanticBaseModel, Field

from auth.authentication import CurrentUser, get_current_user
from database.crud import Database, WatchlistCRUD
from models.schemas import (
    ColumnAutoMappings,
    ColumnDetectionResponse,
    FileUploadErrorItem,
    FileUploadResult,
    FileUploadSkippedItem,
    FileUploadSummary,
    FileUploadWarning,
    PaginatedResponse,
    PortfolioPreviewRequest,
    PortfolioPreviewResponse,
    SuccessResponse,
    WatchlistBulkImport,
    WatchlistBulkImportResult,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistItemUpdate,
)
from api.watchlist_background import run_watchlist_scan_task

logger = logging.getLogger(__name__)

watchlist_router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


# ==========================================
# Column name variants for file upload
# ==========================================

BRAND_NAME_VARIANTS = [
    "marka adı",
    "marka adi",
    "marka",
    "trademark_name",
    "trademark name",
    "brand name",
    "brand_name",
    "name",
    "isim",
]

APP_NO_VARIANTS = [
    "başvuru no",
    "başvuru numarası",
    "başvuru no.",
    "basvuru no",
    "basvuru numarasi",
    "basvuru no.",
    "application no",
    "application number",
    "application_no",
    "app no",
    "app_no",
    "application",
]

CLASS_VARIANTS = [
    "sınıf",
    "sınıflar",
    "sınıf no",
    "sınıf numarası",
    "sinif",
    "siniflar",
    "sinif no",
    "sinif numarasi",
    "nice class",
    "nice classes",
    "nice_class",
    "nice_classes",
    "class",
    "classes",
    "class no",
]

BULLETIN_VARIANTS = [
    "bülten no",
    "bülten numarası",
    "bülten",
    "bulten no",
    "bulten numarasi",
    "bulten",
    "bulletin no",
    "bulletin number",
    "bulletin",
]


def _find_column(columns: List[str], variants: List[str]) -> Optional[str]:
    """Find a column by checking against variant names."""
    for variant in variants:
        if variant in columns:
            return variant
    return None


def _parse_nice_classes(value) -> List[int]:
    """Parse Nice class values into list of integers."""
    if pd.isna(value) or not value:
        return []

    value_str = str(value)
    numbers = re.findall(r"\d+", value_str)

    classes = []
    for num in numbers:
        n = int(num)
        if 1 <= n <= 45:
            classes.append(n)

    return sorted(list(set(classes)))


@watchlist_router.get("/stats")
async def watchlist_stats(
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get aggregate stats for the organization's watchlist."""
    from services.watchlist_service import get_watchlist_stats_summary

    return await get_watchlist_stats_summary(
        current_user=current_user,
        min_score=min_score,
    )


@watchlist_router.get("", response_model=PaginatedResponse)
async def list_watchlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    active_only: bool = True,
    search: Optional[str] = Query(None, min_length=1, max_length=200),
    sort: Optional[str] = Query(None),
    renewal_only: bool = Query(False),
    appeals_only: bool = Query(False),
    status_filter: Optional[str] = Query(None),
    threshold: float = Query(0.70, ge=0.0, le=100.0),
    tm_status: Optional[str] = Query(None, max_length=50),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List watchlist items for organization."""
    from services.watchlist_service import get_watchlist_page

    payload = await get_watchlist_page(
        current_user=current_user,
        page=page,
        page_size=page_size,
        active_only=active_only,
        search=search,
        sort=sort,
        renewal_only=renewal_only,
        appeals_only=appeals_only,
        status_filter=status_filter,
        threshold=threshold,
        tm_status=tm_status,
        logger=logger,
    )
    return PaginatedResponse(**payload)
    '''Legacy inline implementation removed during service extraction.
            try:
                cur = db.cursor()
                cur.execute(
                    """
                SELECT
                    a.watchlist_item_id,
                    COUNT(*) as total_conflicts,
                    COUNT(*) FILTER (WHERE t.final_status = 'Başvuruldu' AND t.bulletin_date IS NULL) as pre_publication_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE AND t.appeal_deadline <= CURRENT_DATE + INTERVAL '7 days') as critical_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE + INTERVAL '7 days' AND t.appeal_deadline <= CURRENT_DATE + INTERVAL '30 days') as urgent_count,
                    COUNT(*) FILTER (WHERE t.appeal_deadline > CURRENT_DATE + INTERVAL '30 days') as active_count,
                    MIN(t.appeal_deadline) FILTER (WHERE t.appeal_deadline > CURRENT_DATE) as nearest_deadline,
                    MAX(CASE a.severity
                        WHEN 'critical'  THEN 5
                        WHEN 'very_high' THEN 4
                        WHEN 'high'      THEN 3
                        WHEN 'medium'    THEN 2
                        WHEN 'low'       THEN 1
                        ELSE 0
                    END) FILTER (WHERE a.status NOT IN ('dismissed', 'resolved')) AS highest_severity_rank,
                    COUNT(*) FILTER (WHERE a.severity = 'critical') as sev_critical,
                    COUNT(*) FILTER (WHERE a.severity = 'very_high') as sev_very_high,
                    COUNT(*) FILTER (WHERE a.severity = 'high') as sev_high,
                    COUNT(*) FILTER (WHERE a.severity = 'medium') as sev_medium,
                    COUNT(*) FILTER (WHERE a.severity = 'low') as sev_low
                FROM alerts_mt a
                LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
                WHERE a.watchlist_item_id = ANY(%s::uuid[])
                    AND a.status NOT IN ('dismissed', 'resolved')
                    AND (t.appeal_deadline IS NULL OR t.appeal_deadline >= CURRENT_DATE)
                    AND a.overall_risk_score >= %s
                GROUP BY a.watchlist_item_id
            """,
                    (item_ids, threshold),
                )
                severity_map = {5: "critical", 4: "very_high", 3: "high", 2: "medium", 1: "low"}
                for row in cur.fetchall():
                    wid = str(row["watchlist_item_id"])
                    nearest = row["nearest_deadline"]
                    days_to_nearest = None
                    if nearest:
                        from datetime import date as date_type

                        today = date_type.today()
                        days_to_nearest = (nearest - today).days
                    conflict_summaries[wid] = {
                        "total": row["total_conflicts"],
                        "pre_publication": row["pre_publication_count"],
                        "active_critical": row["critical_count"],
                        "active_urgent": row["urgent_count"],
                        "active": row["active_count"],
                        "nearest_deadline": nearest.isoformat() if nearest else None,
                        "nearest_deadline_days": days_to_nearest,
                        "highest_severity": severity_map.get(row["highest_severity_rank"]),
                        "sev_critical": row["sev_critical"],
                        "sev_very_high": row["sev_very_high"],
                        "sev_high": row["sev_high"],
                        "sev_medium": row["sev_medium"],
                        "sev_low": row["sev_low"],
                    }
            except Exception:
                logger.exception(
                    "Failed to load conflict summaries; watchlist will render without badge counts"
                )
                conflict_summaries = {}

        response_items = []
        for item in items:
            resp = WatchlistItemResponse(**item)
            resp.conflict_summary = conflict_summaries.get(str(item["id"]))
            response_items.append(resp)

        return PaginatedResponse(
            items=response_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size,
        )
    '''


@watchlist_router.post("", response_model=WatchlistItemResponse)
async def create_watchlist_item(
    data: WatchlistItemCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add trademark to watchlist and trigger an initial scan."""
    from services.watchlist_service import create_watchlist_item_record

    payload = await create_watchlist_item_record(
        data=data,
        current_user=current_user,
    )
    background_tasks.add_task(run_watchlist_scan_task, payload["scan_item_id"])
    return WatchlistItemResponse(**payload["item"])


@watchlist_router.post("/bulk", response_model=WatchlistBulkImportResult)
async def bulk_import_watchlist(
    data: WatchlistBulkImport,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Bulk import watchlist items."""
    from services.watchlist_service import import_watchlist_items_bulk

    payload = await import_watchlist_items_bulk(
        data=data,
        current_user=current_user,
    )
    for item_id in payload["scan_item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = dict(payload["result"])
    result["queued_scans"] = len(payload["scan_item_ids"])
    return WatchlistBulkImportResult(**result)

    with Database() as db:
        from utils.subscription import get_plan_limit, get_user_plan

        plan_info = get_user_plan(db, str(current_user.id))
        plan_name = plan_info.get("plan_name", "free")
        max_items = get_plan_limit(plan_name, "max_watchlist_items")
        cur = db.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (str(current_user.organization_id),),
        )
        current_count = cur.fetchone()["count"]
        remaining_slots = max(0, max_items - current_count)

        if remaining_slots == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "limit_exceeded",
                    "message": f"Izleme listesi limitinize ulastiniz ({max_items}). Daha fazla eklemek icin planinizi yukseltin.",
                    "current_count": current_count,
                    "max_items": max_items,
                },
            )

        cur.execute(
            "SELECT customer_application_no FROM watchlist_mt WHERE organization_id = %s AND customer_application_no IS NOT NULL",
            (str(current_user.organization_id),),
        )
        existing_app_nos = {
            str(row["customer_application_no"]).strip() for row in cur.fetchall()
        }

        created = 0
        failed = 0
        skipped = 0
        errors = []
        created_ids = []

        for index, item in enumerate(data.items):
            app_no_str = str(item.application_no).strip() if item.application_no else None
            if app_no_str and app_no_str in existing_app_nos:
                skipped += 1
                continue

            if created >= remaining_slots:
                errors.append(
                    {
                        "index": index,
                        "brand_name": item.brand_name,
                        "error": f"Izleme listesi limiti asildi ({max_items})",
                    }
                )
                failed += 1
                continue
            try:
                result = WatchlistCRUD.create(
                    db, current_user.organization_id, current_user.id, item
                )
                created += 1
                created_ids.append(UUID(result["id"]))
            except Exception as exc:
                failed += 1
                errors.append(
                    {
                        "index": index,
                        "brand_name": item.brand_name,
                        "error": str(exc),
                    }
                )

        for item_id in created_ids:
            background_tasks.add_task(run_watchlist_scan_task, item_id)

        return WatchlistBulkImportResult(
            total=len(data.items),
            created=created,
            failed=failed,
            skipped=skipped,
            errors=errors,
        )


@watchlist_router.post("/portfolio-preview", response_model=PortfolioPreviewResponse)
async def preview_portfolio_import(
    data: PortfolioPreviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Preview bulk import from portfolio to calculate duplicates before confirming."""
    from services.watchlist_service import preview_watchlist_portfolio_import

    payload = await preview_watchlist_portfolio_import(
        data=data,
        current_user=current_user,
    )
    return PortfolioPreviewResponse(**payload)

    if not data.holder_id and not data.attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    with Database() as db:
        cur = db.cursor()

        if data.holder_id:
            holder_id_str = str(data.holder_id)
            try:
                from uuid import UUID as _UUID

                _UUID(holder_id_str)
                cur.execute(
                    "SELECT application_no FROM trademarks WHERE holder_tpe_client_id = %s OR holder_id = %s",
                    (holder_id_str, holder_id_str),
                )
            except ValueError:
                cur.execute(
                    "SELECT application_no FROM trademarks WHERE holder_tpe_client_id = %s",
                    (holder_id_str,),
                )
        else:
            cur.execute(
                "SELECT application_no FROM trademarks WHERE attorney_no = %s OR attorney_tpe_client_id = %s",
                (str(data.attorney_no), str(data.attorney_no)),
            )

        rows = cur.fetchall()
        source_app_nos = {
            str(row["application_no"]).strip()
            for row in rows
            if row.get("application_no")
        }
        total_items = len(source_app_nos)

        if total_items == 0:
            return PortfolioPreviewResponse(total_items=0, duplicate_count=0, can_add=0)

        cur.execute(
            "SELECT customer_application_no FROM watchlist_mt WHERE organization_id = %s AND customer_application_no IS NOT NULL",
            (str(current_user.organization_id),),
        )
        existing_app_nos = {
            str(row["customer_application_no"]).strip() for row in cur.fetchall()
        }

        duplicate_count = len(source_app_nos.intersection(existing_app_nos))
        can_add = total_items - duplicate_count

        return PortfolioPreviewResponse(
            total_items=total_items,
            duplicate_count=duplicate_count,
            can_add=can_add,
        )


class BulkFromPortfolioRequest(PydanticBaseModel):
    holder_id: Optional[str] = None
    attorney_no: Optional[str] = None


@watchlist_router.post("/bulk-from-portfolio", response_model=WatchlistBulkImportResult)
async def bulk_import_from_portfolio(
    data: BulkFromPortfolioRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Bulk import watchlist items from a holder or attorney portfolio."""
    from services.watchlist_service import import_watchlist_items_from_portfolio

    payload = await import_watchlist_items_from_portfolio(
        data=data,
        current_user=current_user,
    )
    for item_id in payload["scan_item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = dict(payload["result"])
    result["queued_scans"] = len(payload["scan_item_ids"])
    return WatchlistBulkImportResult(**result)

    if not data.holder_id and not data.attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    from utils.subscription import get_plan_limit as _gpl_port, get_user_plan as _gup_port

    with Database() as db_perm:
        _pplan = _gup_port(db_perm, str(current_user.id))
        if not _gpl_port(_pplan["plan_name"], "can_view_holder_portfolio"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Portfoy erisimi icin Business veya ustu plan gereklidir.",
                    "current_plan": _pplan["plan_name"],
                },
            )

    with Database() as db:
        cur = db.cursor()

        if data.holder_id:
            where_col = "holder_tpe_client_id"
            param = data.holder_id
        else:
            where_col = "attorney_no"
            param = data.attorney_no

        from psycopg2 import sql as psql

        cur.execute(
            psql.SQL(
                """
            SELECT application_no, name, nice_class_numbers, image_path,
                   image_embedding::text, dinov2_embedding::text,
                   color_histogram::text, logo_ocr_text, text_embedding::text
            FROM trademarks
            WHERE {} = %s
            ORDER BY application_date DESC NULLS LAST
        """
            ).format(psql.Identifier(where_col)),
            (param,),
        )
        rows = cur.fetchall()

        if not rows:
            return WatchlistBulkImportResult(
                total=0, created=0, failed=0, skipped=0, errors=[]
            )

        def _parse_vec(val):
            if not val:
                return None
            if isinstance(val, list):
                return val
            s = val.strip()
            if s.startswith("[") and s.endswith("]"):
                return [float(x) for x in s[1:-1].split(",") if x.strip()]
            return None

        from utils.subscription import get_plan_limit, get_user_plan

        plan_info = get_user_plan(db, str(current_user.id))
        plan_name = plan_info.get("plan_name", "free")
        max_items = get_plan_limit(plan_name, "max_watchlist_items")
        cur.execute(
            "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (str(current_user.organization_id),),
        )
        current_count = cur.fetchone()["count"]
        remaining_slots = max(0, max_items - current_count)

        cur.execute(
            "SELECT customer_application_no FROM watchlist_mt WHERE organization_id = %s AND customer_application_no IS NOT NULL",
            (str(current_user.organization_id),),
        )
        existing_app_nos = {
            str(row["customer_application_no"]).strip() for row in cur.fetchall()
        }

        created = 0
        failed = 0
        skipped = 0
        errors = []
        created_ids = []
        limit_reached = False

        for index, trademark in enumerate(rows):
            app_no = trademark.get("application_no")
            app_no_str = str(app_no).strip() if app_no else None
            if app_no_str and app_no_str in existing_app_nos:
                skipped += 1
                continue

            if created >= remaining_slots:
                limit_reached = True
                break

            try:
                brand = trademark.get("name") or trademark.get("application_no") or "Unknown"
                classes = trademark.get("nice_class_numbers") or []
                classes = [nice_class for nice_class in classes if 1 <= nice_class <= 45]
                if not classes:
                    classes = [1]

                item_data = WatchlistItemCreate(
                    brand_name=brand,
                    nice_class_numbers=classes,
                    application_no=trademark.get("application_no"),
                )

                img_path = trademark.get("image_path") or None

                cur.execute("SAVEPOINT sp_bulk")
                result = WatchlistCRUD.create_with_embeddings(
                    db,
                    current_user.organization_id,
                    current_user.id,
                    item_data,
                    logo_path=img_path,
                    logo_embedding=_parse_vec(trademark.get("image_embedding")),
                    logo_dinov2_embedding=_parse_vec(trademark.get("dinov2_embedding")),
                    logo_color_histogram=_parse_vec(trademark.get("color_histogram")),
                    logo_ocr_text=trademark.get("logo_ocr_text"),
                    text_embedding=_parse_vec(trademark.get("text_embedding")),
                    auto_commit=False,
                )
                cur.execute("RELEASE SAVEPOINT sp_bulk")
                created += 1
                created_ids.append(UUID(result["id"]))
            except Exception as exc:
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_bulk")
                except Exception:
                    pass
                failed += 1
                errors.append(
                    {
                        "index": index,
                        "brand_name": trademark.get("name", ""),
                        "error": str(exc),
                    }
                )

        db.commit()

        for item_id in created_ids:
            background_tasks.add_task(run_watchlist_scan_task, item_id)

        return WatchlistBulkImportResult(
            total=len(rows),
            created=created,
            failed=failed,
            skipped=skipped,
            errors=errors,
            limit_reached=limit_reached,
            max_allowed=max_items,
            current_count=current_count + created,
        )


@watchlist_router.get("/upload/template")
async def download_template():
    """Generate Excel template with mandatory columns."""
    from services.watchlist_service import build_watchlist_upload_template

    output = build_watchlist_upload_template()
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_listesi_sablon.xlsx"},
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Marka Listesi"

    headers = [
        ("Marka Adı *", True),
        ("Başvuru No *", True),
        ("Sınıflar *", True),
        ("Bülten No", False),
    ]

    required_fill = PatternFill(start_color="DC2626", end_color="DC2626", fill_type="solid")
    optional_fill = PatternFill(start_color="0EA5E9", end_color="0EA5E9", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    for col, (header, is_required) in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = required_fill if is_required else optional_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    sample_data = [
        ["ÖRNEK MARKA 1", "2023/12345", "9, 35", "305"],
        ["ÖRNEK MARKA 2", "2023/67890", "25, 35, 42", "306"],
        ["ÖRNEK MARKA 3", "2022/11111", "30, 43", ""],
    ]

    for row_idx, row_data in enumerate(sample_data, 2):
        for col_idx, value in enumerate(row_data, 1):
            ws.cell(row=row_idx, column=col_idx, value=value)

    ws.cell(row=6, column=1, value="* Zorunlu sütunlar. Bülten No opsiyoneldir.")
    ws.cell(row=6, column=1).font = Font(italic=True, color="666666")

    ws.cell(row=7, column=1, value="Sınıflar: Virgülle ayırarak yazın (örn: 9, 35, 42)")
    ws.cell(row=7, column=1).font = Font(italic=True, color="666666")

    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 15
    ws.column_dimensions["D"].width = 12

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_listesi_sablon.xlsx"},
    )


@watchlist_router.post("/upload/detect-columns", response_model=ColumnDetectionResponse)
async def detect_columns(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Read file and return column names for mapping UI."""
    from services.watchlist_service import detect_watchlist_upload_columns

    contents = await file.read()
    payload = detect_watchlist_upload_columns(
        contents=contents,
        filename=file.filename or "",
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
    )
    return ColumnDetectionResponse(**payload)

    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents), nrows=5)
            df_count = pd.read_excel(io.BytesIO(contents), usecols=[0])
            total_rows = len(df_count)
        elif filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents), nrows=5)
            df_count = pd.read_csv(io.BytesIO(contents), usecols=[0])
            total_rows = len(df_count)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Desteklenmeyen dosya formati. Excel veya CSV yukleyin.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dosya okunamadi: {str(exc)}",
        ) from exc

    original_columns = list(df.columns)
    normalized_columns = [str(col).lower().strip() for col in df.columns]

    auto_mappings = ColumnAutoMappings(
        brand_name=_find_column(normalized_columns, BRAND_NAME_VARIANTS),
        application_no=_find_column(normalized_columns, APP_NO_VARIANTS),
        nice_classes=_find_column(normalized_columns, CLASS_VARIANTS),
        bulletin_no=_find_column(normalized_columns, BULLETIN_VARIANTS),
    )

    norm_to_orig = {str(col).lower().strip(): str(col) for col in original_columns}

    auto_mappings_orig = ColumnAutoMappings(
        brand_name=norm_to_orig.get(auto_mappings.brand_name)
        if auto_mappings.brand_name
        else None,
        application_no=norm_to_orig.get(auto_mappings.application_no)
        if auto_mappings.application_no
        else None,
        nice_classes=norm_to_orig.get(auto_mappings.nice_classes)
        if auto_mappings.nice_classes
        else None,
        bulletin_no=norm_to_orig.get(auto_mappings.bulletin_no)
        if auto_mappings.bulletin_no
        else None,
    )

    df.columns = original_columns
    sample_data = df.head(3).fillna("").to_dict("records")
    sample_data = [{k: str(v) if v != "" else "" for k, v in row.items()} for row in sample_data]

    return ColumnDetectionResponse(
        columns=original_columns,
        sample_data=sample_data,
        auto_mappings=auto_mappings_orig,
        total_rows=total_rows,
    )


@watchlist_router.post("/upload/with-mapping", response_model=FileUploadResult)
async def upload_with_mapping(
    file: UploadFile = File(...),
    column_mapping: str = Form(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload file with custom column mappings."""
    contents = await file.read()
    from services.watchlist_service import import_watchlist_upload_with_mapping

    payload = await import_watchlist_upload_with_mapping(
        contents=contents,
        filename=file.filename,
        column_mapping=column_mapping,
        current_user=current_user,
    )
    if background_tasks:
        for item_id in payload["scan_item_ids"]:
            background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = payload["result"]
    if isinstance(result, dict):
        result = dict(result)
        result["queued_scans"] = len(payload["scan_item_ids"])
    else:
        result.queued_scans = len(payload["scan_item_ids"])
    return result

    import json

    try:
        mappings = json.loads(column_mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gecersiz sutun eslestirme formati",
        ) from exc

    if "nice_class_numbers" in mappings and "nice_classes" not in mappings:
        mappings["nice_classes"] = mappings.pop("nice_class_numbers")

    if not mappings.get("brand_name"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Eksik zorunlu eslestirme: brand_name",
        )

    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents))
        elif filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Desteklenmeyen dosya formati",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Dosya okunamadi: {str(exc)}",
        ) from exc

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dosya bos",
        )

    rename_map = {value: key for key, value in mappings.items() if value}
    df = df.rename(columns=rename_map)
    df.columns = [str(col).lower().strip() for col in df.columns]

    warnings = []
    bulletin_col = mappings.get("bulletin_no")
    if not bulletin_col:
        warnings.append(
            FileUploadWarning(
                column="Bulten No",
                message="Bulten numarasi sutunu eslestirme yapilmadi. Bu opsiyonel bir alandir.",
            )
        )

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT customer_application_no
            FROM watchlist_mt
            WHERE organization_id = %s
              AND customer_application_no IS NOT NULL
              AND is_active = TRUE
        """,
            (org_id,),
        )
        existing = cur.fetchall()
        existing_app_nos = {
            row["customer_application_no"].strip().lower()
            for row in existing
            if row["customer_application_no"]
        }

    from utils.subscription import get_plan_limit, get_user_plan as _get_plan

    with Database() as db_lim:
        _plan = _get_plan(db_lim, user_id)
        _plan_name = _plan.get("plan_name", "free")
        _max_items = get_plan_limit(_plan_name, "max_watchlist_items")
        _cur = db_lim.cursor()
        _cur.execute(
            "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (org_id,),
        )
        _current_count = _cur.fetchone()["count"]
    remaining_slots = max(0, _max_items - _current_count)

    added_count = 0
    skipped_count = 0
    error_count = 0
    skipped_items = []
    error_items = []
    created_ids = []

    with Database() as db:
        cur = db.cursor()

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                if added_count >= remaining_slots:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            error=f"Izleme listesi limiti asildi ({_max_items})",
                        )
                    )
                    continue

                brand_name = str(row.get("brand_name", "")).strip()
                if not brand_name or brand_name.lower() in ["nan", "none", ""]:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            error="Marka adi bos",
                        )
                    )
                    continue

                app_no = str(row.get("application_no", "")).strip()
                if not app_no or app_no.lower() in ["nan", "none", ""]:
                    app_no = f"WL-{uuid4().hex[:8].upper()}"

                classes_raw = row.get("nice_classes", "") if "nice_classes" in df.columns else ""
                classes_str = str(classes_raw).strip() if classes_raw is not None else ""
                if classes_str and classes_str.lower() not in ["nan", "none", ""]:
                    nice_classes = _parse_nice_classes(classes_raw)
                else:
                    nice_classes = []

                bulletin_no = None
                if "bulletin_no" in df.columns:
                    bulletin_no = str(row.get("bulletin_no", "")).strip()
                    if bulletin_no.lower() in ["nan", "none", ""]:
                        bulletin_no = None

                if app_no.lower() in existing_app_nos:
                    skipped_count += 1
                    skipped_items.append(
                        FileUploadSkippedItem(
                            row=row_num,
                            brand_name=brand_name,
                            application_no=app_no,
                            reason="Zaten mevcut",
                        )
                    )
                    continue

                item_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO watchlist_mt (
                        id, organization_id, user_id, brand_name,
                        nice_class_numbers, customer_application_no, customer_bulletin_no,
                        alert_threshold, is_active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        0.7, TRUE, NOW(), NOW()
                    )
                """,
                    (str(item_id), org_id, user_id, brand_name, nice_classes, app_no, bulletin_no),
                )

                added_count += 1
                existing_app_nos.add(app_no.lower())
                created_ids.append(item_id)

            except Exception as exc:
                error_count += 1
                error_items.append(
                    FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name if "brand_name" in dir() else None,
                        error=str(exc)[:100],
                    )
                )

        db.commit()

    if background_tasks and created_ids:
        for item_id in created_ids:
            background_tasks.add_task(run_watchlist_scan_task, item_id)

    message_parts = [f"{added_count} marka eklendi"]
    if skipped_count > 0:
        message_parts.append(f"{skipped_count} zaten mevcut (atlandi)")
    if error_count > 0:
        message_parts.append(f"{error_count} hatali satir")

    return FileUploadResult(
        success=True,
        message=", ".join(message_parts),
        summary=FileUploadSummary(
            total_rows=len(df),
            added=added_count,
            skipped=skipped_count,
            errors=error_count,
        ),
        warnings=warnings,
        skipped_items=skipped_items[:10],
        error_items=error_items[:10],
    )


@watchlist_router.post("/upload", response_model=FileUploadResult)
async def upload_file(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload Excel/CSV file with mandatory column validation."""
    contents = await file.read()
    from services.watchlist_service import import_watchlist_upload_file

    payload = await import_watchlist_upload_file(
        contents=contents,
        filename=file.filename,
        current_user=current_user,
        brand_name_variants=BRAND_NAME_VARIANTS,
        application_no_variants=APP_NO_VARIANTS,
        class_variants=CLASS_VARIANTS,
        bulletin_variants=BULLETIN_VARIANTS,
        find_column=_find_column,
        parse_nice_classes=_parse_nice_classes,
    )
    if background_tasks:
        for item_id in payload["scan_item_ids"]:
            background_tasks.add_task(run_watchlist_scan_task, item_id)
    result = payload["result"]
    if isinstance(result, dict):
        result = dict(result)
        result["queued_scans"] = len(payload["scan_item_ids"])
    else:
        result.queued_scans = len(payload["scan_item_ids"])
    return result

    contents = await file.read()
    filename = file.filename.lower() if file.filename else ""

    try:
        if filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(contents))
        elif filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(contents))
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "unsupported_format",
                    "message": "Desteklenmeyen dosya formati",
                    "detail": "Lutfen Excel (.xlsx, .xls) veya CSV (.csv) dosyasi yukleyin.",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "parse_error",
                "message": "Dosya okunamadi",
                "detail": str(exc),
            },
        ) from exc

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "empty_file",
                "message": "Dosya bos",
            },
        )

    original_columns = list(df.columns)
    df.columns = [str(col).lower().strip() for col in df.columns]

    brand_col = _find_column(df.columns.tolist(), BRAND_NAME_VARIANTS)
    app_no_col = _find_column(df.columns.tolist(), APP_NO_VARIANTS)
    class_col = _find_column(df.columns.tolist(), CLASS_VARIANTS)
    bulletin_col = _find_column(df.columns.tolist(), BULLETIN_VARIANTS)

    missing_columns = []

    if not brand_col:
        missing_columns.append(
            {
                "column": "Marka Adi",
                "variants": "marka adi, brand name, name, isim",
                "reason": "Hangi markalarin izlenecegini belirler",
            }
        )

    if not app_no_col:
        missing_columns.append(
            {
                "column": "Basvuru No",
                "variants": "basvuru no, application no, app no",
                "reason": "Mukerrer kontrol ve cakisma filtreleme icin gerekli",
            }
        )

    if not class_col:
        missing_columns.append(
            {
                "column": "Siniflar",
                "variants": "sinif, siniflar, nice class, classes",
                "reason": "Hangi siniflarda arama yapilacagini belirler",
            }
        )

    if missing_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_mandatory_columns",
                "message": f"{len(missing_columns)} zorunlu sutun eksik",
                "missing_columns": missing_columns,
                "found_columns": original_columns,
                "required_columns": [
                    {"name": "Marka Adi", "variants": "marka adi, brand name, name"},
                    {"name": "Basvuru No", "variants": "basvuru no, application no"},
                    {"name": "Siniflar", "variants": "sinif, siniflar, nice class, classes"},
                ],
                "optional_columns": [
                    {"name": "Bulten No", "variants": "bulten no, bulletin no"},
                ],
                "example": {
                    "headers": ["Marka Adi", "Basvuru No", "Siniflar", "Bulten No"],
                    "rows": [
                        ["ORNEK MARKA", "2023/12345", "9, 35, 42", "305"],
                        ["DIGER MARKA", "2023/67890", "25, 35", "306"],
                    ],
                },
            },
        )

    warnings = []
    if not bulletin_col:
        warnings.append(
            FileUploadWarning(
                column="Bulten No",
                message="Bulten numarasi sutunu bulunamadi. Bu opsiyonel bir alandir.",
            )
        )

    org_id = str(current_user.organization_id)
    user_id = str(current_user.id)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT customer_application_no
            FROM watchlist_mt
            WHERE organization_id = %s
              AND customer_application_no IS NOT NULL
              AND is_active = TRUE
        """,
            (org_id,),
        )
        existing = cur.fetchall()
        existing_app_nos = {
            row["customer_application_no"].strip().lower()
            for row in existing
            if row["customer_application_no"]
        }

        from utils.subscription import get_plan_limit, get_user_plan as _get_plan2

        _plan2 = _get_plan2(db, user_id)
        _plan_name2 = _plan2.get("plan_name", "free")
        _max_items2 = get_plan_limit(_plan_name2, "max_watchlist_items")
        cur.execute(
            "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE",
            (org_id,),
        )
        _current_count2 = cur.fetchone()["count"]
    remaining_slots2 = max(0, _max_items2 - _current_count2)

    added_count = 0
    skipped_count = 0
    error_count = 0
    skipped_items = []
    error_items = []
    created_ids = []

    with Database() as db:
        cur = db.cursor()

        for idx, row in df.iterrows():
            row_num = idx + 2

            try:
                if added_count >= remaining_slots2:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            error=f"Izleme listesi limiti asildi ({_max_items2})",
                        )
                    )
                    continue

                brand_name = str(row.get(brand_col, "")).strip()
                if not brand_name or brand_name.lower() in ["nan", "none", ""]:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            error="Marka adi bos",
                        )
                    )
                    continue

                app_no = str(row.get(app_no_col, "")).strip()
                if not app_no or app_no.lower() in ["nan", "none", ""]:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            brand_name=brand_name,
                            error="Basvuru numarasi bos",
                        )
                    )
                    continue

                classes_raw = row.get(class_col, "")
                nice_classes = _parse_nice_classes(classes_raw)
                if not nice_classes:
                    error_count += 1
                    error_items.append(
                        FileUploadErrorItem(
                            row=row_num,
                            brand_name=brand_name,
                            error="Sinif bilgisi bos veya gecersiz",
                        )
                    )
                    continue

                bulletin_no = None
                if bulletin_col and bulletin_col in row:
                    bulletin_no = str(row.get(bulletin_col, "")).strip()
                    if bulletin_no.lower() in ["nan", "none", ""]:
                        bulletin_no = None

                if app_no.lower() in existing_app_nos:
                    skipped_count += 1
                    skipped_items.append(
                        FileUploadSkippedItem(
                            row=row_num,
                            brand_name=brand_name,
                            application_no=app_no,
                            reason="Zaten mevcut",
                        )
                    )
                    continue

                item_id = uuid4()
                cur.execute(
                    """
                    INSERT INTO watchlist_mt (
                        id, organization_id, user_id, brand_name,
                        nice_class_numbers, customer_application_no, customer_bulletin_no,
                        alert_threshold, is_active, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        0.7, TRUE, NOW(), NOW()
                    )
                """,
                    (str(item_id), org_id, user_id, brand_name, nice_classes, app_no, bulletin_no),
                )

                added_count += 1
                existing_app_nos.add(app_no.lower())
                created_ids.append(item_id)

            except Exception as exc:
                error_count += 1
                error_items.append(
                    FileUploadErrorItem(
                        row=row_num,
                        brand_name=brand_name if "brand_name" in dir() else None,
                        error=str(exc)[:100],
                    )
                )

        db.commit()

    if background_tasks and created_ids:
        for item_id in created_ids:
            background_tasks.add_task(run_watchlist_scan_task, item_id)

    message_parts = [f"{added_count} marka eklendi"]
    if skipped_count > 0:
        message_parts.append(f"{skipped_count} zaten mevcut (atlandi)")
    if error_count > 0:
        message_parts.append(f"{error_count} hatali satir")

    return FileUploadResult(
        success=True,
        message=", ".join(message_parts),
        summary=FileUploadSummary(
            total_rows=len(df),
            added=added_count,
            skipped=skipped_count,
            errors=error_count,
        ),
        warnings=warnings,
        skipped_items=skipped_items[:10],
        error_items=error_items[:10],
    )


@watchlist_router.post("/{item_id}/logo", response_model=SuccessResponse)
async def upload_watchlist_logo(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    logo: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload a logo image for a watchlist item. Generates visual embeddings in background."""
    contents = await logo.read()
    from services.watchlist_service import store_watchlist_logo_upload

    payload = await store_watchlist_logo_upload(
        item_id=item_id,
        current_user=current_user,
        logo_filename=logo.filename or "logo.png",
        content_type=logo.content_type,
        contents=contents,
    )
    background_tasks.add_task(
        _start_watchlist_logo_thread,
        payload["item_id"],
        payload["filepath"],
    )
    return SuccessResponse(success=payload["success"], message=payload["message"])


@watchlist_router.get("/{item_id}/logo")
async def get_watchlist_logo(
    item_id: UUID,
):
    """Get the logo image for a watchlist item (no auth - served as <img src>)"""
    from fastapi.responses import FileResponse as FR
    from services.watchlist_service import resolve_watchlist_logo_file

    payload = await resolve_watchlist_logo_file(item_id=item_id)
    return FR(payload["path"], media_type=payload["media_type"])


@watchlist_router.delete("/{item_id}/logo", response_model=SuccessResponse)
async def delete_watchlist_logo(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove the logo from a watchlist item and clear visual embeddings"""
    from services.watchlist_service import delete_watchlist_logo_asset

    payload = await delete_watchlist_logo_asset(
        item_id=item_id,
        current_user=current_user,
    )
    return SuccessResponse(**payload)


def _process_watchlist_logo(item_id: UUID, filepath: str):
    """Background task: generate CLIP, DINOv2, color, OCR embeddings for uploaded logo"""
    from services.watchlist_service import process_watchlist_logo_embeddings

    process_watchlist_logo_embeddings(
        item_id=item_id,
        filepath=filepath,
        logger=logger,
    )


def _start_watchlist_logo_thread(item_id: UUID, filepath: str):
    """Detach logo embedding work from the request lifecycle."""
    thread = threading.Thread(
        target=_process_watchlist_logo,
        args=(item_id, filepath),
        daemon=True,
        name=f"watchlist-logo-{item_id}",
    )
    thread.start()


@watchlist_router.post("/scan-all", response_model=SuccessResponse)
async def trigger_scan_all(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scan all active watchlist items for the organization."""
    from services.watchlist_service import prepare_watchlist_scan_all

    payload = await prepare_watchlist_scan_all(
        current_user=current_user,
    )
    for item_id in payload["item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    return SuccessResponse(
        success=payload["success"],
        message=payload["message"],
    )

    with Database() as db:
        from utils.subscription import get_plan_limit as _gpl_scan, get_user_plan as _gup_scan

        _scan_plan = _gup_scan(db, str(current_user.id))
        _scan_max = _gpl_scan(_scan_plan["plan_name"], "auto_scan_max_items")
        if _scan_max == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Otomatik tarama icin planinizi yukseltin.",
                    "current_plan": _scan_plan["plan_name"],
                },
            )

        _, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=1
        )
        items, _ = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=max(total, 1)
        )

    if not items:
        return SuccessResponse(message="Izleme listesinde taranacak marka yok")

    items_to_scan = items[:_scan_max] if _scan_max < 999999 else items

    for item in items_to_scan:
        background_tasks.add_task(run_watchlist_scan_task, UUID(item["id"]))

    msg = f"{len(items_to_scan)} marka taramaya alindi (toplam: {total})"
    if len(items_to_scan) < len(items):
        msg += f" - plan limitiniz nedeniyle {_scan_max} marka tarandi"
    return SuccessResponse(message=msg)


@watchlist_router.get("/scan-status")
async def get_scan_status(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get auto-scan schedule status and next scan time."""
    from services.watchlist_service import get_watchlist_scan_status

    return await get_watchlist_scan_status(current_user=current_user)

    from workers.scheduler import get_next_scan_time

    return {
        "auto_scan_enabled": True,
        "schedule": "Daily at 03:00",
        "next_scan_at": get_next_scan_time(),
    }


@watchlist_router.delete("/all", response_model=SuccessResponse)
async def delete_all_watchlist(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all watchlist items and alerts for the organization."""
    from services.watchlist_service import delete_all_watchlist_records

    payload = await delete_all_watchlist_records(
        current_user=current_user,
    )
    return SuccessResponse(**payload)

    with Database() as db:
        cur = db.cursor()
        org_id = str(current_user.organization_id)

        cur.execute(
            """
            DELETE FROM alerts_mt WHERE organization_id = %s
        """,
            (org_id,),
        )
        deleted_alerts = cur.rowcount

        cur.execute(
            """
            DELETE FROM watchlist_mt WHERE organization_id = %s
        """,
            (org_id,),
        )
        deleted_items = cur.rowcount

        db.commit()

    return SuccessResponse(message=f"{deleted_items} marka ve {deleted_alerts} uyari silindi")


@watchlist_router.post("/rescan", response_model=SuccessResponse)
async def rescan_all_watchlist(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Clear old alerts and rescan all watchlist items fresh."""
    from services.watchlist_service import prepare_watchlist_rescan

    payload = await prepare_watchlist_rescan(
        current_user=current_user,
    )
    for item_id in payload["item_ids"]:
        background_tasks.add_task(run_watchlist_scan_task, item_id)
    return SuccessResponse(
        success=payload["success"],
        message=payload["message"],
    )

    with Database() as db:
        from utils.subscription import (
            get_plan_limit as _gpl_rescan,
            get_user_plan as _gup_rescan,
        )

        _rescan_plan = _gup_rescan(db, str(current_user.id))
        _rescan_max = _gpl_rescan(_rescan_plan["plan_name"], "auto_scan_max_items")
        if _rescan_max == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "upgrade_required",
                    "message": "Otomatik tarama icin planinizi yukseltin.",
                    "current_plan": _rescan_plan["plan_name"],
                },
            )

        cur = db.cursor()
        org_id = str(current_user.organization_id)

        cur.execute(
            """
            DELETE FROM alerts_mt WHERE organization_id = %s
        """,
            (org_id,),
        )
        cleared_alerts = cur.rowcount

        cur.execute(
            """
            UPDATE watchlist_mt SET last_scan_at = NULL WHERE organization_id = %s
        """,
            (org_id,),
        )

        _, total = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=1
        )
        items, _ = WatchlistCRUD.get_by_organization(
            db, current_user.organization_id, active_only=True, page_size=max(total, 1)
        )

        db.commit()

    if not items:
        return SuccessResponse(message=f"Eski {cleared_alerts} uyari silindi. Taranacak marka yok.")

    items_to_scan = items[:_rescan_max] if _rescan_max < 999999 else items

    for item in items_to_scan:
        background_tasks.add_task(run_watchlist_scan_task, UUID(item["id"]))

    return SuccessResponse(
        message=f"Eski {cleared_alerts} uyari silindi. {len(items_to_scan)} marka yeniden taramaya alindi."
    )


class _BulkThresholdUpdate(PydanticBaseModel):
    threshold: float = Field(..., ge=0.5, le=0.95)


@watchlist_router.put("/bulk-threshold", response_model=SuccessResponse)
async def update_all_threshold(
    data: _BulkThresholdUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update alert_threshold for all active watchlist items in this org."""
    from services.watchlist_service import update_watchlist_bulk_thresholds

    payload = await update_watchlist_bulk_thresholds(
        threshold=data.threshold,
        current_user=current_user,
    )
    return SuccessResponse(**payload)

    with Database() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE watchlist_mt
            SET alert_threshold = %s, updated_at = NOW()
            WHERE organization_id = %s AND is_active = TRUE
        """,
            (data.threshold, str(current_user.organization_id)),
        )
        updated = cur.rowcount
        db.commit()
    return SuccessResponse(success=True, message=f"{updated} items updated")


@watchlist_router.get("/{item_id}", response_model=WatchlistItemResponse)
async def get_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get watchlist item details."""
    from services.watchlist_service import get_watchlist_item_detail

    item = await get_watchlist_item_detail(
        item_id=item_id,
        current_user=current_user,
    )
    return WatchlistItemResponse(**item)


@watchlist_router.put("/{item_id}", response_model=WatchlistItemResponse)
async def update_watchlist_item(
    item_id: UUID,
    data: WatchlistItemUpdate,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update watchlist item settings."""
    from services.watchlist_service import update_watchlist_item_record

    item = await update_watchlist_item_record(
        item_id=item_id,
        data=data,
        current_user=current_user,
    )
    return WatchlistItemResponse(**item)


@watchlist_router.delete("/{item_id}", response_model=SuccessResponse)
async def delete_watchlist_item(
    item_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a watchlist item and its alerts."""
    from services.watchlist_service import delete_watchlist_item_record

    payload = await delete_watchlist_item_record(
        item_id=item_id,
        current_user=current_user,
    )
    return SuccessResponse(**payload)


@watchlist_router.post("/{item_id}/scan", response_model=SuccessResponse)
async def trigger_scan(
    item_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger a scan for a watchlist item."""
    from services.watchlist_service import prepare_watchlist_item_scan

    payload = await prepare_watchlist_item_scan(
        item_id=item_id,
        current_user=current_user,
    )
    background_tasks.add_task(run_watchlist_scan_task, payload["item_id"])
    return SuccessResponse(success=payload["success"], message=payload["message"])
