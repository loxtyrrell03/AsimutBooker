from datetime import date, datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import book_week as b
from app_settings import load_settings, SettingsError
from assistant_context import _validate_time_preferences
from assistant_tools import BookerToolSurface
from daily_planner import soft_time_weight
from date_time_preferences import resolve_time_preferences, validate_window
from gui import validate_time_preferences_section
from phone_preferences import read_phone_preferences, save_phone_preferences


class OpeningTimePreferencesTests(unittest.TestCase):
    def settings(self, start='12:00', end='rooms_closed'):
        settings = {'unrelated': {'keep': True}}
        BookerToolSurface._apply_time_preferences(settings, {
            'enabled': True, 'start_time': start, 'end_time': end, 'strict_mode': False})
        return settings

    def test_round_trip_all_consumers_and_preserve_other_settings(self):
        for start, end in [('12:00', 'rooms_closed'), ('rooms_open', '18:00'),
                           ('rooms_open', 'rooms_closed')]:
            with self.subTest(start=start, end=end), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'settings.json'
                path.write_text('{"unrelated": {"keep": true}}')
                original = read_phone_preferences(path)
                saved = save_phone_preferences({'revision': original['revision'], 'changes': {
                    'time_preferences': {'enabled': True, 'start_time': start, 'end_time': end}}}, path)
                settings = load_settings(path)
                self.assertEqual(saved, read_phone_preferences(path))
                self.assertEqual(saved['time_preferences']['start_time'], start)
                self.assertEqual(saved['time_preferences']['end_time'], end)
                self.assertEqual(settings['unrelated'], {'keep': True})
                self.assertEqual(validate_time_preferences_section(settings), settings['time_preferences'])
                context = _validate_time_preferences(settings)
                self.assertEqual((context['start_time'], context['end_time']), (start, end))
                bounds = b.load_time_preferences(settings)
                self.assertEqual(bounds['start_hour'], 0 if start == 'rooms_open' else 12)
                self.assertEqual(bounds['end_hour'], 24 if end == 'rooms_closed' else 18)

    def test_fixed_time_and_preset_clear_or_ignore_dormant_boundaries(self):
        settings = self.settings('rooms_open')
        BookerToolSurface._apply_time_preferences(settings, {'start_time': '12:00', 'end_time': '18:00'})
        self.assertNotIn('start_boundary', settings['time_preferences'])
        self.assertNotIn('end_boundary', settings['time_preferences'])
        settings = self.settings()
        BookerToolSurface._apply_time_preferences(settings, {'preset': 'morning'})
        bounds = b.load_time_preferences(settings)
        self.assertEqual((bounds['start_hour'], bounds['end_hour']), (7, 12))
        BookerToolSurface._apply_time_preferences(settings, {'preset': 'custom'})
        self.assertEqual(b.load_time_preferences(settings)['end_hour'], 24)

    def test_date_override_does_not_leak_into_following_days(self):
        settings = self.settings()
        settings['date_time_preferences'] = {'2030-09-23': {
            'enabled': True, 'start_time': 'rooms_open', 'end_time': '13:00', 'strict_mode': True}}
        default = b.load_time_preferences(settings)
        special = resolve_time_preferences(default, date(2030, 9, 23))
        following = resolve_time_preferences(special, date(2030, 9, 24))
        self.assertEqual((special['start_hour'], special['end_hour']), (0, 13))
        self.assertEqual((following['start_hour'], following['end_hour']), (12, 24))
        self.assertFalse(following['strict_mode'])

    def test_invalid_tokens_and_malformed_dormant_clocks_rejected(self):
        for start, end in [('rooms_closed', 'rooms_closed'), ('rooms_open', 'rooms_open'),
                           ('tomorrow', '18:00'), ('20:00', '18:00')]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                validate_window({'enabled': True, 'start_time': start, 'end_time': end, 'strict_mode': False})
        for field, value in [('end_boundary', 'midnight'), ('start_boundary', None),
                             ('custom_end_hour', True), ('custom_start_min', 60)]:
            settings = self.settings()
            settings['time_preferences'][field] = value
            for validate in (b.load_time_preferences, validate_time_preferences_section, _validate_time_preferences):
                with self.subTest(field=field, validator=validate.__name__), self.assertRaises(SettingsError):
                    validate(settings)

    def test_evening_is_fully_preferred_but_before_noon_remains_penalized(self):
        prefs = b.load_time_preferences(self.settings())
        window = b._soft_time_window(prefs)
        self.assertEqual(soft_time_weight(18 * 60, window, 19 * 60), 1)
        self.assertLess(soft_time_weight(11 * 60, window, 12 * 60), 1)

    def test_room_closing_and_opening_still_bound_opportunities(self):
        day = date(2030, 9, 23)
        prefs = b.load_time_preferences(self.settings('rooms_open'))
        # Different/exceptional closing times come from actual room grids, not
        # a global hard-coded college closing hour. Fully closed has no gaps.
        for opening, closing in [(7.5, 22.25), (9, 18), (12, 14), (7, 23)]:
            with self.subTest(opening=opening, closing=closing), patch.multiple(
                b, PRIORITY_ROOMS=['A'], ROOM_HORIZON_MINUTES={'A': 7 * 1440},
                MINIMUM_BLOCK_MINUTES=30, MAX_BOOKING_HOURS=2):
                opportunities = b.build_day_booking_opportunities(
                    [{'room': 'A', 'slots': [{'startHour': opening, 'endHour': closing}]}],
                    day, b.BookingTracker(), prefs, b.DailyPlanningPreferences(),
                    now=datetime(2030, 9, 22, 12), remaining_daily_hours=4)
                self.assertTrue(opportunities)
                self.assertTrue(all(opening * 60 <= item.start_minutes < item.end_minutes <= closing * 60
                                    for item in opportunities))
                self.assertEqual(b.build_day_booking_opportunities(
                    [], day, b.BookingTracker(), prefs, b.DailyPlanningPreferences(),
                    now=datetime(2030, 9, 22, 12), remaining_daily_hours=4), [])


if __name__ == '__main__':
    unittest.main()
