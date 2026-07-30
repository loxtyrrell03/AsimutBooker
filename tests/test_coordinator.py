from __future__ import annotations

from datetime import date, datetime

from asimut_booker.adapters import (
    context_with_event,
    context_with_target_end,
    policy_context,
    rules_with_server_peak_limit,
)
from asimut_booker.agenda import AgendaEvent, AgendaObservation
from asimut_booker.coordinator import (
    _bookable_candidates_at,
    _simulate_dry_run_candidates,
)
from asimut_booker.intervals import Interval
from asimut_booker.models import (
    AvailabilityState,
    BookingCandidate,
    BookingIntent,
    RoomPolicy,
)
from asimut_booker.policies import (
    LONDON,
    BookingRules,
    PolicyContext,
    evaluate_intent,
)

DAY = date(2026, 8, 5)


def reservation(
    event_id: str,
    *,
    day: date = DAY,
    start: int = 600,
    end: int = 630,
    room: str = "B0.14",
) -> AgendaEvent:
    return AgendaEvent(
        event_id=event_id,
        event_date=day,
        start_minute=start,
        end_minute=end,
        title="Reservation",
        room_name=room,
        location_id="49",
        status="provisional",
        arrangement_href=f"/arrangement?eventId={event_id}",
    )


def test_pending_extension_target_reserves_time_and_quota() -> None:
    current = reservation("123")
    actual = context_with_event(PolicyContext(), current)

    planned = context_with_target_end(actual, current, 720)

    assert planned.quota_used_minutes == 120
    assert planned.events[0].interval == Interval(600, 720)
    competing = BookingIntent(
        date=DAY,
        room_id="B0.13",
        interval=Interval(630, 660),
    )
    decision = evaluate_intent(
        competing,
        RoomPolicy("B0.13", horizon_days=7),
        datetime(2026, 7, 29, 12, 0, tzinfo=LONDON),
        context=planned,
        check_horizon=False,
    )
    assert not decision.allowed


def test_policy_context_does_not_count_reservations_beyond_rolling_week() -> None:
    now = datetime(2026, 7, 29, 9, 0, tzinfo=LONDON)
    near = reservation(
        "near",
        day=date(2026, 7, 30),
        start=600,
        end=660,
    )
    far = reservation(
        "far",
        day=date(2026, 8, 20),
        start=600,
        end=900,
    )
    observation = AgendaObservation(
        first_date=date(2026, 7, 29),
        last_date=date(2026, 8, 20),
        day_count=23,
        events=(near, far),
        rolling_quota_remaining=27 * 60,
    )

    context = policy_context(observation, now=now, rules=BookingRules())

    assert context.quota_used_minutes == 60


def test_peak_quota_only_constrains_the_date_named_by_the_agenda() -> None:
    observation = AgendaObservation(
        first_date=DAY,
        last_date=DAY,
        day_count=1,
        events=(),
        rolling_quota_remaining=28 * 60,
        peak_quota_remaining=45,
        peak_quota_date=DAY,
    )
    rules = BookingRules(peak_limit_minutes=120)
    context = PolicyContext()

    current = rules_with_server_peak_limit(
        rules,
        context,
        target_date=DAY,
        agenda=observation,
    )
    future = rules_with_server_peak_limit(
        rules,
        context,
        target_date=date(2026, 8, 6),
        agenda=observation,
    )

    assert current.peak_limit_minutes == 45
    assert future.peak_limit_minutes == 120


def test_missing_peak_widget_keeps_the_configured_conservative_cap() -> None:
    observation = AgendaObservation(
        first_date=DAY,
        last_date=DAY,
        day_count=1,
        events=(),
        rolling_quota_remaining=28 * 60,
    )
    rules = BookingRules(peak_limit_minutes=120)

    active = rules_with_server_peak_limit(
        rules,
        PolicyContext(),
        target_date=DAY,
        agenda=observation,
    )

    assert active.peak_limit_minutes == 120


def test_dry_run_plan_does_not_report_mutually_conflicting_candidates() -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=LONDON)
    release = datetime(2026, 7, 29, 10, 30, tzinfo=LONDON)
    first = BookingCandidate(
        intent=BookingIntent(
            date=DAY,
            room_id="B0.14",
            interval=Interval(600, 630),
            target_end=720,
        ),
        release_at=release,
        state=AvailabilityState.BOOKABLE,
        room_priority=0,
    )
    overlaps_target = BookingCandidate(
        intent=BookingIntent(
            date=DAY,
            room_id="B0.13",
            interval=Interval(660, 690),
            target_end=750,
        ),
        release_at=release,
        state=AvailabilityState.BOOKABLE,
        room_priority=1,
    )
    compatible = BookingCandidate(
        intent=BookingIntent(
            date=DAY,
            room_id="B0.13",
            interval=Interval(1020, 1050),
            target_end=1080,
        ),
        release_at=release,
        state=AvailabilityState.BOOKABLE,
        room_priority=1,
    )
    policies = {
        room.room_id: room
        for room in (
            RoomPolicy("B0.14", horizon_days=7, priority=0),
            RoomPolicy("B0.13", horizon_days=7, priority=1),
        )
    }

    selected, simulated = _simulate_dry_run_candidates(
        [first, overlaps_target, compatible],
        context=PolicyContext(),
        policy_by_room=policies,
        rules_by_day={DAY: BookingRules()},
        now=now,
        maximum=3,
    )

    assert selected == [first, compatible]
    assert simulated.quota_used_minutes == 180


def test_candidate_released_during_scan_is_bookable_in_the_same_run() -> None:
    release = datetime(2026, 7, 29, 10, 30, tzinfo=LONDON)
    candidate = BookingCandidate(
        intent=BookingIntent(
            date=DAY,
            room_id="B0.14",
            interval=Interval(600, 630),
            target_end=720,
        ),
        release_at=release,
        state=AvailabilityState.FREE_OUTSIDE_HORIZON,
        reasons=(),
        room_priority=0,
    )

    before = _bookable_candidates_at(
        [candidate],
        datetime(2026, 7, 29, 10, 29, 59, tzinfo=LONDON),
    )
    after = _bookable_candidates_at(
        [candidate],
        datetime(2026, 7, 29, 10, 30, tzinfo=LONDON),
    )

    assert before == []
    assert len(after) == 1
    assert after[0].state is AvailabilityState.BOOKABLE
