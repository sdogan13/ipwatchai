"""Shared watchlist alert filtering helpers."""

from __future__ import annotations

from typing import Mapping, Iterable, Any, Set


def normalize_holder_identifier(value: Any) -> str | None:
    """Return a stable comparable holder identifier, or None when absent."""
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() if text else None


def holder_identifier_set(record: Mapping[str, Any], keys: Iterable[str]) -> Set[str]:
    """Extract non-empty holder identifiers from a record."""
    identifiers: Set[str] = set()
    for key in keys:
        value = normalize_holder_identifier(record.get(key))
        if value:
            identifiers.add(value)
    return identifiers


def is_same_holder_conflict(
    conflicting_trademark: Mapping[str, Any],
    watchlist_item: Mapping[str, Any],
) -> bool:
    """Return True when a conflict is between marks owned by the same holder."""
    conflict_ids = holder_identifier_set(
        conflicting_trademark,
        ("holder_tpe_client_id", "holder_id"),
    )
    watched_ids = holder_identifier_set(
        watchlist_item,
        (
            "watched_holder_tpe_client_id",
            "watched_holder_id",
            "holder_tpe_client_id",
            "holder_id",
        ),
    )
    return bool(conflict_ids and watched_ids and conflict_ids.intersection(watched_ids))


def same_holder_alert_exclusion_sql(
    alert_alias: str = "a",
    conflict_alias: str = "t",
    watched_alias: str = "my_tm",
) -> str:
    """SQL predicate that keeps non-self similarity alerts.

    Event alerts remain visible; this only filters similarity alerts where the
    watched trademark and the conflicting trademark share a holder identifier.
    """
    return f"""
        NOT (
            {alert_alias}.alert_type = 'similarity'
            AND {watched_alias}.id IS NOT NULL
            AND {conflict_alias}.id IS NOT NULL
            AND (
                (
                    NULLIF(BTRIM({watched_alias}.holder_tpe_client_id::text), '') IS NOT NULL
                    AND NULLIF(BTRIM({conflict_alias}.holder_tpe_client_id::text), '') IS NOT NULL
                    AND BTRIM({watched_alias}.holder_tpe_client_id::text) = BTRIM({conflict_alias}.holder_tpe_client_id::text)
                )
                OR (
                    {watched_alias}.holder_id IS NOT NULL
                    AND {conflict_alias}.holder_id IS NOT NULL
                    AND {watched_alias}.holder_id = {conflict_alias}.holder_id
                )
            )
        )
    """
