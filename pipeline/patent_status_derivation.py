"""Derive a patent's live lifecycle status from its event timeline.

The `patents.record_type` column tells you what KIND of bulletin entry
produced a row (e.g. `GRANTED_UM` — a row from a UM grant bulletin).
It does not move when later events lapse, revive, or expire the
patent. This module derives the live state from `patent_events`.

The output drives `patents.current_status`. Backfill walks every
application_no's events once; the ingest runtime recomputes the
affected applications after each new event batch.

State machine summary (left-to-right over events, oldest first):

  UNKNOWN
    -- APPLICATION_PUBLISHED -->                    PENDING
    -- GRANT_ANNOUNCED / GRANT_FINALIZED -->        ACTIVE
    -- APPLICATION_REJECTED -->                     REJECTED (terminal)
    -- APPLICATION_WITHDRAWN -->                    WITHDRAWN (terminal)
    -- APPLICATION_ABANDONED -->                    WITHDRAWN
    -- APPLICATION_LAPSED_OR_REJECTED -->           LAPSED_APPLICATION
    -- APPLICATION_FEE_LAPSE -->                    LAPSED_APPLICATION
    -- GRANT_FEE_LAPSE -->                          LAPSED_GRANT
    -- GRANT_PROTECTION_EXPIRED -->                 EXPIRED (terminal)
    -- GRANT_INVALIDATED_LEGACY_551 -->             INVALIDATED (terminal)
    -- {LAPSE}_CANCELLED / FEE_REVALIDATION -->     revive (LAPSED_* -> previous-active)
    -- ABANDONED_CANCELLED -->                      revive (WITHDRAWN -> PENDING)

Revival rules never resurrect a terminal state. Same-date events use
a positivity-rank tiebreak: negative events apply first, positive /
revive events apply second — matches TPE's batched "lapse then
revive" bulletins where the legal end state is alive.

Informational events (SEARCH_REPORT_*, AMENDED_*, CONVERSION_*,
ASSIGNMENT_*, MERGER_*, DIVISION_*, LICENSE_OFFER, EP_FASCICLE_*,
POST_PUB_AMENDMENT, UNKNOWN, etc.) never change status — they exist
in the timeline for audit but don't affect the lifecycle field.

If you add or rename an enum value here, mirror it in
migrations/patents_current_status.sql.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, List, Optional, Sequence, Tuple


class PatentStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    LAPSED_APPLICATION = "LAPSED_APPLICATION"
    LAPSED_GRANT = "LAPSED_GRANT"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


# Terminal states cannot be revived by subsequent events. A revival
# event landing after one of these is ignored.
TERMINAL_STATES = frozenset({
    PatentStatus.REJECTED,
    PatentStatus.WITHDRAWN,
    PatentStatus.EXPIRED,
    PatentStatus.INVALIDATED,
})


# Positivity rank for same-date tiebreak. Higher rank applies LATER
# on the same date — so on a bulletin that ships both a lapse and a
# revival, the revival wins. Default rank for unlisted events is 0.
_POSITIVITY_RANK = {
    # Negatives — apply first
    "APPLICATION_REJECTED": -3,
    "APPLICATION_WITHDRAWN": -3,
    "APPLICATION_ABANDONED": -3,
    "APPLICATION_LAPSED_OR_REJECTED": -2,
    "APPLICATION_FEE_LAPSE": -2,
    "GRANT_FEE_LAPSE": -2,
    "GRANT_PROTECTION_EXPIRED": -3,
    "GRANT_INVALIDATED_LEGACY_551": -3,
    # Positives — apply last (revives win on the same date)
    "APPLICATION_PUBLISHED": 1,
    "APPLICATION_FEE_LAPSE_CANCELLED": 2,
    "APPLICATION_ABANDONED_CANCELLED": 2,
    "APPLICATION_FEE_REVALIDATION": 2,
    # PROCEDURAL_REVALIDATION is a generic procedural revival emitted
    # by TPE when an application is restored to processing after a
    # withdrawal / abandonment / examination-stage lapse. Same date
    # as the negative event in practice (TPE batches them); the
    # positivity-rank tiebreak ensures the revival applies after the
    # negative on the same date.
    "PROCEDURAL_REVALIDATION": 2,
    "GRANT_ANNOUNCED": 3,
    "GRANT_ANNOUNCED_LEGACY_551": 3,
    "GRANT_FINALIZED": 3,
    "GRANT_FEE_LAPSE_CANCELLED": 2,
    "GRANT_FEE_REVALIDATION": 2,
}


# Events that drive status transitions. Anything not in this set is
# informational only (search reports, recordings, amendments, EP
# fascicles, etc.) and gets walked-over without changing state.
_STATUS_AFFECTING = frozenset(_POSITIVITY_RANK.keys())


@dataclass(frozen=True)
class StatusResult:
    status: PatentStatus
    last_event_type: Optional[str]
    last_event_date: Optional[date]


# record_type -> initial state. The publication / grant bulletin
# itself IS the implicit publication / grant event for that patent
# row, but only secondary lifecycle events (lapses, search reports,
# transfers, etc.) get extracted into patent_events. So seed the
# state machine from the canonical record_type before walking
# events — that way a patent which only has SEARCH_REPORT_* events
# still surfaces as PENDING (if PUBLISHED_*) or ACTIVE (if
# GRANTED_*), and subsequent lapses still override correctly.
_RECORD_TYPE_SEED = {
    "PUBLISHED_APP":    PatentStatus.PENDING,
    "PUBLISHED_UM_APP": PatentStatus.PENDING,
    "GRANTED_PATENT":   PatentStatus.ACTIVE,
    "GRANTED_UM":       PatentStatus.ACTIVE,
    # UNKNOWN / LEGACY don't seed — start at UNKNOWN and rely on
    # events alone.
}


@dataclass(frozen=True)
class Event:
    """Minimal event tuple the state machine needs. Use real
    patent_events rows or build mocks in tests."""
    event_type: str
    event_date: Optional[date]
    bulletin_date: Optional[date] = None

    @property
    def sort_date(self) -> date:
        # Bulletin date is the authoritative "when did this hit the
        # public record" stamp. event_date is sometimes the same,
        # sometimes a slightly earlier internal date. Use bulletin_date
        # for ordering; fall back to event_date when missing; finally
        # fall back to a sentinel min so untyped events sort first.
        return self.bulletin_date or self.event_date or date.min


def _sort_key(ev: Event):
    """Chronological order with positivity-rank tiebreak on same date.
    Stable secondary key ensures deterministic order when multiple
    same-rank events share a date."""
    return (
        ev.sort_date,
        _POSITIVITY_RANK.get(ev.event_type, 0),
        ev.event_type,
    )


def derive_patent_status(
    events: Iterable[Event],
    *,
    record_type: Optional[str] = None,
) -> StatusResult:
    """Apply the state machine to an iterable of events. Returns the
    final status plus the most recent status-affecting event for
    audit. Idempotent — calling twice on the same input gives the
    same result.

    Events do not need to be pre-sorted; this function sorts them.

    record_type seeds the initial state — see _RECORD_TYPE_SEED.
    The publication / grant bulletin is the implicit publication /
    grant event for a row, but only secondary lifecycle events get
    extracted, so we use the row's record_type as the baseline.
    Subsequent events can still override (a GRANT_FEE_LAPSE on an
    ACTIVE seed correctly moves to LAPSED_GRANT).
    """
    sorted_events: List[Event] = sorted(events, key=_sort_key)

    state = _RECORD_TYPE_SEED.get(record_type, PatentStatus.UNKNOWN)
    last_type: Optional[str] = None
    last_date: Optional[date] = None

    for ev in sorted_events:
        et = ev.event_type
        if et not in _STATUS_AFFECTING:
            # Informational — keep walking but don't touch state or
            # last_event_*. Search reports, assignments, amendments
            # etc. live in the timeline for context but don't move
            # the lifecycle.
            continue

        new_state = _apply(state, et)
        if new_state is None:
            # Event was status-affecting in principle but ignored
            # under the current state (e.g. revival on a terminal
            # state). Don't update last_event_* either — the lifecycle
            # genuinely hasn't moved.
            continue

        state = new_state
        last_type = et
        last_date = ev.sort_date if ev.sort_date != date.min else None

    return StatusResult(
        status=state, last_event_type=last_type, last_event_date=last_date,
    )


def _apply(state: PatentStatus, event_type: str) -> Optional[PatentStatus]:
    """One step of the state machine. Returns None when the event is
    ignored (no state change AND no last_event_* update — i.e. the
    event genuinely doesn't move the lifecycle from the current
    state)."""
    # Revivals get a chance BEFORE the terminal-state guard — TPE
    # does allow procedural cancellation of an abandonment.
    if event_type == "APPLICATION_ABANDONED_CANCELLED":
        if state == PatentStatus.WITHDRAWN:
            return PatentStatus.PENDING
        return None

    # PROCEDURAL_REVALIDATION is a generic procedural revival. It can
    # revive WITHDRAWN (TPE undoes the withdrawal procedurally), and
    # the LAPSED_* states. On ACTIVE / PENDING / terminal-grant
    # states (EXPIRED / INVALIDATED) or truly-rejected applications,
    # it's informational. Placed before the terminal-state guard so
    # it can rescue WITHDRAWN.
    if event_type == "PROCEDURAL_REVALIDATION":
        if state == PatentStatus.WITHDRAWN:
            return PatentStatus.PENDING
        if state == PatentStatus.LAPSED_APPLICATION:
            return PatentStatus.PENDING
        if state == PatentStatus.LAPSED_GRANT:
            return PatentStatus.ACTIVE
        return None

    # A grant cannot occur after a real withdrawal — so a later
    # GRANT_* event landing on WITHDRAWN proves the withdrawal was
    # procedurally undone. Allow the override before the terminal-
    # state guard. The other grant-type → ACTIVE transitions still
    # happen further down for non-WITHDRAWN states.
    if event_type in (
        "GRANT_ANNOUNCED", "GRANT_ANNOUNCED_LEGACY_551", "GRANT_FINALIZED",
    ):
        if state == PatentStatus.WITHDRAWN:
            return PatentStatus.ACTIVE

    # Terminal states are immovable. Any subsequent event is ignored.
    if state in TERMINAL_STATES:
        return None

    # Hard transitions — apply regardless of prior state.
    if event_type in ("APPLICATION_REJECTED",):
        return PatentStatus.REJECTED
    if event_type in ("APPLICATION_WITHDRAWN", "APPLICATION_ABANDONED"):
        return PatentStatus.WITHDRAWN
    if event_type == "GRANT_PROTECTION_EXPIRED":
        return PatentStatus.EXPIRED
    if event_type == "GRANT_INVALIDATED_LEGACY_551":
        return PatentStatus.INVALIDATED

    if event_type == "APPLICATION_PUBLISHED":
        # Publication only moves us into PENDING when we're still in
        # the pre-publication void. If we've already moved past it
        # (granted, lapsed, etc.) this is informational.
        if state == PatentStatus.UNKNOWN:
            return PatentStatus.PENDING
        return None

    if event_type in ("GRANT_ANNOUNCED", "GRANT_ANNOUNCED_LEGACY_551", "GRANT_FINALIZED"):
        return PatentStatus.ACTIVE

    if event_type in ("APPLICATION_LAPSED_OR_REJECTED", "APPLICATION_FEE_LAPSE"):
        # Only meaningful pre-grant — once granted, the application
        # fee path is moot. Treat as informational post-grant.
        if state in (PatentStatus.UNKNOWN, PatentStatus.PENDING):
            return PatentStatus.LAPSED_APPLICATION
        return None

    if event_type == "GRANT_FEE_LAPSE":
        # Grant-fee events imply a prior grant by definition — you
        # can't lapse a grant that doesn't exist. When we see one
        # while still in a pre-grant state (PENDING / LAPSED_APP /
        # UNKNOWN), fast-forward through the implicit grant. This
        # handles patents whose GRANT_ANNOUNCED event was never
        # captured (older bulletins) but whose downstream lifecycle
        # was — the 2016/02872 case in tests.
        if state in (
            PatentStatus.ACTIVE, PatentStatus.PENDING,
            PatentStatus.LAPSED_APPLICATION, PatentStatus.UNKNOWN,
        ):
            return PatentStatus.LAPSED_GRANT
        return None

    if event_type in ("APPLICATION_FEE_LAPSE_CANCELLED", "APPLICATION_FEE_REVALIDATION"):
        # Revive the application — only if we're in the matching
        # lapsed state. A post-grant APPLICATION_FEE_REVALIDATION
        # (as we saw on 2019/02599) is for the application fee path
        # which doesn't apply after grant; ignore.
        if state == PatentStatus.LAPSED_APPLICATION:
            return PatentStatus.PENDING
        return None

    if event_type in ("GRANT_FEE_LAPSE_CANCELLED", "GRANT_FEE_REVALIDATION"):
        # Grant-fee revival also implies the prior grant. From any
        # pre-grant state, fast-forward straight to ACTIVE — same
        # rationale as GRANT_FEE_LAPSE above.
        if state in (
            PatentStatus.LAPSED_GRANT, PatentStatus.PENDING,
            PatentStatus.LAPSED_APPLICATION, PatentStatus.UNKNOWN,
        ):
            return PatentStatus.ACTIVE
        return None

    # Anything else is in _STATUS_AFFECTING but unhandled — shouldn't
    # happen, but treat as informational to fail safe.
    return None


# ---------------------------------------------------------------------------
# DB-side helpers — shared by the backfill script and the ingest path.
# ---------------------------------------------------------------------------


# SQL: canonical record_type per application_no. Same rule as the
# FK resolver in pipeline/ingest_patents.py + the backfill script
# (scripts/backfill_patent_current_status.py): GRANTED_PATENT >
# GRANTED_UM > PUBLISHED_APP > PUBLISHED_UM_APP > UNKNOWN > LEGACY,
# tiebreak by latest bulletin_date.
_CANONICAL_RECORD_TYPE_SQL = """
    SELECT DISTINCT ON (application_no) application_no, record_type::text
    FROM patents
    WHERE application_no = ANY(%s)
    ORDER BY application_no,
        CASE record_type
            WHEN 'GRANTED_PATENT'    THEN 1
            WHEN 'GRANTED_UM'        THEN 2
            WHEN 'PUBLISHED_APP'    THEN 3
            WHEN 'PUBLISHED_UM_APP' THEN 4
            WHEN 'UNKNOWN'           THEN 5
            WHEN 'LEGACY'            THEN 6
            ELSE 7
        END,
        bulletin_date DESC NULLS LAST,
        id
"""


def recompute_current_status(cur, application_nos: Sequence[str]) -> int:
    """Recompute and persist current_status for the given application_no
    set. Pulls events + the canonical record_type per app, runs the
    state machine, and UPDATEs every patents row sharing that
    application_no. Returns the number of patents rows updated.

    Designed for the ingest hook — call once at the end of a bulletin
    ingest with the set of application_no's whose events just landed.
    Empty input is a no-op. Reuses the caller's cursor (and therefore
    transaction) so a partial-write scenario rolls back cleanly with
    the rest of the ingest.

    The backfill script calls a batched version directly — this helper
    is sized for the per-bulletin scope (~dozens to hundreds of
    application_no's at a time).

    Status now computes for applications even when they have no events
    on file (a fresh publication bulletin row has no patent_events
    rows yet but its record_type still seeds the state to PENDING).
    """
    app_nos = [a for a in {*application_nos} if a]
    if not app_nos:
        return 0

    cur.execute(
        """
        SELECT application_no, event_type, event_date, bulletin_date
        FROM patent_events
        WHERE application_no = ANY(%s)
        """,
        (app_nos,),
    )
    grouped: dict = {}
    for app_no, et, ed, bd in cur.fetchall():
        grouped.setdefault(app_no, []).append(
            Event(event_type=et, event_date=ed, bulletin_date=bd),
        )

    cur.execute(_CANONICAL_RECORD_TYPE_SQL, (app_nos,))
    canonical_rt = {row[0]: row[1] for row in cur.fetchall()}

    rows_updated = 0
    for app_no in app_nos:
        events = grouped.get(app_no, [])
        rt = canonical_rt.get(app_no)
        # Skip apps with no events AND no record_type seed — nothing
        # to compute. (Rare: an app_no in patent_events but no patents
        # row at all. Backfill already skips these too.)
        if not events and rt not in _RECORD_TYPE_SEED:
            continue
        res = derive_patent_status(events, record_type=rt)
        cur.execute(
            """
            UPDATE patents
            SET current_status = %s::patent_lifecycle_status,
                last_event_type = %s,
                last_event_date = %s,
                status_computed_at = NOW()
            WHERE application_no = %s
            """,
            (res.status.value, res.last_event_type, res.last_event_date, app_no),
        )
        rows_updated += cur.rowcount
    return rows_updated
