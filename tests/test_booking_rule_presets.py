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
        with patch.object(b,'_load_and_validate_runtime_settings',return_value=({},None,None)), \
             patch.object(b,'SingleInstanceLock'), patch.object(b,'booking_preference_run',return_value=contextlib.nullcontext()), \
             patch.object(b,'run_booking',side_effect=b.RoomCatalogError('ambiguous categories')), \
             patch.object(b,'save_history') as history, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(['--headless']),4)
            self.assertEqual(history.call_args.kwargs['outcome'],'failed')
            self.assertIn('ambiguous categories',history.call_args.args[2][0])
