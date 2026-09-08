import json
import os
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import uuid4

from app_settings import atomic_write_json, load_settings
from agenda_snapshot import publish_agenda_snapshot
from booking_preferences_guard import booking_save_boundary
from operation_control import owned_operation, OperationStopped
from phone_operation_worker import execute
from phone_operations import PhoneOperations
from phone_server import PhoneAssistantService, PhoneActiveTurnError, RequestLedger
from phone_system import read_view, run_local_action, validate_action, SystemConflict
from runtime_guard import SingleInstanceLock, SingleInstanceAlreadyRunning
from tests.test_phone_server import FakeRuntime, HTTPBoundaryTests


class SystemDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        atomic_write_json(self.root / 'data/settings.json', {'unrelated': 'keep'})

    def test_config_is_strict_revision_checked_and_shared_with_booker(self):
        from book_week import load_config, ConfigError
        doc = read_view('config', self.root)
        doc['rules']['same_room_gap_minutes'] = 90
        run_local_action('config_save', doc, self.root)
        self.assertEqual(load_config(self.root / 'config/config.yaml')['same_room_gap_minutes'], 90)
        with self.assertRaises(SystemConflict):
            run_local_action('config_save', doc, self.root)
        doc = read_view('config', self.root)
        doc['rules']['shell'] = 'never'
        with self.assertRaises(ConfigError):
            run_local_action('config_save', doc, self.root)

    def test_history_clear_rejects_new_run_and_preserves_other_files(self):
        path = self.root / 'data/booking_history.json'
        atomic_write_json(path, {'runs': [{'timestamp': '2026-09-08T12:00:00', 'bookings_made': 2,
                                         'details': 'password=not-for-phone'}]})
        doc = read_view('history', self.root)
        self.assertNotIn('password', json.dumps(doc))
        self.assertEqual(doc['runs'][0]['bookings'], 2)
        atomic_write_json(path, {'runs': []})
        with self.assertRaises(SystemConflict):
            run_local_action('history_clear', {'revision': doc['revision']}, self.root)
        run_local_action('history_clear', {'revision': read_view('history', self.root)['revision']}, self.root)
        self.assertEqual(load_settings(self.root / 'data/settings.json'), {'unrelated': 'keep'})

    def test_events_keep_collision_identity_and_unrelated_ignores(self):
        events = [dict(date='2030-10-15', startTime='10:00', endTime='11:00', title=title,
                       isReservation=False, room=None, eventId=index + 10)
                  for index, title in enumerate(['Class A', 'Class B'])]
        publish_agenda_snapshot(events, [date(2030, 10, 15)], observed_at=datetime.now(timezone.utc),
                                path=self.root / 'data/agenda_snapshot.json')
        doc = read_view('events', self.root)
        self.assertNotEqual(doc['events'][0]['key'], doc['events'][1]['key'])
        run_local_action('events_save', {'revision': doc['revision'], 'changes': {doc['events'][0]['key']: True}}, self.root)
        saved = read_view('events', self.root)
        self.assertEqual([e['ignored'] for e in saved['events']], [True, False])
        self.assertEqual(load_settings(self.root / 'data/settings.json')['unrelated'], 'keep')
        with self.assertRaises(SystemConflict):
            run_local_action('events_save', {'revision': doc['revision'], 'changes': {doc['events'][1]['key']: True}}, self.root)

    def test_cleanup_is_only_previewed_inactive_dated_logs(self):
        logs = self.root / 'logs'; logs.mkdir()
        for name in ['booker_2026-09-01.log', 'booker_2026-09-08.log', 'scheduler.log', 'phone_server.log', 'auth.json']:
            (logs / name).write_text('password=private\n2026-09-08 12:00:00 CHECK PASSED\n', encoding='utf-8')
        os.utime(logs / 'booker_2026-09-01.log', (1, 1))
        doc = read_view('cleanup', self.root)
        self.assertEqual([f['name'] for f in doc['files']], ['booker_2026-09-01.log'])
        self.assertNotIn('password', json.dumps(read_view('logs', self.root)))
        run_local_action('cleanup', {'revision': doc['revision']}, self.root)
        self.assertEqual(sorted(p.name for p in logs.iterdir()), ['auth.json', 'booker_2026-09-08.log', 'phone_server.log', 'scheduler.log'])

    def test_active_runtime_prevents_local_writes(self):
        doc = read_view('config', self.root)
        with SingleInstanceLock(self.root / 'data/booker-runtime.lock'):
            with self.assertRaises(SingleInstanceAlreadyRunning):
                run_local_action('config_save', doc, self.root)
        self.assertFalse((self.root / 'config/config.yaml').exists())

    def test_schedule_only_uses_fixed_helper_and_checks_result(self):
        with mock.patch('gui.build_scheduler_remove_command', return_value=['fixed-helper']) as command, \
                mock.patch('phone_system.subprocess.run', return_value=mock.Mock(returncode=0)) as process, \
                mock.patch('gui.query_recurring_task_status', return_value=None):
            result = run_local_action('schedule_remove', {}, self.root)
            self.assertIn('removed', result['message'])
            command.assert_called_once_with()
            self.assertEqual(process.call_args.args[0], ['fixed-helper'])
        with self.assertRaises(ValueError):
            validate_action('schedule_remove', {'task_name': 'other-task'})

    def test_stop_before_save_never_creates_receipt_or_click(self):
        stop = self.root / 'stop'
        click = mock.Mock()
        with owned_operation(stop, lambda text: None, lambda day, rows: None):
            with booking_save_boundary():
                click()
                stop.touch()  # A requested stop does not interrupt this already entered Save.
            with self.assertRaises(OperationStopped), booking_save_boundary():
                click()
        self.assertEqual(click.call_count, 1)

    def test_scan_returns_only_selected_dates_and_typed_gaps(self):
        from operation_control import report_available_gaps
        directory = self.root / 'job'; directory.mkdir()
        def main(flags):
            self.assertEqual(flags, ['--headless', '--check-only'])
            for day in [date(2030, 10, 15), date(2030, 10, 16)]:
                report_available_gaps(day, [{'room': 'B0.14', 'slots': [{'startHour': 10, 'endHour': 11.5, 'clickX': 42}]}])
            return 0
        result = execute('scan', {'dates': ['2030-10-15', '2030-10-17']}, directory, root=self.root, booker_main=main)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['scan']['rows'], [{'date': '2030-10-15', 'room': 'B0.14', 'start': '10:00', 'end': '11:30', 'minutes': 90}])
        self.assertEqual(result['scan']['unavailable_dates'], ['2030-10-17'])


class OwnedJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = PhoneAssistantService(runtime_factory=FakeRuntime,
            ledger=RequestLedger(self.root / 'ledger.json'), state_path=self.root / 'state.json', workspace=self.root / 'workspace')
        self.addCleanup(self.service.close)
        self.snapshot_patch = mock.patch('phone_server.build_phone_snapshot', return_value={})
        self.snapshot_patch.start(); self.addCleanup(self.snapshot_patch.stop)
        self.entered, self.finish = threading.Event(), threading.Event()
        self.calls = []
        def runner(request_id, action, args, directory):
            self.calls.append(action); self.entered.set(); self.finish.wait(3)
            return {'state': 'completed', 'message': 'Completed fixture'}
        self.service.operations.runner = runner

    def submit(self):
        payload = {'request_id': str(uuid4()), 'action': 'run', 'args': {}}
        self.service.operations.submit(payload)
        self.addCleanup(self.finish.set)
        return payload

    def test_duplicate_never_replays_and_active_job_survives_reload(self):
        payload = self.submit(); self.assertTrue(self.entered.wait(1))
        self.assertTrue(self.service.operations.submit(payload)['duplicate'])
        self.assertEqual(self.service.snapshot()['unresolved_reserved_count'], 0)
        self.assertTrue(self.service.snapshot()['system_job']['active'])
        with self.assertRaises(PhoneActiveTurnError):
            self.service.acknowledge_uncertain()
        self.service.operations.stop({'request_id': payload['request_id']})
        self.assertTrue((self.root / 'phone_operations' / payload['request_id'] / 'stop').exists())
        self.finish.set(); self.service.operations.thread.join(2)
        self.assertEqual(self.calls, ['run'])
        self.assertEqual(self.service.ledger.lookup(payload['request_id']), 'accepted')
        self.assertTrue(self.service.operations.submit(payload)['duplicate'])

    def test_uncertain_result_stays_reserved_and_blocks_other_actions(self):
        self.service.operations.runner = lambda *args: {'state': 'uncertain', 'message': 'Unknown'}
        payload = self.submit(); self.service.operations.thread.join(2)
        self.assertEqual(self.service.ledger.lookup(payload['request_id']), 'reserved')
        with self.assertRaises(SystemConflict):
            self.service.operations.submit({'request_id': str(uuid4()), 'action': 'run', 'args': {}})

    def test_queued_stop_does_not_start_runner(self):
        self.service._live_refresh_lock.acquire()
        payload = self.submit()
        self.service.operations.stop({'request_id': payload['request_id']})
        self.service._live_refresh_lock.release()
        self.service.operations.thread.join(2)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.operations.snapshot()['state'], 'stopped')

    def test_restart_keeps_unresolved_outcome_without_replaying(self):
        request_id = str(uuid4())
        self.service.ledger.reserve(request_id)
        atomic_write_json(self.root / 'phone_operations/latest.json', {'request_id': request_id, 'active': True})
        restarted = PhoneOperations(self.service, state_dir=self.root / 'phone_operations', runner=mock.Mock())
        self.assertEqual(restarted.snapshot()['state'], 'uncertain')
        restarted.runner.assert_not_called()

    def test_successful_scan_is_retained_after_a_later_failed_job(self):
        scan = {'rows': [], 'scanned_dates': ['2030-10-15'], 'unavailable_dates': [], 'observed_at': '2030-10-15T12:00:00Z'}
        self.service.operations.runner = lambda *args: {'state': 'completed', 'message': 'Checked', 'scan': scan}
        self.service.operations.submit({'request_id': str(uuid4()), 'action': 'scan', 'args': {'dates': ['2030-10-15']}})
        self.service.operations.thread.join(2)
        self.service.operations.runner = lambda *args: {'state': 'failed', 'message': 'Failed fixture'}
        self.submit(); self.service.operations.thread.join(2)
        self.assertEqual(json.loads((self.root / 'phone_operations/latest-scan.json').read_text(encoding='utf-8')), scan)

    def test_unrecordable_job_never_looks_active_or_dispatches(self):
        with mock.patch('phone_operations.atomic_write_json', side_effect=OSError('fixture disk full')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertFalse(self.service.operations.active)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.service.snapshot()['unresolved_reserved_count'], 1)


class SystemHTTPTests(HTTPBoundaryTests):
    def test_system_views_and_actions_require_identity_cookie_and_csrf(self):
        self.assertEqual(self.request('GET', '/api/v1/system/config')[0], 401)
        cookie, csrf = self.open_session()
        with mock.patch('phone_server.read_view', return_value={'rules': {}}) as read:
            self.assertEqual(self.request('GET', '/api/v1/system/config', headers={'Cookie': cookie})[0], 200)
            read.assert_called_once_with('config')
        headers = {'Cookie': cookie, 'Content-Type': 'application/json'}
        body = json.dumps({'request_id': str(uuid4()), 'action': 'run', 'args': {}})
        self.assertEqual(self.request('POST', '/api/v1/system/jobs', headers=headers, body=body)[0], 403)
        headers['X-Asimut-CSRF'] = csrf
        self.assistant.operations = mock.Mock(active=False)
        self.assistant.operations.submit.return_value = {'accepted': True}
        self.assertEqual(self.request('POST', '/api/v1/system/jobs', headers=headers, body=body)[0], 202)
        self.assistant.operations.submit.assert_called_once()
        self.assertEqual(self.request('GET', '/api/v1/system/../../config.yaml', headers={'Cookie': cookie})[0], 404)


if __name__ == '__main__':
    unittest.main()
