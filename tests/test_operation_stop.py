"""Cooperative Stop races, using isolated journals and no remote actions."""
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from booking_preferences_guard import booking_save_boundary
from mutation_receipts import record_pending_create
from operation_control import (OperationStopped, check_operation_stop,
                               operation_verification, owned_operation)
from phone_operation_worker import execute


class OperationStopTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.directory = self.root / 'job'
        self.directory.mkdir()
        self.stop = self.directory / 'stop'
        self.receipts = self.root / 'data/mutation_receipts.json'

    def pending(self):
        record_pending_create(room='Fixture room', booking_date='2030-10-15',
                              start='10:00', end='10:30', path=self.receipts)

    def test_stop_at_worker_launch_is_settled_without_starting_booker(self):
        self.stop.touch()
        booker = mock.Mock()
        result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        self.assertEqual(result['state'], 'stopped')
        booker.assert_not_called()
        self.assertFalse(self.receipts.exists())

    def test_stop_between_steps_is_settled_without_a_pending_write(self):
        def booker(flags):
            self.stop.touch()
            check_operation_stop()
        result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        self.assertEqual(result['state'], 'stopped')

    def test_stop_never_hides_pending_or_unreadable_receipts(self):
        self.pending()
        original = self.receipts.read_bytes()
        self.stop.touch()
        booker = mock.Mock()
        result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(self.receipts.read_bytes(), original)
        self.receipts.write_text('{broken', encoding='utf-8')
        result = execute('run', {}, self.directory, root=self.root, booker_main=booker)
        self.assertEqual(result['state'], 'uncertain')
        booker.assert_not_called()

    def test_unrelated_error_after_stop_remains_uncertain(self):
        def booker(flags):
            self.stop.touch()
            raise OSError('Unknown write outcome')
        self.assertEqual(execute('run', {}, self.directory, root=self.root,
                                 booker_main=booker)['state'], 'uncertain')

    def test_stop_during_tempo_lock_wait_prevents_receipt_and_save(self):
        @contextmanager
        def tempo_boundary(**interval):
            self.stop.touch()
            yield
        save = mock.Mock()
        with owned_operation(self.stop, mock.Mock(), mock.Mock()), \
                mock.patch('booking_preferences_guard.tempo_save_boundary', tempo_boundary):
            with self.assertRaises(OperationStopped), booking_save_boundary():
                save()
        save.assert_not_called()

    def test_entered_save_finishes_verification_then_stops_next_action(self):
        verified = []
        with owned_operation(self.stop, mock.Mock(), mock.Mock()):
            with booking_save_boundary():
                self.stop.touch()
            with operation_verification():
                check_operation_stop()
                verified.append('exact existing receipt')
            with self.assertRaises(OperationStopped):
                check_operation_stop()
        self.assertEqual(verified, ['exact existing receipt'])

    def test_runtime_wait_observes_stop_before_starting_browser(self):
        import book_week
        settings_path = self.root / 'data/settings.json'
        settings_path.parent.mkdir()
        settings_path.write_text('{}', encoding='utf-8')
        runtime = mock.Mock()
        runtime.acquire.side_effect = [False, True]
        with owned_operation(self.stop, mock.Mock(), mock.Mock()), \
                mock.patch.object(book_week, 'settings_file', str(settings_path)), \
                mock.patch('tempo_coordination.SNAPSHOT_PATH', self.root / 'coordination.json'), \
                mock.patch('preference_runs.kick'), \
                mock.patch.object(book_week, 'save_history'), \
                mock.patch.object(book_week, 'run_report'), \
                mock.patch.object(book_week, 'SingleInstanceLock', return_value=runtime), \
                mock.patch.object(book_week, '_load_and_validate_runtime_settings',
                                  return_value=({}, book_week.PracticePlan(), mock.Mock())), \
                mock.patch.object(book_week.time, 'monotonic', return_value=0), \
                mock.patch.object(book_week.time, 'sleep', side_effect=lambda seconds: self.stop.touch()), \
                mock.patch.object(book_week, 'run_booking', return_value=0) as run, \
                redirect_stdout(io.StringIO()):
            result = book_week.main(['--agenda-only', '--wait-for-runtime-seconds', '180'])
        self.assertEqual(result, 3)
        run.assert_not_called()
        runtime.release.assert_called_once()

    def test_target_and_discovery_waits_observe_stop_before_the_next_poll(self):
        import book_week
        for name in ('wait_until_target_time', 'wait_before_runtime_target_window',
                     'wait_for_horizon_discovery_window'):
            for stopped_at_entry in (False, True):
                with self.subTest(wait=name, stopped_at_entry=stopped_at_entry):
                    self.stop.unlink(missing_ok=True)
                    current = datetime(2030, 10, 15, 10, 0)
                    target = current + timedelta(minutes=4)
                    argument = target if name == 'wait_for_horizon_discovery_window' else '10:04'
                    if stopped_at_entry:
                        self.stop.touch()

                    def tick(seconds):
                        nonlocal current
                        self.stop.touch()
                        current += timedelta(minutes=1)

                    with owned_operation(self.stop, mock.Mock(), mock.Mock()), \
                            mock.patch.object(book_week, 'datetime', wraps=datetime) as clock, \
                            mock.patch.object(book_week.time, 'sleep', side_effect=tick) as sleep, \
                            redirect_stdout(io.StringIO()):
                        clock.now.side_effect = lambda: current
                        with self.assertRaises(OperationStopped):
                            getattr(book_week, name)(argument)
                    self.assertEqual(sleep.call_count, 0 if stopped_at_entry else 1)


if __name__ == '__main__':
    unittest.main()
