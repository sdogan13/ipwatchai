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

    return public_portfolio, public_portfolio_csv
