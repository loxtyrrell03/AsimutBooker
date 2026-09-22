import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import preference_runs as runs
from app_settings import InterProcessFileLock, SettingsError, load_settings, update_settings
from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict
from runtime_guard import SingleInstanceLock


class PreferenceRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'data/settings.json'
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({'practice_plan': {'enabled': True, 'default_hours': 4.0},
                                         'unrelated': 'preserved'}))
        self.kick = patch.object(runs, 'kick').start()
        self.addCleanup(patch.stopall)
        patch.object(runs, 'DEBOUNCE_SECONDS', 0).start()

    def save(self, hours):
        return runs.update_preferences(lambda s: s['practice_plan'].update(default_hours=hours), self.path)

    def request(self):
        return load_settings(self.path).get(runs.KEY)

    def drain(self, main, **kwargs):
        runs.run_pending(root=self.root, booker_main=main, enabled=lambda: True, **kwargs)

    def test_save_and_request_are_one_atomic_write_before_launch(self):
        def launched(path):
            saved = load_settings(path)
            self.assertEqual(saved['practice_plan']['default_hours'], 6)
            self.assertEqual(saved[runs.KEY]['state'], 'pending')
            self.assertEqual(saved['unrelated'], 'preserved')
        self.kick.side_effect = launched
        self.save(6)
        self.kick.assert_called_once_with(self.path)

    def test_no_change_and_worker_bookkeeping_do_not_queue(self):
        self.save(4.0)
        update_settings(lambda s: s.update(extendable_bookings=[{'progress': 30}]), self.path)
        self.assertIsNone(self.request())
        self.kick.assert_not_called()

    def test_invalid_or_failed_save_does_not_queue(self):
        original = self.path.read_bytes()
        def invalid(settings):
            settings['practice_plan']['default_hours'] = 8
            raise ValueError('invalid')
        with self.assertRaises(ValueError):
            runs.update_preferences(invalid, self.path)
        with patch('app_settings.atomic_write_json', side_effect=SettingsError('disk unavailable')):
            with self.assertRaises(SettingsError):
                self.save(6)
        self.assertEqual(self.path.read_bytes(), original)
        self.kick.assert_not_called()

    def test_rapid_saves_coalesce_into_one_run_with_latest_values(self):
        for hours in (5, 6, 7):
            self.save(hours)
        seen = []
        self.drain(lambda flags: seen.append((flags, load_settings(self.path)['practice_plan']['default_hours'])) or 0)
        self.assertEqual(seen, [(['--headless'], 7)])
        self.assertEqual(self.request()['state'], 'completed')

    def test_active_runtime_waits_without_losing_request(self):
        self.save(6)
        lock = SingleInstanceLock(self.root / 'data/booker-runtime.lock')
        self.assertTrue(lock.acquire())
        main = Mock(return_value=0)
        def waiting(_):
            main.assert_not_called()
            self.assertEqual(self.request()['state'], 'pending')
            lock.release()
        try:
            self.drain(main, sleep=waiting)
        finally:
            lock.release()
        main.assert_called_once()

    def test_assistant_owner_is_not_interrupted(self):
        self.save(6)
        lock = InterProcessFileLock(self.root / 'data/assistant-mutation.lock', timeout=0)
        lock.acquire()
        main = Mock(return_value=0)
        try:
            self.drain(main, sleep=lambda _: lock.release())
        finally:
            lock.release()
        main.assert_called_once()

    def test_busy_race_retries_after_ownership_releases(self):
        self.save(6)
        main = Mock(side_effect=[6, 0])
        self.drain(main, sleep=lambda _: None)
        self.assertEqual(main.call_count, 2)
        self.assertEqual(self.request()['state'], 'completed')

    def test_new_save_during_run_survives_old_completion_and_gets_one_followup(self):
        self.save(5)
        seen = []
        def main(_):
            seen.append(load_settings(self.path)['practice_plan']['default_hours'])
            if len(seen) == 1:
                self.save(6)
                self.save(7)
                return 3  # Existing preference guard stops the older pass.
            return 0
        self.drain(main)
        self.assertEqual(seen, [5, 7])
        self.assertEqual(self.request()['state'], 'completed')

    def test_failure_is_recorded_without_an_endless_retry_loop(self):
        for code in (1, 2, 3, 4, 5, 130):
            with self.subTest(code=code):
                self.save(4 + code / 100)
                main = Mock(return_value=code)
                self.drain(main)
                self.drain(main)
                main.assert_called_once()
                self.assertEqual(self.request()['state'], 'failed')

    def test_explicit_automatic_off_prevents_run(self):
        self.save(6)
        main = Mock()
        runs.run_pending(root=self.root, booker_main=main, enabled=lambda: False)
        main.assert_not_called()
        self.assertEqual(self.request()['state'], 'paused')

    def test_second_dispatcher_cannot_run_in_parallel(self):
        self.save(6)
        second = Mock(return_value=0)
        def first(_):
            self.drain(second)
            return 0
        self.drain(first)
        second.assert_not_called()

    def test_restart_retains_pending_request(self):
        self.save(6)
        request = self.request()
        runs.settle(self.path, request['id'], 'running', 'Started before host restart')
        main = Mock(return_value=0)
        self.drain(main)
        main.assert_called_once_with(['--headless'])
        self.assertEqual(self.request()['state'], 'completed')

    def test_ordinary_matching_run_can_fulfil_request_but_stale_or_scoped_run_cannot(self):
        self.save(5)
        old = load_settings(self.path)
        self.save(6)
        runs.complete_matching_run(self.path, old, SimpleNamespace())
        self.assertEqual(self.request()['state'], 'pending')
        latest = load_settings(self.path)
        for args in (SimpleNamespace(plan_only=True), SimpleNamespace(only_date='2026-09-22'),
                     SimpleNamespace(scheduled=True, target_time=None)):
            runs.complete_matching_run(self.path, latest, args)
            self.assertEqual(self.request()['state'], 'pending')
        runs.complete_matching_run(self.path, latest, SimpleNamespace())
        self.assertEqual(self.request()['state'], 'completed')

    def test_phone_save_returns_queued_status_without_changing_revision_for_worker_progress(self):
        current = read_phone_preferences(self.path)
        saved = save_phone_preferences({'revision': current['revision'],
            'changes': {'practice_plan': {'default_hours': 6}}}, self.path)
        self.assertEqual(saved['preference_run']['state'], 'pending')
        self.assertEqual(saved['practice_plan']['default_hours'], 6)
        with self.assertRaises(PreferenceConflict):
            save_phone_preferences({'revision': current['revision'],
                'changes': {'practice_plan': {'default_hours': 8}}}, self.path)
        runs.settle(self.path, self.request()['id'], 'completed', 'Finished')
        self.assertEqual(read_phone_preferences(self.path)['revision'], saved['revision'])

    def test_normal_booker_entry_point_completes_the_matching_saved_request(self):
        import book_week as b
        self.save(6)
        with patch.multiple(b, APP_DIR=self.root, settings_file=self.path, _CONFIG_ERROR=None), \
             patch.object(b, '_load_and_validate_runtime_settings',
                          return_value=(load_settings(self.path), None, None)), \
             patch.object(b, 'install_booking_rules'), \
             patch.object(b, 'run_booking', return_value=0) as run:
            self.assertEqual(b.main(['--headless']), 0)
        run.assert_called_once()
        self.assertEqual(self.request()['state'], 'completed')

    def test_queue_progress_does_not_invalidate_prepared_save_but_new_preferences_do(self):
        from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
        self.save(5)
        settings = load_settings(self.path)
        with booking_preference_run(self.path, settings):
            runs.settle(self.path, self.request()['id'], 'running', 'Running')
            with booking_save_boundary():
                pass
            self.save(6)
            with self.assertRaises(BookingPreferencesChanged):
                with booking_save_boundary():
                    self.fail('A stale Save must not be permitted')

    def test_pending_after_exit_is_checked_after_dispatcher_lock_release(self):
        self.save(6)
        def check_after_exit(path):
            lock = SingleInstanceLock(self.root / 'data/preference-dispatch.lock')
            self.assertTrue(lock.acquire())
            lock.release()
        self.kick.side_effect = check_after_exit
        self.drain(Mock(return_value=0))

    def test_hidden_launch_failure_keeps_successful_save_pending(self):
        self.save(6)
        python = self.root / '.venv/Scripts/python.exe'
        python.parent.mkdir(parents=True)
        python.touch()
        # Call the real launcher with a fixture-owned root and no live account.
        real_kick = ORIGINAL_KICK
        with patch.object(runs, 'ROOT', self.root), patch.object(runs, 'SETTINGS', self.path), \
             patch.object(runs.subprocess, 'Popen', side_effect=OSError('unavailable')) as launch, \
             self.assertLogs(runs.LOGGER, level='ERROR'):
            self.assertFalse(real_kick(self.path))
        self.assertEqual(self.request()['state'], 'pending')
        self.assertEqual(launch.call_args.kwargs['stdin'], runs.subprocess.DEVNULL)
        self.assertEqual(launch.call_args.kwargs['creationflags'], getattr(runs.subprocess, 'CREATE_NO_WINDOW', 0))


ORIGINAL_KICK = runs.kick

if __name__ == '__main__':
    unittest.main()
