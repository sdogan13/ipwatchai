"""Search service helpers used by HTTP route modules."""

import csv
import datetime
import inspect
import io
import os
import time
from datetime import date
from uuid import uuid4

import psycopg2
import psycopg2.extras
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg2 import sql as psql

from services.scoring_service import (
    _calculate_visual_breakdown,
    build_logo_image_profile,
    resolve_logo_image_path,
)

PUBLIC_SEARCH_CLIENT_COOKIE = "public_search_client_id"
PUBLIC_SEARCH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365

_LEGACY_MOJIBAKE_FOLD_REPLACEMENTS = [
    ((195, 8222, 197, 184), "'g'"),       # double-encoded ğ
    ((195, 8222, 197, 190), "'g'"),       # double-encoded Ğ
    ((195, 8222, 194, 177), "'i'"),       # double-encoded ı
    ((195, 8222, 194, 176), "'i'"),       # double-encoded İ
    ((195, 402, 194, 182), "'o'"),        # double-encoded ö
    ((195, 402, 226, 8364, 8220), "'o'"), # double-encoded Ö
    ((195, 402, 194, 188), "'u'"),        # double-encoded ü
    ((195, 402, 197, 8220), "'u'"),       # double-encoded Ü
    ((195, 8230, 197, 184), "'s'"),       # double-encoded ş
    ((195, 8230, 197, 190), "'s'"),       # double-encoded Ş
    ((195, 402, 194, 167), "'c'"),        # double-encoded ç
    ((195, 402, 226, 8364, 161), "'c'"),  # double-encoded Ç
]


def _sql_chr_literal(codes) -> str:
    return " || ".join(f"CHR({code})" for code in codes)


def _legacy_mojibake_normalize_sql(column: str) -> str:
    """Fold legacy double-encoded Turkish text without embedding mojibake literals."""
    expr = column
    for source_codes, target in _LEGACY_MOJIBAKE_FOLD_REPLACEMENTS:
        expr = f"REPLACE({expr}, {_sql_chr_literal(source_codes)}, {target})"
    return f"LOWER({expr})"


def resolve_public_search_client_id(request, id_factory=None):
    """Return the stable anonymous client id used for landing-page quotas."""
    cookie_value = request.cookies.get(PUBLIC_SEARCH_CLIENT_COOKIE) if request else None
    if cookie_value and str(cookie_value).strip():
        return str(cookie_value).strip(), False

    if id_factory is None:
        id_factory = lambda: uuid4().hex

    return str(id_factory()), True


def get_daily_public_searches(db, client_id: str, today_factory=None) -> int:
    """Get the number of public landing-page searches used today by this anonymous client."""
    if today_factory is None:
        today_factory = date.today

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT searches
        FROM public_search_usage
        WHERE client_id = %s AND usage_date = %s
        """,
        (client_id, today_factory()),
    )
    row = cur.fetchone()
    return int(row["searches"]) if row else 0


def increment_public_search_usage(db, client_id: str, today_factory=None) -> int:
    """Increment the anonymous public-search usage counter for today."""
    if today_factory is None:
        today_factory = date.today

    cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        INSERT INTO public_search_usage (client_id, usage_date, searches)
        VALUES (%s, %s, 1)
        ON CONFLICT (client_id, usage_date)
        DO UPDATE SET
            searches = public_search_usage.searches + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING searches
        """,
        (client_id, today_factory()),
    )
    db.commit()
    row = cur.fetchone()
    return int(row["searches"]) if row else 1


def get_public_search_daily_limit(plan_features=None) -> int:
    """Return the public landing-page daily quota from the public plan surface."""
    if plan_features is None:
        from utils.subscription import PLAN_FEATURES

        plan_features = PLAN_FEATURES

    free_plan = plan_features.get("free", {})
    try:
        return int(free_plan.get("max_daily_quick_searches", 5))
    except (TypeError, ValueError):
        return 5


def check_public_search_eligibility(db, client_id: str, daily_limit_getter=None, today_factory=None):
    """Check whether an anonymous visitor can still use the landing-page free-search quota today."""
    if daily_limit_getter is None:
        daily_limit_getter = get_public_search_daily_limit

    daily_limit = int(daily_limit_getter())
    used_today = get_daily_public_searches(db, client_id, today_factory=today_factory)

    if used_today >= daily_limit:
        return False, "daily_limit_exceeded", {
            "error": "daily_limit_exceeded",
            "current_plan": "free",
            "upgrade_context": "public_search",
            "daily_limit": daily_limit,
            "used_today": used_today,
            "remaining": 0,
        }

    return True, "ok", {
        "current_plan": "free",
        "upgrade_context": "public_search",
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining": max(0, daily_limit - used_today),
    }


def _compact_translation_text(value) -> str:
    from utils.idf_scoring import normalize_turkish

    return normalize_turkish(value or "").replace(" ", "")


def _is_duplicate_name_translation(name, name_tr) -> bool:
    name_compact = _compact_translation_text(name)
    name_tr_compact = _compact_translation_text(name_tr)
    return bool(name_compact and name_tr_compact and name_compact == name_tr_compact)


def _display_translation_similarity(result, scores) -> float:
    if _is_duplicate_name_translation(
        result.get("trademark_name") or result.get("name"),
        result.get("name_tr"),
    ):
        return 0.0
    return scores.get("translation_similarity", 0)


def _map_public_search_results(results, status_code_getter=None):
    """Convert raw public search results into the limited response shape."""
    safe_results = []
    for result in (results or [])[:10]:
        scores = result.get("scores") or {}
        trademark_name = result.get("trademark_name") or result.get("name")
        translation_similarity = _display_translation_similarity(result, scores)
        effective_text_score = scores.get("text_idf_score")
        if effective_text_score is None:
            effective_text_score = scores.get("text_similarity", 0)
        image_path = result.get("image_path")
        image_url = f"/api/trademark-image/{image_path}" if image_path else None
        safe_results.append(
            {
                "trademark_name": trademark_name,
                "application_no": result.get("application_no"),
                "status": result.get("status"),
                "status_code": (
                    status_code_getter(result.get("status"))
                    if status_code_getter
                    else None
                ),
                "risk_score": (
                    scores.get("total")
                    if scores.get("total") is not None
                    else result.get("risk_score", 0)
                ),
                "nice_classes": result.get("classes") or result.get("nice_classes") or [],
                "image_url": image_url,
                "name_tr": result.get("name_tr"),
                "holder_name": result.get("holder_name"),
                "holder_tpe_client_id": result.get("holder_tpe_client_id"),
                "attorney_name": result.get("attorney_name"),
                "attorney_no": result.get("attorney_no"),
                "application_date": result.get("application_date"),
                "registration_no": result.get("registration_no"),
                "scoring_path": scores.get("scoring_path"),
                "text_similarity": round(scores.get("text_similarity", 0), 3),
                "text_idf_score": round(effective_text_score, 3),
                "path_a_score": round(
                    scores.get("path_a_score", scores.get("text_similarity", 0)),
                    3,
                ),
                "path_b_score": round(scores.get("path_b_score", 0), 3),
                "scoring_path_source": scores.get("scoring_path_source"),
                "visual_similarity": round(scores.get("visual_similarity", 0), 3),
                "translation_similarity": round(translation_similarity, 3),
                "phonetic_similarity": round(scores.get("phonetic_similarity", 0), 3),
                "has_extracted_goods": result.get("has_extracted_goods", False),
                "extracted_goods": result.get("extracted_goods"),
            }
        )
    return safe_results


def _format_date_for_search(value, date_formatter):
    if not value:
        return None
    if isinstance(value, str):
        return value
    return date_formatter(value)


def _map_risk_engine_results_for_enhanced_search(
    results,
    *,
    search_classes,
    date_formatter,
    status_code_getter,
    image_url_getter,
):
    searched_classes_set = set(search_classes or [])
    mapped = []
    for result in results or []:
        scores = result.get("scores") or {}
        result_classes = result.get("classes") or result.get("nice_classes") or []
        overlap_count = (
            len(searched_classes_set.intersection(set(result_classes)))
            if searched_classes_set
            else 0
        )
        score_val = scores.get("total")
        if score_val is None:
            score_val = result.get("risk_score", 0)
        try:
            similarity_pct = round(float(score_val or 0.0) * 100, 1)
        except (TypeError, ValueError):
            similarity_pct = 0.0

        image_path = result.get("image_path")
        application_no = result.get("application_no")
        bulletin_no = result.get("bulletin_no")
        mapped.append(
            {
                "id": str(result.get("trademark_id") or result.get("id") or application_no),
                "name": result.get("name") or result.get("trademark_name") or "",
                "application_no": application_no,
                "application_date": _format_date_for_search(
                    result.get("application_date"),
                    date_formatter,
                ),
                "registration_date": _format_date_for_search(
                    result.get("registration_date"),
                    date_formatter,
                ),
                "status": result.get("status") or "Bilinmiyor",
                "status_code": status_code_getter(result.get("status")),
                "nice_classes": result_classes,
                "owner": result.get("holder_name"),
                "holder_tpe_client_id": result.get("holder_tpe_client_id"),
                "attorney": result.get("attorney_name"),
                "attorney_no": result.get("attorney_no"),
                "registration_no": result.get("registration_no"),
                "bulletin_no": bulletin_no,
                "image_url": image_url_getter(
                    image_path,
                    application_no,
                    bulletin_no,
                ),
                "similarity": similarity_pct,
                "name_similarity": similarity_pct,
                "text_similarity": round(scores.get("text_similarity", 0), 3),
                "text_idf_score": round(
                    scores.get("text_idf_score", scores.get("text_similarity", 0)),
                    3,
                ),
                "path_a_score": round(
                    scores.get("path_a_score", scores.get("text_similarity", 0)),
                    3,
                ),
                "path_b_score": round(scores.get("path_b_score", 0), 3),
                "translation_similarity": round(
                    _display_translation_similarity(result, scores),
                    3,
                ),
                "scoring_path_source": scores.get("scoring_path_source"),
                "scores": scores,
                "class_overlap_count": overlap_count,
            }
        )
    return mapped


async def run_public_search(
    query,
    image_path=None,
    nice_classes=None,
    status_code_getter=None,
    logger=None,
    searcher_factory=None,
):
    """Run the public landing-page search flow."""
    if searcher_factory is None:
        from agentic_search import AgenticTrademarkSearch

        searcher_factory = AgenticTrademarkSearch

    try:
        with searcher_factory(auto_scrape=False) as searcher:
            result = searcher.search(
                query=query,
                nice_classes=nice_classes,
                image_path=image_path,
            )

        safe_results = _map_public_search_results(
            result.get("results"),
            status_code_getter=status_code_getter,
        )
        return {
            "query": query,
            "results": safe_results,
            "total": len(safe_results),
        }
    except Exception as exc:
        if logger:
            logger.error(f"Public search failed: {exc}")
        raise


def resolve_public_portfolio_lookup(holder_id, attorney_no):
    """Validate the lookup parameters and choose the active portfolio selector."""
    if not holder_id and not attorney_no:
        raise HTTPException(status_code=400, detail="holder_id or attorney_no required")

    if holder_id:
        return "holder_tpe_client_id", holder_id, "holder"
    return "attorney_no", attorney_no, "attorney"


def _map_public_portfolio_results(rows):
    """Convert trademark rows into the public portfolio response shape."""
    results = []
    for trademark in rows:
        image_path = trademark.get("image_path")
        image_url = f"/api/trademark-image/{image_path}" if image_path else None
        results.append(
            {
                "trademark_name": trademark.get("name"),
                "application_no": trademark.get("application_no"),
                "status": trademark.get("final_status"),
                "nice_classes": trademark.get("nice_class_numbers") or [],
                "image_url": image_url,
                "holder_name": trademark.get("holder_name"),
                "holder_tpe_client_id": trademark.get("holder_tpe_client_id"),
                "attorney_name": trademark.get("attorney_name"),
                "attorney_no": trademark.get("attorney_no"),
                "application_date": (
                    trademark["application_date"].isoformat()
                    if trademark.get("application_date")
                    else None
                ),
                "registration_no": trademark.get("registration_no"),
            }
        )
    return results


async def run_public_portfolio_lookup(
    holder_id=None,
    attorney_no=None,
    logger=None,
    database_factory=None,
):
    """Run the public portfolio lookup used by the landing page."""
    if database_factory is None:
        from database.crud import Database

        database_factory = Database

    where_col, param, entity_type = resolve_public_portfolio_lookup(holder_id, attorney_no)

    try:
        with database_factory() as db:
            cur = db.cursor()

            cur.execute(
                psql.SQL("SELECT COUNT(*) as cnt FROM trademarks WHERE {} = %s").format(
                    psql.Identifier(where_col)
                ),
                (param,),
            )
            total_count = cur.fetchone()["cnt"]

            cur.execute(
                psql.SQL(
                    """
                SELECT application_no, name, final_status, nice_class_numbers,
                       application_date, image_path, holder_name, holder_tpe_client_id,
                       attorney_name, attorney_no, registration_no
                FROM trademarks
                WHERE {} = %s
                ORDER BY application_date DESC NULLS LAST
                LIMIT 100
            """
                ).format(psql.Identifier(where_col)),
                (param,),
            )

            rows = cur.fetchall()

        results = _map_public_portfolio_results(rows)

        entity_name = ""
        if rows:
            if entity_type == "holder":
                entity_name = rows[0].get("holder_name") or ""
            else:
                entity_name = rows[0].get("attorney_name") or ""

        return {
            "entity_type": entity_type,
            "entity_name": entity_name,
            "entity_id": param,
            "results": results,
            "total_count": total_count,
        }
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public portfolio failed: {exc}")
        raise


async def build_public_portfolio_csv(
    holder_id=None,
    attorney_no=None,
    logger=None,
    database_factory=None,
    current_user=None,
    user_plan_getter=None,
    plan_limit_getter=None,
):
    """Build the CSV export for a holder or attorney portfolio."""
    if database_factory is None:
        from database.crud import Database

        database_factory = Database
    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan
    if plan_limit_getter is None:
        from utils.subscription import get_plan_limit

        plan_limit_getter = get_plan_limit

    where_col, param, entity_type = resolve_public_portfolio_lookup(holder_id, attorney_no)

    try:
        with database_factory() as db:
            if current_user is not None:
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
                psql.SQL(
                    """
                SELECT application_no, name, final_status,
                       nice_class_numbers, application_date, registration_date,
                       registration_no, holder_name, attorney_name, attorney_no,
                       bulletin_no, gazette_no
                FROM trademarks
                WHERE {} = %s
                ORDER BY application_date DESC NULLS LAST, application_no DESC
            """
                ).format(psql.Identifier(where_col)),
                (param,),
            )
            rows = cur.fetchall()

            if rows:
                name_key = "holder_name" if entity_type == "holder" else "attorney_name"
                entity_name = rows[0].get(name_key) or param
            else:
                entity_name = param

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
                "Vekil",
                "Vekil No",
                "Bulten No",
                "Gazete No",
            ]
        )
        for trademark in rows:
            writer.writerow(
                [
                    trademark.get("name") or "",
                    trademark.get("application_no") or "",
                    trademark.get("final_status") or "",
                    "; ".join(str(c) for c in (trademark.get("nice_class_numbers") or [])),
                    trademark["application_date"].isoformat()
                    if trademark.get("application_date")
                    else "",
                    trademark["registration_date"].isoformat()
                    if trademark.get("registration_date")
                    else "",
                    trademark.get("registration_no") or "",
                    trademark.get("holder_name") or "",
                    trademark.get("attorney_name") or "",
                    trademark.get("attorney_no") or "",
                    trademark.get("bulletin_no") or "",
                    trademark.get("gazette_no") or "",
                ]
            )

        safe_name = "".join(
            c if c.isascii() and (c.isalnum() or c in " _-") else "_"
            for c in entity_name
        )[:50]
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}_portfolio.csv"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public portfolio CSV failed: {exc}")
        raise


async def get_search_credits_summary(
    current_user,
    database_factory=None,
    user_plan_getter=None,
    now_factory=None,
):
    """Return authenticated search credit info for the current user."""
    if database_factory is None:
        from database.crud import Database

        database_factory = Database

    if user_plan_getter is None:
        from utils.subscription import get_user_plan

        user_plan_getter = get_user_plan

    if now_factory is None:
        now_factory = lambda: datetime.datetime.now(datetime.timezone.utc)

    with database_factory() as db:
        plan = user_plan_getter(db, str(current_user.id))

    now = now_factory()
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    return {
        "display_name": plan.get("display_name", plan.get("plan_name", "Free")),
        "resets_on": tomorrow.isoformat(),
    }


def _apply_deprecation_headers(response):
    """Attach deprecation headers when the legacy adapter returns JSON."""
    if isinstance(response, JSONResponse):
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "2026-03-10"
    return response


async def run_legacy_simple_search(
    request,
    q,
    limit,
    search_request_factory,
    enhanced_search_handler,
    risk_level_getter,
    logger,
):
    """Build the deprecated simple-search response from enhanced search results."""
    logger.warning("Deprecated /api/search/simple called - redirect to /api/search")
    search_req = search_request_factory(name=q, limit=limit)
    result = await enhanced_search_handler(request, search_req)

    simple_results = []
    for item in result.results:
        simple_results.append(
            {
                "id": item.id,
                "name": item.name,
                "application_no": item.application_no,
                "nice_classes": item.nice_classes,
                "final_status": item.status,
                "application_date": item.application_date,
                "holder_name": item.owner,
                "bulletin_no": item.bulletin_no,
                "image_url": item.image_url,
                "score": round(item.similarity / 100, 4),
                "risk_level": risk_level_getter(item.similarity / 100),
            }
        )

    response = JSONResponse(
        content={
            "query": q,
            "count": len(simple_results),
            "results": simple_results,
        }
    )
    return _apply_deprecation_headers(response)


def _parse_legacy_classes(classes):
    """Parse deprecated class strings without raising on bad user input."""
    if not classes:
        return None

    try:
        return [int(value.strip()) for value in classes.split(",") if value.strip()]
    except ValueError:
        return None


async def run_legacy_unified_search(
    request,
    name,
    image,
    classes,
    goods_description,
    limit,
    search_request_factory,
    enhanced_search_handler,
    search_by_image_handler,
    risk_level_getter,
    logger,
):
    """Build the deprecated unified-search response for legacy form clients."""
    logger.warning("Deprecated /api/search/unified called - redirecting")

    has_image = image is not None and getattr(image, "filename", None)

    if has_image:
        result = await search_by_image_handler(
            request=request,
            image=image,
            name=name,
            classes=classes,
            limit=limit,
        )
        response = JSONResponse(content=result) if isinstance(result, dict) else result
        return _apply_deprecation_headers(response)

    if not (name and name.strip()):
        raise HTTPException(status_code=400, detail="Marka adi veya gorsel gerekli")

    class_list = _parse_legacy_classes(classes)
    search_req = search_request_factory(
        name=name,
        classes=class_list,
        goods_description=goods_description,
        limit=limit,
    )
    enhanced_result = await enhanced_search_handler(request, search_req)

    results_dicts = []
    for item in enhanced_result.results:
        results_dicts.append(
            {
                "id": item.id,
                "name": item.name,
                "application_no": item.application_no,
                "status": item.status,
                "nice_classes": item.nice_classes,
                "bulletin_no": item.bulletin_no,
                "image_url": item.image_url,
                "similarity": item.similarity,
                "name_similarity": item.name_similarity,
                "risk_level": (
                    risk_level_getter(item.similarity / 100) if item.similarity else "low"
                ),
            }
        )

    response = JSONResponse(
        content={
            "success": True,
            "results": results_dicts,
            "search_type": "text",
            "search_context": {
                "searched_name": name,
                "searched_classes": class_list or [],
                "total_results": len(results_dicts),
                "search_time_ms": enhanced_result.search_time_ms,
            },
            "classes_were_auto_suggested": enhanced_result.classes_were_auto_suggested,
        }
    )
    return _apply_deprecation_headers(response)


async def run_enhanced_search(
    search_request,
    settings,
    logger,
    normalize_turkish_fn,
    score_pair_fn,
    visual_similarity_fn,
    class_suggestions_handler,
    text_embedding_getter,
    encode_query_image_handler,
    date_formatter,
    status_code_getter,
    image_url_getter,
    connect_fn=None,
    timer=None,
    risk_engine_factory=None,
):
    """Run the enhanced text and optional image search flow."""
    if connect_fn is None:
        connect_fn = psycopg2.connect
    if timer is None:
        timer = time.time

    start_time = timer()

    search_classes = search_request.classes or []
    auto_suggested = []
    classes_were_auto_suggested = False
    suggestion_query = None

    if (
        not search_classes
        and search_request.goods_description
        and search_request.auto_suggest_classes
    ):
        if logger:
            logger.info(
                f"Auto-suggesting classes for: {search_request.goods_description[:50]}..."
            )

        try:
            suggestions = class_suggestions_handler(
                goods_description=search_request.goods_description,
                trademark_name=search_request.name,
                limit=5,
            )
            if inspect.isawaitable(suggestions):
                suggestions = await suggestions
            top_suggestions = [item for item in suggestions if item["similarity"] > 0.3][:3]

            if top_suggestions:
                search_classes = [item["class_number"] for item in top_suggestions]
                classes_were_auto_suggested = True
                suggestion_query = (
                    f"{search_request.name}: {search_request.goods_description}"
                )
                auto_suggested = [
                    {
                        "class_number": item["class_number"],
                        "class_name": item["class_name"],
                        "similarity_score": round(item["similarity"], 4),
                    }
                    for item in top_suggestions
                ]
                if logger:
                    logger.info(f"Auto-suggested classes: {search_classes}")
            elif logger:
                logger.warning("No classes met similarity threshold (0.3)")

        except Exception as exc:
            if logger:
                logger.error(f"Class suggestion failed: {exc}")

    use_unified = settings.use_unified_scoring
    if use_unified:
        temp_image_path = None
        engine = None
        try:
            image_path = None
            if search_request.image_url:
                import tempfile
                import urllib.request

                temp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                try:
                    urllib.request.urlretrieve(search_request.image_url, temp_img.name)
                    temp_img.close()
                    temp_image_path = temp_img.name
                    image_path = temp_image_path
                except Exception:
                    temp_img.close()
                    if os.path.exists(temp_img.name):
                        os.unlink(temp_img.name)
                    raise

            if risk_engine_factory is None:
                from risk_engine import RiskEngine

                risk_engine_factory = RiskEngine

            engine = risk_engine_factory()
            risk_result, _ = engine.assess_brand_risk(
                name=search_request.name,
                image_path=image_path,
                target_classes=search_classes if search_classes else None,
                attorney_no=search_request.attorney_no,
            )
            limit = search_request.limit if getattr(search_request, "limit", None) else 100
            mapped_results = _map_risk_engine_results_for_enhanced_search(
                (risk_result.get("top_candidates") or [])[:limit],
                search_classes=search_classes,
                date_formatter=date_formatter,
                status_code_getter=status_code_getter,
                image_url_getter=image_url_getter,
            )
            search_time = (timer() - start_time) * 1000
            return {
                "results": mapped_results,
                "search_context": {
                    "searched_name": search_request.name,
                    "searched_classes": search_classes,
                    "goods_description": search_request.goods_description,
                    "total_results": len(mapped_results),
                    "search_time_ms": round(search_time, 2),
                },
                "query": search_request.name,
                "total_results": len(mapped_results),
                "search_time_ms": round(search_time, 2),
                "search_classes": search_classes,
                "classes_were_auto_suggested": classes_were_auto_suggested,
                "auto_suggested_classes": (
                    auto_suggested
                    if search_request.include_suggested_in_response and auto_suggested
                    else None
                ),
                "suggestion_query": (
                    suggestion_query if search_request.include_suggested_in_response else None
                ),
            }
        except Exception as exc:
            if logger:
                logger.error(f"Enhanced unified search error: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))
        finally:
            if engine is not None and hasattr(engine, "close"):
                try:
                    engine.close()
                except Exception:
                    pass
            if temp_image_path and os.path.exists(temp_image_path):
                os.unlink(temp_image_path)

    conn = None
    cur = None

    try:
        conn = connect_fn(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        name_normalized = normalize_turkish_fn(search_request.name)

        normalize_sql = _legacy_mojibake_normalize_sql("t.name")

        tokens = [token for token in name_normalized.split() if len(token) > 2]
        token_clauses_simple = "FALSE"
        token_params = []
        if len(tokens) > 1:
            token_clauses_simple = "(" + " AND ".join(
                [f"{normalize_sql} LIKE %s" for _ in tokens]
            ) + ")"
            token_params = [f"%{token}%" for token in tokens]

        base_select = f"""
            SELECT
                t.id,
                t.application_no,
                t.name,
                t.final_status,
                t.nice_class_numbers,
                t.application_date,
                t.registration_date,
                t.bulletin_no,
                t.image_path,
                t.holder_name,
                t.holder_tpe_client_id,
                t.attorney_name,
                t.attorney_no,
                t.registration_no,
                t.name_tr,
                t.logo_ocr_text,
                t.text_embedding,
                t.image_embedding,
                t.dinov2_embedding,
                t.color_histogram,
                GREATEST(
                    similarity(LOWER(t.name), LOWER(%s)),
                    similarity({normalize_sql}, LOWER(%s))
                ) as score,
                (LOWER(t.name) LIKE LOWER(%s)) as exact_match,
                (dmetaphone(t.name) = dmetaphone(%s)) as phonetic_match
            FROM trademarks t
        """

        token_where_clause = f" OR {token_clauses_simple}" if len(tokens) > 1 else ""

        where_clause = f"""
            WHERE (
                LOWER(t.name) LIKE LOWER(%s)
                OR {normalize_sql} LIKE LOWER(%s)
                OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                OR similarity({normalize_sql}, LOWER(%s)) > 0.2
                {token_where_clause}
            )
        """

        base_params = [
            search_request.name,
            name_normalized,
            search_request.name,
            search_request.name,
        ]

        where_params = [
            f"%{search_request.name}%",
            f"%{name_normalized}%",
            search_request.name,
            name_normalized,
        ] + token_params

        if search_classes:
            where_clause += (
                " AND (t.nice_class_numbers && %s::integer[] OR 99 = ANY(t.nice_class_numbers))"
            )
            where_params.append(search_classes)

        if search_request.attorney_no:
            where_clause += " AND t.attorney_no = %s"
            where_params.append(search_request.attorney_no)

        order_limit = " ORDER BY score DESC, t.name LIMIT 100"

        cur.execute(base_select + where_clause + order_limit, base_params + where_params)
        rows = cur.fetchall()

        query_text_vec = text_embedding_getter(search_request.name)

        query_img_data = None
        if search_request.image_url and use_unified:
            try:
                import tempfile
                import urllib.request

                temp_img = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                try:
                    urllib.request.urlretrieve(search_request.image_url, temp_img.name)
                    temp_img.close()
                    query_img_data = encode_query_image_handler(temp_img.name)
                finally:
                    if os.path.exists(temp_img.name):
                        os.unlink(temp_img.name)
            except Exception as exc:
                if logger:
                    logger.warning(f"Image URL processing failed: {exc}")

        searched_classes_set = set(search_classes) if search_classes else set()
        results = []

        for row in rows:
            result_classes = row["nice_class_numbers"] or []
            overlap_count = (
                len(searched_classes_set.intersection(set(result_classes)))
                if searched_classes_set
                else 0
            )
            target_name = row["name"] or ""

            pg_text_sim = float(row["score"]) if row["score"] else 0.0
            phon_match = bool(row.get("phonetic_match"))

            semantic_sim = 0.0
            cand_text_emb = row.get("text_embedding")
            if query_text_vec and cand_text_emb:
                try:
                    import numpy as np

                    query_array = np.array(
                        query_text_vec
                        if isinstance(query_text_vec, list)
                        else list(query_text_vec),
                        dtype=np.float32,
                    )
                    if isinstance(cand_text_emb, str):
                        candidate_array = np.array(
                            [float(value) for value in cand_text_emb.strip("[]").split(",")],
                            dtype=np.float32,
                        )
                    else:
                        candidate_array = np.array(list(cand_text_emb), dtype=np.float32)
                    dot = np.dot(query_array, candidate_array)
                    norms = np.linalg.norm(query_array) * np.linalg.norm(candidate_array)
                    semantic_sim = float(dot / norms) if norms > 0 else 0.0
                except Exception:
                    semantic_sim = 0.0

            vis_sim = 0.0
            if query_img_data:
                try:
                    import numpy as np

                    def _cosine(left, right_raw):
                        if left is None or right_raw is None:
                            return 0.0
                        left_array = np.array(left, dtype=np.float32)
                        if isinstance(right_raw, str):
                            right_array = np.array(
                                [float(value) for value in right_raw.strip("[]").split(",")],
                                dtype=np.float32,
                            )
                        else:
                            right_array = np.array(list(right_raw), dtype=np.float32)
                        dot = np.dot(left_array, right_array)
                        norms = np.linalg.norm(left_array) * np.linalg.norm(right_array)
                        return float(dot / norms) if norms > 0 else 0.0

                    clip_sim = _cosine(
                        query_img_data["clip_vec"],
                        row.get("image_embedding"),
                    )
                    dino_sim = _cosine(
                        query_img_data["dino_vec"],
                        row.get("dinov2_embedding"),
                    )
                    color_sim = _cosine(
                        query_img_data["color_vec"],
                        row.get("color_histogram"),
                    )
                    candidate_ocr = (row.get("logo_ocr_text") or "").strip()

                    vis_sim = visual_similarity_fn(
                        clip_sim=clip_sim,
                        dinov2_sim=dino_sim,
                        color_sim=color_sim,
                        ocr_text_a=query_img_data.get("ocr_text", ""),
                        ocr_text_b=candidate_ocr,
                    )
                except Exception:
                    vis_sim = 0.0

            score_breakdown = score_pair_fn(
                query_name=search_request.name,
                candidate_name=target_name,
                text_sim=pg_text_sim,
                semantic_sim=semantic_sim,
                visual_sim=vis_sim,
                phonetic_sim=1.0 if phon_match else 0.0,
                candidate_translations={"name_tr": row.get("name_tr") or ""},
            )

            score_val = score_breakdown["total"]
            similarity_pct = round(score_val * 100, 1)

            if target_name.lower() == "patent" and logger:
                logger.error(
                    "DEBUG_API_INJECT_PATENT | score_val: "
                    f"{score_val} | similarity_pct: {similarity_pct} | "
                    f"semantic_sim: {semantic_sim} | pg_text_sim: {pg_text_sim} | "
                    f"phon_match: {phon_match}"
                )

            results.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "application_no": row["application_no"],
                    "application_date": date_formatter(row.get("application_date")),
                    "registration_date": date_formatter(row.get("registration_date")),
                    "status": row["final_status"] or "Bilinmiyor",
                    "status_code": status_code_getter(row["final_status"]),
                    "nice_classes": result_classes,
                    "owner": row.get("holder_name"),
                    "holder_tpe_client_id": row.get("holder_tpe_client_id"),
                    "attorney": row.get("attorney_name"),
                    "attorney_no": row.get("attorney_no"),
                    "registration_no": row.get("registration_no"),
                    "bulletin_no": row.get("bulletin_no"),
                    "image_url": image_url_getter(
                        row.get("image_path"),
                        row["application_no"],
                        row.get("bulletin_no"),
                    ),
                    "similarity": similarity_pct,
                    "name_similarity": similarity_pct,
                    "class_overlap_count": overlap_count,
                }
            )

        results.sort(key=lambda item: item["similarity"], reverse=True)
        limit = search_request.limit if getattr(search_request, "limit", None) else 100
        results = results[:limit]

        search_time = (timer() - start_time) * 1000
        return {
            "results": results,
            "search_context": {
                "searched_name": search_request.name,
                "searched_classes": search_classes,
                "goods_description": search_request.goods_description,
                "total_results": len(results),
                "search_time_ms": round(search_time, 2),
            },
            "query": search_request.name,
            "total_results": len(results),
            "search_time_ms": round(search_time, 2),
            "search_classes": search_classes,
            "classes_were_auto_suggested": classes_were_auto_suggested,
            "auto_suggested_classes": (
                auto_suggested
                if search_request.include_suggested_in_response and auto_suggested
                else None
            ),
            "suggestion_query": (
                suggestion_query if search_request.include_suggested_in_response else None
            ),
        }
    except Exception as exc:
        if logger:
            logger.error(f"Enhanced search error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


async def run_image_search(
    image,
    name,
    classes,
    limit,
    process_uploaded_image_handler,
    settings,
    logger,
    global_class,
    score_pair_fn,
    visual_similarity_fn,
    risk_level_getter,
    encode_query_image_handler,
    get_image_embedding_handler,
    extract_ocr_text_handler,
    connect_fn=None,
    text_embedding_getter=None,
    name_similarity_fn=None,
):
    """Run the public image-search flow used by the upload endpoint."""
    if connect_fn is None:
        connect_fn = psycopg2.connect

    temp_path, _ = await process_uploaded_image_handler(image)

    try:
        class_list = []
        if classes:
            try:
                class_list = [int(value.strip()) for value in classes.split(",") if value.strip()]
                class_list = [value for value in class_list if (1 <= value <= 45) or value == global_class]
            except ValueError:
                pass

        use_unified = settings.use_unified_scoring

        conn = connect_fn(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("SELECT COUNT(*) as cnt FROM trademarks WHERE image_embedding IS NOT NULL")
        embedding_count = cur.fetchone()["cnt"]

        if embedding_count == 0:
            logger.warning("No image embeddings in database - returning sample results")
            class_filter = (
                "AND (nice_class_numbers && %s::int[] OR 99 = ANY(nice_class_numbers))"
                if class_list
                else ""
            )
            query = f"""
                SELECT id, name, application_no, final_status, nice_class_numbers,
                       bulletin_no, image_path
                FROM trademarks
                WHERE bulletin_no IS NOT NULL {class_filter}
                ORDER BY RANDOM() LIMIT %s
            """
            params = [class_list, limit] if class_list else [limit]
            cur.execute(query, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                image_url = f"/api/trademark-image/{row['image_path']}" if row.get("image_path") else None
                results.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"] or "-",
                        "application_no": row["application_no"],
                        "status": row["final_status"] or "-",
                        "nice_classes": row["nice_class_numbers"] or [],
                        "image_url": image_url,
                        "similarity": 0,
                        "image_similarity": 0,
                        "risk_level": "unknown",
                        "note": "Gorsel embedding veritabaninda bulunamadi - ornek sonuclar",
                    }
                )
            cur.close()
            conn.close()
            return {
                "success": True,
                "search_type": "image",
                "warning": "Gorsel embeddingler henuz olusturulmamis. Ornek sonuclar gosteriliyor.",
                "total_results": len(results),
                "classes_filtered": class_list if class_list else None,
                "results": results,
            }

        if use_unified:
            query_img_data = encode_query_image_handler(temp_path)
            clip_vec_str = "[" + ",".join(str(value) for value in query_img_data["clip_vec"]) + "]"
            dino_vec_str = (
                "[" + ",".join(str(value) for value in query_img_data["dino_vec"]) + "]"
                if query_img_data.get("dino_vec")
                else None
            )
            query_ocr_text = query_img_data.get("ocr_text", "")
            query_logo_profile = build_logo_image_profile(temp_path, query_ocr_text)
        else:
            query_embedding = get_image_embedding_handler(temp_path)
            clip_vec_str = "[" + ",".join(str(value) for value in query_embedding) + "]"
            dino_vec_str = None
            query_ocr_text = ""
            try:
                query_ocr_text = extract_ocr_text_handler(temp_path) or ""
            except Exception:
                pass
            query_logo_profile = None
        class_filter_sql = (
            "AND (t.nice_class_numbers && %s::int[] OR 99 = ANY(t.nice_class_numbers))"
            if class_list
            else ""
        )

        clip_sql = f"""
            SELECT t.id, t.name, t.application_no, t.final_status, t.nice_class_numbers,
                   t.bulletin_no, t.image_path, t.logo_ocr_text, t.name_tr,
                   t.text_embedding, t.image_embedding, t.dinov2_embedding, t.color_histogram,
                   t.holder_name, t.holder_tpe_client_id,
                   t.attorney_name, t.attorney_no, t.registration_no,
                   t.application_date, t.expiry_date,
                   1 - (t.image_embedding <=> %s::halfvec) AS clip_sim
            FROM trademarks t
            WHERE t.image_embedding IS NOT NULL {class_filter_sql}
            ORDER BY t.image_embedding <=> %s::halfvec
            LIMIT 100
        """
        clip_params = [clip_vec_str]
        if class_list:
            clip_params.append(class_list)
        clip_params.append(clip_vec_str)
        cur.execute(clip_sql, clip_params)
        clip_rows = {str(row["id"]): row for row in cur.fetchall()}

        dino_rows = {}
        if use_unified and dino_vec_str:
            dino_sql = f"""
                SELECT t.id, t.name, t.application_no, t.final_status, t.nice_class_numbers,
                       t.bulletin_no, t.image_path, t.logo_ocr_text, t.name_tr,
                       t.text_embedding, t.image_embedding, t.dinov2_embedding, t.color_histogram,
                       t.holder_name, t.holder_tpe_client_id,
                       t.attorney_name, t.attorney_no, t.registration_no,
                       t.application_date, t.expiry_date,
                       1 - (t.dinov2_embedding <=> %s::halfvec) AS dino_sim
                FROM trademarks t
                WHERE t.dinov2_embedding IS NOT NULL {class_filter_sql}
                ORDER BY t.dinov2_embedding <=> %s::halfvec
                LIMIT 100
            """
            dino_params = [dino_vec_str]
            if class_list:
                dino_params.append(class_list)
            dino_params.append(dino_vec_str)
            cur.execute(dino_sql, dino_params)
            dino_rows = {str(row["id"]): row for row in cur.fetchall()}

        merged = {**dino_rows, **clip_rows}

        query_text_vec = None
        has_typed_name = bool(name and name.strip())
        query_name = (name or "").strip()
        query_text_source = "USER_TEXT" if has_typed_name else "IMAGE_ONLY"
        if has_typed_name:
            if text_embedding_getter is None:
                from ai import get_text_embedding_cached

                text_embedding_getter = get_text_embedding_cached
            query_text_vec = text_embedding_getter(query_name)
            if name_similarity_fn is None:
                from risk_engine import calculate_name_similarity

                name_similarity_fn = calculate_name_similarity

        cur.close()
        conn.close()

        import numpy as np

        def _cosine(left, right_raw):
            if left is None or right_raw is None:
                return 0.0
            left_arr = np.array(left, dtype=np.float32)
            if isinstance(right_raw, str):
                right_arr = np.array(
                    [float(value) for value in right_raw.strip("[]").split(",")],
                    dtype=np.float32,
                )
            else:
                right_arr = np.array(list(right_raw), dtype=np.float32)
            dot = np.dot(left_arr, right_arr)
            norms = np.linalg.norm(left_arr) * np.linalg.norm(right_arr)
            return float(dot / norms) if norms > 0 else 0.0

        results = []
        for trademark_id, row in merged.items():
            candidate_name = row["name"] or ""
            candidate_ocr = (row.get("logo_ocr_text") or "").strip()
            image_url = f"/api/trademark-image/{row['image_path']}" if row.get("image_path") else None

            if use_unified:
                clip_sim = _cosine(query_img_data["clip_vec"], row.get("image_embedding"))
                dino_sim = _cosine(query_img_data.get("dino_vec"), row.get("dinov2_embedding"))
                color_sim = _cosine(query_img_data.get("color_vec"), row.get("color_histogram"))
                candidate_profile_path = resolve_logo_image_path(
                    row.get("image_path") or "",
                    roots=[
                        getattr(settings.paths, "data_root", ""),
                        getattr(settings.pipeline, "bulletins_root", ""),
                    ],
                )
                candidate_logo_profile = (
                    build_logo_image_profile(candidate_profile_path, candidate_ocr)
                    if candidate_profile_path
                    else None
                )
                vis_sim, visual_breakdown = _calculate_visual_breakdown(
                    clip_sim=clip_sim,
                    dinov2_sim=dino_sim,
                    color_sim=color_sim,
                    ocr_text_a=query_ocr_text,
                    ocr_text_b=candidate_ocr,
                    logo_profile_a=query_logo_profile,
                    logo_profile_b=candidate_logo_profile,
                )

                text_sim = 0.0
                semantic_sim = 0.0
                phon_sim = 0.0
                if has_typed_name:
                    text_sim = name_similarity_fn(query_name, candidate_name)
                    semantic_sim = _cosine(query_text_vec, row.get("text_embedding"))

                score_breakdown = score_pair_fn(
                    query_name=query_name if has_typed_name else "",
                    candidate_name=candidate_name,
                    text_sim=text_sim,
                    semantic_sim=semantic_sim,
                    visual_sim=vis_sim,
                    phonetic_sim=phon_sim,
                    candidate_translations={"name_tr": row.get("name_tr") or ""},
                    visual_breakdown=visual_breakdown,
                )
                score_breakdown["query_text_source"] = query_text_source
                score_breakdown.setdefault("textual_breakdown", {})[
                    "query_text_source"
                ] = query_text_source

                total = score_breakdown["total"]
                results.append(
                    {
                        "id": trademark_id,
                        "name": candidate_name or "-",
                        "name_tr": row.get("name_tr") or None,
                        "application_no": row["application_no"],
                        "status": row["final_status"] or "-",
                        "nice_classes": row["nice_class_numbers"] or [],
                        "image_url": image_url,
                        "application_date": str(row["application_date"]) if row.get("application_date") else None,
                        "expiry_date": str(row["expiry_date"]) if row.get("expiry_date") else None,
                        "similarity": round(total * 100, 1),
                        "image_similarity": round(clip_sim * 100, 1),
                        "visual_similarity": round(vis_sim * 100, 1),
                        "text_similarity": round(text_sim * 100, 1) if has_typed_name else None,
                        "final_score": total,
                        "risk_level": risk_level_getter(total),
                        "query_text_source": query_text_source,
                        "query_ocr_text_used": False,
                        "scores": score_breakdown,
                    }
                )
            else:
                raw_image_sim = float(row.get("clip_sim", 0) or row.get("dino_sim", 0) or 0)
                final_score = visual_similarity_fn(
                    clip_sim=raw_image_sim,
                    ocr_text_a=query_ocr_text,
                    ocr_text_b=candidate_ocr,
                )
                from difflib import SequenceMatcher

                ocr_sim = 0.0
                if query_ocr_text and candidate_ocr:
                    ocr_sim = SequenceMatcher(
                        None,
                        query_ocr_text.lower().strip(),
                        candidate_ocr.lower().strip(),
                    ).ratio()

                results.append(
                    {
                        "id": trademark_id,
                        "name": candidate_name or "-",
                        "name_tr": row.get("name_tr") or None,
                        "application_no": row["application_no"],
                        "status": row["final_status"] or "-",
                        "nice_classes": row["nice_class_numbers"] or [],
                        "image_url": image_url,
                        "application_date": str(row["application_date"]) if row.get("application_date") else None,
                        "expiry_date": str(row["expiry_date"]) if row.get("expiry_date") else None,
                        "similarity": round(final_score * 100, 1),
                        "image_similarity": round(raw_image_sim * 100, 1),
                        "raw_image_score": round(raw_image_sim * 100, 1),
                        "ocr_boost": round(ocr_sim * 0.20 * 100, 1),
                        "ocr_similarity": round(ocr_sim * 100, 1),
                        "final_score": final_score,
                        "risk_level": risk_level_getter(final_score),
                    }
                )

        results.sort(key=lambda item: item.get("final_score", 0), reverse=True)
        results = results[:limit]

        return {
            "success": True,
            "search_type": "combined" if has_typed_name else "image",
            "scoring_engine": "unified" if use_unified else "legacy",
            "ocr_enabled": True,
            "query_ocr_text": query_ocr_text[:100] if query_ocr_text else None,
            "query_text_source": query_text_source,
            "query_ocr_text_used": False,
            "total_results": len(results),
            "classes_filtered": class_list if class_list else None,
            "results": results,
        }
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


async def run_legacy_rollback_search(
    search_request,
    settings,
    normalize_turkish_fn,
    score_calculator,
    max_results,
    connect_fn=None,
    timer=None,
):
    """Run the legacy rollback text-search path used for regression checks."""
    if connect_fn is None:
        connect_fn = psycopg2.connect
    if timer is None:
        timer = time.time

    start_time = timer()

    try:
        conn = connect_fn(
            host=settings.database.host,
            port=settings.database.port,
            database=settings.database.name,
            user=settings.database.user,
            password=settings.database.password,
        )
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        name_normalized = normalize_turkish_fn(search_request.name)
        normalize_sql = _legacy_mojibake_normalize_sql("t.name")

        sql = f"""
            SELECT t.id, t.application_no, t.name, t.final_status,
                   t.nice_class_numbers, t.application_date, t.registration_date,
                   t.bulletin_no, t.image_path, t.holder_name,
                   t.holder_tpe_client_id, t.attorney_name, t.attorney_no,
                   t.registration_no
            FROM trademarks t
            WHERE LOWER(t.name) LIKE LOWER(%s)
                OR {normalize_sql} LIKE LOWER(%s)
                OR similarity(LOWER(t.name), LOWER(%s)) > 0.2
                OR similarity({normalize_sql}, LOWER(%s)) > 0.2
            ORDER BY GREATEST(
                similarity(LOWER(t.name), LOWER(%s)),
                similarity({normalize_sql}, LOWER(%s))
            ) DESC LIMIT 100
        """
        params = [
            f"%{search_request.name}%",
            f"%{name_normalized}%",
            search_request.name,
            name_normalized,
            search_request.name,
            name_normalized,
        ]

        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        results = []
        for row in rows:
            target_name = row["name"] or ""
            scoring = score_calculator(search_request.name, target_name)
            score = scoring["final_score"]
            results.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "application_no": row["application_no"],
                    "status": row["final_status"] or "Bilinmiyor",
                    "nice_classes": row["nice_class_numbers"] or [],
                    "similarity": round(score * 100, 1),
                    "risk_level": scoring["risk_level"],
                    "scoring_engine": "legacy",
                }
            )

        results.sort(key=lambda item: item["similarity"], reverse=True)
        results = results[:max_results]

        return {
            "query": search_request.name,
            "scoring_engine": "legacy",
            "total_results": len(results),
            "search_time_ms": round((timer() - start_time) * 1000, 2),
            "results": results,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
