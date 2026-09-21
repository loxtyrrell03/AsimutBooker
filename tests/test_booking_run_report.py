import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import book_week as b
import booking_run_report as report


class RunReportTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.multiple(b, MAX_ROLLING_QUOTA_HOURS=6, MAX_PEAK_HOURS=1))
        self.token = report.start(b, {}, b.PracticePlan(enabled=True, default_hours=4))
        self.addCleanup(report.finish, self.token)
        self.day = date(2026, 9, 21)
        self.tracker = b.BookingTracker()
        self.tracker.add_existing_event(self.day, 12, 14, is_reservation=True, room='Weston')
        self.tracker.add_existing_event(self.day, 16, 17, is_reservation=True, room='Weston')
        self.tracker.live_quota_minutes = 0
        self.tracker.quota_observed_hours = 3
        report.observe(self.tracker)

    def test_real_exhausted_quota_and_grandfathered_peak_are_separate(self):
        text = '\n'.join(report.lines(now=datetime(2026, 9, 21, 10)))
        self.assertIn('3h confirmed / 4h target; 1h still needed', text)
        self.assertIn('advance credit: 0m', text)
        self.assertIn('peak use: 2h / 1h limit', text)
        self.assertIn('No further peak time', text)

    def test_agenda_only_upgrade_refresh_does_not_erase_live_quota_source(self):
        fresh = b.BookingTracker()
        report.observe(fresh)
        self.assertIn('last live ASIMUT check', '\n'.join(report.lines(now=datetime(2026, 9, 21, 10))))

    def test_live_peak_cap_does_not_hide_actual_over_limit_agenda_minutes(self):
        self.tracker.live_peak_minutes[str(self.day)] = 0
        self.tracker.peak_observed_minutes = dict(self.tracker.peak_hours_by_day)
        self.assertEqual(self.tracker.get_peak_used_for_day(self.day), 60)
        for hour in (11, 15):
            text='\n'.join(report.lines(now=datetime(2026,9,21,hour)))
            self.assertIn('peak use: 2h / 1h limit', text)
            self.assertIn('No further peak time', text)

    def test_live_peak_refusal_remains_visible_when_local_agenda_usage_is_lower(self):
        tracker=b.BookingTracker()
        tracker.live_peak_minutes[str(self.day)]=0
        report.observe(tracker)
        text='\n'.join(report.lines(now=datetime(2026,9,21,11)))
        self.assertIn('peak use: 0m / 1h limit',text)
        self.assertIn('No further peak time',text)

    def test_unknown_credit_is_never_labelled_live(self):
        nested = report.start(b, {}, b.PracticePlan())
        try:
            report.observe(b.BookingTracker())
            text = '\n'.join(report.lines(now=datetime(2026, 9, 21, 10)))
            self.assertIn('Estimated advance credit', text)
            self.assertIn('not verified', text)
        finally:
            report.finish(nested)

    def test_wait_message_has_exact_seed_opening_and_no_invented_booking(self):
        primary = SimpleNamespace(room='B1.16', start_time='16:30', end_time='17:30',
            unlock_at=datetime(2026, 9, 21, 12), initial_minutes=30)
        plan = SimpleNamespace(primary=primary, reason='Best visible opportunity')
        option = SimpleNamespace(room='B1.16', start_text='16:30', room_priority=5)
        report.day_plan(self.day, plan, [option], SimpleNamespace(priority_mode='time_first'), now=datetime(2026, 9, 21, 10))
        text = '\n'.join(report.lines(now=datetime(2026, 9, 21, 10)))
        self.assertIn('First 30m eligible Mon 21 Sep 12:00', text)
        self.assertIn('planned, not booked', text)
        self.assertIn('No eligible higher-ranked option in this scan', text)

    def test_confirmation_matches_actual_room_and_keeps_tail_unconfirmed(self):
        report.choice(self.day, 'Weston', 960, 1080, 'advance allocation')
        report.choice(self.day, 'Corus', 960, 1080, 'Same-time fallback after Weston could not be booked.')
        receipt = dict(date=str(self.day), room='Corus', start='16:00', end='16:30', kind='create')
        text = report.confirmation(receipt)
        self.assertIn('fallback after Weston', text)
        self.assertNotIn('advance allocation', text)
        self.assertIn('extend to 18:00; that extra time is not booked yet', text)
        self.assertNotIn('Why:', report.confirmation({**receipt, 'room':'Other'}))

    def test_higher_room_comparison_uses_room_rank_and_distinct_rooms(self):
        primary=SimpleNamespace(room='Other',start_time='16:30',end_time='17:30',
            unlock_at=datetime(2026,9,21,12),initial_minutes=30)
        def opportunity(room, rank, start):
            return SimpleNamespace(room=room,room_priority=rank,start_text=start,end_text='20:00',
                unlock_at=datetime(2026,9,21,14),initial_minutes=30)
        options=[opportunity('Other',3,'16:30'),opportunity('B0.11',2,'18:00'),
                 opportunity('Weston',0,'19:00'),opportunity('Weston',0,'19:15'),
                 opportunity('Corus',1,'19:00')]
        report.day_plan(self.day,SimpleNamespace(primary=primary,reason='Whole-day choice'),options,
                        SimpleNamespace(priority_mode='time_first'),now=datetime(2026,9,21,10))
        text=report.lines(now=datetime(2026,9,21,10))[-1]
        self.assertIn('Weston 19:00',text)
        self.assertIn('Corus 19:00',text)
        self.assertNotIn('Weston 19:15',text)
        self.assertNotIn('B0.11',text)
        self.assertIn('first 30m from 14:00',text)

    def test_full_utf8_history_and_bounded_push(self):
        report.note('long', 'Room ' + '\u00e9'*5000)
        with tempfile.TemporaryDirectory() as directory, patch.object(b, 'history_file', Path(directory)/'history.json'), patch.object(b, 'send_notification') as send:
            b.save_history(0, 12, [])
            entry=json.loads(b.history_file.read_text(encoding="utf-8"))['runs'][0]
            self.assertIn('\u00e9'*5000, '\n'.join(entry['explanation']))
            self.assertLessEqual(len(send.call_args.args[1].encode('utf-8')),3700)
            self.assertIn('local booking-history file', send.call_args.args[1])

    def test_quiet_run_keeps_explanation_in_history_without_push(self):
        report.note('reason', 'Waiting for the 12:00 opening.')
        with tempfile.TemporaryDirectory() as directory, patch.object(b, 'history_file', Path(directory)/'history.json'), patch.object(b, 'send_notification') as send:
            b.save_history(0,12,[],notify=False)
            self.assertIn('12:00 opening',json.loads(b.history_file.read_text(encoding="utf-8"))['runs'][0]['details'])
            send.assert_not_called()

    def test_short_detail_deduplicates_against_verified_receipt(self):
        detail='2026-09-21 Corus Recital Room 16:00-16:30'
        formatted=b.format_booking_notification_detail('2026-09-21 Corus Recital Room 16:00 16:30 30')
        with tempfile.TemporaryDirectory() as directory, patch.object(b, 'history_file', Path(directory)/'history.json'), patch.object(b, 'send_notification') as send, patch.object(b, '_notified_booking_details', {formatted}):
            b.save_history(1,12,[detail])
            send.assert_not_called()

    def test_no_report_leaks_between_runs(self):
        report.note('only-first', 'Private first-run reason')
        token=report.start(b,{},None)
        try:
            self.assertEqual(report.lines(), [])
        finally:
            report.finish(token)

    def test_preferences_reloaded_after_lock_wait_update_report_target(self):
        report.preferences({}, b.PracticePlan(enabled=True, default_hours=5))
        self.assertIn('3h confirmed / 5h target', '\n'.join(report.lines(now=datetime(2026,9,21,10))))

    def test_sign_in_and_quota_policy_failures_reach_failure_notifications(self):
        for error, expected in [(b.AutonomousLoginError('Session expired'),2),
                                (b.QuotaPolicyError('Quota response unreadable'),4)]:
            with self.subTest(error=type(error).__name__), patch.object(b,'_load_and_validate_runtime_settings',return_value=({},None,None)), patch.object(b,'SingleInstanceLock') as lock, patch.object(b,'run_booking',side_effect=error), patch.object(b,'save_history') as history:
                lock.return_value.acquire.return_value=True
                self.assertEqual(b.main(['--headless']),expected)
                self.assertEqual(history.call_args.kwargs['outcome'],'failed')
                self.assertIsNot(history.call_args.kwargs.get('notify'),False)

    def test_actual_short_notice_wait_records_selected_room_and_opening(self):
        from tests.test_new_booking_quotas import ShortNoticeIntegrationTests
        fixture = ShortNoticeIntegrationTests()
        fixture.setUp()
        try:
            fixture.grid.return_value=[{'room':'A','slots':[{'startHour':18.75,'endHour':20}]}]
            count, _ = fixture.run_pass()
            self.assertEqual(count,0)
            text='\n'.join(report.lines(now=fixture.now))
            self.assertIn('A 18:45-',text)
            self.assertIn('14:15',text)
            self.assertIn('planned, not booked',text)
            fixture.attempt.assert_not_called()
        finally:
            fixture.doCleanups()

    def test_real_prepared_boundary_refusal_reaches_explanation(self):
        from tests.test_free_horizon_preparation import FreeHorizonPreparationTests
        fixture=FreeHorizonPreparationTests()
        fixture.setUp()
        try:
            result,_,receipts,_,_,_=fixture.execute(allowed=False)
            self.assertFalse(result)
            self.assertEqual(receipts,0)
            self.assertIn('boundary check refused the booking: test; no Save attempted',
                          '\n'.join(report.lines(now=datetime(2026,9,21,11,30))))
        finally:
            fixture.doCleanups()

    def test_transport_sends_readable_utf8_with_existing_topic_and_priority(self):
        response=SimpleNamespace(status=200)
        with patch.object(b,'NTFY_ENABLED',True), patch.object(b,'NTFY_TOPIC','synthetic-test-topic'), patch.object(b.urllib.request,'urlopen',return_value=contextlib.nullcontext(response)) as send:
            b.send_notification('AsimutBooker: no booking changes','Corus: waiting until 12:00. \u00e9',priority='low')
            request=send.call_args.args[0]
            self.assertEqual(request.full_url,'https://ntfy.sh/synthetic-test-topic')
            self.assertEqual(request.data.decode('utf-8'),'Corus: waiting until 12:00. \u00e9')
            self.assertEqual(request.get_header('Priority'),'low')


if __name__ == '__main__':
    unittest.main()
