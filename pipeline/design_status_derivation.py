"""Derive a design's live lifecycle status from its event timeline.

`designs.current_status` (design_status enum) is set at ingest from
the publication section: tr_native/hague/republished/deferred_lifted
-> "Yayında", deferred -> "Yayım Ertelendi". That value reflects how
the design appeared in the bulletin — it doesn't move when later
events cancel, transfer, renew, or expire the design.

This module derives the live state from `design_events`. State
machine summary (oldest events first), seeded from the section:

  seed (from section)
    -- renewal / partial_renewal -->        Yenilendi
    -- transfer -->                         Devredildi  (positive,
                                            non-terminal — a later
                                            cancellation still wins)
    -- full_cancellation_board -->          Hükümsüz   (terminal)
    -- full_cancellation_applicant -->      İptal Edildi (terminal)
    -- partial_cancellation_board   ┐
    -- partial_cancellation_owner   │ → only when this design's
                                    │   index is in details.design_indices
                                    │   then matching terminal state
    -- partial_renewal              ┘ → Yenilendi for targeted indices
    -- seizure / provisional_seizure        (informational, no change)
    -- partial_provisional_injunction       (informational, no change)
    -- provisional_injunction_lifted        (informational, no change)

Terminal: Hükümsüz, İptal Edildi, Süresi Doldu. These cannot be
revived by subsequent events.

After the event-driven state machine settles, a hard 25-year cap is
applied: if application_date is at least 25 years in the past and
the state isn't already terminal, override to Süresi Doldu. (TR
design protection caps at 25 years regardless of renewals.)

Same-date tiebreak: positivity-rank ordering — revivals / renewals
apply after cancellations on the same date. Matches the patent
module's convention so the bug case where a bulletin ships both a
cancellation and a renewal on the same date resolves consistently.

If you add or rename a transition, mirror it in
tests/test_design_status_derivation.py and the backfill script.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Iterable, List, Optional, Sequence


class DesignStatus(str, Enum):
    """Mirrors the design_status enum in the DB. Values are the
    Turkish labels as stored. UI translation happens in the locale
    files (landing.status_*) via window.translateStatus."""
    YAYINDA = "Yayında"
    YAYIM_ERTELENDI = "Yayım Ertelendi"
    YENILENDI = "Yenilendi"
    DEVREDILDI = "Devredildi"
    HUKUMSUZ = "Hükümsüz"
    IPTAL_EDILDI = "İptal Edildi"
    SURESI_DOLDU = "Süresi Doldu"
    TESCIL_EDILDI = "Tescil Edildi"
    BILINMIYOR = "Bilinmiyor"


TERMINAL_STATES = frozenset({
    DesignStatus.HUKUMSUZ,
    DesignStatus.IPTAL_EDILDI,
    DesignStatus.SURESI_DOLDU,
})


# Hard cap on TR design term — 5y initial + four 5y renewals = 25y.
# Past this point, the design is legally expired regardless of how
# many renewals were recorded. Applied after the event-driven state
# machine settles, only when state isn't already terminal.
DESIGN_TERM_YEARS = 25


SECTION_STATUS_SEED = {
    "tr_native": DesignStatus.YAYINDA,
    "deferred_lifted": DesignStatus.YAYINDA,
    "republished": DesignStatus.YAYINDA,
    "hague": DesignStatus.YAYINDA,
    "deferred": DesignStatus.YAYIM_ERTELENDI,
}


# Same convention as the patent module: higher rank applies later on
# the same date, so the more-positive event wins the tiebreak.
_POSITIVITY_RANK = {
    # Negatives — apply first
    "full_cancellation_board": -3,
    "full_cancellation_applicant": -3,
    "partial_cancellation_board": -2,
    "partial_cancellation_owner": -2,
    "seizure": -1,
    "provisional_seizure": -1,
    "partial_provisional_injunction": -1,
    # Positives — apply last (so a renewal after a same-day partial
    # cancellation still ends in Yenilendi if it touched a non-
    # cancelled index, etc.)
    "provisional_injunction_lifted": 1,
    "transfer": 2,
    "renewal": 3,
    "partial_renewal": 3,
}


# Only events that can move state. Anything else is informational
# (will show up in the events timeline but doesn't claim authorship
# of current_status).
_STATUS_AFFECTING = frozenset(_POSITIVITY_RANK.keys()) - {
    "seizure",
    "provisional_seizure",
    "partial_provisional_injunction",
    "provisional_injunction_lifted",
}


@dataclass(frozen=True)
class StatusResult:
    status: DesignStatus
    last_event_type: Optional[str]
    last_event_date: Optional[date]


@dataclass(frozen=True)
class Event:
    """Minimal event tuple. `design_indices` is the parsed list from
    `design_events.details->'design_indices'` (may be None for
    full-scope events)."""
    event_type: str
    event_date: Optional[date]
    bulletin_date: Optional[date] = None
    design_indices: Optional[Sequence[int]] = None

    @property
    def sort_date(self) -> date:
        return self.bulletin_date or self.event_date or date.min


def _sort_key(ev: Event):
    return (
        ev.sort_date,
        _POSITIVITY_RANK.get(ev.event_type, 0),
        ev.event_type,
    )


def derive_design_status(
    events: Iterable[Event],
    *,
    section: Optional[str] = None,
    design_index: Optional[int] = None,
    application_date: Optional[date] = None,
    today: Optional[date] = None,
) -> StatusResult:
    """Apply the state machine to a design's events. Pass the design's
    `section` to seed the initial state and `design_index` so partial
    events can be filtered to those that actually target this design.

    application_date drives the 25-year hard-cap override after the
    event-driven state machine settles. `today` is injectable so
    tests can pin a fixed date.
    """
    state = SECTION_STATUS_SEED.get(section or "", DesignStatus.BILINMIYOR)
    last_type: Optional[str] = None
    last_date: Optional[date] = None

    sorted_events: List[Event] = sorted(events, key=_sort_key)
    for ev in sorted_events:
        et = ev.event_type
        if et not in _STATUS_AFFECTING:
            continue

        if not _targets_design(ev, design_index):
            continue

        new_state = _apply(state, et)
        if new_state is None:
            continue
        state = new_state
        last_type = et
        last_date = ev.sort_date if ev.sort_date != date.min else None

    # 25-year hard-cap override: a design's protection can't outlast
    # 25 years from filing regardless of recorded renewal events.
    if state not in TERMINAL_STATES and application_date:
        today = today or date.today()
        # timedelta(days=) is cheaper than relativedelta and 25*365.25
        # is close enough for a yes/no expiry check.
        expiry_approx = application_date + timedelta(days=int(25 * 365.25))
        if expiry_approx < today:
            state = DesignStatus.SURESI_DOLDU
            last_type = "term_expired"
            last_date = expiry_approx

    return StatusResult(
        status=state, last_event_type=last_type, last_event_date=last_date,
    )


def _targets_design(ev: Event, design_index: Optional[int]) -> bool:
    """Does this event apply to the design we're computing for?

      * If event has no design_indices: applies to all designs in the
        application (return True). full_* events typically land here.
      * If event has design_indices and we have a design_index: only
        apply when this design's index is in the list.
      * If event has design_indices but we don't know our index:
        conservatively return False — better to under-flag than
        over-flag (a partial cancellation we can't target shouldn't
        accidentally take down a design).
    """
    if not ev.design_indices:
        return True
    if design_index is None:
        return False
    return design_index in ev.design_indices


def _apply(state: DesignStatus, event_type: str) -> Optional[DesignStatus]:
    """One step of the state machine. Returns None when the event is
    ignored under the current state.

    Terminal states are immovable. Non-terminal transitions overwrite
    state — most recent event wins for the non-terminal slots
    (Yayında / Yayım Ertelendi / Yenilendi / Devredildi). The
    chronological walk + positivity-rank tiebreak gives that for
    free.
    """
    if state in TERMINAL_STATES:
        return None

    if event_type == "full_cancellation_board":
        return DesignStatus.HUKUMSUZ
    if event_type == "full_cancellation_applicant":
        return DesignStatus.IPTAL_EDILDI
    if event_type == "partial_cancellation_board":
        # Targeting was checked in _targets_design — if we got here,
        # this design's index is in the cancellation set, so the
        # cancellation applies to it.
        return DesignStatus.HUKUMSUZ
    if event_type == "partial_cancellation_owner":
        return DesignStatus.IPTAL_EDILDI
    if event_type in ("renewal", "partial_renewal"):
        return DesignStatus.YENILENDI
    if event_type == "transfer":
        return DesignStatus.DEVREDILDI

    # Defensive: unknown but status-affecting event. Skip.
    return None


# ---------------------------------------------------------------------------
# DB-side helper — shared by backfill and ingest.
# ---------------------------------------------------------------------------


def recompute_design_current_status(
    cur, design_ids: Sequence[str], *, today: Optional[date] = None,
) -> int:
    """Recompute and persist current_status for the given design_id set.
    Pulls events that match each design (by FK or via the design's
    application_no/registration_no), runs the state machine per
    design, and UPDATEs designs accordingly.

    Returns the count of rows updated. Empty input is a no-op.
    Reuses the caller's cursor (and therefore transaction).

    Used by:
      - scripts/backfill_design_current_status.py (one-shot full pass)
      - pipeline/ingest_designs.py (per-bulletin recompute after
        design_events insert).
    """
    ids = [d for d in {*design_ids} if d]
    if not ids:
        return 0

    # Fetch design metadata in one shot.
    cur.execute(
        """
        SELECT id::text, application_no, registration_no, section,
               design_index, application_date
        FROM designs
        WHERE id = ANY(%s::uuid[])
        """,
        (ids,),
    )
    designs_meta = {row[0]: row for row in cur.fetchall()}
    if not designs_meta:
        return 0

    # Collect every (application_no, registration_no) the designs
    # use, then pull all matching events in one query. The same
    # event row can apply to multiple designs (when design_indices
    # is None or covers multiple indices).
    app_nos = {m[1] for m in designs_meta.values() if m[1]}
    reg_nos = {m[2] for m in designs_meta.values() if m[2]}
    events_by_app: dict = {}
    events_by_reg: dict = {}
    if app_nos or reg_nos:
        cur.execute(
            """
            SELECT application_no, registration_no, event_type,
                   event_date, bulletin_date,
                   details->'design_indices' AS design_indices
            FROM design_events
            WHERE (application_no = ANY(%s) AND application_no IS NOT NULL)
               OR (registration_no = ANY(%s) AND registration_no IS NOT NULL)
            """,
            (list(app_nos), list(reg_nos)),
        )
        for row in cur.fetchall():
            app, reg, et, ed, bd, di = row
            di_parsed = _parse_design_indices(di)
            ev = Event(
                event_type=et, event_date=ed, bulletin_date=bd,
                design_indices=di_parsed,
            )
            if app:
                events_by_app.setdefault(app, []).append(ev)
            if reg:
                events_by_reg.setdefault(reg, []).append(ev)

    rows_updated = 0
    for did, meta in designs_meta.items():
        _, app_no, reg_no, section, design_index, app_date = meta
        events: List[Event] = []
        if app_no and app_no in events_by_app:
            events.extend(events_by_app[app_no])
        if reg_no and reg_no in events_by_reg:
            # Dedup against the app_no list — same event row might
            # match on both keys.
            events.extend(
                ev for ev in events_by_reg[reg_no]
                if ev not in events
            )
        res = derive_design_status(
            events,
            section=section,
            design_index=design_index,
            application_date=app_date,
            today=today,
        )
        cur.execute(
            """
            UPDATE designs
            SET current_status = %s::design_status,
                last_event_type = %s,
                last_event_date = %s,
                status_computed_at = NOW()
            WHERE id = %s::uuid
            """,
            (res.status.value, res.last_event_type, res.last_event_date, did),
        )
        rows_updated += cur.rowcount
    return rows_updated


def _parse_design_indices(raw) -> Optional[List[int]]:
    """The `details->'design_indices'` column comes back from psycopg2
    already JSON-decoded (a list, or None). Coerce to int list."""
    if raw is None:
        return None
    if isinstance(raw, str):
        # Belt-and-braces: some rows might return raw JSON text.
        import json as _json
        try:
            raw = _json.loads(raw)
        except Exception:
            return None
    if isinstance(raw, list):
        out = []
        for v in raw:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out or None
    return None
