"""Shared anonymous-search quota helpers for landing-page public routes.

All four public search endpoints (trademark / patent / design / cografi)
share a single daily counter per anonymous client, keyed by a long-lived
cookie. The 5/day free-tier limit is enforced as one bucket — switching
registries does not reset the count, so a visitor can mix-and-match across
the four corpora within their free quota.

The underlying primitives live in ``services.search_service`` (cookie
resolution, eligibility check, usage increment). This module exposes the
two route-level wrappers — one that sets the cookie + raises 429, and one
that records a successful search.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, Response


def enforce_public_search_quota(request: Request, response: Response) -> str:
    """Resolve the anonymous client_id, set the tracking cookie on first
    visit, and raise 429 if today's free quota is exhausted.

    Returns the client_id so the caller can pass it to
    ``record_public_search_usage`` after a successful retrieval — failed
    searches must not burn quota.
    """
    from database.crud import Database
    from services.search_service import (
        PUBLIC_SEARCH_CLIENT_COOKIE,
        PUBLIC_SEARCH_COOKIE_MAX_AGE_SECONDS,
        check_public_search_eligibility,
        resolve_public_search_client_id,
    )

    client_id, should_set_cookie = resolve_public_search_client_id(request)
    if should_set_cookie:
        response.set_cookie(
            key=PUBLIC_SEARCH_CLIENT_COOKIE,
            value=client_id,
            max_age=PUBLIC_SEARCH_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )

    with Database() as db:
        allowed, _reason, detail = check_public_search_eligibility(db, client_id)
    if not allowed:
        raise HTTPException(status_code=429, detail=detail)

    return client_id


def record_public_search_usage(client_id: str) -> None:
    """Increment the shared anonymous counter for today. Must be called
    after a successful search so failed retrievals don't burn quota."""
    from database.crud import Database
    from services.search_service import increment_public_search_usage

    with Database() as db:
        increment_public_search_usage(db, client_id)
