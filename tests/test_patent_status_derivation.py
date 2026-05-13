"""Tests for pipeline.patent_status_derivation.

Each test names a real-world lifecycle scenario. The 2019/02599 case
near the bottom is the bug that motivated this module — the patent
detail UI was showing "Tescilli faydalı model" (granted UM bulletin
classification) while the most recent event was GRANT_FEE_LAPSE, so
the user couldn't tell the patent had actually dropped.
"""
from datetime import date

from pipeline.patent_status_derivation import (
    Event,
    PatentStatus,
    derive_patent_status,
)


def _ev(t: str, d: str | None = None) -> Event:
    """Tiny helper — builds an Event from an event_type + ISO date.
    bulletin_date is set to event_date when not given."""
    parsed = date.fromisoformat(d) if d else None
    return Event(event_type=t, event_date=parsed, bulletin_date=parsed)


# ---- Single-event happy paths ------------------------------------


def test_no_events_is_unknown():
    res = derive_patent_status([])
    assert res.status == PatentStatus.UNKNOWN
    assert res.last_event_type is None
    assert res.last_event_date is None


def test_application_published_alone_is_pending():
    res = derive_patent_status([_ev("APPLICATION_PUBLISHED", "2020-01-01")])
    assert res.status == PatentStatus.PENDING
    assert res.last_event_type == "APPLICATION_PUBLISHED"
    assert res.last_event_date == date(2020, 1, 1)


def test_grant_announced_is_active():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("GRANT_ANNOUNCED", "2021-06-01"),
    ])
    assert res.status == PatentStatus.ACTIVE
    assert res.last_event_type == "GRANT_ANNOUNCED"


def test_application_rejected_is_terminal():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("APPLICATION_REJECTED", "2020-08-01"),
        # Subsequent events ignored
        _ev("APPLICATION_FEE_REVALIDATION", "2021-01-01"),
    ])
    assert res.status == PatentStatus.REJECTED
    assert res.last_event_type == "APPLICATION_REJECTED"


def test_grant_protection_expired_is_terminal():
    res = derive_patent_status([
        _ev("GRANT_ANNOUNCED", "2010-01-01"),
        _ev("GRANT_PROTECTION_EXPIRED", "2030-01-01"),
    ])
    assert res.status == PatentStatus.EXPIRED


# ---- Lapses + revivals ------------------------------------------


def test_application_fee_lapse_then_revival():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("APPLICATION_FEE_LAPSE", "2020-06-01"),
        _ev("APPLICATION_FEE_REVALIDATION", "2020-09-01"),
    ])
    assert res.status == PatentStatus.PENDING
    assert res.last_event_type == "APPLICATION_FEE_REVALIDATION"


def test_grant_fee_lapse_then_revival():
    res = derive_patent_status([
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
        _ev("GRANT_FEE_LAPSE", "2022-01-01"),
        _ev("GRANT_FEE_REVALIDATION", "2022-06-01"),
    ])
    assert res.status == PatentStatus.ACTIVE


def test_grant_fee_lapse_without_revival_stays_lapsed():
    res = derive_patent_status([
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
        _ev("GRANT_FEE_LAPSE", "2022-01-01"),
    ])
    assert res.status == PatentStatus.LAPSED_GRANT


def test_terminal_state_blocks_revival():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("APPLICATION_REJECTED", "2020-06-01"),
        # A revival on a terminal state is ignored.
        _ev("GRANT_FEE_REVALIDATION", "2021-01-01"),
    ])
    assert res.status == PatentStatus.REJECTED


def test_application_abandoned_then_cancelled():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("APPLICATION_ABANDONED", "2020-06-01"),
        _ev("APPLICATION_ABANDONED_CANCELLED", "2020-09-01"),
    ])
    assert res.status == PatentStatus.PENDING


# ---- Same-date tiebreak -----------------------------------------


def test_same_date_lapse_then_revival_revival_wins():
    # On a single bulletin shipping both a lapse and the matching
    # revival, the legal end state is alive. The positivity-rank
    # tiebreak orders the revival AFTER the lapse on the same date.
    res = derive_patent_status([
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
        _ev("GRANT_FEE_REVALIDATION", "2022-10-21"),
        _ev("GRANT_FEE_LAPSE", "2022-10-21"),
    ])
    assert res.status == PatentStatus.ACTIVE
    assert res.last_event_type == "GRANT_FEE_REVALIDATION"


def test_same_date_grant_and_publication_grant_wins():
    # GRANT_ANNOUNCED has higher positivity rank than
    # APPLICATION_PUBLISHED, so a bulletin that ships both on the
    # same date lands the patent in ACTIVE, not PENDING.
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2021-01-01"),
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
    ])
    assert res.status == PatentStatus.ACTIVE


# ---- Informational events are no-ops ----------------------------


def test_search_reports_and_assignments_dont_move_status():
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("SEARCH_REPORT_PATENT", "2020-08-01"),
        _ev("SEARCH_REPORT_ARTICLE_96", "2020-08-01"),
        _ev("ASSIGNMENT_RECORDED", "2020-10-01"),
        _ev("POST_PUB_AMENDMENT", "2020-11-01"),
        _ev("LICENSE_OFFER", "2020-12-01"),
    ])
    assert res.status == PatentStatus.PENDING
    # last_event_type tracks status-affecting events only — search
    # reports etc. show up in the timeline but don't claim authorship
    # of the lifecycle field.
    assert res.last_event_type == "APPLICATION_PUBLISHED"


def test_unsorted_input_still_works():
    res = derive_patent_status([
        _ev("GRANT_FEE_LAPSE", "2022-01-01"),
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
    ])
    # Same outcome as the sorted version.
    assert res.status == PatentStatus.LAPSED_GRANT


# ---- The bug case that motivated the module ---------------------


def test_2019_02599_real_case():
    # The patent the user reported on. Bulletins (oldest -> newest):
    #   2019/5  APPLICATION_PUBLISHED
    #   2020/10 SEARCH_REPORT_UM + SEARCH_REPORT_ARTICLE_96 (informational)
    #   2021/2  GRANT_ANNOUNCED
    #   2022/10 APPLICATION_FEE_REVALIDATION (for the pre-grant
    #           application fee — doesn't apply post-grant)
    #   2022/10 GRANT_FEE_LAPSE (the kill signal)
    #
    # Expected: LAPSED_GRANT. The same-day APPLICATION_FEE_REVALIDATION
    # is ignored because the patent is already ACTIVE (no pending
    # application-fee lapse to revive).
    res = derive_patent_status([
        _ev("APPLICATION_PUBLISHED", "2019-05-21"),
        _ev("SEARCH_REPORT_UM", "2020-10-21"),
        _ev("SEARCH_REPORT_ARTICLE_96", "2020-10-21"),
        _ev("GRANT_ANNOUNCED", "2021-02-22"),
        _ev("APPLICATION_FEE_REVALIDATION", "2022-10-21"),
        _ev("GRANT_FEE_LAPSE", "2022-10-21"),
    ])
    assert res.status == PatentStatus.LAPSED_GRANT
    assert res.last_event_type == "GRANT_FEE_LAPSE"
    assert res.last_event_date == date(2022, 10, 21)


# ---- Idempotency ------------------------------------------------


def test_idempotent_double_call():
    events = [
        _ev("APPLICATION_PUBLISHED", "2020-01-01"),
        _ev("GRANT_ANNOUNCED", "2021-01-01"),
        _ev("GRANT_FEE_LAPSE", "2022-01-01"),
    ]
    assert derive_patent_status(events) == derive_patent_status(events)


# ---- record_type seeding ----------------------------------------


def test_published_app_seed_with_only_search_report_event():
    # The bug case: a freshly-published A1 application that only has
    # a SEARCH_REPORT event in patent_events. record_type alone tells
    # us it's PENDING; the search report is informational.
    res = derive_patent_status(
        [_ev("SEARCH_REPORT_WITH_APPLICATION_PATENT", "2025-12-22")],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.PENDING
    # last_event_* still None — search reports don't claim authorship
    # of the lifecycle field.
    assert res.last_event_type is None


def test_granted_seed_with_no_events_is_active():
    # Fresh grant bulletin row with no secondary events yet.
    res = derive_patent_status([], record_type="GRANTED_PATENT")
    assert res.status == PatentStatus.ACTIVE


def test_granted_seed_then_lapse():
    # GRANTED_UM seed + later GRANT_FEE_LAPSE → LAPSED_GRANT.
    # Matches the 2019/02599 flow when the GRANT_ANNOUNCED event is
    # absent but record_type still tells us we got there.
    res = derive_patent_status(
        [_ev("GRANT_FEE_LAPSE", "2022-10-21")],
        record_type="GRANTED_UM",
    )
    assert res.status == PatentStatus.LAPSED_GRANT
    assert res.last_event_type == "GRANT_FEE_LAPSE"


def test_unknown_record_type_does_not_seed():
    # record_type=UNKNOWN / None falls back to state=UNKNOWN.
    res = derive_patent_status([], record_type="UNKNOWN")
    assert res.status == PatentStatus.UNKNOWN
    res2 = derive_patent_status([], record_type=None)
    assert res2.status == PatentStatus.UNKNOWN


def test_seed_does_not_override_terminal_event():
    # Even if seeded ACTIVE, a REJECTED event still terminates.
    res = derive_patent_status(
        [_ev("APPLICATION_REJECTED", "2020-01-01")],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.REJECTED


# ---- Grant-fee inference (the 2016/02872 case) -------------------


def test_grant_fee_lapse_on_pending_infers_grant():
    # PENDING seed (publication only) + GRANT_FEE_LAPSE. The lapse
    # implies a prior grant the state machine never saw — fast-forward.
    res = derive_patent_status(
        [_ev("GRANT_FEE_LAPSE", "2020-01-01")],
        record_type="PUBLISHED_UM_APP",
    )
    assert res.status == PatentStatus.LAPSED_GRANT
    assert res.last_event_type == "GRANT_FEE_LAPSE"


def test_grant_fee_revalidation_on_pending_infers_grant():
    # PENDING seed + GRANT_FEE_REVALIDATION → ACTIVE (the revalidation
    # implies the patent was granted then lapsed then revived).
    res = derive_patent_status(
        [_ev("GRANT_FEE_REVALIDATION", "2020-01-01")],
        record_type="PUBLISHED_UM_APP",
    )
    assert res.status == PatentStatus.ACTIVE


def test_grant_fee_lapse_cancelled_on_pending_infers_grant():
    res = derive_patent_status(
        [_ev("GRANT_FEE_LAPSE_CANCELLED", "2020-01-01")],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.ACTIVE


def test_grant_fee_lapse_on_unknown_infers_grant():
    # No record_type seed — the lapse alone is enough to infer the
    # prior grant. Older UNKNOWN-typed rows with only fee events end
    # up correctly classified.
    res = derive_patent_status(
        [_ev("GRANT_FEE_LAPSE", "2020-01-01")],
    )
    assert res.status == PatentStatus.LAPSED_GRANT


# ---- PROCEDURAL_REVALIDATION + WITHDRAWN softening -------------


def test_procedural_revalidation_revives_withdrawn():
    # Same date: APPLICATION_WITHDRAWN (rank -3) applied first,
    # PROCEDURAL_REVALIDATION (rank +2) applied second → PENDING.
    res = derive_patent_status(
        [
            _ev("APPLICATION_WITHDRAWN", "2025-04-21"),
            _ev("PROCEDURAL_REVALIDATION", "2025-04-21"),
        ],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.PENDING
    assert res.last_event_type == "PROCEDURAL_REVALIDATION"


def test_procedural_revalidation_revives_lapsed_application():
    res = derive_patent_status(
        [
            _ev("APPLICATION_FEE_LAPSE", "2020-01-01"),
            _ev("PROCEDURAL_REVALIDATION", "2020-06-01"),
        ],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.PENDING


def test_procedural_revalidation_revives_lapsed_grant():
    res = derive_patent_status(
        [
            _ev("GRANT_FEE_LAPSE", "2020-01-01"),
            _ev("PROCEDURAL_REVALIDATION", "2020-06-01"),
        ],
        record_type="GRANTED_PATENT",
    )
    assert res.status == PatentStatus.ACTIVE


def test_procedural_revalidation_on_active_is_noop():
    # Don't accidentally downgrade an active patent — the revival
    # is informational when there's nothing to revive.
    res = derive_patent_status(
        [_ev("PROCEDURAL_REVALIDATION", "2020-01-01")],
        record_type="GRANTED_PATENT",
    )
    assert res.status == PatentStatus.ACTIVE


def test_procedural_revalidation_on_rejected_is_blocked():
    # REJECTED is truly terminal — even a PROCEDURAL_REVALIDATION
    # doesn't revive it (different from WITHDRAWN which is reversible).
    res = derive_patent_status(
        [
            _ev("APPLICATION_REJECTED", "2020-01-01"),
            _ev("PROCEDURAL_REVALIDATION", "2020-06-01"),
        ],
        record_type="PUBLISHED_APP",
    )
    assert res.status == PatentStatus.REJECTED


def test_grant_announced_overrides_withdrawn():
    # The 2022/019658 case: withdrawn at year N, grant announced at
    # year N+1. The grant proves the withdrawal was undone.
    res = derive_patent_status(
        [
            _ev("APPLICATION_WITHDRAWN", "2025-04-21"),
            _ev("GRANT_ANNOUNCED", "2026-02-23"),
        ],
        record_type="GRANTED_PATENT",
    )
    assert res.status == PatentStatus.ACTIVE
    assert res.last_event_type == "GRANT_ANNOUNCED"


def test_grant_finalized_overrides_withdrawn():
    res = derive_patent_status(
        [
            _ev("APPLICATION_WITHDRAWN", "2024-01-01"),
            _ev("GRANT_FINALIZED", "2025-01-01"),
        ],
        record_type="GRANTED_PATENT",
    )
    assert res.status == PatentStatus.ACTIVE


def test_2022_019658_real_case():
    # The bug case the user reported. Patent application that was
    # withdrawn on 2025-04-21 then immediately procedurally revived
    # the same day, then granted ten months later.
    res = derive_patent_status(
        [
            _ev("APPLICATION_PUBLISHED", "2024-07-22"),
            _ev("SEARCH_REPORT_WITH_APPLICATION_PATENT", "2024-07-22"),
            _ev("POST_PUB_AMENDMENT", "2024-12-23"),
            _ev("APPLICATION_WITHDRAWN", "2025-04-21"),
            _ev("PROCEDURAL_REVALIDATION", "2025-04-21"),
            _ev("GRANT_ANNOUNCED", "2026-02-23"),
        ],
        record_type="GRANTED_PATENT",
    )
    assert res.status == PatentStatus.ACTIVE
    assert res.last_event_type == "GRANT_ANNOUNCED"
    assert res.last_event_date == date(2026, 2, 23)


def test_2016_02872_cycling_grant_fee_events():
    # The bug case the user reported. UM with no GRANT_ANNOUNCED
    # captured but four full cycles of GRANT_FEE_LAPSE +
    # GRANT_FEE_REVALIDATION + APPLICATION_FEE_REVALIDATION
    # (which is the application-fee path, doesn't apply post-grant
    # so it's a no-op once we've inferred the grant).
    #
    # Most-recent event (2023-10-23) is a grant fee lapse with no
    # matching same-date revalidation, so final state must be
    # LAPSED_GRANT.
    events = [
        _ev("GRANT_FEE_LAPSE", "2019-01-21"),
        _ev("APPLICATION_FEE_REVALIDATION", "2019-01-21"),
        _ev("GRANT_FEE_REVALIDATION", "2019-02-21"),
        _ev("GRANT_FEE_REVALIDATION", "2019-02-21"),
        _ev("GRANT_FEE_LAPSE", "2020-04-21"),
        _ev("APPLICATION_FEE_REVALIDATION", "2020-04-21"),
        _ev("GRANT_FEE_REVALIDATION", "2020-05-21"),
        _ev("GRANT_FEE_REVALIDATION", "2020-05-21"),
        _ev("GRANT_FEE_LAPSE", "2021-09-21"),
        _ev("APPLICATION_FEE_REVALIDATION", "2021-09-21"),
        _ev("GRANT_FEE_REVALIDATION", "2021-10-21"),
        _ev("GRANT_FEE_REVALIDATION", "2021-10-21"),
        _ev("GRANT_FEE_LAPSE", "2022-10-21"),
        _ev("APPLICATION_FEE_REVALIDATION", "2022-10-21"),
        _ev("GRANT_FEE_REVALIDATION", "2022-11-21"),
        _ev("GRANT_FEE_REVALIDATION", "2022-11-21"),
        _ev("GRANT_FEE_LAPSE", "2023-10-23"),
        _ev("APPLICATION_FEE_REVALIDATION", "2023-10-23"),
    ]
    res = derive_patent_status(events, record_type="PUBLISHED_UM_APP")
    assert res.status == PatentStatus.LAPSED_GRANT
    assert res.last_event_type == "GRANT_FEE_LAPSE"
    assert res.last_event_date == date(2023, 10, 23)
