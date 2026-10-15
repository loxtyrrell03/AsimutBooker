"""Display timing follows every selected session, not the room ranking."""
import contextlib
import io
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import book_week as b
from tempo_coordination import Snapshot


class PlanOpeningSummaryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.multiple(b, FREE_HORIZON_MINUTES=300,
            FREE_HORIZON_OVERRIDES_PEAK=True, MINIMUM_BLOCK_MINUTES=30,
            ALLOW_FRAGMENTED_SESSIONS=True, MAX_PEAK_HOURS=1,
            PRIORITY_ROOMS=['Evening', 'Morning', 'Alternative']))
        self.now = datetime(2026, 10, 15, 0)
        self.day = self.now.date()
        self.stack.enter_context(patch.object(b, 'tempo_coordination_snapshot',
            return_value=Snapshot(None, Path('unused-coordination.json'))))
        self.tracker = b.BookingTracker()
        self.tracker.live_quota_minutes = 0
        self.tracker.existing_reservation_hours = b.MAX_ROLLING_QUOTA_HOURS
        self.planning = b.DailyPlanningPreferences(foresight_minutes=60)
        self.evening = self.opportunity('Evening', 21*60+15, 22*60+15, 0)
        self.morning = self.opportunity('Morning', 10*60+45, 11*60+30, 1)

    def opportunity(self, room, start, end, priority):
        opening = start + 30 - 300
        return b.BookingOpportunity(room=room, target_date=self.day,
            start_minutes=start, end_minutes=end,
            unlock_at=self.now.replace(hour=opening//60, minute=opening%60),
            room_priority=priority, initial_minutes=30, potential_minutes=end-start,
            preferred_minutes=0, soft_preferred_minutes=end-start, peak_minutes=0,
            peak_window_start_minutes=540, peak_window_end_minutes=960,
            source_gap_start_minutes=start, source_gap_end_minutes=end)

    def day_plan(self, now=None):
        return b.build_display_day_plan(self.day, [self.evening, self.morning],
            self.tracker, self.planning, now=now or self.now, target_minutes=105,
            free_horizon_only=True, include_future_outside_foresight=True)

    def test_day_summary_names_earliest_selected_opening_without_changing_rank(self):
        day = self.day_plan()
        self.assertEqual(day.primary.room, 'Evening')
        self.assertEqual([item.room for item in day.additional], ['Morning'])
        self.assertIn('booking starts opening Thu 15 Oct at 06:15', day.reason)
        self.assertIn('16:45', day.primary.reason)
        self.assertIn('06:15', day.additional[0].reason)
        self.assertEqual(day.status, 'waiting')

    def test_ready_morning_stays_a_runtime_priority(self):
        day = self.day_plan(now=self.now.replace(hour=7))
        self.assertEqual((day.primary.room, day.primary.state), ('Morning', 'ready'))
        self.assertEqual(day.status, 'planned')
        self.assertNotIn('booking starts opening', day.reason)

    def publish(self, day):
        fixed_now = self.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is None else fixed_now.astimezone(tz)
        with (patch.object(b, 'datetime', Clock),
              patch.object(b, 'booking_window_dates', return_value=(self.day,)),
              patch.object(b, 'open_practice_room_overview'),
              patch.object(b, 'wait_for_practice_room_grid'),
              patch.object(b, 'get_available_slots', return_value=[]),
              patch.object(b, 'build_day_booking_opportunities', return_value=[]),
              patch.object(b, 'build_display_day_plan', return_value=day),
              patch.object(b, 'publish_booking_plan') as publish):
            b.generate_read_only_booking_plan(Mock(), Mock(), {},
                b.PracticePlan(enabled=True, default_hours=2), self.tracker,
                {'enabled': False, 'strict_mode': False}, self.planning, set(),
                SimpleNamespace(only_date=None, only_room=None), today=self.day)
        return publish.call_args.kwargs['summary']

    def test_overall_summary_uses_additional_session_not_earlier_unselected_backup(self):
        day = self.day_plan()
        backup = replace(day.additional[0], room='Alternative',
            unlock_at=day.additional[0].unlock_at.replace(hour=5), state='potential')
        self.assertIn('10:45-11:30 in Morning', self.publish(replace(day, backups=(backup,))))

    def test_waiting_extension_primary_cannot_hide_earlier_create(self):
        day = self.day_plan()
        extension = replace(day.primary, confirmed_minutes=30)
        self.assertIn('10:45-11:30 in Morning', self.publish(
            replace(day, primary=extension, status='in_progress')))


if __name__ == '__main__':
    unittest.main()
