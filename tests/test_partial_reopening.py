"""Exact partial reopening uses temporary settings, never real bookings."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app_settings import SettingsError, atomic_write_json, load_settings
from booking_blackouts import validate_reopening
from phone_system import SystemConflict, read_view, run_local_action
from runtime_guard import SingleInstanceAlreadyRunning, SingleInstanceLock


def window(start='10:00', end='14:00', day='2030-10-15'):
    return dict(date=day, start_time=start, end_time=end)


class PartialReopeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='booker-partial-reopen-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'data/settings.json'
        self.settings = dict(rebooking_blackouts=[window(), window('16:00', '17:00'),
            window('10:00', '11:00', '2030-10-16')],
            manual_booking_overrides={'101': dict(date='2030-10-15', room='B0.14',
                                                   startTime='14:00', endTime='15:00')},
            ignored_events=['keep'], extendable_bookings=[{'untouched': 'legacy goal'}],
            observed_reservations={'old': 'unchanged'}, unrelated={'keep': True})
        self.seed()

    def seed(self):
        atomic_write_json(self.path, self.settings)

    def args(self, selection=None):
        doc = read_view('protected', self.root)
        args = dict(revision=doc['revision'], window=window())
        if selection is not None:
            args['selection'] = selection
        return args

    def save(self, selection=None, *, args=None):
        return run_local_action('reopen', args or self.args(selection), self.root)

    def test_middle_prefix_suffix_and_quarter_hour_preserve_exact_complement(self):
        cases = [(window('11:00', '12:00'), [window('10:00', '11:00'), window('12:00', '14:00')]),
                 (window('10:00', '11:00'), [window('11:00', '14:00')]),
                 (window('13:00', '14:00'), [window('10:00', '13:00')]),
                 (window('10:15', '10:30'), [window('10:00', '10:15'), window('10:30', '14:00')])]
        for selected, remainder in cases:
            with self.subTest(selected=selected):
                self.seed()
                result = self.save(selected)
                expected = self.settings | dict(rebooking_blackouts=remainder + self.settings['rebooking_blackouts'][1:])
                self.assertEqual(load_settings(self.path), expected)
                self.assertEqual(result['selection'], selected)
                self.assertEqual(result['window'], window())
                self.assertEqual(result['protected'], read_view('protected', self.root))
                self.assertNotIn('preference_run', load_settings(self.path))

    def test_legacy_omission_and_explicit_full_selection_are_equivalent(self):
        for selected in (None, window()):
            self.seed()
            result = self.save(selected)
            self.assertEqual(load_settings(self.path), self.settings |
                dict(rebooking_blackouts=self.settings['rebooking_blackouts'][1:]))
            self.assertEqual(result['selection'], window())
            self.assertEqual(result['message'], 'This time is available to the automatic Booker again.')

    def test_wrong_date_outside_bounds_empty_and_nonquarter_are_no_write(self):
        cases = [window('09:45', '11:00'), window('13:00', '14:15'), window('14:00', '15:00'),
                 window('10:00', '11:00', '2030-10-16'), window('11:00', '11:00'),
                 window('11:00', '10:00'), window('10:05', '11:00')]
        before = self.path.read_bytes()
        for selected in cases:
            with self.subTest(selected=selected), patch('booking_plan.clear_booking_plan') as clear:
                with self.assertRaises((ValueError, SettingsError)):
                    self.save(selected)
                clear.assert_not_called()
                self.assertEqual(self.path.read_bytes(), before)

    def test_unknown_and_null_fields_rejected_without_writes(self):
        args = self.args(window('11:00', '12:00'))
        invalid = [args | dict(selection=None), args | dict(selection=[]), args | dict(extra='no'),
                   args | dict(selection=args['selection'] | dict(room='any')),
                   args | dict(window=args['window'] | dict(room='any')),
                   args | dict(revision='not-a-revision')]
        before = self.path.read_bytes()
        for value in invalid:
            with self.assertRaises((ValueError, SettingsError)):
                run_local_action('reopen', value, self.root)
            self.assertEqual(self.path.read_bytes(), before)

    def test_original_must_be_exact_displayed_row_even_with_current_revision(self):
        args = self.args(window('11:00', '12:00'))
        args['window'] = window('10:00', '13:00')
        before = self.path.read_bytes()
        with patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SystemConflict):
            self.save(args=args)
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_any_changed_blackout_rejects_stale_revision_and_does_not_clear_cache(self):
        args = self.args(window('11:00', '12:00'))
        settings = deepcopy(self.settings)
        settings['rebooking_blackouts'][-1]['end_time'] = '11:15'
        atomic_write_json(self.path, settings)
        before = self.path.read_bytes()
        with patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SystemConflict):
            self.save(args=args)
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_request_replay_cannot_release_remaining_parts(self):
        args = self.args(window('11:00', '12:00'))
        self.save(args=args)
        before = self.path.read_bytes()
        with self.assertRaises(SystemConflict):
            self.save(args=args)
        self.assertEqual(self.path.read_bytes(), before)

    def test_unrelated_concurrent_settings_are_preserved_by_independent_revision(self):
        args = self.args(window('11:00', '12:00'))
        settings = load_settings(self.path)
        settings['manual_booking_overrides']['102'] = dict(date='2030-10-16', room='B0.15',
                                                           startTime='12:00', endTime='13:00')
        settings['ignored_events'].append('concurrent-choice')
        atomic_write_json(self.path, settings)
        self.save(args=args)
        saved = load_settings(self.path)
        self.assertEqual(saved['manual_booking_overrides'], settings['manual_booking_overrides'])
        self.assertEqual(saved['ignored_events'], settings['ignored_events'])

    def test_ended_and_current_end_reject_but_started_selection_is_allowed(self):
        selected = window('11:00', '12:00')
        # October 15 is still British Summer Time: 12:00 London is 11:00 UTC.
        for now in (datetime(2030, 10, 15, 11, tzinfo=timezone.utc),
                    datetime(2030, 10, 16, tzinfo=timezone.utc)):
            with self.assertRaises(ValueError):
                validate_reopening(window(), selected, now=now)
        original, actual = validate_reopening(window(), selected,
            now=datetime(2030, 10, 15, 10, 30, tzinfo=timezone.utc))
        self.assertEqual((original.to_dict(), actual.to_dict()), (window(), selected))

    def test_dst_nonexistent_and_ambiguous_endpoints_reject_deterministically(self):
        now = datetime(2029, 1, 1, tzinfo=timezone.utc)
        for day in ('2030-03-31', '2030-10-27'):
            for selected in (window('01:00', '02:00', day), window('00:30', '01:30', day)):
                with self.assertRaises(ValueError):
                    validate_reopening(window('00:00', '03:00', day), selected, now=now)
            self.assertEqual(validate_reopening(window('00:00', '03:00', day),
                window('02:00', '03:00', day), now=now)[1].start_time, '02:00')

    def test_expiry_after_validation_is_rechecked_inside_settings_lock(self):
        args = self.args(window('11:00', '12:00'))
        pair = validate_reopening(args['window'], args['selection'])
        before = self.path.read_bytes()
        with patch('booking_blackouts.validate_reopening', side_effect=[pair, ValueError('Selected time ended')]), \
                patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SystemConflict):
            self.save(args=args)
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_revision_is_rechecked_after_runtime_ownership_wait(self):
        args = self.args(window('11:00', '12:00'))
        settings = deepcopy(self.settings)
        settings['rebooking_blackouts'][0]['end_time'] = '14:15'
        @contextmanager
        def competing_change(_path):
            atomic_write_json(self.path, settings)
            yield
        with patch('phone_system.SingleInstanceLock', side_effect=competing_change), self.assertRaises(SystemConflict):
            self.save(args=args)
        self.assertEqual(load_settings(self.path), settings)

    def test_busy_runtime_keeps_settings_and_caches_untouched(self):
        before = self.path.read_bytes()
        with SingleInstanceLock(self.root / 'data/booker-runtime.lock'), \
                patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SingleInstanceAlreadyRunning):
            self.save(window('11:00', '12:00'))
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_blackouts_fail_closed(self):
        args = self.args(window('11:00', '12:00'))
        settings = deepcopy(self.settings)
        settings['rebooking_blackouts'][0]['unknown'] = True
        atomic_write_json(self.path, settings)
        before = self.path.read_bytes()
        with self.assertRaises(SettingsError):
            self.save(args=args)
        self.assertEqual(self.path.read_bytes(), before)

    def test_success_invalidates_both_plans_and_full_save_guard_without_queue(self):
        from booking_preferences_guard import BookingPreferencesChanged, booking_preference_run, booking_save_boundary
        args = self.args(window('11:00', '12:00'))
        with patch('booking_preferences_guard.coordination_run', side_effect=nullcontext), \
                patch('booking_preferences_guard.tempo_save_boundary', side_effect=lambda **_: nullcontext()), \
                booking_preference_run(self.path, self.settings), patch('booking_plan.clear_booking_plan') as clear:
            self.save(args=args)
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail('Previously prepared Save must reject changed protections')
        self.assertEqual([call.args[0].name for call in clear.call_args_list], ['booking_plan.json', 'upgrade_plan.json'])
        self.assertNotIn('preference_run', load_settings(self.path))

    def test_postcommit_cache_failure_retains_exact_saved_complement_for_recovery(self):
        for responses in ([OSError('fixture booking cache error')], [None, OSError('fixture upgrade cache error')]):
            self.seed()
            args = self.args(window('11:00', '12:00'))
            with patch('booking_plan.clear_booking_plan', side_effect=responses), self.assertRaises(OSError):
                self.save(args=args)
            self.assertEqual(read_view('protected', self.root)['windows'],
                [window('10:00', '11:00'), window('12:00', '14:00')] + self.settings['rebooking_blackouts'][1:])

    def test_original_and_selection_input_documents_are_not_mutated(self):
        args = self.args(window('11:00', '12:00'))
        original = deepcopy(args)
        self.save(args=args)
        self.assertEqual(args, original)
