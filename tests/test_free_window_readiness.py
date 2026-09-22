"""Ready preferred practice must not be withheld for exempt peak credit."""
import contextlib
import io
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

import book_week as b
from booking_strategy import DailyPlanningPreferences
from date_time_preferences import resolve_time_preferences
from daily_planner import choose_horizon_opportunity
from tests import test_new_booking_quotas as short_notice_fixture


class FreeWindowReadinessTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.multiple(b, FREE_HORIZON_OVERRIDES_PEAK=True,
            FREE_HORIZON_MINUTES=300, MAX_PEAK_HOURS=1, MAX_ROLLING_QUOTA_HOURS=6,
            PEAK_START=9, PEAK_END=16, MINIMUM_BLOCK_MINUTES=30,
            MIN_BOOKING_MINUTES=30, MAX_BOOKING_HOURS=2, ALLOW_FRAGMENTED_SESSIONS=True,
            PRIORITY_ROOMS=['Weston', 'Corus', 'Early best', 'Early fallback']))
        self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=7200))
        self.now = datetime(2026, 9, 22, 9)
        self.day = self.now.date()
        self.planning = DailyPlanningPreferences()
        self.settings = {
            'time_preferences': {'enabled': True, 'preset': 'custom',
                'custom_start_hour': 12, 'custom_start_min': 0,
                'custom_end_hour': 18, 'custom_end_min': 0, 'end_boundary': 'rooms_closed',
                'strict_mode': False},
            'date_time_preferences': {str(self.day): {'enabled': True,
                'start_time': '10:30', 'end_time': 'rooms_closed', 'strict_mode': False}},
        }
        self.tracker = b.BookingTracker()
        for start, end in ((12, 12.75), (13, 14), (15, 16), (16, 16.5), (17.25, 18)):
            self.tracker.add_existing_event(self.day, start, end, is_reservation=True, room='Existing')
        self.tracker.existing_reservation_hours = 6
        self.tracker.live_quota_minutes = 0
        self.tracker.live_peak_minutes = {str(self.day): 0}
        self.gaps = [
            {'room': room, 'slots': [{'startHour': start, 'endHour': end}]}
            for room, start, end in (('Early fallback', 10.5, 12),
                ('Early best', 10.5, 12), ('Corus', 18, 20), ('Weston', 18, 20))]

    def opportunities(self, **changes):
        return b.build_day_booking_opportunities(self.gaps, self.day, self.tracker,
            b.load_time_preferences(self.settings), self.planning, now=self.now,
            remaining_daily_hours=2, free_horizon_only=True,
            include_free_horizon_intent=True, **changes)

    def plan(self, opportunities=None, **changes):
        return b.build_display_day_plan(self.day,
            self.opportunities() if opportunities is None else opportunities,
            self.tracker, self.planning, now=self.now, target_minutes=360,
            free_horizon_only=True, **changes)

    def test_saved_date_override_reaches_current_plan_without_changing_other_days(self):
        preferences = b.load_time_preferences(self.settings)
        self.assertEqual(resolve_time_preferences(preferences, self.day)['start_hour'], 10.5)
        self.assertEqual(resolve_time_preferences(preferences, self.day + timedelta(days=1))['start_hour'], 12)
        opportunities = self.opportunities()
        self.assertTrue(all(item.soft_preferred_window == (630, 1440) for item in opportunities))
        plan = self.plan(opportunities)
        self.assertEqual(plan.primary.state, 'ready')
        self.assertEqual((plan.primary.room, plan.primary.start_time), ('Early best', '10:30'))
        self.assertEqual(plan.held_peak_minutes, 0)
        self.assertIn('no peak allowance is held', plan.reason)
        self.assertTrue(b._runtime_ordered_day_opportunities(opportunities, plan, self.planning, now=self.now))

    def test_ready_interval_is_not_needlessly_trimmed_to_favour_evening(self):
        plan = self.plan()
        self.assertEqual((plan.primary.start_time, plan.primary.end_time), ('10:30', '12:00'))
        self.assertEqual(sum(item.potential_minutes for item in (plan.primary, *plan.additional)), 120)

    def test_exception_off_keeps_real_peak_limit(self):
        with patch.object(b, 'FREE_HORIZON_OVERRIDES_PEAK', False):
            plan = self.plan()
        self.assertNotEqual(plan.primary.state, 'ready')
        self.assertGreaterEqual(plan.primary.start_time, '16:00')

    def test_current_ninety_minute_shortfall_is_filled_without_evening_hold(self):
        self.tracker.add_existing_event(self.day, 14, 14.5, is_reservation=True, room='Existing')
        plan = self.plan()
        self.assertEqual((plan.primary.start_time, plan.primary.end_time), ('10:30', '12:00'))
        self.assertEqual(plan.additional, ())

    def test_comfort_and_room_first_keep_ready_preferred_practice(self):
        self.planning = replace(self.planning, preferred_block_minutes=60,
            preferred_rest_minutes=30, prefer_fewer_room_changes=True, priority_mode='room_first')
        plan = self.plan()
        self.assertEqual(plan.primary.room, 'Early best')
        self.assertEqual((plan.primary.start_time, plan.primary.end_time), ('10:30', '12:00'))

    def test_reserving_extension_capacity_does_not_overfill_target(self):
        plan = self.plan(reserved_daily_minutes=60, reserved_peak_minutes=60)
        self.assertEqual(sum(item.potential_minutes for item in (plan.primary, *plan.additional)), 60)
        self.assertEqual(plan.primary.state, 'ready')

    def test_soft_preference_can_still_wait_when_ready_slot_is_outside_window(self):
        self.settings['date_time_preferences'] = {}
        plan = self.plan()
        self.assertEqual(plan.primary.state, 'waiting')
        self.assertNotIn('preserve 0 peak', plan.reason)

    def test_outside_free_window_stays_waiting_until_seed_fits(self):
        self.now = self.now.replace(hour=5, minute=59)
        self.assertEqual(self.plan().primary.state, 'waiting')
        self.now = self.now.replace(hour=6, minute=0)
        plan = self.plan()
        self.assertEqual(plan.primary.state, 'ready')
        self.assertFalse(self.tracker.can_book('Early best', self.day, 10.5, 90, now=self.now)[0])
        self.assertTrue(self.tracker.can_book('Early best', self.day, 10.5, 30, now=self.now)[0])

    def test_ordinary_advance_foresight_remains_available(self):
        opportunities = self.opportunities()
        current = [item for item in opportunities if item.unlock_at <= self.now]
        future = [item for item in opportunities if item.unlock_at > self.now]
        decision = choose_horizon_opportunity(current, future, self.planning, now=self.now)
        self.assertEqual(decision.action, 'wait')
        self.assertNotIn('preserve 0 peak', decision.reason)
        self.assertIn('better visible later', decision.reason)

    def test_prepared_notification_uses_matching_projected_candidate_reason(self):
        fixture = short_notice_fixture.ShortNoticeIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.now = fixture.now.replace(hour=14, minute=13)
        fixture.args.scheduled = True
        fixture.args.target_time = '14:15'
        fixture.plan = b.PracticePlan(enabled=True, default_hours=.5)
        fixture.grid.return_value = [{'room': 'A', 'slots': [{'startHour': 18.75, 'endHour': 20}]}]
        builder = b.build_display_day_plan

        def labelled_plan(*args, **kwargs):
            plan = builder(*args, **kwargs)
            reason = 'Actual prepared choice' if kwargs['now'].minute == 15 else 'Old waiting decision'
            return replace(plan, reason=reason, primary=replace(plan.primary, reason=reason))

        def prepare(page, slot, day, *args, **kwargs):
            self.assertIn('Actual prepared choice', slot['decision_reason'])
            self.assertNotIn('Old waiting decision', slot['decision_reason'])
            fixture.now = slot['bookable_from']
            return fixture.book(page, slot, day, *args, **kwargs)

        fixture.attempt.side_effect = prepare
        with patch.object(b, 'build_display_day_plan', side_effect=labelled_plan):
            self.assertEqual(fixture.run_pass()[0], 1)


if __name__ == '__main__':
    unittest.main()
