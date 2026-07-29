"""Tests for exhaustive, horizon-aware booking candidate generation."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from asimut_booker.intervals import Interval
from asimut_booker.models import (
    AvailabilityState,
    BookingCandidate,
    BookingIntent,
    CalendarEvent,
    OverviewSnapshot,
    RoomObservation,
    RoomPolicy,
)
from asimut_booker.planner import (
    InvalidSnapshotError,
    PlannerOptions,
    candidate_start_minutes,
    generate_candidates,
    order_snipe_candidates,
    upcoming_snipe_candidates,
)
from asimut_booker.policies import LONDON, PolicyContext

TARGET_DATE = date(2026, 8, 5)
OBSERVED_AT = datetime(2026, 7, 29, 9, 0, tzinfo=LONDON)


def snapshot_for(
    *intervals: Interval,
    room_id: str = "B0.11",
    validated: bool = True,
) -> OverviewSnapshot:
    rooms = (
        RoomObservation(
            room_id=room_id,
            date=TARGET_DATE,
            free_intervals=tuple(intervals),
            observed_at=OBSERVED_AT,
        ),
    )
    return OverviewSnapshot(
        displayed_date=TARGET_DATE,
        observed_at=OBSERVED_AT,
        rooms=rooms,
        validated=validated,
        contract_version="test-v1",
    )


class CandidateBoundaryTests(unittest.TestCase):
    def test_every_15_minute_start_boundary_is_generated(self) -> None:
        candidates = generate_candidates(
            snapshot_for(Interval(540, 660)),
            [RoomPolicy("B0.11", horizon_days=7)],
            OBSERVED_AT,
        )
        self.assertEqual(
            [candidate.interval.start for candidate in candidates],
            [540, 555, 570, 585, 600, 615, 630],
        )

    def test_non_grid_preference_boundaries_are_considered_explicitly(self) -> None:
        starts = candidate_start_minutes(
            Interval(540, 660),
            preference_windows=(Interval(547, 620),),
        )
        self.assertIn(547, starts)
        self.assertEqual(starts[:2], (540, 547))

    def test_strict_preference_windows_contain_the_whole_target(self) -> None:
        preference = Interval(1080, 1260)
        candidates = generate_candidates(
            snapshot_for(Interval(450, 1320)),
            [RoomPolicy("B0.11", horizon_days=7)],
            OBSERVED_AT,
            options=PlannerOptions(
                preference_windows=(preference,),
                strict_preferences=True,
            ),
        )

        self.assertEqual(
            [candidate.interval.start for candidate in candidates],
            list(range(1080, 1231, 15)),
        )
        self.assertTrue(
            all(preference.contains(candidate.target_interval) for candidate in candidates)
        )

    def test_unvalidated_snapshot_fails_closed(self) -> None:
        snapshot = snapshot_for(Interval(540, 660), validated=False)
        with self.assertRaises(InvalidSnapshotError):
            generate_candidates(
                snapshot,
                [RoomPolicy("B0.11", horizon_days=7)],
                OBSERVED_AT,
            )


class HorizonPlannerTests(unittest.TestCase):
    def test_current_intent_is_released_slice_and_target_is_preserved(self) -> None:
        now = datetime(2026, 7, 29, 10, 0, tzinfo=LONDON)
        candidates = generate_candidates(
            snapshot_for(Interval(570, 720)),
            [RoomPolicy("B0.11", horizon_days=7)],
            now,
        )
        by_start = {candidate.interval.start: candidate for candidate in candidates}

        released = by_start[570]
        next_snipe = by_start[585]
        self.assertEqual(released.interval, Interval(570, 600))
        self.assertEqual(released.target_interval, Interval(570, 690))
        self.assertEqual(released.state, AvailabilityState.BOOKABLE)
        self.assertEqual(next_snipe.state, AvailabilityState.FREE_OUTSIDE_HORIZON)
        self.assertEqual(
            next_snipe.release_at,
            datetime(2026, 7, 29, 10, 15, tzinfo=LONDON),
        )

    def test_upcoming_snipes_are_filtered_and_release_ordered(self) -> None:
        now = datetime(2026, 7, 29, 9, 55, tzinfo=LONDON)
        candidates = generate_candidates(
            snapshot_for(Interval(570, 660)),
            [RoomPolicy("B0.11", horizon_days=7)],
            now,
        )
        snipes = upcoming_snipe_candidates(candidates, now, within=timedelta(minutes=40))

        self.assertEqual(
            [candidate.release_at for candidate in snipes],
            sorted(candidate.release_at for candidate in snipes),
        )
        self.assertTrue(
            all(candidate.state is AvailabilityState.FREE_OUTSIDE_HORIZON for candidate in snipes)
        )


class OrderingAndRuleTests(unittest.TestCase):
    def test_snipe_order_prioritizes_release_time_before_room_priority(self) -> None:
        early = BookingCandidate(
            intent=BookingIntent(TARGET_DATE, "B0.99", Interval(600, 630), target_end=720),
            release_at=datetime(2026, 7, 29, 10, 30, tzinfo=LONDON),
            state=AvailabilityState.FREE_OUTSIDE_HORIZON,
            room_priority=99,
        )
        late_preferred = BookingCandidate(
            intent=BookingIntent(TARGET_DATE, "B0.11", Interval(615, 645), target_end=735),
            release_at=datetime(2026, 7, 29, 10, 45, tzinfo=LONDON),
            state=AvailabilityState.FREE_OUTSIDE_HORIZON,
            room_priority=0,
            preferred=True,
        )

        self.assertEqual(
            order_snipe_candidates([late_preferred, early]),
            [early, late_preferred],
        )

    def test_planner_finds_after_peak_candidate_when_peak_quota_is_full(self) -> None:
        existing = CalendarEvent(
            date=TARGET_DATE,
            interval=Interval(540, 660),
            room_id="B0.13",
            is_reservation=True,
        )
        now = datetime(2026, 7, 29, 18, 0, tzinfo=LONDON)
        candidates = generate_candidates(
            snapshot_for(Interval(480, 1020)),
            [RoomPolicy("B0.11", horizon_days=7)],
            now,
            context=PolicyContext(events=(existing,)),
        )
        by_start = {candidate.interval.start: candidate for candidate in candidates}

        self.assertEqual(by_start[960].state, AvailabilityState.BOOKABLE)
        self.assertEqual(by_start[960].target_interval, Interval(960, 1020))
        self.assertNotEqual(by_start[945].state, AvailabilityState.BOOKABLE)


if __name__ == "__main__":
    unittest.main()
