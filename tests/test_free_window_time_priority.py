"""Short preferred-time sessions must survive a free-window day forecast."""
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
from datetime import date, datetime
import io
from pathlib import Path
import unittest
from unittest.mock import patch

import book_week as b
from booking_strategy import DailyPlanningPreferences
from daily_planner import select_day_plan
from tempo_coordination import Snapshot


class FreeWindowTimePriorityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.multiple(b, FREE_HORIZON_OVERRIDES_PEAK=True,
            FREE_HORIZON_MINUTES=300, MAX_PEAK_HOURS=1, MAX_ROLLING_QUOTA_HOURS=6,
            PEAK_START=9, PEAK_END=16, MINIMUM_BLOCK_MINUTES=30,
            MIN_BOOKING_MINUTES=30, MAX_BOOKING_HOURS=2, SAME_ROOM_GAP_MINUTES=60,
            ALLOW_FRAGMENTED_SESSIONS=True, ACTIVE_ROOM_POLICY=None,
            PRIORITY_ROOMS=['Evening A', 'Evening B', 'Afternoon A', 'Afternoon B', 'Afternoon C']))
        self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=10080))
        self.stack.enter_context(patch.object(b, 'tempo_coordination_snapshot',
            return_value=Snapshot(None, Path('unused-synthetic-coordination.json'))))
        self.day = date(2026, 11, 6)
        self.now = datetime(2026, 11, 5, 23)
        self.planning = DailyPlanningPreferences()
        self.preferences = {'enabled': True, 'start_hour': 12, 'end_hour': 24, 'strict_mode': False}
        self.tracker = b.BookingTracker()
        self.tracker.add_existing_event(self.day, 12.25, 13.25, is_reservation=True, room='Existing')
        self.tracker.add_conflict(self.day, 0, 13.5)
        self.tracker.existing_reservation_hours = 6
        self.tracker.live_quota_minutes = 0
        self.gaps = [dict(room=room, slots=[dict(startHour=start, endHour=end)]) for room,start,end in (
            ('Afternoon A',13.5,14), ('Afternoon B',14,14.5), ('Afternoon C',15.25,16),
            ('Evening A',18,20), ('Evening B',20,22))]

    def opportunities(self):
        return b.build_day_booking_opportunities(self.gaps, self.day, self.tracker,
            self.preferences, self.planning, now=self.now, remaining_daily_hours=3,
            free_horizon_only=True, include_free_horizon_intent=True)

    def select(self, **kwargs):
        return select_day_plan(self.opportunities(), self.planning, now=self.now,
            target_minutes=180, allow_fragmented_sessions=True, remaining_peak_minutes=1440,
            same_room_gap_minutes=60, **kwargs)

    def test_future_free_plan_preserves_short_preferred_minutes_and_full_target(self):
        plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.potential_minutes for i in plan), 180)
        self.assertEqual(sum(i.preferred_minutes for i in plan), 105)
        self.assertEqual(len(plan), 4)

    def test_display_and_execution_choose_the_same_afternoon_sources(self):
        for now in (self.now, datetime.combine(self.day, datetime.min.time()).replace(hour=9)):
            with self.subTest(now=now):
                self.now = now
                plan = b.build_display_day_plan(self.day, self.opportunities(), self.tracker,
                    self.planning, now=now, target_minutes=240, free_horizon_only=True,
                    include_future_outside_foresight=True)
                selected = (plan.primary, *plan.additional)
                self.assertEqual(sum(i.potential_minutes for i in selected), 180)
                self.assertEqual({i.room for i in selected if i.start_time < '16:00'},
                    {'Afternoon A', 'Afternoon B', 'Afternoon C'})

    def test_room_first_and_ordinary_advance_keep_existing_priority(self):
        for ready, room_first in ((False, False), (True, True)):
            with self.subTest(ready=ready, room_first=room_first):
                self.planning = replace(self.planning, priority_mode='room_first' if room_first else 'time_first')
                plan = self.select(prefer_ready_sessions=ready)
                self.assertEqual(len(plan), 2)
                self.assertTrue(all(i.start_minutes >= 18*60 for i in plan))

    def test_an_unselected_soft_fallback_cannot_restore_a_fragmentation_penalty(self):
        self.tracker.conflict_ranges[str(self.day)] = [(12.25,13.5)]
        self.gaps.append(dict(room='Afternoon A', slots=[dict(startHour=11,endHour=12)]))
        plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.potential_minutes for i in plan), 180)
        self.assertEqual(sum(i.preferred_minutes for i in plan), 105)

    def test_ready_useful_practice_still_precedes_future_preferred_time(self):
        self.now = datetime(2026,11,6,3,30)
        self.preferences = {'enabled':False}
        self.tracker.conflict_ranges[str(self.day)] = [(12.25,13.25)]
        self.gaps = [dict(room=room,slots=[dict(startHour=start,endHour=end)])
            for room,start,end in (('Evening A',8,10),('Afternoon A',13.5,15.5))]
        plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.potential_minutes for i in plan if i.start_minutes==8*60),120)
        self.assertEqual(sum(i.potential_minutes for i in plan),180)

    def test_comfort_cannot_replace_preferred_minutes_with_evening_time(self):
        self.planning = replace(self.planning, preferred_block_minutes=60,
            preferred_rest_minutes=30, prefer_fewer_room_changes=True)
        plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.preferred_minutes for i in plan), 105)
        self.assertEqual(sum(i.potential_minutes for i in plan), 180)

    def test_busy_grid_keeps_best_preferred_portfolio_found_before_search_limit(self):
        self.gaps.append(dict(room='Afternoon B',slots=[dict(startHour=13.75,endHour=14.5)]))
        rooms = [f'Late {i}' for i in range(17)]
        self.gaps.extend(dict(room=room,slots=[dict(startHour=18,endHour=22.25)]) for room in rooms)
        with patch.object(b,'PRIORITY_ROOMS',[*b.PRIORITY_ROOMS,*rooms]):
            plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.potential_minutes for i in plan),180)
        self.assertEqual(sum(i.preferred_minutes for i in plan),105)

    def test_strict_dated_time_limit_remains_a_hard_boundary(self):
        self.preferences = b.load_time_preferences({'time_preferences':{
            'enabled':True,'preset':'custom','custom_start_hour':12,'custom_start_min':0,
            'custom_end_hour':18,'custom_end_min':0,'end_boundary':'rooms_closed','strict_mode':False},
            'date_time_preferences':{str(self.day):{'enabled':True,'start_time':'13:30','end_time':'15:00','strict_mode':True}}})
        plan = self.select(prefer_ready_sessions=True)
        self.assertEqual(sum(i.potential_minutes for i in plan), 60)
        self.assertTrue(all(13.5*60 <= i.start_minutes < i.end_minutes <= 15*60 for i in plan))

    def test_disabled_peak_exception_still_excludes_exhausted_peak_time(self):
        with patch.object(b, 'FREE_HORIZON_OVERRIDES_PEAK', False):
            plan = self.select(prefer_ready_sessions=False)
        self.assertTrue(all(i.peak_minutes == 0 for i in plan))


if __name__ == '__main__':
    unittest.main()
