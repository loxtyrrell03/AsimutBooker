import contextlib
import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
import book_week as b
from app_settings import SettingsError, save_settings
from booking_rules import load_booking_rules, apply_booking_rules
from booking_plan import booking_plan_fingerprint
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
from phone_preferences import read_phone_preferences, save_phone_preferences

class BookingRulesTests(unittest.TestCase):
    def test_current_peak_cutoff_migrates_only_the_old_new_preset(self):
        old = dict(preset='new', rolling_quota_hours=6, peak_quota_minutes=60,
            free_horizon_minutes=300, peak_start_minutes=540, peak_end_minutes=960,
            free_horizon_overrides_peak=True)
        for raw in ({}, {'preset':'new'}, old):
            with self.subTest(raw=raw):
                self.assertEqual(load_booking_rules({'booking_rules':raw}).peak_end_minutes, 900)
        self.assertEqual(old['peak_end_minutes'], 960)  # Loading does not write owner data.
        self.assertTrue(load_booking_rules({'booking_rules':old}).free_horizon_overrides_peak)
        self.assertEqual(load_booking_rules({'booking_rules':old | {'preset':'custom'}}).peak_end_minutes, 960)
        self.assertEqual(load_booking_rules({'booking_rules':{'preset':'legacy'}}).peak_end_minutes, 960)
        with self.assertRaises(SettingsError):
            load_booking_rules({'booking_rules':old | {'peak_quota_minutes':120}})

    def test_old_phone_preset_payload_saves_current_rule_without_changing_practice_times(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            settings = {'booking_strategy':{'daily_planning':{'preferred_peak_start':'12:00','preferred_peak_end':'16:00'}},
                'booking_rules':{'preset':'new','peak_end_minutes':960,'free_horizon_overrides_peak':True}}
            save_settings(settings,path)
            before = read_phone_preferences(path)
            after = save_phone_preferences({'revision':before['revision'],
                'changes':{'booking_rules':settings['booking_rules']}},path)
            self.assertEqual(after['booking_rules']['peak_end_minutes'],900)
            self.assertTrue(after['booking_rules']['free_horizon_overrides_peak'])
            from app_settings import load_settings
            self.assertEqual(load_settings(path)['booking_strategy'],settings['booking_strategy'])
            self.assertEqual(after['booking_strategy']['daily_planning']['preferred_peak_end'],'16:00')

    def test_exhausted_peak_does_not_block_advance_practice_after_three(self):
        names = ('MAX_PEAK_HOURS','MAX_ROLLING_QUOTA_HOURS','FREE_HORIZON_MINUTES',
            'FREE_HORIZON_OVERRIDES_PEAK','PEAK_START','PEAK_END')
        with patch.multiple(b, **{name:getattr(b,name) for name in names}), contextlib.redirect_stdout(io.StringIO()):
            b.install_booking_rules({'booking_rules':{'preset':'new','peak_end_minutes':960}})
            future = date(2099,9,21)
            tracker = b.BookingTracker()
            tracker.add_existing_event(future,12,13,is_reservation=True,room='A')
            self.assertFalse(tracker.can_book('B',future,14.5,30)[0])
            self.assertTrue(tracker.can_book('B',future,15,120)[0])

    def test_presets_switch_and_custom_is_validated(self):
        settings = {'keep':True}
        apply_booking_rules(settings, {'preset':'legacy'})
        self.assertEqual(load_booking_rules(settings).peak_quota_minutes, 120)
        apply_booking_rules(settings, {'preset':'custom', 'rolling_quota_hours':10, 'peak_quota_minutes':90})
        self.assertEqual(load_booking_rules(settings).rolling_quota_hours, 10)
        apply_booking_rules(settings, {'preset':'new'})
        self.assertEqual(load_booking_rules(settings).rolling_quota_hours, 6)
        self.assertTrue(settings['keep'])
        for patch_value in ({'preset':'old'}, {'preset':'new','peak_quota_minutes':120},
                {'preset':'custom','free_horizon_minutes':-1},
                {'preset':'custom','rolling_quota_hours':float('nan')},
                {'preset':'custom','peak_start_minutes':960,'peak_end_minutes':540}):
            with self.subTest(value=patch_value), self.assertRaises(SettingsError):
                apply_booking_rules(settings, patch_value)

    def test_worker_switch_changes_all_quota_paths_and_still_obeys_live_balance(self):
        names = ('MAX_PEAK_HOURS','MAX_ROLLING_QUOTA_HOURS','FREE_HORIZON_MINUTES','PEAK_START','PEAK_END')
        with patch.multiple(b, **{name:getattr(b,name) for name in names}), contextlib.redirect_stdout(io.StringIO()):
            b.install_booking_rules({'booking_rules':{'preset':'legacy'}})
            tracker = b.BookingTracker()
            future = date(2099,9,21)
            self.assertTrue(tracker.can_book('A',future,12,120)[0])
            tracker.live_quota_minutes = 30
            self.assertFalse(tracker.can_book('A',future,16,120)[0])
            b.install_booking_rules({'booking_rules':{'preset':'new'}})
            self.assertFalse(b.BookingTracker().can_book('A',future,12,120)[0])
            b.install_booking_rules({'booking_rules':{'preset':'custom','free_horizon_minutes':60}})
            tracker.live_quota_minutes = 0
            self.assertEqual(tracker.booking_capacity_hours(date(2026,9,20),15.5,
                now=datetime(2026,9,20,14)), 0)

    def test_switch_invalidates_plan_and_stops_inflight_save(self):
        before = {'booking_rules':{'preset':'new'}}
        after = {'booking_rules':{'preset':'legacy'}}
        self.assertNotEqual(booking_plan_fingerprint(before),booking_plan_fingerprint(after))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            save_settings(before,path)
            with booking_preference_run(path,before):
                save_settings(after,path)
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail('Save should be blocked')

    def test_phone_preset_save_preserves_unrelated_fields_and_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'settings.json'
            save_settings({'keep':True},path)
            before=read_phone_preferences(path)
            after=save_phone_preferences({'revision':before['revision'],
                'changes':{'booking_rules':{'preset':'legacy'}}},path)
            self.assertEqual(after['booking_rules']['peak_quota_minutes'],120)
            self.assertNotEqual(before['revision'],after['revision'])

    def test_policy_failure_is_published_in_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'settings.json'
            save_settings({},path)
            self._check_policy_failure(path)

    def _check_policy_failure(self, path):
        with patch.object(b,'settings_file',path), patch.object(b,'run_report'), \
             patch('preference_runs.kick'), patch.object(b,'install_booking_rules'), \
             patch.object(b,'_load_and_validate_runtime_settings',return_value=({},None,None)), \
             patch.object(b,'SingleInstanceLock'), patch.object(b,'booking_preference_run',return_value=contextlib.nullcontext()), \
             patch.object(b,'run_booking',side_effect=b.RoomCatalogError('ambiguous categories')), \
             patch.object(b,'save_history') as history, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(['--headless']),4)
            self.assertEqual(history.call_args.kwargs['outcome'],'failed')
            self.assertIn('ambiguous categories',history.call_args.args[2][0])

    def test_policy_failure_notification_reports_reason_without_repeating(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(b,'history_file',Path(temp)/'history.json'), \
             patch.object(b,'send_notification') as notify, contextlib.redirect_stdout(io.StringIO()):
            b.save_history(0,0,['Booking paused: ambiguous categories'],outcome='failed')
            notify.assert_called_once()
            self.assertEqual(notify.call_args.args[0], 'AsimutBooker needs attention')
            self.assertIn('Booking paused: ambiguous categories', notify.call_args.args[1])
            b.save_history(0,0,['Booking paused: ambiguous categories'],outcome='failed')
            self.assertEqual(notify.call_count,1)
            b.save_history(0,0,['Booking paused: different failure'],outcome='failed')
            self.assertEqual(notify.call_count,2)
