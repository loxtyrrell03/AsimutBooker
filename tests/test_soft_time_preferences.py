import contextlib
import io
import unittest
from datetime import date, datetime
from unittest import mock

import book_week as b
from daily_planner import (
    enumerate_gap_opportunities, opportunity_rank, select_day_plan,
    soft_time_is_worth_booking, soft_time_weight,
)


class SoftTimePreferenceTests(unittest.TestCase):
    window = (720, 1080)
    day = date(2026, 9, 15)
    now = datetime(2026, 9, 8, 13, 15)
    prefs = {"enabled": True, "strict_mode": False, "start_hour": 12, "end_hour": 18}

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(mock.patch.multiple(
            b, PRIORITY_ROOMS=["Weston Gallery", "B0.11"],
            ROOM_HORIZON_MINUTES={"Weston Gallery": 7 * 1440, "B0.11": 7 * 1440},
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2,
            ALLOW_FRAGMENTED_SESSIONS=True,
        ))

    def opportunities(self, start, end, **changes):
        args = dict(
            room="Weston Gallery", target_date=self.day, gap_start_hour=start,
            gap_end_hour=end, horizon_minutes=7 * 1440, room_priority=0,
            planning=b.DailyPlanningPreferences(), now=self.now,
            minimum_block_minutes=30, soft_preferred_window=self.window,
        )
        args.update(changes)
        return enumerate_gap_opportunities(**args)

    def test_distance_penalty_is_steep_symmetric_and_repeatable(self):
        for offset, expected in [(0, 1), (60, .5), (120, 1 / 16), (240, 1 / 65536)]:
            for _ in range(100):
                self.assertEqual(soft_time_weight(720 - offset, self.window), expected)
                self.assertEqual(soft_time_weight(1080 + offset, self.window), expected)

    def test_nearby_fallback_allowed_but_remote_only_slot_left_unfilled(self):
        self.assertTrue(soft_time_is_worth_booking(660, 30, self.window))
        self.assertTrue(soft_time_is_worth_booking(630, 120, self.window))
        self.assertFalse(soft_time_is_worth_booking(630, 30, self.window))
        self.assertEqual(self.opportunities(8, 10), [])
        self.assertEqual(self.opportunities(20, 22), [])
        self.assertTrue(self.opportunities(11, 11.5))

    def test_better_time_beats_top_room_and_before_peak_advantage(self):
        near = self.opportunities(11, 13)[0]
        preferred = self.opportunities(12, 14, room="B0.11", room_priority=20)[0]
        planning = b.DailyPlanningPreferences(priority_mode="room_first")
        self.assertLess(opportunity_rank(preferred, planning, now=self.now),
                        opportunity_rank(near, planning, now=self.now))

    def test_horizon_order_across_dates_keeps_time_penalty_ahead_of_room_priority(self):
        early = {"bookable_from": self.now, "start_hour": 11, "duration": 120,
                 "room_priority": 0, "days_ahead": 7}
        preferred = early | {"start_hour": 12, "room_priority": 8, "days_ahead": 6}
        self.assertLess(b.horizon_candidate_rank(preferred, self.prefs),
                        b.horizon_candidate_rank(early, self.prefs))
        disabled = self.prefs | {"enabled": False}
        self.assertLess(b.horizon_candidate_rank(early, disabled),
                        b.horizon_candidate_rank(preferred, disabled))

    def test_logged_day_cannot_add_eight_am_to_fill_target(self):
        tracker = b.BookingTracker()
        tracker.add_booking("Weston Gallery", self.day, 12.75, 13.25)
        tracker.add_conflict(self.day, 12.75, 13.25)
        grid = [{"room": room, "slots": [{"startHour": 8, "endHour": 18}]}
                for room in ["Weston Gallery", "B0.11"]]
        for enabled in [True, False]:
            planning = b.DailyPlanningPreferences(enabled=enabled)
            opportunities = b.build_day_booking_opportunities(
                grid, self.day, tracker, self.prefs, planning,
                now=self.now, remaining_daily_hours=2.5,
            )
            self.assertTrue(opportunities)
            self.assertFalse(any(item.start_minutes == 480 for item in opportunities))
            plan = select_day_plan(opportunities, planning, now=self.now,
                                   target_minutes=150, allow_fragmented_sessions=True,
                                   remaining_peak_minutes=90, same_room_gap_minutes=60)
            self.assertTrue(plan)
            self.assertTrue(all(item.start_minutes >= 630 for item in plan))
            self.assertTrue(b.uses_time_aware_planner(planning, self.prefs))

    def test_portfolio_truncation_cannot_turn_acceptable_long_fallback_into_bad_short_one(self):
        # At 10:30 a two-hour fallback is viable; a 30-minute fragment is not.
        opportunity = self.opportunities(10.5, 12.5)[0]
        self.assertEqual(opportunity.start_minutes, 630)
        plan = select_day_plan([opportunity], b.DailyPlanningPreferences(), now=self.now,
                               target_minutes=30, allow_fragmented_sessions=True)
        self.assertEqual(plan, ())

    def test_disabled_preferences_and_strict_window_keep_their_meaning(self):
        self.assertTrue(self.opportunities(8, 10, soft_preferred_window=None))
        self.assertFalse(self.opportunities(11, 11.5, strict_window=self.window))
        disabled = self.prefs | {"enabled": False}
        self.assertTrue(b.soft_time_allows_booking(8, 30, disabled))
        self.assertFalse(b.uses_time_aware_planner(b.DailyPlanningPreferences(enabled=False), disabled))
        for day in [date(2026, 9, 19), date(2026, 9, 20)]:
            self.assertFalse(self.opportunities(8, 10, target_date=day))

    def test_normal_save_rechecks_actual_duration_after_horizon_cap(self):
        # 10:30-12:30 is acceptable as a plan, but only 30 minutes are unlocked.
        now = datetime(2026, 9, 8, 11)
        class Clock(datetime):
            @classmethod
            def now(cls):
                return now
        with mock.patch.object(b, "datetime", Clock), mock.patch.object(b, "get_room_slot_coordinates") as click:
            result = b.try_book_slot(
                object(), {"room": "Weston Gallery", "start_hour": 10.5, "end_hour": 12.5},
                self.day, b.BookingTracker(), 7, time_prefs=self.prefs,
            )
        self.assertFalse(result)
        click.assert_not_called()

    def test_horizon_save_rejects_remote_time_before_browser_access(self):
        candidate = {"room": "Weston Gallery", "start_hour": 8,
                     "bookable_from": self.now, "horizon_minutes": 7 * 1440,
                     "booking_minutes": 30}
        self.assertFalse(b.try_horizon_snipe(object(), candidate, self.day,
                                           b.BookingTracker(), 7, time_prefs=self.prefs))

    def test_current_plan_uses_unlocked_duration_and_preserves_better_alternative(self):
        grid = [{"room": "Weston Gallery", "slots": [{"startHour": 10.5, "endHour": 12.5}]}]
        result = b.build_day_booking_opportunities(
            grid, self.day, b.BookingTracker(), self.prefs,
            b.DailyPlanningPreferences(), now=datetime(2026, 9, 8, 11),
        )
        self.assertFalse(any(item.start_minutes == 630 for item in result))
        self.assertTrue(any(item.start_minutes == 660 for item in result))

    def test_remote_existing_booking_is_not_extended_or_reserved_as_future_capacity(self):
        booking = {"room": "Weston Gallery", "date": self.day.isoformat(),
                   "startTime": "08:00", "endTime": "08:30", "target_end": "10:00",
                   "created_at": "2026-09-08T08:30:00"}
        result = b.try_extend_booking(object(), booking, time_prefs=self.prefs)
        self.assertFalse(result[0])
        with mock.patch.object(b, "booking_window_dates", return_value=[self.day]):
            targets, peaks, held = b.calculate_extension_capacity_holds(
                [booking], b.BookingTracker(), b.PracticePlan(), set(),
                time_prefs=self.prefs, now=self.now,
            )
        self.assertEqual((targets, peaks, held), ({}, {}, ()))

    def test_room_fallback_cannot_reintroduce_remote_time(self):
        grid = [{"room": "B0.11", "slots": [{"startHour": 8, "endHour": 18}]}]
        slot = {"room": "Weston Gallery", "start_hour": 8, "end_hour": 8.5}
        result = b.same_time_room_backups(
            grid, slot, self.day, b.BookingTracker(), self.prefs,
            b.DailyPlanningPreferences(), now=self.now, excluded_rooms={"Weston Gallery"},
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
