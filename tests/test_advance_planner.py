import unittest
from datetime import date, datetime, timedelta

from advance_planner import AdvanceDay, allocate_advance_week
from booking_strategy import DailyPlanningPreferences
from daily_planner import enumerate_gap_opportunities


class AdvancePlannerTests(unittest.TestCase):
    now = datetime(2026, 9, 20, 10)
    first_day = date(2026, 9, 21)
    planning = DailyPlanningPreferences()

    def day(self, offset, *, confirmed=0, quality=0, target=240, horizon=10080,
            room='Weston', peak=60, start=12, end=18, held=0):
        day = self.first_day + timedelta(days=offset)
        opportunities = enumerate_gap_opportunities(room=room, target_date=day,
            gap_start_hour=start, gap_end_hour=end, horizon_minutes=horizon,
            room_priority=0, planning=self.planning, now=self.now,
            minimum_block_minutes=30, remaining_daily_minutes=target-confirmed-held,
            remaining_peak_minutes=peak, soft_preferred_window=(720, 1080))
        return AdvanceDay(day, target, confirmed, quality, tuple(opportunities), peak,
                          held_target_minutes=held, held_quality_minutes=held)

    def allocate(self, days, budget=360, **kwargs):
        return allocate_advance_week(days, self.planning, now=self.now,
            budget_minutes=budget, minimum_block_minutes=30,
            allow_fragmented_sessions=True, same_room_gap_minutes=60, **kwargs)

    def test_six_hours_cover_all_seven_dates_before_enlarging_covered_days(self):
        result = self.allocate([self.day(i) for i in range(7)])
        self.assertEqual(sorted(item.minutes for item in result), [45,45,45,45,60,60,60])
        self.assertEqual(sum(item.minutes for item in result), 360)
        self.assertTrue(all(len(item.sessions) == 1 for item in result))
        self.assertTrue(all(item.room == 'Weston' for day in result for item in day.sessions))

    def test_unopened_five_day_room_keeps_its_share_of_credit(self):
        days = [self.day(i, horizon=7200 if i > 3 else 10080) for i in range(7)]
        result = self.allocate(days)
        self.assertTrue(all(item.minutes >= 45 for item in result))
        later = result[-1].sessions[0]
        self.assertGreater(later.unlock_at, self.now)
        self.assertEqual(later.unlock_at,
            datetime.combine(later.target_date, datetime.min.time())
            + timedelta(minutes=later.start_minutes + 30 - 7200))

    def test_existing_quality_blocks_send_remaining_credit_to_other_dates(self):
        days = [self.day(0, confirmed=180, quality=60), self.day(1, confirmed=180, quality=60)]
        days.extend(self.day(i) for i in range(2,7))
        result = self.allocate(days, budget=150)
        self.assertEqual([item.minutes for item in result], [0,0,30,30,30,30,30])

    def test_low_target_and_unavailable_day_do_not_trap_credit(self):
        days = [self.day(0, target=30), self.day(1, start=12, end=12)]
        days.extend(self.day(i) for i in range(2,7))
        result = self.allocate(days)
        self.assertEqual(result[0].minutes, 30)
        self.assertEqual(result[1].minutes, 0)
        self.assertEqual(sum(item.minutes for item in result), 360)

    def test_pending_extension_and_confirmed_hours_cannot_be_double_allocated(self):
        days = [self.day(0, target=60, confirmed=30, quality=30, held=30)]
        days.extend(self.day(i) for i in range(1,4))
        result = self.allocate(days, budget=90)
        self.assertEqual([item.minutes for item in result], [0,30,30,30])

    def test_zero_credit_creates_no_advance_allocation(self):
        self.assertTrue(all(item.minutes == 0 for item in self.allocate(
            [self.day(i) for i in range(7)], budget=0)))

    def test_saved_reverse_order_only_breaks_equivalent_fair_allocations(self):
        days = [self.day(i) for i in range(7)]
        forward = self.allocate(days)
        reverse = self.allocate(days, reverse_date_order=True)
        self.assertEqual(sorted(item.minutes for item in forward),
                         sorted(item.minutes for item in reverse))
        self.assertEqual([item.target_date for item in reverse],
                         list(reversed([item.target_date for item in forward])))


if __name__ == '__main__':
    unittest.main()
