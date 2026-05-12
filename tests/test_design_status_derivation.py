"""Tests for pipeline.design_status_derivation."""
from datetime import date

from pipeline.design_status_derivation import (
    DesignStatus,
    Event,
    derive_design_status,
)


def _ev(t: str, d: str | None = None, indices: list[int] | None = None) -> Event:
    parsed = date.fromisoformat(d) if d else None
    return Event(
        event_type=t,
        event_date=parsed,
        bulletin_date=parsed,
        design_indices=indices,
    )


# ---- Section seeding --------------------------------------------


def test_tr_native_section_seeds_yayinda():
    res = derive_design_status([], section="tr_native")
    assert res.status == DesignStatus.YAYINDA
    assert res.last_event_type is None


def test_deferred_section_seeds_yayim_ertelendi():
    res = derive_design_status([], section="deferred")
    assert res.status == DesignStatus.YAYIM_ERTELENDI


def test_unknown_section_seeds_bilinmiyor():
    res = derive_design_status([], section=None)
    assert res.status == DesignStatus.BILINMIYOR
    res2 = derive_design_status([], section="not_a_real_section")
    assert res2.status == DesignStatus.BILINMIYOR


# ---- Status-affecting events ------------------------------------


def test_renewal_moves_to_yenilendi():
    res = derive_design_status(
        [_ev("renewal", "2022-01-01")], section="tr_native",
    )
    assert res.status == DesignStatus.YENILENDI
    assert res.last_event_type == "renewal"


def test_transfer_moves_to_devredildi():
    res = derive_design_status(
        [_ev("transfer", "2022-01-01")], section="tr_native",
    )
    assert res.status == DesignStatus.DEVREDILDI


def test_full_cancellation_board_is_terminal():
    res = derive_design_status(
        [_ev("full_cancellation_board", "2022-01-01")], section="tr_native",
    )
    assert res.status == DesignStatus.HUKUMSUZ


def test_full_cancellation_applicant_is_iptal():
    res = derive_design_status(
        [_ev("full_cancellation_applicant", "2022-01-01")],
        section="tr_native",
    )
    assert res.status == DesignStatus.IPTAL_EDILDI


def test_terminal_state_blocks_later_transfer():
    res = derive_design_status([
        _ev("full_cancellation_board", "2022-01-01"),
        _ev("transfer", "2023-01-01"),
    ], section="tr_native")
    assert res.status == DesignStatus.HUKUMSUZ


def test_transfer_then_cancellation_terminates():
    # Transfer is non-terminal so a later board cancellation still
    # takes the design down. Important: the new owner can cancel.
    res = derive_design_status([
        _ev("transfer", "2022-01-01"),
        _ev("full_cancellation_board", "2023-01-01"),
    ], section="tr_native")
    assert res.status == DesignStatus.HUKUMSUZ


# ---- Partial events with design_indices targeting ---------------


def test_partial_cancellation_targets_only_listed_indices():
    # Application has indices 1, 2, 3. Partial cancellation hits {1, 3}.
    # Design with index 2 stays alive (Yayında); 1 and 3 go Hükümsüz.
    ev = _ev("partial_cancellation_board", "2022-01-01", indices=[1, 3])
    res1 = derive_design_status([ev], section="tr_native", design_index=1)
    res2 = derive_design_status([ev], section="tr_native", design_index=2)
    res3 = derive_design_status([ev], section="tr_native", design_index=3)
    assert res1.status == DesignStatus.HUKUMSUZ
    assert res2.status == DesignStatus.YAYINDA
    assert res3.status == DesignStatus.HUKUMSUZ


def test_partial_event_without_indices_skipped_if_index_unknown():
    # Defensive: if we know our index but the event has no indices,
    # the event applies (no targeting filter). If we don't know our
    # index but the event HAS indices, we skip.
    ev_targeted = _ev("partial_cancellation_owner", "2022-01-01", indices=[2])
    # We don't know our index; event has indices -> skip.
    res = derive_design_status(
        [ev_targeted], section="tr_native", design_index=None,
    )
    assert res.status == DesignStatus.YAYINDA


def test_partial_renewal_targets_only_listed_indices():
    ev = _ev("partial_renewal", "2022-01-01", indices=[2])
    res1 = derive_design_status([ev], section="tr_native", design_index=1)
    res2 = derive_design_status([ev], section="tr_native", design_index=2)
    assert res1.status == DesignStatus.YAYINDA
    assert res2.status == DesignStatus.YENILENDI


# ---- Informational events ---------------------------------------


def test_seizures_and_injunctions_dont_move_status():
    res = derive_design_status([
        _ev("seizure", "2022-01-01"),
        _ev("provisional_seizure", "2022-06-01"),
        _ev("partial_provisional_injunction", "2022-12-01", indices=[1]),
        _ev("provisional_injunction_lifted", "2023-01-01"),
    ], section="tr_native", design_index=1)
    assert res.status == DesignStatus.YAYINDA
    assert res.last_event_type is None


# ---- Same-date tiebreak -----------------------------------------


def test_same_date_renewal_after_partial_cancellation_for_other_index():
    # Bulletin ships both a partial cancellation of index 2 and a
    # renewal of index 1 on the same date. Design at index 1 should
    # end at Yenilendi; design at index 2 at Hükümsüz.
    events = [
        _ev("partial_cancellation_board", "2024-01-01", indices=[2]),
        _ev("partial_renewal", "2024-01-01", indices=[1]),
    ]
    res1 = derive_design_status(events, section="tr_native", design_index=1)
    res2 = derive_design_status(events, section="tr_native", design_index=2)
    assert res1.status == DesignStatus.YENILENDI
    assert res2.status == DesignStatus.HUKUMSUZ


# ---- 25-year term-cap override ----------------------------------


def test_25_year_cap_overrides_yayinda():
    # Filed 30 years ago, no events, no renewals — should be expired.
    res = derive_design_status(
        [],
        section="tr_native",
        design_index=1,
        application_date=date(1995, 1, 1),
        today=date(2025, 6, 1),
    )
    assert res.status == DesignStatus.SURESI_DOLDU
    assert res.last_event_type == "term_expired"


def test_25_year_cap_does_not_override_terminal():
    # Filed 30 years ago AND cancelled at year 5. Stays Hükümsüz
    # (terminal state wins over the time-cap override).
    res = derive_design_status(
        [_ev("full_cancellation_board", "2000-01-01")],
        section="tr_native",
        design_index=1,
        application_date=date(1995, 1, 1),
        today=date(2025, 6, 1),
    )
    assert res.status == DesignStatus.HUKUMSUZ


def test_25_year_cap_does_not_apply_when_within_term():
    # Filed 10 years ago, no events. Stays Yayında.
    res = derive_design_status(
        [],
        section="tr_native",
        design_index=1,
        application_date=date(2015, 6, 1),
        today=date(2025, 6, 1),
    )
    assert res.status == DesignStatus.YAYINDA


def test_25_year_cap_overrides_yenilendi():
    # Renewed once at year 5, but filed 30 years ago. Still expired
    # — renewal doesn't extend past the 25y hard cap.
    res = derive_design_status(
        [_ev("renewal", "2000-01-01")],
        section="tr_native",
        design_index=1,
        application_date=date(1995, 1, 1),
        today=date(2025, 6, 1),
    )
    assert res.status == DesignStatus.SURESI_DOLDU


# ---- Idempotency ------------------------------------------------


def test_idempotent_double_call():
    events = [
        _ev("renewal", "2022-01-01"),
        _ev("transfer", "2023-01-01"),
    ]
    args = dict(section="tr_native", design_index=1)
    assert derive_design_status(events, **args) == derive_design_status(events, **args)
