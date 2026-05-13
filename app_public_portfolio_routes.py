"""Public portfolio route extraction from the legacy main app."""

from fastapi import Depends, HTTPException, Query, Request

from auth.authentication import CurrentUser, get_current_user


async def public_portfolio_impl(
    holder_id=None,
    attorney_no=None,
    logger=None,
):
    """Run the public portfolio lookup used by the landing page."""
    from services.search_service import run_public_portfolio_lookup

    try:
        return await run_public_portfolio_lookup(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public portfolio failed: {exc}")
        raise HTTPException(status_code=500, detail="Portfolio lookup temporarily unavailable")


async def public_portfolio_csv_impl(
    holder_id=None,
    attorney_no=None,
    logger=None,
    current_user=None,
):
    """Build the public CSV export for a holder or attorney portfolio."""
    from services.search_service import build_public_portfolio_csv

    try:
        return await build_public_portfolio_csv(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
            current_user=current_user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public portfolio CSV failed: {exc}")
        raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")


async def public_design_portfolio_impl(
    holder_id=None,
    logger=None,
):
    """Public lookup that returns the first ten designs by a holder.
    Response shape matches the trademark variant so the dashboard
    portfolio modal can render either registry."""
    from services.design_search_service import run_public_design_portfolio_lookup

    try:
        return await run_public_design_portfolio_lookup(
            holder_id=holder_id,
            logger=logger,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public design portfolio failed: {exc}")
        raise HTTPException(
            status_code=500, detail="Design portfolio lookup temporarily unavailable",
        )


async def public_design_portfolio_csv_impl(
    holder_id=None,
    logger=None,
    current_user=None,
):
    """CSV export for all designs by a given holder, plan-gated by
    can_download_portfolio (mirrors the trademark version)."""
    from services.design_search_service import build_public_design_portfolio_csv

    try:
        return await build_public_design_portfolio_csv(
            holder_id=holder_id,
            logger=logger,
            current_user=current_user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public design portfolio CSV failed: {exc}")
        raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")


async def public_designer_portfolio_impl(
    name=None,
    logger=None,
):
    """Look up up to 10 designs whose ``designers`` array contains the
    given name (matched via the conservative-normalization GIN index).
    Response shape mirrors the holder portfolio endpoint."""
    from services.design_search_service import run_public_designer_portfolio_lookup

    try:
        return await run_public_designer_portfolio_lookup(
            designer_name=name,
            logger=logger,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public designer portfolio failed: {exc}")
        raise HTTPException(
            status_code=500, detail="Designer portfolio lookup temporarily unavailable",
        )


async def public_designer_portfolio_csv_impl(
    name=None,
    logger=None,
    current_user=None,
):
    """CSV export for every design that lists this designer.
    Plan-gated by can_download_portfolio."""
    from services.design_search_service import build_public_designer_portfolio_csv

    try:
        return await build_public_designer_portfolio_csv(
            designer_name=name,
            logger=logger,
            current_user=current_user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public designer portfolio CSV failed: {exc}")
        raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")


async def public_attorney_portfolio_impl(
    name=None,
    firm=None,
    logger=None,
):
    """Lookup designs by (attorney_name, attorney_firm) pair under
    conservative normalization. firm is optional — empty firm matches
    rows where attorney_firm is also empty/NULL."""
    from services.design_search_service import run_public_attorney_portfolio_lookup

    try:
        return await run_public_attorney_portfolio_lookup(
            attorney_name=name,
            attorney_firm=firm,
            logger=logger,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public attorney portfolio failed: {exc}")
        raise HTTPException(
            status_code=500, detail="Attorney portfolio lookup temporarily unavailable",
        )


async def public_attorney_portfolio_csv_impl(
    name=None,
    firm=None,
    logger=None,
    current_user=None,
):
    """CSV export for every design representing this (name, firm) pair.
    Plan-gated by can_download_portfolio."""
    from services.design_search_service import build_public_attorney_portfolio_csv

    try:
        return await build_public_attorney_portfolio_csv(
            attorney_name=name,
            attorney_firm=firm,
            logger=logger,
            current_user=current_user,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public attorney portfolio CSV failed: {exc}")
        raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")


def register_public_portfolio_routes(app, limiter, logger):
    """Register the extracted public portfolio endpoints on the app."""

    @limiter.limit("5/minute")
    async def public_portfolio(
        request: Request,
        holder_id: str = Query(None, max_length=50, description="Holder TPE Client ID"),
        attorney_no: str = Query(None, max_length=50, description="Attorney number"),
    ):
        """
        Public portfolio lookup for landing page.
        Returns max 10 trademarks by holder or attorney.
        """
        return await public_portfolio_impl(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
        )

    @limiter.limit("3/minute")
    async def public_portfolio_csv(
        request: Request,
        holder_id: str = Query(None, max_length=50),
        attorney_no: str = Query(None, max_length=50),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        """CSV export for all trademarks by holder or attorney."""
        return await public_portfolio_csv_impl(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
            current_user=current_user,
        )

    @limiter.limit("5/minute")
    async def public_design_portfolio(
        request: Request,
        holder_id: str = Query(None, max_length=50, description="Holder TPE Client ID"),
    ):
        """Public lookup for the first 10 designs by a holder. Mirrors
        the trademark /portfolio/public endpoint so the dashboard
        portfolio modal can render design rows the same way."""
        return await public_design_portfolio_impl(
            holder_id=holder_id,
            logger=logger,
        )

    @limiter.limit("3/minute")
    async def public_design_portfolio_csv(
        request: Request,
        holder_id: str = Query(None, max_length=50),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        """CSV export for all designs by a holder. Plan-gated by
        can_download_portfolio (paid plans only)."""
        return await public_design_portfolio_csv_impl(
            holder_id=holder_id,
            logger=logger,
            current_user=current_user,
        )

    app.add_api_route(
        "/api/v1/portfolio/public",
        public_portfolio,
        methods=["GET"],
        tags=["Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/csv",
        public_portfolio_csv,
        methods=["GET"],
        tags=["Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/designs",
        public_design_portfolio,
        methods=["GET"],
        tags=["Design Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/designs/csv",
        public_design_portfolio_csv,
        methods=["GET"],
        tags=["Design Search"],
    )

    @limiter.limit("5/minute")
    async def public_designer_portfolio(
        request: Request,
        name: str = Query(None, max_length=255, description="Designer name (conservative-normalization match)"),
    ):
        """Lookup designs by designer name. Matches on the same
        conservative normalization used by the migration, backed by
        idx_des_designers_normalized_gin."""
        return await public_designer_portfolio_impl(name=name, logger=logger)

    @limiter.limit("3/minute")
    async def public_designer_portfolio_csv(
        request: Request,
        name: str = Query(None, max_length=255),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return await public_designer_portfolio_csv_impl(
            name=name, logger=logger, current_user=current_user,
        )

    app.add_api_route(
        "/api/v1/portfolio/public/designers",
        public_designer_portfolio,
        methods=["GET"],
        tags=["Design Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/designers/csv",
        public_designer_portfolio_csv,
        methods=["GET"],
        tags=["Design Search"],
    )

    @limiter.limit("5/minute")
    async def public_attorney_portfolio(
        request: Request,
        name: str = Query(None, max_length=255, description="Attorney name"),
        firm: str = Query(None, max_length=255, description="Attorney firm (optional)"),
    ):
        """Lookup designs by (attorney_name, attorney_firm) pair.
        Match uses the conservative-normalization helpers from the
        designer index migration (functions are name-agnostic)."""
        return await public_attorney_portfolio_impl(
            name=name, firm=firm, logger=logger,
        )

    @limiter.limit("3/minute")
    async def public_attorney_portfolio_csv(
        request: Request,
        name: str = Query(None, max_length=255),
        firm: str = Query(None, max_length=255),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        return await public_attorney_portfolio_csv_impl(
            name=name, firm=firm, logger=logger, current_user=current_user,
        )

    app.add_api_route(
        "/api/v1/portfolio/public/attorneys",
        public_attorney_portfolio,
        methods=["GET"],
        tags=["Design Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/attorneys/csv",
        public_attorney_portfolio_csv,
        methods=["GET"],
        tags=["Design Search"],
    )

    # ---- Patent portfolios (Phase 2 of actor click-through work) ----

    @limiter.limit("5/minute")
    async def public_patent_portfolio(
        request: Request,
        holder_id: str = Query(None, max_length=64, description="Holder TPE Client ID or internal UUID"),
    ):
        """Public lookup for the first 10 patents by a holder. Same
        response shape as the design + trademark variants so the
        dashboard portfolio modal renders patent rows identically."""
        from services.patent_portfolio_service import (
            run_public_patent_portfolio_lookup,
        )

        try:
            return await run_public_patent_portfolio_lookup(
                holder_id=holder_id, logger=logger,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public patent portfolio failed: {exc}")
            raise HTTPException(
                status_code=500, detail="Patent portfolio lookup temporarily unavailable",
            )

    @limiter.limit("3/minute")
    async def public_patent_portfolio_csv(
        request: Request,
        holder_id: str = Query(None, max_length=64),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from services.patent_portfolio_service import (
            build_public_patent_portfolio_csv,
        )

        try:
            return await build_public_patent_portfolio_csv(
                holder_id=holder_id, logger=logger, current_user=current_user,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public patent portfolio CSV failed: {exc}")
            raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")

    @limiter.limit("5/minute")
    async def public_inventor_portfolio(
        request: Request,
        name: str = Query(None, max_length=255, description="Inventor name (conservative-normalization match)"),
    ):
        """Lookup patents by inventor name under conservative
        normalization. Backed by idx_pinv_normalized_name."""
        from services.patent_portfolio_service import (
            run_public_inventor_portfolio_lookup,
        )

        try:
            return await run_public_inventor_portfolio_lookup(
                inventor_name=name, logger=logger,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public inventor portfolio failed: {exc}")
            raise HTTPException(
                status_code=500, detail="Inventor portfolio lookup temporarily unavailable",
            )

    @limiter.limit("3/minute")
    async def public_inventor_portfolio_csv(
        request: Request,
        name: str = Query(None, max_length=255),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from services.patent_portfolio_service import (
            build_public_inventor_portfolio_csv,
        )

        try:
            return await build_public_inventor_portfolio_csv(
                inventor_name=name, logger=logger, current_user=current_user,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public inventor portfolio CSV failed: {exc}")
            raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")

    @limiter.limit("5/minute")
    async def public_patent_attorney_portfolio(
        request: Request,
        name: str = Query(None, max_length=255, description="Attorney name"),
        firm: str = Query(None, max_length=255, description="Attorney firm (optional)"),
    ):
        """Lookup patents by (attorney_name, attorney_firm) pair under
        conservative normalization. Backed by idx_patt_normalized_pair."""
        from services.patent_portfolio_service import (
            run_public_patent_attorney_portfolio_lookup,
        )

        try:
            return await run_public_patent_attorney_portfolio_lookup(
                attorney_name=name, attorney_firm=firm, logger=logger,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public patent attorney portfolio failed: {exc}")
            raise HTTPException(
                status_code=500, detail="Attorney portfolio lookup temporarily unavailable",
            )

    @limiter.limit("3/minute")
    async def public_patent_attorney_portfolio_csv(
        request: Request,
        name: str = Query(None, max_length=255),
        firm: str = Query(None, max_length=255),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from services.patent_portfolio_service import (
            build_public_patent_attorney_portfolio_csv,
        )

        try:
            return await build_public_patent_attorney_portfolio_csv(
                attorney_name=name, attorney_firm=firm,
                logger=logger, current_user=current_user,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public patent attorney portfolio CSV failed: {exc}")
            raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")

    app.add_api_route(
        "/api/v1/portfolio/public/patents",
        public_patent_portfolio,
        methods=["GET"],
        tags=["Patent Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/patents/csv",
        public_patent_portfolio_csv,
        methods=["GET"],
        tags=["Patent Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/patent-inventors",
        public_inventor_portfolio,
        methods=["GET"],
        tags=["Patent Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/patent-inventors/csv",
        public_inventor_portfolio_csv,
        methods=["GET"],
        tags=["Patent Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/patent-attorneys",
        public_patent_attorney_portfolio,
        methods=["GET"],
        tags=["Patent Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/patent-attorneys/csv",
        public_patent_attorney_portfolio_csv,
        methods=["GET"],
        tags=["Patent Search"],
    )

    # ---- Cografi (GI) portfolios (Phase 3 of actor click-through) ----

    @limiter.limit("5/minute")
    async def public_cografi_applicant_portfolio(
        request: Request,
        holder_id: str = Query(None, max_length=64, description="Applicant TPE Client ID or internal UUID"),
    ):
        """Public lookup for the first 10 cografi records by applicant.
        Filtered to cografi_holders.role = 'APPLICANT'."""
        from services.cografi_portfolio_service import (
            run_public_cografi_applicant_portfolio_lookup,
        )

        try:
            return await run_public_cografi_applicant_portfolio_lookup(
                holder_id=holder_id, logger=logger,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public cografi applicant portfolio failed: {exc}")
            raise HTTPException(
                status_code=500, detail="Applicant portfolio lookup temporarily unavailable",
            )

    @limiter.limit("3/minute")
    async def public_cografi_applicant_portfolio_csv(
        request: Request,
        holder_id: str = Query(None, max_length=64),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from services.cografi_portfolio_service import (
            build_public_cografi_applicant_portfolio_csv,
        )

        try:
            return await build_public_cografi_applicant_portfolio_csv(
                holder_id=holder_id, logger=logger, current_user=current_user,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public cografi applicant portfolio CSV failed: {exc}")
            raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")

    @limiter.limit("5/minute")
    async def public_cografi_agent_portfolio(
        request: Request,
        name: str = Query(None, max_length=255, description="Agent name (conservative-normalization match)"),
    ):
        """Lookup cografi records by agent name under conservative
        normalization. Backed by idx_cog_agent_normalized."""
        from services.cografi_portfolio_service import (
            run_public_cografi_agent_portfolio_lookup,
        )

        try:
            return await run_public_cografi_agent_portfolio_lookup(
                agent_name=name, logger=logger,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public cografi agent portfolio failed: {exc}")
            raise HTTPException(
                status_code=500, detail="Agent portfolio lookup temporarily unavailable",
            )

    @limiter.limit("3/minute")
    async def public_cografi_agent_portfolio_csv(
        request: Request,
        name: str = Query(None, max_length=255),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from services.cografi_portfolio_service import (
            build_public_cografi_agent_portfolio_csv,
        )

        try:
            return await build_public_cografi_agent_portfolio_csv(
                agent_name=name, logger=logger, current_user=current_user,
            )
        except HTTPException:
            raise
        except Exception as exc:
            if logger:
                logger.error(f"Public cografi agent portfolio CSV failed: {exc}")
            raise HTTPException(status_code=500, detail="CSV export temporarily unavailable")

    app.add_api_route(
        "/api/v1/portfolio/public/cografi-applicants",
        public_cografi_applicant_portfolio,
        methods=["GET"],
        tags=["Cografi Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/cografi-applicants/csv",
        public_cografi_applicant_portfolio_csv,
        methods=["GET"],
        tags=["Cografi Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/cografi-agents",
        public_cografi_agent_portfolio,
        methods=["GET"],
        tags=["Cografi Search"],
    )
    app.add_api_route(
        "/api/v1/portfolio/public/cografi-agents/csv",
        public_cografi_agent_portfolio_csv,
        methods=["GET"],
        tags=["Cografi Search"],
    )

    return public_portfolio, public_portfolio_csv
