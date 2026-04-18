"""Small authenticated search metadata routes for the legacy FastAPI app."""

from auth.authentication import CurrentUser


async def get_search_credits(current_user: CurrentUser):
    """Return search credit info: plan display name and next reset date."""
    from services.search_service import get_search_credits_summary

    return await get_search_credits_summary(
        current_user=current_user,
    )


def register_search_meta_routes(app, dependency_get_current_user):
    """Register lightweight authenticated search metadata routes."""

    @app.get("/api/v1/search/credits", tags=["Search"])
    async def search_credits_route(current_user: CurrentUser = dependency_get_current_user):
        return await get_search_credits(current_user)
