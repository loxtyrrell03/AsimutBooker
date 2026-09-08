import contextlib
import io
import unittest
from itertools import combinations
from random import Random
from datetime import date, datetime
from unittest import mock

import book_week as b
from daily_planner import enumerate_gap_opportunities, select_day_plan, soft_time_extension_end


class BookingSelectionEdgeTests(unittest.TestCase):
    now = datetime(2026, 9, 8, 13)
    day = date(2026, 9, 15)
    prefs = {"enabled": True, "strict_mode": False, "start_hour": 12, "end_hour": 18}

    def opportunities(self, room, start, end, *, day=None, priority=0, soft=True):
        return enumerate_gap_opportunities(
            room=room, target_date=day or self.day, gap_start_hour=start,
            gap_end_hour=end, horizon_minutes=7 * 1440, room_priority=priority,
            planning=b.DailyPlanningPreferences(), now=self.now,
            minimum_block_minutes=30,
            soft_preferred_window=(720, 1080) if soft else None,
        )

    def plan(self, items, **changes):
        args = dict(now=self.now, target_minutes=120, allow_fragmented_sessions=True,
                    remaining_peak_minutes=120, same_room_gap_minutes=60)
        args.update(changes)
        return select_day_plan(items, b.DailyPlanningPreferences(), **args)

    def test_short_preferred_session_beats_more_minutes_outside_window(self):
        early = self.opportunities("Weston Gallery", 10.5, 12.5)[0]
        noon = self.opportunities("Corus Recital Room", 12, 13, priority=1)[0]
        for fragmented in (True, False):
            with self.subTest(fragmented=fragmented):
                chosen = self.plan([early, noon], allow_fragmented_sessions=fragmented)
                self.assertEqual([(x.room, x.start_text, x.end_text) for x in chosen],
                                 [("Corus Recital Room", "12:00", "13:00")])

    def test_weekend_portfolio_has_no_weekday_peak_cost(self):
        for day in (date(2026, 9, 19), date(2026, 9, 20)):
            chosen = self.plan(self.opportunities("Corus Recital Room", 12, 14, day=day),
                               remaining_peak_minutes=0)
            self.assertEqual(sum(x.potential_minutes for x in chosen), 120)
            self.assertEqual(sum(x.peak_minutes for x in chosen), 0)

    def test_shortened_session_recalculates_actual_preferred_overlap(self):
        source = self.opportunities("Weston Gallery", 11, 13, soft=False)[0]
        chosen = self.plan([source], target_minutes=60)
        self.assertEqual(chosen[0].end_text, "12:00")
        self.assertEqual(chosen[0].preferred_minutes, 0)

    def test_start_inside_window_does_not_license_hours_after_end(self):
        self.assertFalse(b.soft_time_allows_booking(17.75, 240, self.prefs))
        self.assertTrue(b.soft_time_allows_booking(17.75, 45, self.prefs))

    def test_late_gap_keeps_short_useful_fallback_instead_of_long_bad_tail(self):
        chosen = self.plan(self.opportunities("Corus Recital Room", 18, 21))
        self.assertTrue(chosen)
        self.assertLessEqual(max(x.end_minutes for x in chosen), 19 * 60)

    def test_excluded_extension_cannot_block_other_rooms_before_filtering(self):
        removed = {"room": "Removed room", "date": self.day.isoformat(),
                   "startTime": "12:00", "endTime": "12:30", "target_end": "14:00"}
        with mock.patch.multiple(b, PRIORITY_ROOMS=["Corus Recital Room"],
                                 ROOM_HORIZON_MINUTES={"Corus Recital Room": 7 * 1440}), \
                mock.patch.object(b, "booking_window_dates", return_value=[self.day]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.calculate_extension_capacity_holds(
                [removed], b.BookingTracker(), b.PracticePlan(), set(),
                time_prefs=self.prefs, now=self.now), ({}, {}, ()))

    def test_late_extension_stops_at_best_end_and_can_keep_a_useful_partial_target(self):
        self.assertEqual(soft_time_extension_end(1080, 1110, 1200, (720, 1080)), 1125)
        self.assertIsNone(soft_time_extension_end(1080, 1125, 1200, (720, 1080)))
        self.assertEqual(soft_time_extension_end(1080, 1110, 1200, None), 1200)

    def test_mixed_dates_are_rejected_instead_of_competing_for_one_day_budget(self):
        items = self.opportunities("A", 12, 13) + self.opportunities(
            "B", 12, 13, day=date(2026, 9, 16))
        with self.assertRaisesRegex(ValueError, "mix dates"):
            self.plan(items)

    def test_normal_create_enforces_strict_window_before_browser_interaction(self):
        now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls):
                return now
        with mock.patch.object(b, "datetime", Clock), \
                mock.patch.multiple(b, ROOM_HORIZON_MINUTES={"A": 7 * 1440}, MINIMUM_BLOCK_MINUTES=30), \
                mock.patch.object(b, "get_room_slot_coordinates") as coordinates, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(b.try_book_slot(
                object(), {"room": "A", "start_hour": 11, "end_hour": 11.5},
                self.day, b.BookingTracker(), 7, time_prefs=self.prefs | {"strict_mode": True},
            ))
        coordinates.assert_not_called()

    def test_small_portfolios_match_exhaustive_feasible_preference_scores(self):
        rng = Random(508)
        for case in range(35):
            sources = []
            for index in range(3):
                start = rng.choice([10.5, 11, 11.5, 12, 12.5, 17.5, 18, 18.5])
                options = self.opportunities(str(index % 2), start,
                                             start + rng.choice([.5, .75, 1]), priority=index % 2)
                if options:
                    sources.append(options[0])
            if not sources:
                continue
            # Independent exhaustive reference over exact interval tuples.
            variants = set()
            for item in sources:
                for duration in range(30, item.potential_minutes + 1, 15):
                    start, end = item.start_minutes, item.start_minutes + duration
                    distance = max(720 - start, end - 1080, 0) / 60
                    value = round(duration * 2 ** -(distance ** 2), 8)
                    if value >= 15:
                        peak = max(0, min(end, 960) - max(start, 540))
                        variants.add((item.room, start, end, duration, peak, value))
            variants = sorted(variants)
            tradeoff = any(v[5] < v[3] for v in variants)
            overhead = 7.5 if tradeoff else 0
            target, peak_limit = 90, 60
            feasible = [()]
            for count in range(1, min(3, len(variants)) + 1):
                for plan in combinations(variants, count):
                    if sum(v[3] for v in plan) > target or sum(v[4] for v in plan) > peak_limit:
                        continue
                    if any(not (a[2] + (60 if a[0] == c[0] else 0) <= c[1]
                                or c[2] + (60 if a[0] == c[0] else 0) <= a[1])
                           for a, c in combinations(plan, 2)):
                        continue
                    feasible.append(plan)
            expected = max(sum(v[5] - overhead for v in plan) for plan in feasible)
            actual_plan = self.plan(sources, target_minutes=target, remaining_peak_minutes=peak_limit)
            actual = sum(round(x.potential_minutes * 2 ** -(max(
                720 - x.start_minutes, x.end_minutes - 1080, 0) / 60) ** 2, 8) - overhead
                         for x in actual_plan)
            with self.subTest(case=case):
                self.assertAlmostEqual(actual, expected, places=6)


if __name__ == "__main__":
    unittest.main()
