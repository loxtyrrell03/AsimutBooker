import contextlib
import io
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import advance_runtime as runtime
import book_week as b
from booking_plan import _day_to_dict


class AdvanceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.now = datetime(2026, 9, 20, 14)
        self.days = tuple(date(2026, 9, 21) + timedelta(days=i) for i in range(7))
        self.settings = {'booking_rules': {'preset': 'new'}}
        self.practice = b.PracticePlan(enabled=True, default_hours=4)
        self.args = b.build_argument_parser().parse_args(['--headless'])
        self.events = []
        self.details = []
        self.grids = {day: [{'room':room, 'slots':[{'startHour':12,'endHour':18}]}
                           for room in ('Weston', 'Corus', 'Other')] for day in self.days}
        self.current_day = None
        self.stack.enter_context(patch.multiple(b, PRIORITY_ROOMS=['Weston','Corus','Other'],
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2, MAX_PEAK_HOURS=1,
            MAX_ROLLING_QUOTA_HOURS=6, ALLOW_FRAGMENTED_SESSIONS=True))
        self.horizons = self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=10080))
        clock = self.stack.enter_context(patch.object(runtime, 'datetime', wraps=datetime))
        clock.now.side_effect = lambda: self.now
        self.stack.enter_context(patch.object(b, 'booking_window_dates', return_value=self.days))
        self.stack.enter_context(patch.object(b, 'load_extendable_bookings', return_value=[]))
        self.stack.enter_context(patch.object(b, 'load_settings_document', return_value=self.settings))
        self.stack.enter_context(patch.object(b, 'load_disabled_dates', return_value=set()))
        self.stack.enter_context(patch.object(b, 'load_time_preferences', return_value={
            'enabled':True, 'strict_mode':False, 'start_hour':12, 'end_hour':18}))
        self.stack.enter_context(patch.object(b, 'list_pending_mutation_receipts', return_value=[]))
        self.stack.enter_context(patch.object(runtime, 'open_day', side_effect=self.open))
        self.stack.enter_context(patch.object(b, 'get_available_slots', side_effect=lambda page:self.grids[self.current_day]))
        self.attempt = self.stack.enter_context(patch.object(b, 'attempt_booking_with_room_fallback', side_effect=self.book))
        self.stack.enter_context(patch.object(b, 'scan_agenda', side_effect=self.scan))
        self.stack.enter_context(patch.object(runtime, 'refresh_quota_balances', side_effect=self.quota))
        self.publications = []
        self.stack.enter_context(patch.object(b, 'publish_booking_plan', side_effect=self.publish))
        self.tracker = b.BookingTracker()
        self.quota(None, self.tracker, self.days)

    def open(self, engine, page, day, today):
        self.current_day = day

    def quota(self, page, tracker, days):
        tracker.live_quota_minutes = 360 - sum(round((end-start)*60) for _,_,start,end in self.events)
        tracker.quota_observed_hours = tracker.get_total_booking_hours()

    def book(self, page, slot, day, tracker, offset, **kwargs):
        self.assertIn(slot['room'], ('Weston','Corus'))
        self.assertEqual(kwargs['allowed_rooms'], ('Weston','Corus'))
        self.assertFalse(kwargs['horizon'])
        start, end = slot['start_hour'], slot['end_hour']
        self.assertLessEqual(end-start, kwargs['remaining_daily_hours'])
        self.assertTrue(tracker.can_book(slot['room'], day, start, round((end-start)*60))[0])
        self.events.append((slot['room'], day, start, end))
        return {'date':str(day), 'room':slot['room'], 'start':b.time_text(round(start*60)),
                'end':b.time_text(round(end*60))}, slot

    def scan(self, page, tracker, *args, **kwargs):
        for room, day, start, end in self.events:
            tracker.add_existing_event(day, start, end, is_reservation=True, room=room)
        return len(self.events), []

    def publish(self, rows, *args, **kwargs):
        for row in rows:
            _day_to_dict(row, 'day')
        self.publications.append(rows)

    def run_week(self, **kwargs):
        return runtime.run(b, None, Mock(), self.settings, self.practice, self.args,
                           self.tracker, 0, self.details, **kwargs)

    def test_real_planner_replans_after_each_booking_and_spreads_six_hours(self):
        actions, tracker, handled = self.run_week()
        self.assertTrue(handled)
        self.assertEqual(actions, 7)
        self.assertEqual(len({day for _,day,_,_ in self.events}), 7)
        self.assertEqual(sorted(round((end-start)*60) for _,_,start,end in self.events),
                         [45,45,45,45,60,60,60])
        self.assertEqual(tracker.get_remaining_quota_hours(), 0)

    def test_shorter_room_horizons_hold_credit_without_premature_create(self):
        self.horizons.return_value = 7200
        actions, tracker, handled = self.run_week()
        self.assertTrue(handled)
        self.assertEqual(actions, 5)
        self.assertGreaterEqual(tracker.get_remaining_quota_hours(), 1.5)
        waiting = [row for row in self.publications[-1] if row.primary and row.primary.state == 'waiting']
        self.assertEqual(len(waiting), 2)

    def test_read_only_week_discovery_never_mutates(self):
        self.run_week(read_only=True)
        self.attempt.assert_not_called()
        self.assertEqual(sum(row.primary.potential_minutes for row in self.publications[-1]), 360)

    def test_action_limit_stops_after_verified_action(self):
        self.args.max_actions = 1
        self.assertEqual(self.run_week()[0], 1)
        self.assertEqual(len(self.events), 1)

    def test_soft_window_keeps_shorter_useful_tail_of_a_long_room_gap(self):
        self.grids[self.days[0]] = [{'room':'Weston', 'slots':[{'startHour':16.5,'endHour':22}]}]
        days, allocations, _ = runtime.plan_from_grids(b, self.grids, self.settings,
            self.practice, self.tracker, self.args, now=self.now)
        first = next(item for item in allocations if item.target_date == self.days[0])
        self.assertGreaterEqual(first.minutes, 45)
        self.assertTrue(all(item.start_hour >= 16.5 and item.end_hour <= 18 for item in first.sessions))

    def test_completed_action_scope_does_no_further_room_scan(self):
        self.args.max_actions = 0
        self.assertEqual(self.run_week()[0], 0)
        self.assertIsNone(self.current_day)

    def test_no_advance_credit_does_not_hide_todays_free_window_plan(self):
        today = self.now.date()
        self.grids[today] = self.grids[self.days[0]]
        self.tracker.live_quota_minutes = 0
        with patch.object(b, 'booking_window_dates', return_value=(today,)):
            self.run_week(read_only=True)
        row = self.publications[-1][0]
        self.assertIsNotNone(row.primary)
        self.assertEqual(row.primary.state, 'ready')
        self.assertEqual(row.existing_minutes, 0)
        self.attempt.assert_not_called()

    def test_free_preview_reserves_extension_time_and_can_publish_remaining_session(self):
        self.now = datetime(2026, 9, 21, 14, 36)
        today = self.now.date()
        self.practice = b.PracticePlan(enabled=True, default_hours=2)
        self.grids = {today: [{'room':'Weston','slots':[{'startHour':19.5,'endHour':20.5}]}]}
        self.tracker.add_existing_event(today,18.5,19.5,is_reservation=True,room='Other')
        self.tracker.live_quota_minutes = 0
        booking = dict(room='Other',date=str(today),startTime='18:30',endTime='19:30',
            target_end='20:00',created_at=self.now.isoformat(),eventId=42,
            event_url='https://rwcmd.asimut.net/arrangement?eventId=42')
        with patch.object(b,'booking_window_dates',return_value=(today,)), \
             patch.object(b,'load_time_preferences',return_value={'enabled':False}), \
             patch.object(b,'load_extendable_bookings',return_value=[booking]):
            days, allocations, context = runtime.plan_from_grids(b,self.grids,self.settings,
                self.practice,self.tracker,self.args,now=self.now)
            runtime.publish(b,days,allocations,context,Mock(),self.settings,self.tracker,
                now=self.now,grids=self.grids)
        row = self.publications[-1][0]  # publish validates all candidates, including overlap.
        self.assertEqual((row.primary.start_time,row.primary.end_time),('18:30','20:00'))
        self.assertEqual([(c.start_time,c.end_time) for c in row.additional],[('20:00','20:30')])
        self.assertEqual(row.existing_minutes,60)  # Holds never inflate confirmed practice.
        self.assertFalse(self.tracker.can_book('Weston',today,19.5,30,now=self.now.replace(hour=15))[0])
        self.assertFalse(self.tracker.can_book('Other',today,20.5,30,now=self.now.replace(hour=16))[0])
        # Fresh evidence can retire the hold without leaving stale phantom conflicts.
        with patch.object(b,'booking_window_dates',return_value=(today,)), \
             patch.object(b,'load_extendable_bookings',return_value=[]):
            b.refresh_extension_capacity_holds({},self.tracker,self.practice,set(),now=self.now)
        self.assertTrue(self.tracker.can_book('Weston',today,19.5,30,now=self.now.replace(hour=15))[0])

    def test_scoped_and_legacy_runs_retain_existing_behaviour(self):
        self.args.only_date = str(self.days[0])
        self.assertFalse(self.run_week()[2])
        self.args.only_date = None
        self.settings['booking_rules']['preset'] = 'legacy'
        self.assertFalse(self.run_week()[2])
        self.attempt.assert_not_called()

    def test_completed_session_credit_secures_new_date_before_enlarging_covered_days(self):
        _, self.tracker, _ = self.run_week()
        completed = self.days[0]
        self.events = [event for event in self.events if event[1] != completed]
        self.now = datetime(2026, 9, 21, 19)
        new_day = self.days[-1] + timedelta(days=1)
        self.days = (*self.days[1:], new_day)
        self.grids[new_day] = self.grids[completed]
        self.tracker = b.BookingTracker()
        self.scan(None, self.tracker)
        self.quota(None, self.tracker, self.days)
        self.args.max_actions = 1
        with patch.object(b, 'booking_window_dates', return_value=self.days):
            self.assertEqual(self.run_week()[0], 1)
        self.assertEqual(self.events[-1][1], new_day)
        self.assertEqual(len({event[1] for event in self.events}), 7)

    def test_known_refusal_stops_without_claiming_or_repeating_a_booking(self):
        self.attempt.side_effect = lambda *a, **kw: (False, None)
        self.assertEqual(self.run_week()[0], 0)
        self.assertEqual(self.attempt.call_count, len(self.days))
        self.assertEqual(len({call.args[2] for call in self.attempt.call_args_list}), len(self.days))
        self.assertEqual(self.events, [])
        self.assertEqual(self.details, [])


if __name__ == '__main__':
    unittest.main()
