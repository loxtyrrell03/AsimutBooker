"""Execution tests for the real tools with temporary files and a fake CLI only."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app_settings import load_settings
from assistant_tools import AssistantToolError
from assistant_availability import resolve_query, filter_scan
from tests import test_assistant_tools as fixtures


class DatedWindowTests(unittest.TestCase):
    setUp = fixtures.AssistantToolSurfaceTests.setUp
    tearDown = fixtures.AssistantToolSurfaceTests.tearDown
    def test_dated_window_preserves_global_other_dates_and_target(self):
        original = load_settings(self.paths.settings)
        window = {'enabled': True, 'start_time': '12:00', 'end_time': '18:00', 'strict_mode': True}
        self.surface.dispatch('update_booker_preferences', {
            'request_quote': 'Book tomorrow afternoon',
            'date_time_preferences': [{'date': '2026-09-01', 'window': window}],
        }, user_request='Book tomorrow afternoon')
        saved = load_settings(self.paths.settings)
        self.assertEqual(saved, {**original, 'date_time_preferences': {'2026-09-01': window}})
        self.surface.dispatch('update_booker_preferences', {
            'request_quote': 'Restore usual times tomorrow',
            'date_time_preferences': [{'date': '2026-09-01', 'window': None}],
        }, user_request='Restore usual times tomorrow')
        self.assertEqual(load_settings(self.paths.settings)['date_time_preferences'], {})
        self.assertEqual(self.commands, [])

    def test_invalid_dated_window_rolls_back_entire_patch(self):
        before = self.paths.settings.read_bytes()
        for entries in (
            [{'date': '2026-09-01', 'window': {'enabled': True}}],
            [{'date': '2026-09-01', 'window': None}] * 2,
            [{'date': '2026-09-01', 'window': {'enabled': True, 'strict_mode': True, 'start_time': '18:00', 'end_time': '12:00'}}],
        ):
            with self.subTest(entries=entries), self.assertRaises(AssistantToolError):
                self.surface.dispatch('update_booker_preferences', {
                    'request_quote': 'Book tomorrow afternoon',
                    'practice_plan': {'date_overrides': [{'date': '2026-09-01', 'hours': 3}]},
                    'date_time_preferences': entries,
                }, user_request='Book tomorrow afternoon')
            self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_availability_tool_uses_owned_scan_output_without_settings_writes(self):
        before = self.paths.settings.read_bytes()
        now = datetime.now(ZoneInfo('Europe/London'))
        day = (now + timedelta(days=1)).date().isoformat()
        def runner(flags, **kwargs):
            self.commands.append(flags)
            output = Path(flags[flags.index('--output') + 1])
            output.write_text(json.dumps({'observed_at': now.isoformat(), 'scanned_dates': [day],
                'rows': [{'date': day, 'room': 'B0.29', 'start': '12:00', 'end': '16:00'}]}), encoding='utf-8')
            return {'exit_code': 0}
        self.surface._command_runner = runner
        result = self.surface.dispatch('find_availability', {'date': day, 'start_time': '13:00', 'end_time': '14:00'})
        self.assertEqual(result['rows'][0]['minutes'], 60)
        self.assertEqual(result['rows'][0]['start_time'], '13:00')
        self.assertEqual(self.paths.settings.read_bytes(), before)
        self.assertFalse(Path(self.commands[0][1]).exists())


class AvailabilityWindowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 31, 10, 7, tzinfo=ZoneInfo('Europe/London'))
        self.scan = {'observed_at': self.now.isoformat(), 'scanned_dates': ['2026-08-31'],
            'rows': [{'date': '2026-08-31', 'room': 'B0.29', 'start': '09:00', 'end': '12:00'},
                     {'date': '2026-08-31', 'room': 'B1.09', 'start': '11:15', 'end': '12:00'}]}

    def test_rolling_window_clips_and_rounds_future_start(self):
        query = resolve_query({'next_minutes': 60}, self.now)
        result = filter_scan(self.scan, query, now=self.now)
        self.assertEqual(result['rows'], [{'date': '2026-08-31', 'room': 'B0.29',
            'start_time': '10:15', 'end_time': '11:07', 'minutes': 52}])
        self.assertEqual(filter_scan(self.scan, {**query, 'minimum_minutes': 60}, now=self.now)['rows'], [])

    def test_missing_date_is_unknown_but_scanned_empty_is_empty(self):
        query = resolve_query({'date': '2026-09-01', 'start_time': '12:00', 'end_time': '18:00'}, self.now)
        result = filter_scan(self.scan, query, now=self.now)
        self.assertEqual(result['unavailable_dates'], ['2026-09-01'])
        self.scan['scanned_dates'].append('2026-09-01')
        self.assertEqual(filter_scan(self.scan, query, now=self.now)['unavailable_dates'], [])

    def test_midnight_window_and_elapsed_scan_time(self):
        late = self.now.replace(hour=23, minute=30)
        query = resolve_query({'next_minutes': 60}, late)
        self.assertEqual(query['dates'], ['2026-08-31', '2026-09-01'])
        query = resolve_query({'next_minutes': 5}, self.now)
        self.assertEqual(filter_scan(self.scan, query, now=self.now + timedelta(minutes=6))['rows'], [])

    def test_bad_queries_and_stale_output_fail_closed(self):
        for args in ({}, {'next_minutes': True}, {'next_minutes': 60, 'date': '2026-09-01'},
                     {'next_minutes': 60, 'minimum_minutes': 0},
                     {'date': '2026-09-01', 'start_time': '18:00', 'end_time': '12:00'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolve_query(args, self.now)
        with self.assertRaises(ValueError):
            filter_scan(self.scan, resolve_query({'next_minutes': 60}, self.now), now=self.now + timedelta(hours=1))

    def test_scan_worker_is_strictly_read_only(self):
        import tempfile
        from phone_operation_worker import execute
        from operation_control import report_available_gaps
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            def booker(flags):
                self.assertEqual(flags, ['--headless', '--check-only'])
                report_available_gaps(self.now.date(), [{'room': 'B0.29', 'slots': [{'startHour': 10, 'endHour': 12}]}])
                return 0
            result = execute('scan', {'dates': ['2026-08-31']}, root, root=root, booker_main=booker)
            self.assertEqual(result['state'], 'completed')
            self.assertEqual(result['scan']['rows'][0]['minutes'], 120)
