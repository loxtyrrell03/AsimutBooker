"""Desktop terminal messages use checked outcomes, with no live app or writes."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import gui
from desktop_operation_worker import execute, read_result
from mutation_receipts import record_pending_create
from operation_control import check_operation_stop, report_pause_stop


class DesktopOutcomeTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.key = str(uuid4())
        self.directory = self.root / 'data/desktop_operations' / self.key
        self.directory.mkdir(parents=True)
        self.stop = self.directory / 'stop'
        self.journal = self.root / 'data/mutation_receipts.json'

    def finish(self, code, operation='Booker'):
        app = object.__new__(gui.AsimutBookerGUI)
        app._desktop_run_id = self.key
        app._desktop_stop_path = self.stop
        app.root = Mock()
        callbacks, messages = [], []
        app.root.after.side_effect = lambda delay, callback: callbacks.append(callback)
        app.log = lambda message, tag='info': messages.append((message, tag))
        app._on_booker_finished = Mock()
        app._quiet_plan_refresh_finished = Mock()
        process = Mock(returncode=code, stdout=io.StringIO(''))
        with patch.object(gui, 'APP_DIR', self.root), patch('gui.subprocess.Popen', return_value=process):
            app._run_booker_thread(True, operation_name=operation)
        for callback in callbacks:
            callback()
        process.wait.assert_called_once()
        process.terminate.assert_not_called()
        app._on_booker_finished.assert_called_once()
        return messages[-1], app

    def run_worker(self, worker):
        with redirect_stdout(io.StringIO()):
            return execute(self.key, ['--headless'], root=self.root, booker_main=worker)

    def test_pause_has_a_normal_explanation_without_a_stop_marker(self):
        def worker(flags):
            report_pause_stop()
            return 3
        code = self.run_worker(worker)
        self.assertEqual(code, 3)
        message, tag = self.finish(code)[0]
        self.assertEqual(tag, 'info')
        self.assertIn('Pause', message)
        self.assertFalse(self.stop.exists())

    def test_stop_cannot_hide_an_unresolved_booking(self):
        record_pending_create(room='Example', booking_date='2030-10-15', start='12:00',
                              end='12:30', path=self.journal)
        before = self.journal.read_bytes()
        def worker(flags):
            self.stop.touch()
            check_operation_stop()
        code = self.run_worker(worker)
        message, tag = self.finish(code)[0]
        self.assertEqual(tag, 'warning')
        self.assertIn('needs checking', message)
        self.assertEqual(self.journal.read_bytes(), before)

    def test_unreadable_journal_cannot_be_reported_as_success(self):
        self.journal.write_text('{broken', encoding='utf-8')
        code = self.run_worker(lambda flags: 0)
        (message, tag), app = self.finish(code, 'Plan refresh')
        self.assertEqual(tag, 'warning')
        self.assertIn('needs checking', message)
        app._quiet_plan_refresh_finished.assert_called_once_with(1)
        self.assertEqual(self.journal.read_text(), '{broken')

    def test_crashed_worker_with_stop_marker_does_not_claim_safe_completion(self):
        self.stop.touch()
        message, tag = self.finish(1)[0]
        self.assertEqual(tag, 'warning')
        self.assertIn('could not be confirmed', message)

    def test_clean_stop_preserves_verified_bookings_without_restarting(self):
        self.stop.touch()
        worker = Mock()
        code = self.run_worker(worker)
        self.assertEqual(code, 130)
        worker.assert_not_called()
        message, tag = self.finish(code)[0]
        self.assertEqual(tag, 'info')
        self.assertIn('Stopped', message)
        self.assertIn('verified', message)

    def test_error_with_stop_marker_is_not_mistaken_for_a_deliberate_stop(self):
        def worker(flags):
            self.stop.touch()
            return 1
        code = self.run_worker(worker)
        message, tag = self.finish(code)[0]
        self.assertEqual(tag, 'warning')
        self.assertNotIn('Stopped.', message)

    def test_result_is_bound_to_exact_request_and_process_exit(self):
        self.run_worker(lambda flags: 0)
        path = self.directory / 'result.json'
        original = json.loads(path.read_text())
        for field, value in [('request_id', str(uuid4())), ('exit_code', 3), ('state', 'unknown'),
                             ('state', []), ('protocol', True)]:
            with self.subTest(field=field):
                path.write_text(json.dumps(dict(original, **{field: value})), encoding='utf-8')
                self.assertEqual(read_result(self.key, 0, root=self.root)['state'], 'uncertain')
        path.write_text('{broken', encoding='utf-8')
        self.assertEqual(read_result(self.key, 0, root=self.root)['state'], 'uncertain')

    def test_newer_pending_outcome_is_visible_without_rewriting_old_result(self):
        self.run_worker(lambda flags: 0)
        path = self.directory / 'result.json'
        before = path.read_bytes()
        record_pending_create(room='Example', booking_date='2030-10-15', start='12:00',
                              end='12:30', path=self.journal)
        self.assertEqual(read_result(self.key, 0, root=self.root)['state'], 'uncertain')
        self.assertEqual(path.read_bytes(), before)

    def test_result_publication_failure_cannot_report_completion_or_retry(self):
        worker = Mock(return_value=0)
        with patch('desktop_operation_worker.atomic_write_json', side_effect=OSError('Write failed')):
            with self.assertRaises(OSError):
                self.run_worker(worker)
        worker.assert_called_once()
        self.assertEqual(read_result(self.key, 1, root=self.root)['state'], 'uncertain')

    def test_finished_check_does_not_claim_a_new_booking_or_retry_after_late_stop(self):
        worker = Mock(return_value=0)
        code = self.run_worker(worker)
        self.stop.touch()  # Delivered after the check completed.
        message, tag = self.finish(code)[0]
        self.assertEqual(tag, 'success')
        self.assertIn('Check finished', message)
        self.assertNotIn('Stopped', message)
        self.assertNotIn('Booked', message)
        worker.assert_called_once()


if __name__ == '__main__':
    unittest.main()
