"""Pause during waits stops mutation work without blocking read-only scopes."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import book_week as b
from app_settings import atomic_write_json, load_settings, update_settings
from booking_pause import read_pause, save_pause, assert_booking_unpaused
from booking_preferences_guard import BookingPreferencesChanged, PreparedCoordinationChanged, booking_preference_run
from operation_control import owned_operation
from phone_operation_worker import execute
from mutation_receipts import record_pending_create


class PauseWaitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.path = self.root / 'settings.json'
        atomic_write_json(self.path, {})
        self.now = datetime(2030, 10, 15, 10)
        clock = self.stack.enter_context(patch.object(b, 'datetime', wraps=datetime))
        clock.now.side_effect = lambda: self.now
        self.sleep = self.stack.enter_context(patch.object(b.time, 'sleep', side_effect=self.tick))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch.object(b, 'settings_file', self.path))
        self.stack.enter_context(patch('tempo_coordination.SNAPSHOT_PATH', self.root / 'coordination.json'))
        self.stack.enter_context(patch('preference_runs.kick'))
        self.stack.enter_context(patch('preference_runs.complete_matching_run'))
        self.history = self.stack.enter_context(patch.object(b, 'save_history'))
        self.stack.enter_context(patch.object(b, 'run_report'))
        self.stack.enter_context(patch.object(b, 'install_booking_rules'))
        self.stack.enter_context(patch.object(b, 'list_pending_mutation_receipts', return_value=[]))
        self.loader = self.stack.enter_context(patch.object(b, '_load_and_validate_runtime_settings',
            side_effect=lambda: (load_settings(self.path), b.PracticePlan(), Mock())))
        self.lock = Mock()
        self.lock.acquire.return_value = True
        self.stack.enter_context(patch.object(b, 'SingleInstanceLock', return_value=self.lock))

    def pause(self, paused=True):
        return save_pause(dict(paused=paused, revision=read_pause(self.path)['revision'],
                               request_id=str(uuid4())), self.path)

    def tick(self, seconds):
        self.now += timedelta(minutes=1)

    def test_paused_mutation_does_not_begin_the_pre_authentication_wait(self):
        self.pause()
        with patch.object(b, 'wait_before_runtime_target_window') as wait, patch.object(b, 'run_booking') as worker:
            self.assertEqual(b.main(['--headless', '--target-time', '10:04']), 3)
        wait.assert_not_called()
        worker.assert_not_called()
        self.history.assert_not_called()

    def test_pause_or_pause_resume_during_early_wait_retires_that_run(self):
        for resume in (False, True):
            with self.subTest(resume=resume):
                atomic_write_json(self.path, {})
                self.now = datetime(2030, 10, 15, 10)
                self.sleep.reset_mock()
                def change(seconds):
                    self.pause()
                    if resume:
                        self.pause(False)
                    self.tick(seconds)
                self.sleep.side_effect = change
                with patch.object(b, 'run_booking', return_value=0) as worker:
                    self.assertEqual(b.main(['--headless', '--target-time', '10:10']), 3)
                self.assertEqual(self.sleep.call_count, 1)
                worker.assert_not_called()
                self.history.assert_not_called()

    def test_read_only_wait_remains_available_while_paused(self):
        self.pause()
        with patch.object(b, 'run_booking', return_value=0) as worker:
            self.assertEqual(b.main(['--headless', '--check-only', '--target-time', '10:04']), 0)
        worker.assert_called_once()
        self.assertGreater(self.sleep.call_count, 0)
        self.history.assert_not_called()

    def test_read_only_wait_does_not_require_valid_mutation_hold_evidence(self):
        atomic_write_json(self.path, {'booking_pause': {'paused': 'unknown'}})
        with patch.object(b, 'run_booking', return_value=0) as worker:
            self.assertEqual(b.main(['--headless', '--check-only', '--target-time', '10:04']), 0)
        worker.assert_called_once()
        self.history.assert_not_called()

    def test_early_wait_reloads_non_pause_preferences_before_planning(self):
        def change(seconds):
            update_settings(lambda s: s.update(disabled_dates=['2030-10-16']), self.path)
            self.tick(seconds)
        self.sleep.side_effect = change
        with patch.object(b, 'run_booking', return_value=0) as worker:
            self.assertEqual(b.main(['--headless', '--target-time', '10:04']), 0)
        self.assertEqual(worker.call_args.args[1]['disabled_dates'], ['2030-10-16'])

    def test_stop_during_early_wait_does_not_become_a_settings_failure(self):
        stop = self.root / 'stop'
        def stop_during_wait(seconds):
            stop.touch()
            self.tick(seconds)
        self.sleep.side_effect = stop_during_wait
        with owned_operation(stop, Mock(), Mock()), patch.object(b, 'run_booking', return_value=0) as worker:
            self.assertEqual(b.main(['--headless', '--target-time', '10:10']), 3)
        worker.assert_not_called()
        self.history.assert_not_called()

    def test_pause_during_read_only_runtime_wait_does_not_abort_the_refresh(self):
        self.lock.acquire.side_effect = [False, True]
        self.sleep.side_effect = lambda seconds: self.pause()
        with patch.object(b, 'run_booking', return_value=0) as worker:
            self.assertEqual(b.main(['--headless', '--agenda-only', '--wait-for-runtime-seconds', '180']), 0)
        worker.assert_called_once()
        self.history.assert_not_called()
        self.lock.release.assert_called_once()

    def test_scheduled_runtime_wait_retires_paused_work_even_after_resume(self):
        for resume in (False, True):
            with self.subTest(resume=resume):
                atomic_write_json(self.path, {})
                self.lock.acquire.reset_mock(side_effect=True)
                self.lock.acquire.side_effect = [False, True]
                def change(seconds):
                    self.pause()
                    if resume:
                        self.pause(False)
                self.sleep.side_effect = change
                with patch.object(b, '_scheduled_target_time', return_value=None), \
                        patch.object(b, 'run_booking', return_value=0) as worker:
                    self.assertEqual(b.main(['--scheduled']), 0)
                worker.assert_not_called()
                self.assertEqual(self.lock.acquire.call_count, 1)
                self.history.assert_not_called()

    def test_pause_resume_between_tempo_replans_retires_the_original_run(self):
        def worker(*args):
            self.pause()
            self.pause(False)
            raise PreparedCoordinationChanged('Synthetic shared-plan revision changed')
        with patch.object(b, '_scheduled_target_time', return_value=None), \
                patch.object(b, 'run_booking', side_effect=worker) as run:
            self.assertEqual(b.main(['--scheduled']), 0)
        run.assert_called_once()
        self.history.assert_not_called()

    def test_pause_seen_by_fresh_scheduled_replan_is_not_a_settings_failure(self):
        calls = []
        def worker(args, settings, *rest):
            calls.append(settings)
            if len(calls) == 1:
                self.pause()
                raise PreparedCoordinationChanged('Synthetic shared-plan revision changed')
            assert_booking_unpaused(settings)
        with patch.object(b, 'run_booking', side_effect=worker):
            self.assertEqual(b.main(['--scheduled']), 0)
        self.assertEqual(len(calls), 1)
        self.history.assert_not_called()

    def test_mutation_countdowns_observe_pause_and_resume_generation(self):
        for name in ('wait_until_target_time', 'wait_for_horizon_discovery_window'):
            for resume in (False, True):
                with self.subTest(wait=name, resume=resume):
                    atomic_write_json(self.path, {})
                    self.now = datetime(2030, 10, 15, 10)
                    self.sleep.reset_mock()
                    def change(seconds):
                        self.pause()
                        if resume:
                            self.pause(False)
                        self.tick(seconds)
                    self.sleep.side_effect = change
                    argument = self.now + timedelta(minutes=4) if name == 'wait_for_horizon_discovery_window' else '10:04'
                    with booking_preference_run(self.path, {}), self.assertRaises(BookingPreferencesChanged):
                        getattr(b, name)(argument)
                    self.assertEqual(self.sleep.call_count, 1)

    def test_owned_operation_explains_pause_as_a_normal_stop(self):
        for moment in ('before', 'during', 'pause_resume'):
            with self.subTest(moment=moment):
                atomic_write_json(self.path, {})
                directory = self.root / moment
                directory.mkdir()
                if moment == 'before':
                    self.pause()
                def worker(*args):
                    self.pause()
                    if moment == 'pause_resume':
                        self.pause(False)
                    with b.booking_save_boundary():
                        self.fail('Pause must prevent the Save')
                with patch.object(b, 'run_booking', side_effect=worker):
                    result = execute('run', {}, directory, root=self.root, booker_main=b.main)
                self.assertEqual(result['state'], 'stopped')
                self.assertEqual(result.get('reason'), 'booking_pause')
                self.assertIn('Pause', result['message'])
                self.history.assert_not_called()

    def test_pause_explanation_never_hides_pending_or_unreadable_outcomes(self):
        self.pause()
        directory = self.root / 'job'
        directory.mkdir()
        journal = self.root / 'data/mutation_receipts.json'
        record_pending_create(room='Fixture', booking_date='2030-10-15',
                              start='10:00', end='10:30', path=journal)
        for corrupt in (False, True):
            if corrupt:
                journal.write_text('{broken', encoding='utf-8')
            original = journal.read_bytes()
            with patch.object(b, 'run_booking') as worker:
                result = execute('run', {}, directory, root=self.root, booker_main=b.main)
            self.assertEqual(result['state'], 'uncertain')
            worker.assert_not_called()
            self.assertEqual(journal.read_bytes(), original)

    def test_unrelated_failure_while_paused_does_not_claim_pause_stopped_the_run(self):
        directory = self.root / 'failed-job'
        directory.mkdir()
        def worker(*args):
            self.pause()
            raise OSError('Synthetic unrelated failure')
        with patch.object(b, 'run_booking', side_effect=worker):
            result = execute('run', {}, directory, root=self.root, booker_main=b.main)
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('reason', result)


if __name__ == '__main__':
    unittest.main()
