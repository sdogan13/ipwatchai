"""Service helpers for trademark upload flows."""

import io
import logging
import os
import tempfile
from typing import List, Optional

import pandas as pd
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from database.crud import Database


logger = logging.getLogger(__name__)


COLUMN_ALIASES = {
    "trademark_name": [
        "marka adÄ±",
        "marka",
        "name",
        "brand",
        "trademark",
        "mark",
        "isim",
        "ad",
        "marka adi",
    ],
    "nice_classes": [
        "sÄ±nÄ±flar",
        "sÄ±nÄ±f",
        "classes",
        "class",
        "nice",
        "nice class",
        "sinif",
        "siniflar",
        "siniflar",
    ],
    "application_no": [
        "baÅŸvuru no",
        "baÅŸvuru numarasÄ±",
        "application number",
        "app no",
        "basvuru no",
        "basvuru numarasi",
    ],
    "owner": [
        "hak sahibi",
        "sahip",
        "owner",
        "holder",
        "applicant",
        "baÅŸvurucu",
        "basvurucu",
    ],
    "status": ["durum", "status", "state"],
    "description": ["aciklama", "aÃ§Ä±klama", "description", "notes", "notlar"],
}


def find_column(df_columns: List[str], field_name: str) -> Optional[str]:
    """Find matching column name from aliases."""
    aliases = COLUMN_ALIASES.get(field_name, [])
    for col in df_columns:
        col_lower = col.lower().strip()
        if col_lower in aliases:
            return col
        for alias in aliases:
            if alias in col_lower:
                return col
    return None


def parse_nice_classes(value) -> List[int]:
    """Parse Nice classes from various formats."""
    if pd.isna(value) or not value:
        return []

    import re

    value = str(value).strip()
    classes = []

    for part in re.split(r"[,\\s;/]+", value):
        try:
            cls = int(float(part))
            if 1 <= cls <= 45:
                classes.append(cls)
        except Exception:
            continue

    return sorted(list(set(classes)))


def build_upload_template_response(
    *,
    dataframe_lib=pd,
) -> StreamingResponse:
    """Create a sample Excel template for trademark uploads."""
    sample_data = {
        "Marka Adi": ["ORNEK MARKA 1", "Sample Brand 2", "Marka Ornegi 3"],
        "Siniflar": ["35", "25, 35, 42", "9, 35"],
        "Basvuru No": ["2025/123456", "2025/789012", ""],
        "Hak Sahibi": ["ABC Sirketi Ltd.", "XYZ Company", ""],
        "Aciklama": ["Tekstil markasi", "Yazilim hizmetleri", ""],
    }

    df = dataframe_lib.DataFrame(sample_data)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=marka_sablonu.xlsx"},
    )


async def process_trademark_upload(
    *,
    file,
    add_to_watchlist: bool,
    run_analysis: bool,
    alert_threshold: float,
    current_user,
    db_factory=Database,
    dataframe_lib=pd,
    temp_file_factory=tempfile.NamedTemporaryFile,
    file_exists=os.path.exists,
    file_unlink=os.unlink,
    uuid_factory=None,
):
    """Validate, parse, and optionally add uploaded trademarks to watchlist."""
    _ = run_analysis
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(
            status_code=400,
            detail="Desteklenmeyen dosya formati. Excel (.xlsx, .xls) veya CSV (.csv) yukleyin.",
        )

    temp_path = None
    uuid_builder = uuid_factory
    if uuid_builder is None:
        from uuid import uuid4

        uuid_builder = uuid4

    try:
        content = await file.read()
        file_size = len(content)
        file_size_mb = file_size / (1024 * 1024)

        if filename.endswith((".xlsx",)):
            if len(content) < 4 or content[:4] != b"PK\x03\x04":
                raise HTTPException(
                    status_code=400,
                    detail="Gecersiz XLSX dosyasi. Dosya icerigi Excel formatina uymuyor.",
                )
        elif filename.endswith((".xls",)):
            if len(content) < 8 or content[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                raise HTTPException(
                    status_code=400,
                    detail="Gecersiz XLS dosyasi. Dosya icerigi Excel formatina uymuyor.",
                )
        elif filename.endswith((".csv",)):
            try:
                content[:1024].decode("utf-8")
            except UnicodeDecodeError:
                try:
                    content[:1024].decode("latin-1")
                except UnicodeDecodeError:
                    raise HTTPException(
                        status_code=400,
                        detail="Gecersiz CSV dosyasi. Metin kodlamasi tanÄ±namadi.",
                    )

        max_upload_size = 50 * 1024 * 1024
        warning_size = 20 * 1024 * 1024

        if file_size > max_upload_size:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Dosya cok buyuk ({file_size_mb:.1f} MB). Maksimum: 100 MB. "
                    "Ipucu: Dosyayi parcalara bolun."
                ),
            )

        is_large_file = file_size > warning_size

        suffix = os.path.splitext(filename)[1]
        temp_file = temp_file_factory(delete=False, suffix=suffix)
        temp_file.write(content)
        temp_file.close()
        temp_path = temp_file.name

        df = None
        if filename.endswith(".csv"):
            for encoding in ["utf-8", "utf-8-sig", "latin1", "cp1254", "iso-8859-9"]:
                try:
                    df = dataframe_lib.read_csv(temp_path, encoding=encoding)
                    break
                except Exception:
                    continue
            if df is None:
                raise HTTPException(
                    status_code=400,
                    detail="CSV dosyasi okunamadi. Encoding hatasi.",
                )
        else:
            df = dataframe_lib.read_excel(temp_path)

        name_col = find_column(df.columns.tolist(), "trademark_name")
        class_col = find_column(df.columns.tolist(), "nice_classes")
        app_no_col = find_column(df.columns.tolist(), "application_no")
        owner_col = find_column(df.columns.tolist(), "owner")
        desc_col = find_column(df.columns.tolist(), "description")

        if not name_col:
            raise HTTPException(
                status_code=400,
                detail="'Marka Adi' sutunu bulunamadi. Lutfen dosyanizda 'Marka Adi', 'Name', veya 'Trademark' sutunu olduggundan emin olun.",
            )

        if not class_col:
            raise HTTPException(
                status_code=400,
                detail="'Siniflar' sutunu bulunamadi. Lutfen dosyanizda 'Siniflar', 'Classes', veya 'Nice Class' sutunu oldugundan emin olun.",
            )

        trademarks = []
        errors = []

        for idx, row in df.iterrows():
            try:
                name = str(row[name_col]).strip() if pd.notna(row[name_col]) else ""
                classes = parse_nice_classes(row[class_col])
                app_no = (
                    str(row[app_no_col]).strip()
                    if app_no_col and pd.notna(row.get(app_no_col))
                    else None
                )
                owner = (
                    str(row[owner_col]).strip()
                    if owner_col and pd.notna(row.get(owner_col))
                    else None
                )
                desc = (
                    str(row[desc_col]).strip()
                    if desc_col and pd.notna(row.get(desc_col))
                    else None
                )

                if not name or name.lower() == "nan":
                    errors.append(f"Satir {idx + 2}: Marka adi bos")
                    continue

                if not classes:
                    errors.append(
                        f"Satir {idx + 2}: '{name}' icin gecerli sinif bulunamadi"
                    )
                    continue

                trademarks.append(
                    {
                        "row": idx + 2,
                        "name": name,
                        "classes": classes,
                        "application_no": app_no,
                        "owner": owner,
                        "description": desc,
                    }
                )
            except Exception as exc:
                errors.append(f"Satir {idx + 2}: Hata - {str(exc)}")

        if not trademarks:
            raise HTTPException(
                status_code=400,
                detail=f"Gecerli marka bulunamadi. Hatalar: {'; '.join(errors[:5])}",
            )

        watchlist_results = []
        if add_to_watchlist:
            with db_factory() as db:
                cur = db.cursor()

                for tm in trademarks:
                    try:
                        cur.execute(
                            """
                            SELECT id FROM watchlist_mt
                            WHERE organization_id = %s AND LOWER(brand_name) = LOWER(%s)
                        """,
                            (str(current_user.organization_id), tm["name"]),
                        )

                        existing = cur.fetchone()

                        if existing:
                            watchlist_results.append(
                                {
                                    "name": tm["name"],
                                    "status": "exists",
                                    "message": "Zaten izleme listesinde",
                                }
                            )
                            continue

                        item_id = str(uuid_builder())
                        cur.execute(
                            """
                            INSERT INTO watchlist_mt (
                                id, user_id, organization_id, brand_name,
                                nice_class_numbers, description, alert_threshold,
                                customer_application_no, is_active, created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW()
                            )
                        """,
                            (
                                item_id,
                                str(current_user.id),
                                str(current_user.organization_id),
                                tm["name"],
                                tm["classes"],
                                tm.get("description")
                                or f"Dosyadan eklendi: {file.filename}",
                                alert_threshold,
                                tm.get("application_no"),
                            ),
                        )

                        watchlist_results.append(
                            {
                                "name": tm["name"],
                                "status": "added",
                                "watchlist_id": item_id,
                            }
                        )
                    except Exception as exc:
                        watchlist_results.append(
                            {
                                "name": tm["name"],
                                "status": "error",
                                "message": str(exc),
                            }
                        )

                db.commit()

        response = {
            "success": True,
            "file_name": file.filename,
            "file_size_mb": round(file_size_mb, 1),
            "total_rows": len(df),
            "valid_trademarks": len(trademarks),
            "trademarks": trademarks,
            "validation_errors": errors,
            "watchlist_results": watchlist_results if add_to_watchlist else None,
        }

        if is_large_file:
            response["warning"] = (
                f"Buyuk dosya islendi ({file_size_mb:.1f} MB). Gelecekte daha hizli "
                "yukleme icin dosyayi parcalara bolmeyi dusunun."
            )

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("File upload error: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Dosya isleme hatasi. Lutfen tekrar deneyin.",
        )
    finally:
        if temp_path and file_exists(temp_path):
            file_unlink(temp_path)
