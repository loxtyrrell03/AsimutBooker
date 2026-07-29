"""Tests for pure interval and booking-policy behavior."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from asimut_booker.intervals import Interval, merge_intervals, subtract_intervals
from asimut_booker.models import (
    AvailabilityState,
    BookingIntent,
    CalendarEvent,
    PolicyReason,
    RoomPolicy,
)
from asimut_booker.policies import (
    LONDON,
    PolicyContext,
    evaluate_intent,
    horizon_release_at,
    minimum_booking_release_at,
)


class IntervalTests(unittest.TestCase):
    def test_half_open_overlap_and_duration(self) -> None:
        first = Interval(9 * 60, 10 * 60)
        second = Interval(10 * 60, 11 * 60)

        self.assertEqual(first.duration, 60)
        self.assertFalse(first.overlaps(second))
        self.assertTrue(first.touches(second))
        self.assertTrue(first.overlaps(Interval(9 * 60 + 59, 10 * 60 + 1)))

    def test_merge_and_subtract_split_intervals(self) -> None:
        merged = merge_intervals([Interval(600, 630), Interval(540, 570), Interval(570, 600)])
        self.assertEqual(merged, [Interval(540, 630)])

        remaining = subtract_intervals(
            Interval(480, 720),
            [Interval(450, 510), Interval(555, 585), Interval(690, 750)],
        )
        self.assertEqual(
            remaining,
            [Interval(510, 555), Interval(585, 690)],
        )


class HorizonTests(unittest.TestCase):
    def test_minimum_booking_releases_at_end_minus_room_horizon(self) -> None:
        target = date(2026, 8, 5)
        self.assertEqual(
            minimum_booking_release_at(target, 10 * 60, 7),
            datetime(2026, 7, 29, 10, 30, tzinfo=LONDON),
        )
        self.assertEqual(
            horizon_release_at(target, 10 * 60 + 30, 5),
            datetime(2026, 7, 31, 10, 30, tzinfo=LONDON),
        )
        self.assertEqual(
            horizon_release_at(target, 10 * 60 + 30, 3),
            datetime(2026, 8, 2, 10, 30, tzinfo=LONDON),
        )

    def test_horizon_is_exact_and_inclusive_at_release(self) -> None:
        intent = BookingIntent(
            date=date(2026, 8, 5),
            room_id="B0.11",
            interval=Interval(600, 630),
        )
        room = RoomPolicy("B0.11", horizon_days=7)
        release = datetime(2026, 7, 29, 10, 30, tzinfo=LONDON)

        before = evaluate_intent(intent, room, release - timedelta(seconds=1))
        at_release = evaluate_intent(intent, room, release)

        self.assertEqual(before.state, AvailabilityState.FREE_OUTSIDE_HORIZON)
        self.assertEqual(before.reasons, (PolicyReason.OUTSIDE_HORIZON,))
        self.assertTrue(at_release.allowed)
        self.assertEqual(at_release.state, AvailabilityState.BOOKABLE)

    def test_release_preserves_london_wall_clock_across_dst(self) -> None:
        release = horizon_release_at(date(2025, 3, 31), 10 * 60 + 30, 3)

        self.assertEqual(release.date(), date(2025, 3, 28))
        self.assertEqual((release.hour, release.minute), (10, 30))
        self.assertEqual(release.utcoffset(), timedelta(0))

    def test_naive_now_is_rejected(self) -> None:
        intent = BookingIntent(
            date=date(2026, 8, 5),
            room_id="B0.11",
            interval=Interval(600, 630),
        )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            evaluate_intent(
                intent,
                RoomPolicy("B0.11", horizon_days=7),
                datetime(2026, 7, 29, 10, 30),
            )


class BookingPolicyTests(unittest.TestCase):
    target_date = date(2026, 8, 5)
    now = datetime(2026, 7, 20, 8, 0, tzinfo=LONDON)
    room = RoomPolicy("B0.11", horizon_days=7)

    def evaluate(
        self,
        interval: Interval,
        *,
        context: PolicyContext | None = None,
        day: date | None = None,
    ):
        intent = BookingIntent(
            date=day or self.target_date,
            room_id=self.room.room_id,
            interval=interval,
        )
        return evaluate_intent(
            intent,
            self.room,
            self.now,
            context=context,
            check_horizon=False,
        )

    def test_future_quota_allows_exact_limit_and_rejects_one_minute_over(self) -> None:
        exact = self.evaluate(Interval(480, 510), context=PolicyContext(quota_used_minutes=1650))
        over = self.evaluate(Interval(480, 510), context=PolicyContext(quota_used_minutes=1651))

        self.assertTrue(exact.allowed)
        self.assertIn(PolicyReason.QUOTA_EXCEEDED, over.reasons)

    def test_personal_conflict_is_rejected_and_cancelled_event_is_ignored(self) -> None:
        active = CalendarEvent(
            date=self.target_date,
            interval=Interval(600, 660),
            title="Class",
        )
        cancelled = CalendarEvent(
            date=self.target_date,
            interval=Interval(600, 660),
            title="Cancelled class",
            cancelled=True,
        )

        blocked = self.evaluate(Interval(630, 690), context=PolicyContext(events=(active,)))
        allowed = self.evaluate(Interval(630, 690), context=PolicyContext(events=(cancelled,)))

        self.assertIn(PolicyReason.PERSONAL_CONFLICT, blocked.reasons)
        self.assertTrue(allowed.allowed)

    def test_daily_peak_limit_is_based_on_overlap_only(self) -> None:
        existing = CalendarEvent(
            date=self.target_date,
            interval=Interval(540, 660),
            room_id="B0.13",
            is_reservation=True,
        )
        context = PolicyContext(events=(existing,))

        partly_peak = self.evaluate(Interval(945, 975), context=context)
        after_peak = self.evaluate(Interval(960, 990), context=context)

        self.assertIn(PolicyReason.PEAK_QUOTA_EXCEEDED, partly_peak.reasons)
        self.assertTrue(after_peak.allowed)

    def test_peak_limit_does_not_apply_on_weekend(self) -> None:
        saturday = date(2026, 8, 8)
        existing = CalendarEvent(
            date=saturday,
            interval=Interval(540, 660),
            room_id="B0.13",
            is_reservation=True,
        )
        decision = self.evaluate(
            Interval(660, 720),
            day=saturday,
            context=PolicyContext(events=(existing,)),
        )
        self.assertTrue(decision.allowed)

    def test_same_room_zero_and_59_minute_gaps_are_rejected(self) -> None:
        touching = CalendarEvent(
            date=self.target_date,
            interval=Interval(720, 780),
            room_id=self.room.room_id,
            is_reservation=True,
        )
        gap_59 = CalendarEvent(
            date=self.target_date,
            interval=Interval(720, 781),
            room_id=self.room.room_id,
            is_reservation=True,
        )

        zero = self.evaluate(Interval(780, 810), context=PolicyContext(events=(touching,)))
        fifty_nine = self.evaluate(Interval(840, 870), context=PolicyContext(events=(gap_59,)))

        self.assertIn(PolicyReason.SAME_ROOM_GAP, zero.reasons)
        self.assertIn(PolicyReason.SAME_ROOM_GAP, fifty_nine.reasons)

    def test_same_room_exact_60_minute_gap_is_allowed(self) -> None:
        existing = CalendarEvent(
            date=self.target_date,
            interval=Interval(720, 780),
            room_id=self.room.room_id,
            is_reservation=True,
        )
        decision = self.evaluate(Interval(840, 870), context=PolicyContext(events=(existing,)))
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
