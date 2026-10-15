"""Prepared editors are discarded early; only the existing worker can replan."""
import contextlib
from datetime import datetime, timedelta
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import book_week as booker
import booking_preferences_guard as guards
from app_settings import atomic_write_json, load_settings, update_settings
import tempo_coordination as tempo
from tests.test_tempo_coordination import document, NOW, DAY


class PreparedCoordinationReplanTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(TemporaryDirectory()))
        self.path = folder / 'coordination.json'
        self.settings = folder / 'settings.json'
        atomic_write_json(self.settings, {})
        self.stack.enter_context(mock.patch.object(tempo, 'SNAPSHOT_PATH', self.path))
        self.stack.enter_context(mock.patch.object(tempo, 'RUNTIME_LOCK_PATH', folder / 'runtime.lock'))
        self.stack.enter_context(mock.patch.object(tempo, '_now', side_effect=lambda value=None: value or NOW))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        tempo.publish_snapshot(document(), now=NOW)
        self.revision = 1
        self.opening = NOW.replace(tzinfo=None) + timedelta(minutes=2)
        self.elapsed = 0
        self.local_time = NOW.replace(tzinfo=None)
        clock = self.stack.enter_context(mock.patch.object(booker, 'datetime', wraps=datetime))
        clock.now.side_effect = lambda tz=None: self.local_time
        self.stack.enter_context(mock.patch.object(booker.time, 'monotonic', side_effect=lambda: self.elapsed))
        self.sleep = self.stack.enter_context(mock.patch.object(booker.time, 'sleep', side_effect=self.advance))

    def advance(self, seconds):
        self.elapsed += seconds
        self.local_time += timedelta(seconds=seconds)

    def change_while_prepared(self, **changes):
        self.revision += 1
        tempo.publish_snapshot(document(revision=self.revision,
            generated_at=(NOW + timedelta(seconds=self.revision)).isoformat(), **changes),
            expected_revision=self.revision - 1, now=NOW)
        booker.wait_until_datetime(self.opening, label='EXTEND')
        self.fail('An editor prepared under the old revision must be discarded')

    def run_worker(self, worker, *, scheduled=True, pending=()):
        lock = mock.Mock()
        lock.acquire.return_value = True
        with self.stack.enter_context(contextlib.ExitStack()) as local:
            local.enter_context(mock.patch.object(booker, 'SingleInstanceLock', return_value=lock))
            local.enter_context(mock.patch.object(booker, 'settings_file', self.settings))
            loader = local.enter_context(mock.patch.object(booker, '_load_and_validate_runtime_settings',
                side_effect=lambda: (load_settings(self.settings), booker.PracticePlan(), mock.Mock())))
            local.enter_context(mock.patch.object(booker, '_scheduled_target_time', return_value=None))
            local.enter_context(mock.patch.object(booker, 'install_booking_rules'))
            local.enter_context(mock.patch.object(booker, 'list_pending_mutation_receipts', return_value=pending))
            local.enter_context(mock.patch('preference_runs.complete_matching_run'))
            local.enter_context(mock.patch('preference_runs.kick'))
            local.enter_context(mock.patch.object(booker, 'run_report'))
            local.enter_context(mock.patch.object(booker, 'save_history'))
            local.enter_context(mock.patch.object(booker, 'send_notification'))
            run = local.enter_context(mock.patch.object(booker, 'run_booking', side_effect=worker))
            result = booker.main(['--scheduled'] if scheduled else ['--headless'])
        lock.acquire.assert_called_once()
        lock.release.assert_called_once()
        return result, run, loader

    def test_renewal_discards_old_editor_before_opening_then_replans_fresh(self):
        seen = []
        saved = mock.Mock()

        def worker(*_args):
            seen.append(tempo.current_snapshot().document['revision'])
            if len(seen) == 1:
                self.change_while_prepared()
            with guards.booking_save_boundary(day=DAY, start='09:00', end='10:00', room='Practice A'):
                saved()
            return 0

        result, run, loader = self.run_worker(worker)
        self.assertEqual(result, 0)
        self.assertEqual(seen, [1, 2])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(loader.call_count, 2)
        saved.assert_called_once()
        self.sleep.assert_not_called()  # Replanning starts two minutes before opening.

    def test_changed_permissions_are_replanned_and_old_candidate_is_rejected(self):
        attempts = []

        def worker(*_args):
            attempts.append(tempo.current_snapshot())
            if len(attempts) == 1:
                self.change_while_prepared(windows=[dict(date=str(DAY), start='13:00', end='14:00', rooms=['Practice B'])])
            fresh = tempo.current_snapshot()
            self.assertFalse(fresh.permits(DAY, 9, 10, 'Practice A'))
            self.assertTrue(fresh.permits(DAY, 13, 14, 'Practice B'))
            with guards.booking_save_boundary(day=DAY, start='09:00', end='10:00', room='Practice A'):
                self.fail('Old permission cannot survive replanning')

        result, run, _ = self.run_worker(worker)
        self.assertEqual(result, 3)
        self.assertEqual(run.call_count, 2)

    def test_pending_receipt_blocks_replan_without_replaying_anything(self):
        result, run, loader = self.run_worker(lambda *_: self.change_while_prepared(),
                                             pending=[{'status': 'pending'}])
        self.assertEqual(result, 5)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(loader.call_count, 1)

    def test_second_revision_change_stops_instead_of_looping(self):
        result, run, _ = self.run_worker(lambda *_: self.change_while_prepared())
        self.assertEqual(result, 3)
        self.assertEqual(run.call_count, 2)

    def test_revision_changed_during_countdown_is_seen_within_one_second(self):
        def advance(seconds):
            self.advance(seconds)
            if self.revision == 1:
                self.revision = 2
                tempo.publish_snapshot(document(revision=2), expected_revision=1, now=NOW)
        self.sleep.side_effect = advance
        with guards.booking_preference_run(self.settings, {}):
            with self.assertRaises(guards.PreparedCoordinationChanged):
                booker.wait_until_datetime(self.opening)
        self.assertLessEqual(self.elapsed, 1)
        self.assertLess(self.local_time, self.opening)

    def test_change_after_preparation_check_still_stops_at_final_save(self):
        save = mock.Mock()
        with guards.booking_preference_run(self.settings, {}):
            guards.check_prepared_booking()
            tempo.publish_snapshot(document(revision=2), expected_revision=1, now=NOW)
            with self.assertRaises(guards.BookingPreferencesChanged) as failure:
                with guards.booking_save_boundary(day=DAY, start='09:00', end='10:00', room='Practice A'):
                    save()
        self.assertNotIsInstance(failure.exception, guards.PreparedCoordinationChanged)
        save.assert_not_called()

    def test_explicit_action_is_never_retried(self):
        result, run, _ = self.run_worker(lambda *_: self.change_while_prepared(), scheduled=False)
        self.assertEqual(result, 3)
        self.assertEqual(run.call_count, 1)

    def test_owner_preference_change_stops_without_automatic_retry(self):
        def worker(*_args):
            update_settings(lambda settings: settings.update(disabled_dates=[str(DAY)]), self.settings)
            booker.wait_until_datetime(self.opening)
        result, run, _ = self.run_worker(worker)
        self.assertEqual(result, 3)
        self.assertEqual(run.call_count, 1)
        self.sleep.assert_not_called()

    def test_preference_change_after_tempo_invalidation_cannot_enter_replan(self):
        calls = []
        def worker(*_args):
            calls.append(True)
            if len(calls) == 1:
                try:
                    self.change_while_prepared()
                except guards.PreparedCoordinationChanged:
                    update_settings(lambda settings: settings.update(disabled_dates=[str(DAY)]), self.settings)
                    raise
            return 0
        result, run, _ = self.run_worker(worker)
        self.assertEqual(result, 3)
        run.assert_called_once()

    def test_runtime_progress_between_tempo_passes_is_reloaded_and_retained(self):
        seen = []
        progress = [{'date': str(DAY), 'room': 'Practice A', 'startTime': '09:00',
                     'endTime': '09:30', 'target_end': '10:00', 'eventId': 123}]
        def worker(args, settings, *_rest):
            seen.append(settings)
            if len(seen) == 1:
                try:
                    self.change_while_prepared()
                except guards.PreparedCoordinationChanged:
                    update_settings(lambda s: s.update(extendable_bookings=progress), self.settings)
                    raise
            self.assertEqual(settings['extendable_bookings'], progress)
            return 0
        result, run, _ = self.run_worker(worker)
        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(load_settings(self.settings)['extendable_bookings'], progress)

    def test_preparation_check_without_a_run_does_not_read_live_state(self):
        with mock.patch.object(guards, 'tempo_save_boundary') as boundary:
            guards.check_prepared_booking()
        boundary.assert_not_called()


if __name__ == '__main__':
    unittest.main()
