import contextlib
import io
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch, Mock

import book_week as b
from booking_quotas import (parse_quota_balance, refresh_quota_balances,
    free_horizon_hours, in_free_horizon, check_quota_refusal, QuotaWait, QuotaPolicyError)
from booking_strategy import DailyPlanningPreferences
from room_catalog import SITE_TIMEZONE
from types import SimpleNamespace
import short_notice_bookings as short_notice


def quota(rolling=21600, peak=3600, **extra):
    return {'response': {'success': True, 'quota_overview': {
        'booking_quota': rolling, 'booking_quota_p': peak,
        'booking_quota_w': None, 'booking_quota_d': None,
        'booking_quota_t': None, 'booking_quota_t_dates': None, **extra}}}


class NewQuotaTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 17)
        self.now = datetime(2026, 9, 17, 14)

    def test_live_seconds_and_null_peak(self):
        self.assertEqual(parse_quota_balance(quota(1800, 900)).rolling_minutes, 30)
        self.assertEqual(parse_quota_balance(quota(1800, 900)).peak_minutes, 15)
        self.assertIsNone(parse_quota_balance(quota(0, None)).peak_minutes)

    def test_bad_or_new_rules_fail_closed(self):
        bad = [quota(rolling=True), quota(rolling=-1), quota(rolling=float('nan')),
               quota(booking_quota_w=600), quota(unknown_rule=0), {},
               {'response': {'success': False, 'quota_overview': {}}}]
        for document in bad:
            with self.subTest(document=document), self.assertRaises(QuotaPolicyError):
                parse_quota_balance(document)

    def test_both_endpoints_and_minute_boundary(self):
        self.assertTrue(in_free_horizon(self.day, 15, 17, now=self.now))
        self.assertTrue(in_free_horizon(self.day, 17, 19, now=self.now))
        self.assertFalse(in_free_horizon(self.day, 18, 19.25, now=self.now))
        self.assertFalse(in_free_horizon(self.day, 13, 15, now=self.now))
        self.assertFalse(in_free_horizon(self.day + timedelta(days=1), 8, 9, now=self.now))
        self.assertEqual(free_horizon_hours(self.day, 17, now=self.now.replace(second=59)), 2)

    def test_midnight_and_clock_changes_use_elapsed_hours(self):
        self.assertTrue(in_free_horizon(self.day + timedelta(days=1), 0, 2,
                                      now=self.now.replace(hour=22)))
        autumn = datetime(2026, 10, 25, 0, tzinfo=SITE_TIMEZONE)
        self.assertEqual(free_horizon_hours(autumn.date(), 3, now=autumn), 1)
        self.assertEqual(free_horizon_hours(autumn.date(), 1.5, now=autumn), 0)
        spring = datetime(2026, 3, 29, 0, tzinfo=SITE_TIMEZONE)
        self.assertEqual(free_horizon_hours(spring.date(), 4, now=spring), 2)

    def test_live_balance_overrides_already_completed_agenda_time(self):
        tracker = b.BookingTracker()
        tracker.existing_reservation_hours = 9  # Includes sessions earlier today.
        with patch('booking_quotas.read_quota_balance', return_value=parse_quota_balance(quota(7200))):
            refresh_quota_balances(None, tracker, (self.day,))
        self.assertEqual(tracker.get_remaining_quota_hours(), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.add_booking('A', self.day, 17, 18)
        self.assertEqual(tracker.get_remaining_quota_hours(), 1)

    def test_free_booking_does_not_create_future_credit(self):
        tracker = b.BookingTracker()
        tracker.existing_reservation_hours = 8
        self.assertEqual(tracker.get_remaining_quota_hours(), 0)
        self.assertEqual(tracker.booking_capacity_hours(self.day, 17, now=self.now), 2)
        self.assertEqual(tracker.booking_capacity_hours(self.day + timedelta(days=1), 17, now=self.now), 0)

    def test_partial_quota_is_still_consumed(self):
        tracker = b.BookingTracker()
        tracker.existing_reservation_hours = 5.5
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.add_booking('A', self.day, 17, 18)
        self.assertEqual(tracker.get_remaining_quota_hours(), 0)
        self.assertEqual(tracker.get_hours_for_day(self.day), 1)

    def test_live_peak_exception_cannot_allow_more_than_one_hour_locally(self):
        tracker = b.BookingTracker()
        future = date(2099, 9, 17)  # Weekday, independent of wall-clock passage.
        with contextlib.redirect_stdout(io.StringIO()):
            tracker.add_existing_event(future, 12, 12.5, is_reservation=True, room='A')
        with patch('booking_quotas.read_quota_balance', return_value=parse_quota_balance(quota(0, 3600))):
            refresh_quota_balances(None, tracker, (future,))
        self.assertEqual(tracker.get_remaining_peak_minutes(future), 30)

    def test_free_horizon_plan_remains_visible_with_no_advance_quota(self):
        tracker = b.BookingTracker()
        tracker.existing_reservation_hours = 6
        with patch.multiple(b, PRIORITY_ROOMS=['A'], MINIMUM_BLOCK_MINUTES=30,
                            ALLOW_FRAGMENTED_SESSIONS=True), patch.object(b, 'room_horizon_minutes', return_value=10080):
            opportunities = b.build_day_booking_opportunities(
                [{'room':'A','slots':[{'startHour':16,'endHour':19}]}], self.day,
                tracker, {'enabled':False}, DailyPlanningPreferences(), now=self.now,
                remaining_daily_hours=2, free_horizon_only=True)
            for legacy in (False, True):
                args = [self.day, opportunities, tracker, DailyPlanningPreferences()]
                if legacy:
                    args.append({'enabled':False})
                builder = b.build_legacy_display_day_plan if legacy else b.build_display_day_plan
                result = builder(*args, now=self.now, target_minutes=120, free_horizon_only=True)
                self.assertIsNotNone(result.primary)
                self.assertEqual(result.primary.state, 'ready')

    def test_incomplete_refresh_never_partially_replaces_balance(self):
        tracker = b.BookingTracker()
        tracker.live_quota_minutes = 12
        with patch('booking_quotas.read_quota_balance', side_effect=[parse_quota_balance(quota()), QuotaPolicyError('unavailable')]):
            with self.assertRaises(QuotaPolicyError):
                refresh_quota_balances(None, tracker, (self.day, self.day + timedelta(days=1)))
        self.assertEqual(tracker.live_quota_minutes, 12)

    def test_explicit_quota_refusal_ends_run_not_room_fallback(self):
        document = {'response': {'bookingrules': {'issues': [{'class': 'message-warning',
            'type': 'category', 'text': 'Requested booking exceeds your peak quota'}]}}}
        with self.assertRaises(QuotaWait):
            check_quota_refusal(document)
        with patch.object(b, 'try_book_slot', side_effect=QuotaWait('full')) as attempt:
            with self.assertRaises(QuotaWait):
                b.attempt_booking_with_room_fallback(None, {'room': 'A'}, self.day,
                    b.BookingTracker(), 0, time_prefs={'enabled': False},
                    daily_planning=DailyPlanningPreferences())
        self.assertEqual(attempt.call_count, 1)

    def test_free_horizon_planner_respects_peak_and_cutoff(self):
        tracker = b.BookingTracker()
        tracker.existing_reservation_hours = 6
        tracker.peak_hours_by_day[str(self.day)] = 30
        with patch.multiple(b, PRIORITY_ROOMS=['A'], MINIMUM_BLOCK_MINUTES=30,
                            ROOM_HORIZON_MINUTES={'A': 10080}), patch.object(b, 'room_horizon_minutes', return_value=10080):
            opportunities = b.build_day_booking_opportunities(
                [{'room': 'A', 'slots': [{'startHour': 14, 'endHour': 23}]}],
                self.day, tracker, {'enabled': False}, DailyPlanningPreferences(),
                now=self.now, remaining_daily_hours=4, free_horizon_only=True)
        self.assertTrue(opportunities)
        self.assertTrue(all(item.end_minutes <= 19 * 60 for item in opportunities))
        self.assertTrue(all(item.peak_minutes <= 30 for item in opportunities))


class QuotaStopReportingTests(unittest.TestCase):
    def test_quota_wait_preserves_verified_changes_and_pending_transaction_status(self):
        for pending, expected_status, expected_code in (([], 'completed', 0),
                ([{'kind': 'transfer'}], 'reconciliation_required', 5)):
            with self.subTest(pending=bool(pending)), contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(patch.object(b, '_load_and_validate_runtime_settings',
                                                return_value=({}, None, None)))
                stack.enter_context(patch.object(b, 'SingleInstanceLock'))
                stack.enter_context(patch.object(b, 'booking_preference_run',
                                                return_value=contextlib.nullcontext()))
                stack.enter_context(patch.object(b, 'list_pending_mutation_receipts', return_value=pending))
                history = stack.enter_context(patch.object(b, 'save_history'))
                def run(*args):
                    b._run_verified_details.get().append('VERIFIED: earlier booking')
                    raise QuotaWait('quota changed')
                attempt = stack.enter_context(patch.object(b, 'run_booking', side_effect=run))
                self.assertEqual(b.main(['--headless']), expected_code)
                attempt.assert_called_once()
                self.assertEqual(history.call_args.args[0], 1)
                self.assertIn('VERIFIED: earlier booking', history.call_args.args[2])
                self.assertEqual(history.call_args.kwargs.get('outcome', 'completed'), expected_status)


class PeakExtensionTests(unittest.TestCase):
    def run_extension(self, start, end, target, now, *, used_peak=None, full_quota=False, added_this_run=False):
        day = now.date()
        booking = dict(room='A', date=str(day), startTime=b.time_text(round(start*60)),
                       endTime=b.time_text(round(end*60)), target_end=b.time_text(round(target*60)),
                       created_at=now.isoformat(), eventId=100,
                       event_url='https://rwcmd.asimut.net/arrangement?eventId=100')
        tracker = b.BookingTracker()
        with contextlib.redirect_stdout(io.StringIO()):
            if added_this_run:
                tracker.add_booking('A', day, start, end)
            else:
                tracker.add_existing_event(day, start, end, is_reservation=True, room='A')
        if full_quota:
            tracker.existing_reservation_hours = 6
        if used_peak is not None:
            tracker.peak_hours_by_day[str(day)] = used_peak
        before = tracker.get_total_booking_hours()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(b, 'calculate_max_extension', return_value=(True, target, 'ready')), \
             patch.object(b, 'edit_reservation_end_time', return_value=True) as edit, \
             patch.object(b, 'update_extendable_booking_end_time'), patch.object(b, 'remove_extendable_booking'):
            result = b.try_extend_booking(None, booking, tracker, now=now)
        return result, edit, tracker, before

    def test_peak_extension_caps_at_one_hour_inside_free_horizon(self):
        result, edit, _, _ = self.run_extension(14, 14.5, 16, datetime(2026, 9, 17, 12), full_quota=True)
        self.assertTrue(result[0], result[2])
        self.assertEqual(result[1], '15:00')
        self.assertEqual(edit.call_args.args[2], '15:00')

    def test_other_peak_reservations_reduce_extension_allowance(self):
        result, _, _, _ = self.run_extension(14, 14.5, 16, datetime(2026, 9, 17, 12), used_peak=45)
        self.assertEqual(result[1], '14:45')

    def test_peak_full_does_no_editor_work(self):
        result, edit, _, _ = self.run_extension(14, 15, 16, datetime(2026, 9, 17, 12))
        self.assertFalse(result[0])
        edit.assert_not_called()

    def test_crossing_peak_end_keeps_full_two_hour_session(self):
        result, _, _, _ = self.run_extension(15, 15.5, 17, datetime(2026, 9, 17, 12))
        self.assertEqual(result[1], '17:00')

    def test_offpeak_extends_at_full_quota_only_to_free_cutoff(self):
        result, _, _, _ = self.run_extension(17, 17.5, 19, datetime(2026, 9, 17, 13), full_quota=True)
        self.assertEqual(result[1], '18:00')

    def test_extension_created_this_run_counts_added_time_once(self):
        result, _, tracker, before = self.run_extension(17, 17.5, 18, datetime(2026, 9, 17, 13), added_this_run=True)
        self.assertTrue(result[0])
        self.assertEqual(tracker.get_total_booking_hours() - before, .5)


class FreeHorizonExtensionIntentTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.now = datetime(2026, 9, 21, 7, 30)
        self.day = self.now.date()
        self.stack.enter_context(patch.multiple(b, PRIORITY_ROOMS=['A'],
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2, MAX_PEAK_HOURS=1,
            FREE_HORIZON_MINUTES=300))
        self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=10080))
        self.stack.enter_context(patch.object(b, 'booking_window_dates', return_value=(self.day,)))
        clock = self.stack.enter_context(patch.object(b, 'datetime', wraps=datetime))
        clock.now.side_effect = lambda: self.now
        self.tracker = b.BookingTracker()
        self.tracker.existing_reservation_hours = 6
        self.booking = dict(room='A', date=str(self.day), startTime='12:00',
            endTime='12:30', target_end='13:00', created_at=self.now.isoformat(), eventId=42)

    def test_create_saves_only_open_prefix_but_retains_later_target(self):
        caps = []
        cap = b._bounded_create_duration_minutes
        def record(*args):
            value = cap(*args)
            caps.append(value)
            return value
        with patch.object(b, '_bounded_create_duration_minutes', side_effect=record), \
             patch.object(b, 'get_room_slot_coordinates', return_value=None) as coordinates:
            self.assertFalse(b.try_book_slot(None, dict(room='A', start_hour=12,
                end_hour=13, free_horizon_intent=True), self.day, self.tracker, 0,
                remaining_daily_hours=4, time_prefs={'enabled':False}))
        self.assertEqual(caps, [60, 30])
        self.assertEqual(coordinates.call_args.args[2:], (12, 12.5))
        self.assertEqual(b._extension_target_end_hour(12, 12.5, caps[0]), 13)

    def test_pending_peak_tail_keeps_allowance_before_its_window_opens(self):
        self.tracker.add_existing_event(self.day, 12, 12.5, is_reservation=True, room='A')
        targets, peaks, held = b.calculate_extension_capacity_holds(
            [self.booking], self.tracker, b.PracticePlan(enabled=True, default_hours=4),
            set(), time_prefs={'enabled':False}, now=self.now)
        self.assertEqual(targets, {str(self.day):30})
        self.assertEqual(peaks, {str(self.day):30})
        self.assertEqual(held[0]['target_end'], '13:00')

    def test_future_hold_never_authorizes_an_early_extension(self):
        self.tracker.add_existing_event(self.day, 12, 12.5, is_reservation=True, room='A')
        with patch.object(b, 'edit_reservation_end_time') as edit:
            result = b.try_extend_booking(None, self.booking, self.tracker, now=self.now,
                remaining_daily_hours=3.5, time_prefs={'enabled':False})
        self.assertFalse(result[0])
        edit.assert_not_called()

    def test_free_intent_respects_live_test_action_ceiling(self):
        with patch.object(b, 'get_room_slot_coordinates', return_value=None) as coordinates:
            b.try_book_slot(None, dict(room='A', start_hour=12, end_hour=13,
                free_horizon_intent=True), self.day, self.tracker, 0,
                remaining_daily_hours=4, max_action_minutes=30, time_prefs={'enabled':False})
        self.assertEqual(coordinates.call_args.args[2:], (12, 12.5))


class ShortNoticeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.now = datetime(2026, 9, 17, 14)
        clock = self.stack.enter_context(patch.object(short_notice, 'datetime', wraps=datetime))
        clock.now.side_effect = lambda: self.now
        self.args = b.build_argument_parser().parse_args(['--headless'])
        self.plan = b.PracticePlan(enabled=True, default_hours=2)
        self.tracker = b.BookingTracker()
        self.tracker.existing_reservation_hours = 6
        self.events = []
        self.details = []
        self.stack.enter_context(patch.multiple(b, PRIORITY_ROOMS=['A'],
            MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2, ALLOW_FRAGMENTED_SESSIONS=True))
        self.stack.enter_context(patch.object(b, 'room_horizon_minutes', return_value=10080))
        self.stack.enter_context(patch.object(b, 'booking_window_dates',
            side_effect=lambda today: (today, today + timedelta(days=1))))
        self.stack.enter_context(patch.object(b, 'load_disabled_dates', return_value=set()))
        self.stack.enter_context(patch.object(b, 'load_time_preferences', return_value={'enabled':False}))
        self.stack.enter_context(patch.object(b, 'load_booking_strategy_preferences',
            return_value=SimpleNamespace(daily_planning=DailyPlanningPreferences())))
        self.stack.enter_context(patch.object(short_notice, 'refresh_quota_balances'))
        self.holds = self.stack.enter_context(patch.object(b, 'refresh_extension_capacity_holds',
            return_value=({}, {}, ())))
        self.open = self.stack.enter_context(patch.object(b, 'open_practice_room_overview'))
        self.navigate = self.stack.enter_context(patch.object(b, 'navigate_to_day'))
        self.stack.enter_context(patch.object(b, 'wait_for_practice_room_grid'))
        self.grid = self.stack.enter_context(patch.object(b, 'get_available_slots',
            return_value=[{'room':'A', 'slots':[{'startHour':16, 'endHour':21}]}]))
        self.attempt = self.stack.enter_context(patch.object(b, 'attempt_booking_with_room_fallback', side_effect=self.book))
        self.stack.enter_context(patch.object(b, 'scan_agenda', side_effect=self.scan))

    def book(self, page, slot, day, tracker, *args, **kwargs):
        self.events.append((day, slot['start_hour'], slot['end_hour']))
        return dict(date=str(day), room='A', start=b.time_text(round(slot['start_hour']*60)),
                    end=b.time_text(round(slot['end_hour']*60))), slot

    def scan(self, page, tracker, *args, **kwargs):
        tracker.existing_reservation_hours = 6
        for day, start, end in self.events:
            tracker.add_existing_event(day, start, end, is_reservation=True, room='A')
        return len(self.events), []

    def run_pass(self, **kwargs):
        return short_notice.run_short_notice_pass(b, None, {}, self.plan, self.args,
                                                  self.tracker, 0, self.details, **kwargs)

    def test_full_quota_books_once_then_stops_at_daily_target(self):
        count, tracker = self.run_pass()
        self.assertEqual(count, 1)
        self.assertEqual(tracker.get_hours_for_day(self.now.date()), 2)
        self.assertEqual(self.attempt.call_count, 1)
        self.assertEqual(len(self.details), 1)

    def test_pending_extensions_keep_priority_over_short_notice_creates(self):
        self.holds.return_value = ({str(self.now.date()):120}, {}, ())
        self.assertEqual(self.run_pass()[0], 0)
        self.attempt.assert_not_called()

    def test_scheduled_edge_run_still_checks_free_horizon_when_quota_full(self):
        self.args.target_time = '14:15'
        self.args.scheduled = True
        self.assertEqual(self.run_pass()[0], 1)

    def test_available_advance_quota_preserves_edge_priority_then_checks_free(self):
        self.tracker.existing_reservation_hours = 0
        self.args.target_time = '14:15'
        self.assertEqual(self.run_pass()[0], 0)
        self.attempt.assert_not_called()
        self.assertEqual(self.run_pass(after_horizon=True)[0], 1)

    def test_prepares_new_free_window_then_rechecks_before_booking(self):
        self.now=self.now.replace(minute=13)
        self.args.scheduled=True
        self.args.target_time='14:15'
        self.plan=b.PracticePlan(enabled=True,default_hours=.5)
        self.grid.return_value=[{'room':'A','slots':[{'startHour':18.75,'endHour':20}]}]
        def wait(boundary,*args,**kwargs):
            self.assertFalse(self.events)
            self.now=boundary
        with patch.object(b,'wait_until_datetime',side_effect=wait) as waiting:
            self.assertEqual(self.run_pass()[0],1)
        waiting.assert_called_once()
        self.assertGreaterEqual(self.grid.call_count,2)
        self.assertEqual(self.events,[(self.now.date(),18.75,19.25)])

    def test_strict_preferred_times_survive_free_horizon(self):
        with patch.object(b,'load_time_preferences',return_value={
                'enabled':True,'strict_mode':True,'start_hour':12,'end_hour':16}):
            self.assertEqual(self.run_pass()[0],0)
        self.attempt.assert_not_called()

    def test_free_window_seed_retains_the_whole_peak_session_intent(self):
        self.now = self.now.replace(hour=7, minute=30)
        self.grid.return_value = [{'room': 'A', 'slots': [{'startHour': 12, 'endHour': 14}]}]
        self.attempt.side_effect = None
        self.attempt.return_value = False, {}
        self.run_pass()
        slot = self.attempt.call_args.args[1]
        self.assertEqual((slot['start_hour'], slot['end_hour']), (12, 13))
        self.assertTrue(slot['free_horizon_intent'])

    def test_free_window_uses_existing_foresight_before_spending_peak_early(self):
        self.now = self.now.replace(hour=7)
        self.grid.return_value = [
            {'room': room, 'slots': [{'startHour': 11.5, 'endHour': 14}]}
            for room in ('A', 'B')]
        with patch.object(b, 'PRIORITY_ROOMS', ['A', 'B']), \
             patch.object(b, 'load_time_preferences', return_value={
                 'enabled': True, 'strict_mode': False, 'start_hour': 12, 'end_hour': 18}):
            self.assertEqual(self.run_pass()[0], 0)
        self.attempt.assert_not_called()

    def test_single_session_setting_can_seed_a_full_session(self):
        self.now = self.now.replace(hour=11, minute=30)
        self.grid.return_value = [{'room': 'A', 'slots': [{'startHour': 16, 'endHour': 18}]}]
        self.attempt.side_effect = None
        self.attempt.return_value = False, {}
        with patch.object(b, 'ALLOW_FRAGMENTED_SESSIONS', False):
            self.run_pass()
        slot = self.attempt.call_args.args[1]
        self.assertEqual((slot['start_hour'], slot['end_hour']), (16, 18))
        self.assertTrue(slot['free_horizon_intent'])

    def test_room_priority_is_retained_with_equal_time_fit(self):
        with patch.object(b,'PRIORITY_ROOMS',['Preferred','Fallback']):
            self.grid.return_value=[{'room':r,'slots':[{'startHour':16,'endHour':18}]} for r in ('Fallback','Preferred')]
            self.assertEqual(self.run_pass()[0],1)
        self.assertEqual(self.attempt.call_args.args[1]['room'],'Preferred')

    def test_soft_preferred_time_keeps_priority_over_a_better_room_outside_it(self):
        self.plan = b.PracticePlan(enabled=True, default_hours=1)
        self.grid.return_value = [
            {'room': 'Preferred', 'slots': [{'startHour': 18, 'endHour': 19}]},
            {'room': 'Fallback', 'slots': [{'startHour': 16, 'endHour': 17}]},
        ]
        with patch.object(b, 'PRIORITY_ROOMS', ['Preferred', 'Fallback']), \
             patch.object(b, 'load_time_preferences', return_value={
                 'enabled': True, 'strict_mode': False, 'start_hour': 12, 'end_hour': 18}):
            self.assertEqual(self.run_pass()[0], 1)
        self.assertEqual(self.attempt.call_args.args[1]['room'], 'Fallback')
        self.assertEqual(self.events[0][1:], (16, 17))

    def test_no_save_rejection_is_not_retried_in_second_pass(self):
        self.attempt.side_effect = None
        self.attempt.return_value = False, {'room':'A'}
        self.assertEqual(self.run_pass()[0], 0)
        self.assertEqual(self.run_pass(after_horizon=True)[0], 0)
        self.assertEqual(self.attempt.call_count, 1)

    def test_scoped_modes_never_add_free_horizon_bookings(self):
        for flag in ('horizon_only','extensions_only','upgrades_only','upgrade_dry_run','plan_only','check_only','agenda_only'):
            with self.subTest(flag=flag):
                setattr(self.args, flag, True)
                self.assertEqual(self.run_pass()[0], 0)
                setattr(self.args, flag, False)
        self.attempt.assert_not_called()

    def test_midnight_uses_verified_navigation_for_next_day(self):
        self.now = self.now.replace(hour=22)
        tomorrow = self.now.date() + timedelta(days=1)
        self.args.only_date = str(tomorrow)
        self.grid.return_value = [{'room':'A','slots':[{'startHour':1,'endHour':4}]}]
        self.assertEqual(self.run_pass()[0], 1)
        self.assertTrue(all(call.args[1] == self.now.date() for call in self.open.call_args_list))
        self.navigate.assert_called_with(None, 1, 0, base_date=self.now.date())
        self.assertEqual(self.events[0], (tomorrow, 1, 3))

    def test_explicit_action_limit_and_disabled_date_prevent_attempt(self):
        self.args.max_actions = 0
        self.assertEqual(self.run_pass()[0], 0)
        self.attempt.assert_not_called()

    def test_unknown_result_propagates_without_retry(self):
        self.attempt.side_effect = b.BookingVerificationError('uncertain Save')
        with self.assertRaises(b.BookingVerificationError):
            self.run_pass()
        self.assertEqual(self.attempt.call_count, 1)


if __name__ == '__main__':
    unittest.main()
