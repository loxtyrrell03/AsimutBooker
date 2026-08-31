import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import book_week
from app_settings import SettingsError, load_settings
from book_week import (
    BookingTracker,
    calculate_target_hours_for_day,
    page_is_asimut_origin,
    interval_is_strictly_preferred,
    load_booking_strategy,
    load_disabled_dates,
    load_extendable_bookings,
    load_time_preferences,
    trim_to_strict_time_window,
)
from practice_plan import PracticePlan, remaining_target_hours
from runtime_guard import booking_times_match, hhmm_values_match, parse_confirmed_event_id


class SameRoomGapBoundaryTests(unittest.TestCase):
    def setUp(self):
        # 2026-08-30 is a Sunday, so these tests isolate same-room behavior
        # without a weekday peak-hours limit affecting the result.
        self.day = date(2026, 8, 30)
        self.day_at_noon = datetime(2026, 8, 30, 12, 0)

    def test_session_booking_accepts_date_datetime_mix_and_enforces_exact_gap(self):
        tracker = BookingTracker()
        tracker.add_booking("B0.29", self.day_at_noon, 9.0, 10.0)

        allowed, reason = tracker.can_book("B0.29", self.day, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("0min after", reason)

        allowed, reason = tracker.can_book("B0.29", self.day, 10.75, 30)
        self.assertFalse(allowed)
        self.assertIn("45min after", reason)

        allowed, reason = tracker.can_book("B0.29", self.day, 11.0, 30)
        self.assertTrue(allowed, reason)
        self.assertEqual(tracker.bookings_today(self.day), 1)
        self.assertEqual(tracker.bookings_today(self.day_at_noon), 1)
    def test_session_booking_enforces_gap_before_at_zero_45_and_60_minutes(self):
        tracker = BookingTracker()
        tracker.add_booking("B1.09", self.day, 12.0, 13.0)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 11.5, 30)
        self.assertFalse(allowed)
        self.assertIn("0min before", reason)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 10.75, 30)
        self.assertFalse(allowed)
        self.assertIn("45min before", reason)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 10.5, 30)
        self.assertTrue(allowed, reason)

    def test_existing_reservation_gap_is_room_specific_across_date_types(self):
        tracker = BookingTracker()
        tracker.add_existing_event(
            self.day,
            9.0,
            10.0,
            is_reservation=True,
            room="B0.27",
        )

        allowed, reason = tracker.can_book("B0.27", self.day_at_noon, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("existing reservation", reason)

        allowed, reason = tracker.can_book("B0.27", self.day_at_noon, 11.0, 30)
        self.assertTrue(allowed, reason)

        # A room gap must not block a different room at the same adjacent time.
        allowed, reason = tracker.can_book("B1.16", self.day_at_noon, 10.0, 30)
        self.assertTrue(allowed, reason)


class DailyPlanningCapacityHoldTests(unittest.TestCase):
    def test_wait_reserves_daily_weekly_and_peak_capacity_together(self):
        now = datetime(2026, 8, 30, 9, 30)
        selected = mock.Mock(
            potential_minutes=120,
            start_minutes=12 * 60,
            end_minutes=14 * 60,
            room="B0.29",
        )
        decision = mock.Mock(
            action="wait",
            selected=selected,
            held_peak_minutes=90,
        )
        ready = mock.Mock(
            unlock_at=now,
            start_minutes=16 * 60,
            end_minutes=17 * 60,
            room="B1.09",
        )
        future = mock.Mock(
            unlock_at=datetime(2026, 8, 30, 10, 0),
            start_minutes=18 * 60,
            end_minutes=19 * 60,
            room="B1.09",
        )

        with mock.patch.object(
            book_week,
            "build_day_booking_opportunities",
            return_value=[ready, future],
        ) as rebuild:
            result = book_week.current_opportunities_after_hold(
                [],
                date(2026, 9, 4),
                mock.Mock(),
                {"enabled": False},
                mock.Mock(),
                decision,
                now=now,
                remaining_daily_hours=2.5,
                only_room="B0.29",
            )

        self.assertEqual(result, [ready])
        self.assertEqual(rebuild.call_args.kwargs["remaining_daily_hours"], 0.5)
        self.assertEqual(rebuild.call_args.kwargs["reserved_weekly_minutes"], 120)
        self.assertEqual(rebuild.call_args.kwargs["reserved_peak_minutes"], 90)

    def test_wait_rejects_surplus_that_overlaps_the_held_session(self):
        now = datetime(2026, 8, 30, 9, 30)
        selected = mock.Mock(
            potential_minutes=120,
            start_minutes=12 * 60,
            end_minutes=14 * 60,
            room="B0.29",
        )
        decision = mock.Mock(
            action="wait",
            selected=selected,
            held_peak_minutes=120,
        )
        overlapping = mock.Mock(
            unlock_at=now,
            start_minutes=11 * 60,
            end_minutes=13 * 60,
            room="B1.09",
        )
        safe = mock.Mock(
            unlock_at=now,
            start_minutes=16 * 60,
            end_minutes=18 * 60,
            room="B1.09",
        )

        with mock.patch.object(
            book_week,
            "build_day_booking_opportunities",
            return_value=[overlapping, safe],
        ):
            result = book_week.current_opportunities_after_hold(
                [],
                date(2026, 9, 4),
                mock.Mock(),
                {"enabled": False},
                mock.Mock(),
                decision,
                now=now,
                remaining_daily_hours=4.0,
            )

        self.assertEqual(result, [safe])

    def test_non_strict_time_preference_reaches_the_smart_ranker(self):
        tracker = mock.Mock()
        tracker.get_remaining_quota_hours.return_value = 10.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.conflict_ranges = {}
        tracker.get_same_room_blocked_ranges.return_value = []
        with (
            mock.patch.object(book_week, "PRIORITY_ROOMS", ["B0.29"]),
            mock.patch.object(book_week, "room_horizon_minutes", return_value=7200),
            mock.patch.object(
                book_week,
                "enumerate_gap_opportunities",
                return_value=[],
            ) as enumerate_gap,
        ):
            book_week.build_day_booking_opportunities(
                [
                    {
                        "room": "B0.29",
                        "slots": [{"startHour": 16.0, "endHour": 20.0}],
                    }
                ],
                date(2026, 9, 4),
                tracker,
                {
                    "enabled": True,
                    "strict_mode": False,
                    "start_hour": 18.0,
                    "end_hour": 22.0,
                },
                book_week.DailyPlanningPreferences(),
                now=datetime(2026, 8, 30, 12, 0),
            )

        self.assertEqual(
            enumerate_gap.call_args.kwargs["soft_preferred_window"],
            (18 * 60, 22 * 60),
        )
        self.assertIsNone(enumerate_gap.call_args.kwargs["strict_window"])

    def test_pending_extension_reserves_daily_peak_and_weekly_capacity(self):
        today = date(2026, 8, 30)
        target_date = date(2026, 9, 4)
        tracker = mock.Mock()
        tracker.get_remaining_quota_hours.return_value = 1.5
        tracker.get_remaining_peak_minutes.return_value = 90
        tracker.get_hours_for_day.return_value = 0.5
        tracker.conflict_ranges = {}
        tracker.reservation_ranges = {}
        tracker.bookings = []
        booking = {
            "date": target_date.isoformat(),
            "room": "B0.29",
            "startTime": "12:00",
            "endTime": "12:30",
            "target_end": "14:00",
        }

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["B0.29"],
            ROOM_HORIZON_MINUTES={"B0.29": 5 * 24 * 60},
            BOOKING_WINDOW_DATES=tuple(
                today + book_week.timedelta(days=offset) for offset in range(8)
            ),
        ):
            target_holds, peak_holds, held = (
                book_week.calculate_extension_capacity_holds(
                    [booking],
                    tracker,
                    PracticePlan(enabled=True, default_hours=2.0),
                    set(),
                    now=datetime(2026, 8, 30, 10, 0),
                )
            )

        self.assertEqual(target_holds, {target_date.isoformat(): 90})
        self.assertEqual(peak_holds, {target_date.isoformat(): 90})
        self.assertEqual(held, (booking,))

    def test_extension_hold_stops_at_the_first_strict_conflict_or_room_gap_limit(self):
        today = date(2026, 8, 30)
        target_date = date(2026, 9, 4)
        tracker = BookingTracker()
        tracker.add_existing_event(
            target_date,
            12.0,
            12.5,
            is_reservation=True,
            room="B0.29",
        )
        tracker.add_existing_event(target_date, 13.5, 14.0)
        tracker.add_existing_event(
            target_date,
            14.25,
            14.75,
            is_reservation=True,
            room="B0.29",
        )
        booking = {
            "date": target_date.isoformat(),
            "room": "B0.29",
            "startTime": "12:00",
            "endTime": "12:30",
            "target_end": "14:00",
        }

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["B0.29"],
            ROOM_HORIZON_MINUTES={"B0.29": 5 * 24 * 60},
            BOOKING_WINDOW_DATES=tuple(
                today + book_week.timedelta(days=offset) for offset in range(8)
            ),
        ):
            target_holds, peak_holds, held = (
                book_week.calculate_extension_capacity_holds(
                    [booking],
                    tracker,
                    PracticePlan(enabled=True, default_hours=3.0),
                    set(),
                    time_prefs={
                        "enabled": True,
                        "strict_mode": True,
                        "start_hour": 12.0,
                        "end_hour": 13.75,
                    },
                    now=datetime(2026, 8, 30, 10, 0),
                )
            )

        # The class permits 60 minutes and the strict end permits 75, but the
        # fresh 60-minute same-room gap before 14:15 permits only 45.
        self.assertEqual(target_holds, {target_date.isoformat(): 45})
        self.assertEqual(peak_holds, {target_date.isoformat(): 45})
        self.assertEqual(held[0]["target_end"], "13:15")

    def test_extension_outside_the_current_strict_window_holds_no_capacity(self):
        today = date(2026, 8, 30)
        target_date = date(2026, 9, 4)
        tracker = BookingTracker()
        tracker.add_existing_event(
            target_date,
            12.0,
            12.5,
            is_reservation=True,
            room="B0.29",
        )
        booking = {
            "date": target_date.isoformat(),
            "room": "B0.29",
            "startTime": "12:00",
            "endTime": "12:30",
            "target_end": "14:00",
        }

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["B0.29"],
            ROOM_HORIZON_MINUTES={"B0.29": 5 * 24 * 60},
            BOOKING_WINDOW_DATES=tuple(
                today + book_week.timedelta(days=offset) for offset in range(8)
            ),
        ):
            target_holds, peak_holds, held = (
                book_week.calculate_extension_capacity_holds(
                    [booking],
                    tracker,
                    PracticePlan(enabled=True, default_hours=2.0),
                    set(),
                    time_prefs={
                        "enabled": True,
                        "strict_mode": True,
                        "start_hour": 13.0,
                        "end_hour": 16.0,
                    },
                    now=datetime(2026, 8, 30, 10, 0),
                )
            )

        self.assertEqual(target_holds, {})
        self.assertEqual(peak_holds, {})
        self.assertEqual(held, ())

    def test_display_primary_matches_current_fallback_booking_decision(self):
        target_date = date(2026, 9, 4)
        now = datetime(2026, 8, 30, 9, 30)

        def opportunity(room, start, end, unlock, priority, preferred):
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=unlock,
                room_priority=priority,
                initial_minutes=30,
                potential_minutes=end - start,
                preferred_minutes=preferred,
                soft_preferred_minutes=preferred,
                peak_minutes=end - start,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=start,
                source_gap_end_minutes=end,
            )

        current = opportunity(
            "Early",
            9 * 60,
            11 * 60,
            now,
            0,
            0,
        )
        future = opportunity(
            "Afternoon",
            12 * 60,
            14 * 60,
            now + book_week.timedelta(hours=2),
            1,
            120,
        )
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.get_peak_used_for_day.return_value = 0

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["Early", "Afternoon"],
            ALLOW_FRAGMENTED_SESSIONS=True,
            SAME_ROOM_GAP_MINUTES=60,
        ):
            day_plan = book_week.build_display_day_plan(
                target_date,
                [current, future],
                tracker,
                book_week.DailyPlanningPreferences(),
                now=now,
                target_minutes=120,
            )

        self.assertEqual(day_plan.primary.room, "Early")
        self.assertEqual(day_plan.primary.state, "ready")
        self.assertIn("Only 1 better later room", day_plan.reason)

    def test_display_keeps_best_full_day_portfolio_over_best_single_session(self):
        target_date = date(2026, 9, 4)
        now = datetime(2026, 8, 30, 17, 0)

        def opportunity(room, start, end, unlock, priority):
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=unlock,
                room_priority=priority,
                initial_minutes=30,
                potential_minutes=end - start,
                preferred_minutes=0,
                soft_preferred_minutes=0,
                peak_minutes=0,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=start,
                source_gap_end_minutes=end,
            )

        best = opportunity(
            "Best",
            16 * 60 + 30,
            18 * 60 + 30,
            now,
            0,
        )
        earlier = opportunity(
            "Earlier",
            16 * 60,
            17 * 60 + 30,
            now - book_week.timedelta(minutes=30),
            1,
        )
        later = opportunity(
            "Later",
            17 * 60 + 30,
            19 * 60,
            now + book_week.timedelta(hours=1),
            2,
        )
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.get_peak_used_for_day.return_value = 0

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["Best", "Earlier", "Later"],
            ALLOW_FRAGMENTED_SESSIONS=True,
            SAME_ROOM_GAP_MINUTES=60,
        ):
            day_plan = book_week.build_display_day_plan(
                target_date,
                [best, earlier, later],
                tracker,
                book_week.DailyPlanningPreferences(),
                now=now,
                target_minutes=180,
            )

        selected = (day_plan.primary, *day_plan.additional)
        self.assertEqual(
            [(item.room, item.start_time, item.end_time) for item in selected],
            [
                ("Earlier", "16:00", "17:30"),
                ("Later", "17:30", "19:00"),
            ],
        )
        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(day_plan.primary.state, "ready")
        self.assertEqual(day_plan.additional[0].state, "waiting")
        self.assertNotIn("Best", {item.room for item in selected})

    def test_future_only_portfolio_waits_only_with_existing_confidence(self):
        target_date = date(2026, 9, 4)
        now = datetime(2026, 8, 30, 9, 30)

        def opportunity(room, start, end, unlock, priority, preferred):
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=unlock,
                room_priority=priority,
                initial_minutes=30,
                potential_minutes=end - start,
                preferred_minutes=preferred,
                soft_preferred_minutes=preferred,
                peak_minutes=end - start,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=start,
                source_gap_end_minutes=end,
            )

        current = opportunity("Early", 9 * 60, 11 * 60, now, 0, 0)
        future_a = opportunity(
            "Afternoon A",
            12 * 60,
            14 * 60,
            now + book_week.timedelta(hours=2),
            1,
            120,
        )
        future_b = opportunity(
            "Afternoon B",
            12 * 60,
            14 * 60,
            now + book_week.timedelta(hours=2),
            2,
            120,
        )
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.get_peak_used_for_day.return_value = 0

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["Early", "Afternoon A", "Afternoon B"],
            ALLOW_FRAGMENTED_SESSIONS=True,
            SAME_ROOM_GAP_MINUTES=60,
        ):
            day_plan = book_week.build_display_day_plan(
                target_date,
                [current, future_a, future_b],
                tracker,
                book_week.DailyPlanningPreferences(),
                now=now,
                target_minutes=120,
            )

        self.assertEqual(day_plan.primary.room, "Afternoon A")
        self.assertEqual(day_plan.primary.state, "waiting")
        self.assertEqual(day_plan.status, "waiting")
        self.assertIn("2 better visible later", day_plan.reason)
        self.assertEqual(
            book_week._runtime_ordered_day_opportunities(
                [current],
                day_plan,
                book_week.DailyPlanningPreferences(),
                now=now,
            ),
            [],
        )

    def test_current_fallback_is_trimmed_to_preserve_complete_day_target(self):
        target_date = date(2026, 9, 4)
        now = datetime(2026, 8, 30, 17, 0)

        def opportunity(room, start, end, unlock, priority, *, initial=30):
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=unlock,
                room_priority=priority,
                initial_minutes=initial,
                potential_minutes=end - start,
                preferred_minutes=0,
                soft_preferred_minutes=0,
                peak_minutes=0,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=start,
                source_gap_end_minutes=end,
            )

        current = opportunity("Current", 17 * 60, 19 * 60, now, 10)
        future_early = opportunity(
            "Future Early",
            16 * 60,
            18 * 60,
            now + book_week.timedelta(minutes=30),
            0,
            initial=120,
        )
        future_late = opportunity(
            "Future Late",
            18 * 60,
            20 * 60,
            now + book_week.timedelta(minutes=45),
            1,
        )
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.get_peak_used_for_day.return_value = 0

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["Future Early", "Future Late", "Current"],
            ALLOW_FRAGMENTED_SESSIONS=True,
            SAME_ROOM_GAP_MINUTES=60,
        ):
            day_plan = book_week.build_display_day_plan(
                target_date,
                [current, future_early, future_late],
                tracker,
                book_week.DailyPlanningPreferences(priority_mode="room_first"),
                now=now,
                target_minutes=180,
            )

        selected = (day_plan.primary, *day_plan.additional)
        self.assertEqual(
            [(item.room, item.start_time, item.end_time) for item in selected],
            [
                ("Current", "17:00", "18:00"),
                ("Future Late", "18:00", "20:00"),
            ],
        )
        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(day_plan.primary.state, "ready")
        self.assertIn("fallback", day_plan.reason.casefold())

    def test_runtime_orders_selected_current_first_and_keeps_trimmed_end(self):
        target_date = date(2026, 9, 4)
        now = datetime(2026, 8, 30, 17, 0)

        def opportunity(room, start, end, priority, preferred):
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=start,
                end_minutes=end,
                unlock_at=now,
                room_priority=priority,
                initial_minutes=30,
                potential_minutes=end - start,
                preferred_minutes=preferred,
                soft_preferred_minutes=preferred,
                peak_minutes=book_week.interval_overlap_minutes(
                    start,
                    end,
                    9 * 60,
                    16 * 60,
                ),
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=start,
                source_gap_end_minutes=end,
            )

        individually_best = opportunity(
            "Best",
            12 * 60,
            14 * 60,
            0,
            120,
        )
        selected_source = opportunity(
            "Earlier",
            16 * 60,
            18 * 60,
            1,
            0,
        )
        planned_primary = book_week.PlanCandidate(
            room="Earlier",
            date=target_date.isoformat(),
            start_time="16:00",
            end_time="17:30",
            unlock_at=now.astimezone(),
            initial_minutes=30,
            potential_minutes=90,
            confirmed_minutes=0,
            state="ready",
            reason="Selected by the full-day plan",
        )
        day_plan = book_week.DayPlan(
            date=target_date.isoformat(),
            target_minutes=180,
            existing_minutes=0,
            peak_used_minutes=0,
            peak_limit_minutes=120,
            status="planned",
            primary=planned_primary,
            additional=(),
            backups=(),
            held_peak_minutes=0,
            reason="Selected by the full-day plan",
        )

        ordered = book_week._runtime_ordered_day_opportunities(
            [individually_best, selected_source],
            day_plan,
            book_week.DailyPlanningPreferences(),
            now=now,
        )

        self.assertEqual([item.room for item in ordered], ["Earlier"])
        self.assertEqual(ordered[0].potential_minutes, 90)
        self.assertEqual(ordered[0].end_minutes, 17 * 60 + 30)
        slot = book_week._opportunity_to_normal_slot(ordered[0])
        self.assertEqual(slot["duration"], 1.5)
        self.assertEqual(slot["end_hour"], 17.5)
        self.assertEqual(slot["planned_target_end_hour"], 17.5)


class StrictTimeWindowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.strict = {
            "enabled": True,
            "start_hour": 14.0,
            "end_hour": 22.0,
            "strict_mode": True,
        }

    def test_full_interval_must_fit_including_exact_boundaries(self):
        self.assertTrue(interval_is_strictly_preferred(14.0, 22.0, self.strict))
        self.assertTrue(interval_is_strictly_preferred(14.0, 14.5, self.strict))
        self.assertFalse(interval_is_strictly_preferred(13.75, 14.5, self.strict))
        self.assertFalse(interval_is_strictly_preferred(21.75, 22.25, self.strict))

    def test_trim_keeps_exactly_30_minutes_and_rejects_15_minutes(self):
        self.assertEqual(
            trim_to_strict_time_window(13.5, 14.5, self.strict),
            (14.0, 14.5),
        )
        self.assertEqual(
            trim_to_strict_time_window(21.5, 22.5, self.strict),
            (21.5, 22.0),
        )
        self.assertEqual(
            trim_to_strict_time_window(13.5, 14.25, self.strict),
            (None, None),
        )
        self.assertEqual(
            trim_to_strict_time_window(21.75, 22.25, self.strict),
            (None, None),
        )

    def test_non_strict_preferences_do_not_trim_or_reject(self):
        preferred = dict(self.strict, strict_mode=False)
        self.assertEqual(
            trim_to_strict_time_window(8.0, 23.0, preferred),
            (8.0, 23.0),
        )
        self.assertTrue(interval_is_strictly_preferred(8.0, 23.0, preferred))

    def test_custom_preference_clock_values_are_parsed_exactly(self):
        prefs = load_time_preferences(
            {
                "time_preferences": {
                    "enabled": True,
                    "preset": "custom",
                    "custom_start_hour": 14,
                    "custom_start_min": 30,
                    "custom_end_hour": 21,
                    "custom_end_min": 45,
                    "strict_mode": True,
                }
            }
        )
        self.assertEqual(prefs["start_hour"], 14.5)
        self.assertEqual(prefs["end_hour"], 21.75)
        self.assertTrue(prefs["strict_mode"])


class FailClosedSettingsBoundaryTests(unittest.TestCase):
    def test_invalid_utf8_and_non_object_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_bytes(b"{\"value\": \xff}")
            with self.assertRaises(SettingsError):
                load_settings(path)

            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)

    def test_booking_setting_sections_reject_wrong_shapes(self):
        invalid_calls = (
            lambda: load_disabled_dates({"disabled_dates": "2026-09-01"}),
            lambda: load_disabled_dates({"disabled_dates": ["2026-09-01", 2]}),
            lambda: load_time_preferences({"time_preferences": []}),
            lambda: load_booking_strategy({"booking_strategy": []}),
            lambda: load_extendable_bookings({"extendable_bookings": {}}),
            lambda: load_extendable_bookings({"extendable_bookings": [{}]}),
        )
        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call):
                with self.assertRaises(SettingsError):
                    invalid_call()

    def test_invalid_custom_time_window_is_rejected(self):
        invalid_preferences = (
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": 24,
                "custom_end_hour": 22,
            },
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": 18,
                "custom_end_hour": 14,
            },
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": "late",
                "custom_end_hour": 22,
            },
        )
        for prefs in invalid_preferences:
            with self.subTest(prefs=prefs):
                with self.assertRaises(SettingsError):
                    load_time_preferences({"time_preferences": prefs})

    def test_time_preferences_reject_malformed_dormant_and_coerced_values(self):
        invalid_preferences = (
            {"enabled": False, "preset": "surprise"},
            {"enabled": False, "custom_start_hour": "14"},
            {"enabled": False, "custom_start_min": True},
            {"enabled": "false"},
            {"strict_mode": "true"},
            {"preset": 1},
        )
        for prefs in invalid_preferences:
            with self.subTest(prefs=prefs):
                with self.assertRaises(SettingsError):
                    load_time_preferences({"time_preferences": prefs})


class ExtensionPracticeTargetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.booking = {
            "room": "B0.29",
            "date": "2026-09-01",
            "startTime": "17:00",
            "endTime": "17:30",
            "target_end": "19:00",
            "created_at": "2026-08-27T17:30:00",
            "horizon_days": 5,
        }

    def test_met_daily_target_clears_extension_before_any_remote_work(self):
        with (
            mock.patch.object(book_week, "remove_extendable_booking") as remove,
            mock.patch.object(
                book_week,
                "calculate_max_extension",
                side_effect=AssertionError("must not calculate or edit"),
            ),
        ):
            result = book_week.try_extend_booking(
                object(), self.booking, remaining_daily_hours=0.0
            )

        self.assertEqual(
            result,
            (False, None, "Daily practice target is met; extension state cleared"),
        )
        remove.assert_called_once_with("B0.29", "2026-09-01", "17:00")

    def test_met_daily_target_reports_failure_to_clear_state(self):
        with mock.patch.object(
            book_week,
            "remove_extendable_booking",
            side_effect=SettingsError("locked"),
        ):
            success, new_end, message = book_week.try_extend_booking(
                object(), self.booking, remaining_daily_hours=0.0
            )

        self.assertFalse(success)
        self.assertIsNone(new_end)
        self.assertIn("could not be cleared", message)


class DailyTargetBoundaryTests(unittest.TestCase):
    class TrackerStub:
        def __init__(self, hours_by_day=None, weekly_remaining=28.0):
            self.hours_by_day = hours_by_day or {}
            self.weekly_remaining = weekly_remaining

        def get_hours_for_day(self, target_date):
            return self.hours_by_day.get(target_date.strftime("%Y-%m-%d"), 0.0)

        def get_remaining_quota_hours(self):
            return self.weekly_remaining

    def test_override_default_existing_hours_and_weekly_quota_all_cap_target(self):
        override_day = date(2026, 9, 1)
        default_day = date(2026, 9, 2)
        plan = PracticePlan(
            enabled=True,
            default_hours=2.5,
            date_overrides={override_day.isoformat(): 0.5},
        )

        tracker = self.TrackerStub()
        self.assertEqual(
            calculate_target_hours_for_day(
                override_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.5, 1),
        )

        tracker = self.TrackerStub({default_day.isoformat(): 0.75})
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (1.75, 4),
        )

        tracker = self.TrackerStub(weekly_remaining=0.5)
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.5, 1),
        )

        tracker = self.TrackerStub({default_day.isoformat(): 2.5})
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.0, 0),
        )

    def test_fractional_existing_time_rounds_remaining_down_to_quarter_hour(self):
        plan = PracticePlan(enabled=True, default_hours=2.5, date_overrides={})
        self.assertEqual(remaining_target_hours(plan, date(2026, 9, 2), 0.6), 1.75)


class ExactConfirmationBoundaryTests(unittest.TestCase):
    def test_encoded_or_decorated_event_ids_are_not_confirmation(self):
        rejected = (
            "/arrangement?eventId=%31",
            "/arrangement?event%49d=1",
            "/arrangement?eventId=1%20",
            "/arrangement?eventId=1;other=2",
            "/arrangement//?eventId=1",
            "/ARRANGEMENT?eventId=1",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(parse_confirmed_event_id(url))

    def test_time_values_with_seconds_or_whitespace_cannot_confirm(self):
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30:00", "11:00")
        )
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30", "11:00 ")
        )
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30", "10:59")
        )

    def test_unicode_digits_are_not_canonical_confirmation_values(self):
        self.assertIsNone(parse_confirmed_event_id("/arrangement?eventId=1²"))
        self.assertIsNone(parse_confirmed_event_id("/arrangement?eventId=1２"))
        self.assertFalse(hhmm_values_match("0９:30", "0９:30"))


class LoginOriginBoundaryTests(unittest.TestCase):
    class Page:
        def __init__(self, url, closed=False):
            self.url = url
            self._closed = closed

        def is_closed(self):
            return self._closed

    def test_only_exact_secure_rwcmd_asimut_origin_is_accepted(self):
        self.assertTrue(
            page_is_asimut_origin(self.Page("https://rwcmd.asimut.net/overview"))
        )
        for url in (
            "http://rwcmd.asimut.net/overview",
            "https://rwcmd.asimut.net.evil.example/overview",
            "https://evil.example/?next=rwcmd.asimut.net",
            "https://asimut.net/overview",
        ):
            with self.subTest(url=url):
                self.assertFalse(page_is_asimut_origin(self.Page(url)))
        self.assertFalse(
            page_is_asimut_origin(
                self.Page("https://rwcmd.asimut.net/overview", closed=True)
            )
        )


class EntryPointSafetyBoundaryTests(unittest.TestCase):
    def test_missing_session_starts_with_no_storage_state_for_secure_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_state = Path(temp_dir) / "missing-state.json"
            with mock.patch.object(book_week, "state_file", missing_state):
                options = book_week.runtime_context_options()

            self.assertEqual(
                options,
                {"viewport": {"width": 1920, "height": 1080}},
            )
            self.assertNotIn("storage_state", options)


if __name__ == "__main__":
    unittest.main()
