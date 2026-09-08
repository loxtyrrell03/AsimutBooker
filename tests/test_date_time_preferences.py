import contextlib
import io
import json
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import book_week as b
from date_time_preferences import apply_date_time_preferences, resolve_time_preferences, load_date_time_preferences
from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged


class DateTimePreferencesTests(unittest.TestCase):
    day = date(2026, 9, 15)
    window = {'enabled': True, 'start_time': '18:00', 'end_time': '20:00', 'strict_mode': True}

    def test_re_resolving_dates_uses_own_override_or_original_default(self):
        settings = {'time_preferences': {'enabled': True, 'preset': 'morning'},
                    'date_time_preferences': {'2026-09-15': self.window, '2026-09-16': self.window | {'enabled': False}}}
        prefs = b.load_time_preferences(settings)
        evening = resolve_time_preferences(prefs, self.day)
        self.assertEqual((evening['start_hour'], evening['end_hour']), (18, 20))
        any_time = resolve_time_preferences(evening, date(2026, 9, 16))
        self.assertFalse(any_time['enabled'])
        default = resolve_time_preferences(any_time, date(2026, 9, 17))
        self.assertEqual((default['start_hour'], default['end_hour']), (7, 12))
        self.assertTrue(default['enabled'])
        apply_date_time_preferences(settings, {'2026-09-15': None})
        self.assertNotIn('2026-09-15', load_date_time_preferences(settings))

    def test_disabled_global_preferences_do_not_disable_a_day_override(self):
        prefs = b.load_time_preferences({'date_time_preferences': {'2026-09-15': self.window}})
        self.assertTrue(resolve_time_preferences(prefs, self.day)['enabled'])
        self.assertFalse(resolve_time_preferences(prefs, date(2026, 9, 17))['enabled'])

    def test_planner_filters_each_date_including_soft_day_override(self):
        prefs = b.load_time_preferences({'date_time_preferences': {'2026-09-15': self.window}})
        grid = [{'room': 'B0.29', 'slots': [{'startHour': 8, 'endHour': 22}]}]
        with patch.multiple(b, PRIORITY_ROOMS=['B0.29'], ROOM_HORIZON_MINUTES={'B0.29': 7 * 1440}), contextlib.redirect_stdout(io.StringIO()):
            items = b.build_day_booking_opportunities(grid, self.day, b.BookingTracker(), prefs,
                    b.DailyPlanningPreferences(), now=datetime(2026, 9, 8, 22), remaining_daily_hours=3)
            self.assertTrue(items)
            self.assertTrue(all(item.start_minutes >= 1080 and item.end_minutes <= 1200 for item in items))
            defaults = b.build_day_booking_opportunities(grid, date(2026, 9, 14), b.BookingTracker(), prefs,
                    b.DailyPlanningPreferences(), now=datetime(2026, 9, 8, 22), remaining_daily_hours=3)
            self.assertTrue(any(item.start_minutes < 1080 for item in defaults))

    def test_horizon_save_and_extension_reject_outside_day_window(self):
        prefs = b.load_time_preferences({'date_time_preferences': {'2026-09-15': self.window}})
        slot = {'room': 'B0.29', 'start_hour': 8, 'end_hour': 8.5, 'duration': 30, 'bookable_from': datetime(2026, 9, 8, 8), 'horizon_minutes': 7 * 1440, 'booking_minutes': 30}
        booking = {'room': 'B0.29', 'date': self.day.isoformat(), 'startTime': '08:00',
                   'endTime': '08:30', 'target_end': '10:00', 'created_at': '2026-09-08T08:30:00'}
        with patch.multiple(b, ROOM_HORIZON_MINUTES={'B0.29': 7 * 1440}, SITE_CLOCK_OFFSET_BOUNDS=(0, 0)), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(b.try_horizon_snipe(object(), slot, self.day, b.BookingTracker(), 7, time_prefs=prefs))
            self.assertFalse(b.try_extend_booking(object(), booking, time_prefs=prefs)[0])

    def test_new_controls_are_atomic_revision_checked_and_guard_prepared_save(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.json'
            path.write_text('{}')
            before = read_phone_preferences(path)
            changes = {'booking_strategy': {'reverse_date_order': True}, 'date_time_preferences': {'2026-09-15': self.window}}
            saved = save_phone_preferences({'revision': before['revision'], 'changes': changes}, path)
            self.assertTrue(saved['booking_strategy']['reverse_date_order'])
            with self.assertRaises(PreferenceConflict):
                save_phone_preferences({'revision': before['revision'], 'changes': changes}, path)
            data = path.read_bytes()
            with self.assertRaises(ValueError):
                save_phone_preferences({'revision': saved['revision'], 'changes': {'booking_strategy': {'reverse_date_order': False}, 'date_time_preferences': {'2026-09-15': self.window | {'end_time': '10:00'}}}}, path)
            self.assertEqual(path.read_bytes(), data)
            original = json.loads(data)
            with booking_preference_run(path, original):
                save_phone_preferences({'revision': saved['revision'], 'changes': {'date_time_preferences': {'2026-09-15': None}}}, path)
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail('No Save after a date preference changes')

    def test_invalid_or_dormant_windows_fail_closed(self):
        for value in [self.window | {'enabled': 1}, self.window | {'start_time': '25:00'}, self.window | {'start_time': '18:01'}, self.window | {'end_time': '17:00'}, self.window | {'extra': True}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                b.load_time_preferences({'date_time_preferences': {'2026-09-15': value}})


if __name__ == '__main__': unittest.main()
