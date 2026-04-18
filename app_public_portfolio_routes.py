"""Public portfolio route extraction from the legacy main app."""

from fastapi import HTTPException, Query, Request


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
):
    """Build the public CSV export for a holder or attorney portfolio."""
    from services.search_service import build_public_portfolio_csv

    try:
        return await build_public_portfolio_csv(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if logger:
            logger.error(f"Public portfolio CSV failed: {exc}")
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
    ):
        """Public CSV export - all trademarks by holder or attorney."""
        return await public_portfolio_csv_impl(
            holder_id=holder_id,
            attorney_no=attorney_no,
            logger=logger,
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

    return public_portfolio, public_portfolio_csv
