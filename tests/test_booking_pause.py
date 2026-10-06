"""Synthetic shared hold and final-Save races; no live settings or site writes."""
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest import mock
from uuid import uuid4

import book_week as engine
from app_settings import SettingsError, atomic_write_json, load_settings, update_settings
from booking_pause import PauseConflict, pause_document, read_pause, save_pause, assert_booking_unpaused
from booking_preferences_guard import BookingPreferencesChanged, booking_preference_run, booking_save_boundary


class BookingPauseTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'settings.json'
        self.original = {'practice_plan': {'enabled': True, 'default_hours': 4}, 'owner_data': {'keep': 17}}
        atomic_write_json(self.path, self.original)
        self.coordination = mock.patch('booking_preferences_guard.coordination_run', contextlib.nullcontext)
        self.coordination.start()
        self.addCleanup(self.coordination.stop)
        boundary = mock.patch('booking_preferences_guard.tempo_save_boundary', lambda **kw: contextlib.nullcontext())
        boundary.start()
        self.addCleanup(boundary.stop)

    def choice(self, paused, revision=None, request_id=None):
        return dict(paused=paused, revision=revision or read_pause(self.path)['revision'], request_id=request_id or str(uuid4()))

    def test_round_trip_preserves_every_other_key(self):
        self.assertFalse(read_pause(self.path)['paused'])
        paused = save_pause(self.choice(True), self.path)
        self.assertTrue(paused['paused'])
        save_pause(self.choice(False), self.path)
        final = load_settings(self.path)
        self.assertFalse(read_pause(self.path)['paused'])
        self.assertEqual({k: v for k, v in final.items() if k != 'booking_pause'}, self.original)
        self.assertNotIn('preference_run', final)

    def test_same_request_cannot_toggle_twice_or_change_its_choice(self):
        choice = self.choice(True)
        result = save_pause(choice, self.path)
        self.assertEqual(save_pause(choice, self.path), result)
        with self.assertRaises(PauseConflict):
            save_pause(dict(choice, paused=False), self.path)
        self.assertEqual(read_pause(self.path), result)

    def test_stale_client_cannot_unpause_a_newer_pause(self):
        old = self.choice(False)
        save_pause(self.choice(True), self.path)
        with self.assertRaises(PauseConflict):
            save_pause(old, self.path)
        self.assertTrue(read_pause(self.path)['paused'])

    def test_pause_after_preparation_prevents_receipt_and_save(self):
        with booking_preference_run(self.path, self.original):
            save_pause(self.choice(True), self.path)
            click = mock.Mock()
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                click()
            click.assert_not_called()

    def test_pause_then_unpause_still_retires_inflight_run(self):
        with booking_preference_run(self.path, self.original):
            save_pause(self.choice(True), self.path)
            save_pause(self.choice(False), self.path)
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail('Old run must not regain Save permission')
        latest = load_settings(self.path)
        with booking_preference_run(self.path, latest), booking_save_boundary():
            pass

    def test_run_started_paused_cannot_save(self):
        save_pause(self.choice(True), self.path)
        with booking_preference_run(self.path, load_settings(self.path)):
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail('Paused Save')

    def test_pause_waits_for_the_current_save_then_blocks_the_next(self):
        started, finished = Event(), Event()
        choice = self.choice(True)
        errors = []
        def writer():
            started.set()
            try:
                save_pause(choice, self.path)
            except Exception as exc:
                errors.append(exc)
            finished.set()
        with booking_preference_run(self.path, self.original):
            with booking_save_boundary():
                thread = Thread(target=writer)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(.1))
            thread.join(2)
            self.assertFalse(errors)
            self.assertTrue(finished.is_set())
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail('Subsequent Save')

    def test_independent_preferences_are_preserved_between_read_and_pause(self):
        choice = self.choice(True)
        update_settings(lambda s: s.update(disabled_dates=['2030-01-01']), self.path)
        save_pause(choice, self.path)
        self.assertEqual(load_settings(self.path)['disabled_dates'], ['2030-01-01'])

    def test_unknown_or_invalid_pause_never_grants_booking_permission(self):
        for raw in (True, {'paused': 'false', 'generation': 1}, {'paused': False, 'generation': -1}):
            with self.subTest(raw=raw), self.assertRaises(SettingsError):
                assert_booking_unpaused({'booking_pause': raw})
        self.path.write_text('{', encoding='utf-8')
        with self.assertRaises(SettingsError):
            read_pause(self.path)

    def test_missing_settings_between_read_and_save_are_not_recreated(self):
        choice = self.choice(True)
        self.path.unlink()
        with self.assertRaises(SettingsError):
            save_pause(choice, self.path)
        self.assertFalse(self.path.exists())

    def test_paused_runtime_stops_before_browser_creation(self):
        args = engine.build_argument_parser().parse_args(['--headless'])
        save_pause(self.choice(True), self.path)
        with mock.patch.object(engine, 'sync_playwright') as browser:
            with self.assertRaises(SettingsError):
                engine.run_booking(args, load_settings(self.path), engine.PracticePlan())
            browser.assert_not_called()

    def test_main_paused_schedule_is_a_quiet_hold_without_history_or_worker(self):
        save_pause(self.choice(True), self.path)
        with mock.patch.object(engine, '_load_and_validate_runtime_settings', return_value=(load_settings(self.path), engine.PracticePlan(), None)), \
                mock.patch.object(engine, 'run_booking') as worker, mock.patch.object(engine, 'save_history') as history, \
                mock.patch.object(engine, 'wait_before_runtime_target_window'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(engine.main(['--scheduled']), 0)
            worker.assert_not_called()
            history.assert_not_called()
