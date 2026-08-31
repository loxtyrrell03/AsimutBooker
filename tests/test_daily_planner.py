import unittest
from datetime import date, datetime, timedelta
from time import perf_counter

from booking_strategy import DailyPlanningPreferences
from daily_planner import (
    choose_horizon_opportunity,
    enumerate_gap_opportunities,
    opportunity_rank,
    select_day_plan,
)


class DailyPlannerTests(unittest.TestCase):
    target_date = date(2026, 9, 4)  # Friday
    horizon = 5 * 24 * 60

    def prefs(self, **changes):
        values = DailyPlanningPreferences().__dict__ | changes
        return DailyPlanningPreferences(**values)

    def opportunities(self, room, gap_start, gap_end, now, *, priority=0, **prefs):
        return enumerate_gap_opportunities(
            room=room,
            target_date=self.target_date,
            gap_start_hour=gap_start,
            gap_end_hour=gap_end,
            horizon_minutes=self.horizon,
            room_priority=priority,
            planning=self.prefs(**prefs),
            now=now,
            minimum_block_minutes=30,
            remaining_daily_minutes=240,
            remaining_weekly_minutes=600,
            remaining_peak_minutes=120,
        )

    def test_early_edge_waits_for_two_visible_full_afternoon_rooms(self):
        boundary = datetime(2026, 8, 30, 9, 30)
        planning = self.prefs()
        room_a = self.opportunities("B0.29", 9, 16, boundary, priority=0)
        room_b = self.opportunities("B1.09", 12, 16, boundary, priority=1)
        current = [item for item in room_a if item.start_minutes == 9 * 60]
        future = [
            item
            for item in room_a + room_b
            if item.start_minutes >= 12 * 60 and item.unlock_at > boundary
        ]

        decision = choose_horizon_opportunity(
            current,
            future,
            planning,
            now=boundary,
        )

        self.assertEqual(decision.action, "wait")
        self.assertEqual(decision.selected.start_text, "12:00")
        self.assertEqual(decision.selected.end_text, "14:00")
        self.assertEqual(decision.held_peak_minutes, 120)
        self.assertIn("Skipped 09:00", decision.reason)
        self.assertIn("2 better visible", decision.reason)

    def test_before_peak_slot_can_wait_when_it_would_consume_the_day_target(self):
        boundary = datetime(2026, 8, 30, 8, 30)
        planning = self.prefs()
        early = self.opportunities("B0.29", 8, 10, boundary)
        afternoon_a = self.opportunities("B0.29", 12, 14, boundary)
        afternoon_b = self.opportunities("B1.09", 12, 14, boundary, priority=1)
        current = [item for item in early if item.start_minutes == 8 * 60]

        decision = choose_horizon_opportunity(
            current,
            afternoon_a + afternoon_b,
            planning,
            now=boundary,
        )

        self.assertEqual(decision.action, "wait")
        self.assertEqual(decision.selected.start_text, "12:00")

    def test_one_later_room_is_not_enough_when_two_are_required(self):
        boundary = datetime(2026, 8, 30, 9, 30)
        planning = self.prefs(minimum_later_options=2)
        all_items = self.opportunities("B0.29", 9, 16, boundary)
        current = [item for item in all_items if item.start_minutes == 9 * 60]
        future = [item for item in all_items if item.start_minutes >= 12 * 60]

        decision = choose_horizon_opportunity(current, future, planning, now=boundary)

        self.assertEqual(decision.action, "book_now")
        self.assertIn("1 better later room option", decision.reason)

    def test_short_later_gap_does_not_beat_full_early_session(self):
        boundary = datetime(2026, 8, 30, 9, 30)
        planning = self.prefs(minimum_later_options=1)
        current_items = self.opportunities("B0.29", 9, 11, boundary)
        later_items = self.opportunities("B1.09", 12, 12.5, boundary, priority=1)
        current = [item for item in current_items if item.start_minutes == 9 * 60]

        decision = choose_horizon_opportunity(
            current,
            later_items,
            planning,
            now=boundary,
        )

        self.assertEqual(decision.action, "book_now")

    def test_fallback_at_fourteen_hundred_stops_waiting(self):
        boundary = datetime(2026, 8, 30, 14, 30)
        planning = self.prefs(minimum_later_options=1, fallback_lead_minutes=120)
        current_items = self.opportunities("B0.29", 14, 16, boundary)
        later_items = self.opportunities("B1.09", 15, 17, boundary, priority=1)
        current = [item for item in current_items if item.start_minutes == 14 * 60]

        decision = choose_horizon_opportunity(
            current,
            later_items,
            planning,
            now=boundary,
        )

        self.assertEqual(decision.action, "book_now")
        self.assertIn("fallback", decision.reason)

    def test_after_peak_longest_first_prefers_two_hours(self):
        now = datetime(2026, 8, 30, 15, 0)
        planning = self.prefs(after_peak_mode="longest_first")
        short = self.opportunities("B0.29", 16, 17, now)[0]
        long = self.opportunities("B1.09", 17, 20, now, priority=1)[0]

        ranked = sorted((short, long), key=lambda item: opportunity_rank(item, planning, now=now))

        self.assertEqual(ranked[0].room, "B1.09")
        self.assertEqual(ranked[0].potential_minutes, 120)

    def test_full_off_peak_session_beats_unpreferred_peak_session(self):
        now = datetime(2026, 8, 30, 9, 30)
        planning = self.prefs()
        early_peak = next(
            item
            for item in self.opportunities("B0.29", 9, 11, now)
            if item.start_minutes == 9 * 60
        )
        after_peak = next(
            item
            for item in self.opportunities("B1.09", 16, 18, now, priority=1)
            if item.start_minutes == 16 * 60
        )

        ranked = sorted(
            (early_peak, after_peak),
            key=lambda item: opportunity_rank(item, planning, now=now),
        )

        self.assertEqual(ranked[0], after_peak)

    def test_full_preferred_peak_session_still_beats_off_peak(self):
        now = datetime(2026, 8, 30, 11, 30)
        planning = self.prefs()
        preferred = next(
            item
            for item in self.opportunities("B0.29", 12, 14, now)
            if item.start_minutes == 12 * 60
        )
        after_peak = next(
            item
            for item in self.opportunities("B1.09", 16, 18, now, priority=1)
            if item.start_minutes == 16 * 60
        )

        ranked = sorted(
            (preferred, after_peak),
            key=lambda item: opportunity_rank(item, planning, now=now),
        )

        self.assertEqual(ranked[0], preferred)

    def test_soft_time_preference_orders_equal_after_peak_sessions(self):
        now = datetime(2026, 8, 30, 12, 0)
        planning = self.prefs(after_peak_mode="longest_first")
        early = enumerate_gap_opportunities(
            room="B0.29",
            target_date=self.target_date,
            gap_start_hour=16,
            gap_end_hour=18,
            horizon_minutes=self.horizon,
            room_priority=0,
            planning=planning,
            now=now,
            minimum_block_minutes=30,
            soft_preferred_window=(18 * 60, 22 * 60),
        )[0]
        preferred = enumerate_gap_opportunities(
            room="B1.09",
            target_date=self.target_date,
            gap_start_hour=18,
            gap_end_hour=20,
            horizon_minutes=self.horizon,
            room_priority=1,
            planning=planning,
            now=now,
            minimum_block_minutes=30,
            soft_preferred_window=(18 * 60, 22 * 60),
        )[0]

        ranked = sorted(
            (early, preferred),
            key=lambda item: opportunity_rank(item, planning, now=now),
        )

        self.assertEqual(ranked[0], preferred)

    def test_strict_window_and_peak_budget_are_hard_caps(self):
        now = datetime(2026, 8, 30, 10, 0)
        items = enumerate_gap_opportunities(
            room="B0.29",
            target_date=self.target_date,
            gap_start_hour=8,
            gap_end_hour=18,
            horizon_minutes=self.horizon,
            room_priority=0,
            planning=self.prefs(),
            now=now,
            minimum_block_minutes=30,
            remaining_peak_minutes=60,
            strict_window=(10 * 60, 21 * 60),
        )

        self.assertTrue(items)
        self.assertTrue(all(item.start_minutes >= 10 * 60 for item in items))
        self.assertTrue(all(item.peak_minutes <= 60 for item in items))

    def test_non_quarter_site_gap_is_normalized_inward_to_booking_grid(self):
        now = datetime(2026, 8, 30, 10, 0)
        items = enumerate_gap_opportunities(
            room="B0.29",
            target_date=self.target_date,
            gap_start_hour=(10 * 60 + 20) / 60,
            gap_end_hour=(12 * 60 + 10) / 60,
            horizon_minutes=self.horizon,
            room_priority=0,
            planning=self.prefs(),
            now=now,
            minimum_block_minutes=30,
        )

        self.assertTrue(items)
        self.assertEqual(items[0].start_text, "10:30")
        self.assertEqual(items[0].end_text, "12:00")
        self.assertTrue(all(item.start_minutes >= 10 * 60 + 30 for item in items))
        self.assertTrue(all(item.end_minutes <= 12 * 60 for item in items))
        self.assertTrue(
            all(item.source_gap_start_minutes == 10 * 60 + 30 for item in items)
        )
        self.assertTrue(
            all(item.source_gap_end_minutes == 12 * 60 for item in items)
        )

    def test_inward_normalized_gap_shorter_than_minimum_is_skipped(self):
        now = datetime(2026, 8, 30, 10, 0)
        items = enumerate_gap_opportunities(
            room="B0.29",
            target_date=self.target_date,
            gap_start_hour=(10 * 60 + 20) / 60,
            gap_end_hour=(10 * 60 + 49) / 60,
            horizon_minutes=self.horizon,
            room_priority=0,
            planning=self.prefs(),
            now=now,
            minimum_block_minutes=30,
        )

        self.assertEqual(items, [])

    def test_gap_boundaries_still_require_finite_whole_minutes(self):
        now = datetime(2026, 8, 30, 10, 0)
        common = {
            "room": "B0.29",
            "target_date": self.target_date,
            "gap_end_hour": 12,
            "horizon_minutes": self.horizon,
            "room_priority": 0,
            "planning": self.prefs(),
            "now": now,
            "minimum_block_minutes": 30,
        }
        with self.assertRaisesRegex(ValueError, "finite"):
            enumerate_gap_opportunities(gap_start_hour=float("nan"), **common)
        with self.assertRaisesRegex(ValueError, "whole minutes"):
            enumerate_gap_opportunities(
                gap_start_hour=(10 * 60 + 20.5) / 60,
                **common,
            )

    def test_day_plan_respects_target_and_fragmentation(self):
        now = datetime(2026, 8, 30, 8, 0)
        planning = self.prefs()
        items = self.opportunities("B0.29", 12, 16, now)
        one = select_day_plan(
            items,
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=False,
        )
        many = select_day_plan(
            items,
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
        )

        self.assertEqual(len(one), 1)
        self.assertLessEqual(sum(item.potential_minutes for item in many), 180)
        for first, second in zip(many, many[1:]):
            self.assertTrue(
                first.end_minutes <= second.start_minutes
                or second.end_minutes <= first.start_minutes
            )

    def test_day_plan_fulfils_three_hours_with_peak_and_off_peak_sessions(self):
        now = datetime(2026, 8, 30, 8, 0)
        planning = self.prefs()
        preferred_peak = self.opportunities("B0.29", 12, 14, now)[0]
        off_peak = self.opportunities("B1.09", 16, 17, now, priority=1)[0]

        selected = select_day_plan(
            [preferred_peak, off_peak],
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=60,
        )

        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(sum(item.peak_minutes for item in selected), 120)
        self.assertEqual(
            {(item.start_text, item.end_text) for item in selected},
            {("12:00", "14:00"), ("16:00", "17:00")},
        )

    def test_day_portfolio_avoids_best_single_slot_when_two_fill_target(self):
        now = datetime(2026, 8, 30, 8, 0)
        planning = self.prefs()
        individually_best = self.opportunities("B0.29", 16.5, 18.5, now)[0]
        earlier = self.opportunities("B1.09", 16, 17.5, now, priority=1)[0]
        later = self.opportunities("B1.10", 17.5, 19, now, priority=2)[0]

        selected = select_day_plan(
            [individually_best, earlier, later],
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=60,
        )

        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual({item.room for item in selected}, {"B1.09", "B1.10"})

    def test_required_current_source_can_trim_to_complete_future_portfolio(self):
        now = datetime(2026, 8, 30, 17, 0)
        planning = self.prefs()
        current = self.opportunities("Current", 17, 19, now)[0]
        future = self.opportunities("Future", 18, 20, now, priority=1)[0]

        selected = select_day_plan(
            [current, future],
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=60,
            required_opportunity=current,
        )

        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(
            {
                (item.room, item.start_text, item.end_text)
                for item in selected
            },
            {
                ("Current", "17:00", "18:00"),
                ("Future", "18:00", "20:00"),
            },
        )

    def test_day_portfolio_finds_three_session_target_beyond_one_seed_greedy(self):
        now = datetime(2026, 8, 30, 17, 0)
        planning = self.prefs()

        def fixed(room, start, end, priority):
            return self.opportunities(
                room,
                start / 60,
                end / 60,
                now,
                priority=priority,
            )[0]

        # A one-seed greedy completion used to choose R5 for 60 minutes and R0
        # for 105, then stop at 165.  Reaching 180 requires two non-greedy
        # truncations before the final 105-minute block.
        candidates = [
            fixed("R1", 1125, 1230, 1),
            fixed("R0", 1140, 1245, 2),
            fixed("R6", 1155, 1260, 3),
            fixed("R7", 1095, 1185, 5),
            fixed("R4", 1170, 1260, 0),
            fixed("R3", 1170, 1245, 4),
            fixed("R5", 1080, 1140, 6),
            fixed("R2", 1170, 1200, 7),
        ]

        selected = select_day_plan(
            candidates,
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=0,
        )

        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {
                (item.room, item.start_text, item.end_text)
                for item in selected
            },
            {
                ("R5", "18:00", "18:45"),
                ("R1", "18:45", "19:15"),
                ("R6", "19:15", "21:00"),
            },
        )

    def test_large_day_portfolio_search_stays_bounded(self):
        now = datetime(2026, 8, 30, 8, 0)
        planning = self.prefs()
        candidates = []
        for room_index in range(22):
            for start_index in range(50):
                start = 8 * 60 + start_index * 15
                end = min(start + 120, 22 * 60)
                candidates.append(
                    self.opportunities(
                        f"R{room_index:02d}",
                        start / 60,
                        end / 60,
                        now,
                        priority=room_index,
                    )[0]
                )
        self.assertEqual(len(candidates), 1100)

        started = perf_counter()
        selected = select_day_plan(
            candidates,
            planning,
            now=now,
            target_minutes=180,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=60,
        )
        elapsed = perf_counter() - started

        self.assertEqual(sum(item.potential_minutes for item in selected), 180)
        self.assertEqual(len(selected), 2)
        self.assertLess(elapsed, 2.0)

    def test_day_plan_respects_aggregate_peak_and_same_room_gap(self):
        now = datetime(2026, 8, 30, 8, 0)
        planning = self.prefs(desired_peak_block_minutes=60)
        first_room = self.opportunities("B0.29", 12, 16, now)
        second_room = self.opportunities("B1.09", 12, 16, now, priority=1)

        selected = select_day_plan(
            first_room + second_room,
            planning,
            now=now,
            target_minutes=240,
            allow_fragmented_sessions=True,
            remaining_peak_minutes=120,
            same_room_gap_minutes=60,
        )

        self.assertLessEqual(sum(item.peak_minutes for item in selected), 120)
        same_room = [item for item in selected if item.room == "B0.29"]
        for left, right in zip(same_room, same_room[1:]):
            gap = max(
                right.start_minutes - left.end_minutes,
                left.start_minutes - right.end_minutes,
            )
            self.assertGreaterEqual(gap, 60)

    def test_future_unlock_uses_fresh_arbitrary_horizon(self):
        now = datetime(2026, 8, 30, 8, 0)
        arbitrary_horizon = self.horizon + 15
        item = enumerate_gap_opportunities(
            room="B0.29",
            target_date=self.target_date,
            gap_start_hour=12,
            gap_end_hour=14,
            horizon_minutes=arbitrary_horizon,
            room_priority=0,
            planning=self.prefs(),
            now=now,
            minimum_block_minutes=45,
        )[0]
        expected = datetime.combine(self.target_date, datetime.min.time()).replace(
            hour=12
        ) - timedelta(minutes=arbitrary_horizon - 45)

        self.assertEqual(item.unlock_at, expected)
        self.assertEqual(item.initial_minutes, 45)


if __name__ == "__main__":
    unittest.main()
