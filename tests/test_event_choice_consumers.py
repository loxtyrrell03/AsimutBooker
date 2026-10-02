"""Invented event data only: scoped desktop saves and exact scanner consumers."""
import contextlib
import io
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import book_week as b
import gui
import room_upgrade_runtime
import tempo_coordination
from agenda_snapshot import publish_agenda_snapshot
from app_settings import atomic_write_json, load_settings
from event_choice_editor import EventChoiceDraft
from event_identity import event_identity, event_identity_v2, event_respect_key
from phone_system import read_view, run_local_action, SystemConflict
from tests.test_config_and_agenda_regressions import _AgendaPage


def event(identifier=101, **changes):
    return dict(dict(eventId=identifier, date='2030-10-15', startTime='10:00',
                     endTime='11:00', title='Example class', room=None, isReservation=False), **changes)


class EventChoiceEditorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.events = [event(), event(102), event(103, title='Reservation', isReservation=True, room='Example A')]
        self.outside = event_identity(event(900, date='2030-12-01'))
        self.settings = self.root / 'data/settings.json'
        atomic_write_json(self.settings, {'ignored_events': [self.outside], 'unrelated': 'keep',
                                         'manual_booking_overrides': {'keep': 'untouched'}})
        publish_agenda_snapshot(self.events, [date(2030, 10, 15)], observed_at=datetime.now(timezone.utc),
                                path=self.root / 'data/agenda_snapshot.json')
        self.draft = EventChoiceDraft(read_view('events', self.root))
        self.key = next(row['key'] for row in self.draft.rows.values() if row['event_id'] == 101)

    def test_one_exact_checkbox_preserves_out_of_view_choices_and_pins(self):
        self.draft.select({self.key: False})
        self.assertEqual(self.draft.request()['changes'], {self.key: True})
        self.draft.save(self.root)
        saved = load_settings(self.settings)
        self.assertEqual(set(saved['ignored_events']), {self.outside, event_identity(self.events[0])})
        self.assertEqual(saved['manual_booking_overrides'], {'keep': 'untouched'})
        self.assertEqual(saved['unrelated'], 'keep')
        self.assertEqual([row['ignored'] for row in read_view('events', self.root)['events']], [True, False, False])

    def test_phone_interleaving_rejects_original_desktop_revision_and_keeps_draft(self):
        other = next(row['key'] for row in self.draft.rows.values() if row['event_id'] == 102)
        run_local_action('events_save', {'revision': self.draft.document['revision'], 'changes': {other: True}}, self.root)
        self.draft.select({self.key: False})
        before = self.settings.read_bytes()
        with self.assertRaises(SystemConflict):
            self.draft.save(self.root)
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertTrue(self.draft.dirty)
        self.assertFalse(self.draft.uncertain)
        self.assertEqual(self.draft.request()['changes'], {self.key: True})

    def test_post_commit_failure_blocks_another_save_and_preserves_exact_draft(self):
        self.draft.select({self.key: False})
        with mock.patch('booking_plan.clear_booking_plan', side_effect=OSError('example failure')):
            with self.assertRaises(OSError):
                self.draft.save(self.root)
        self.assertTrue(self.draft.uncertain)
        self.assertIn(event_identity(self.events[0]), load_settings(self.settings)['ignored_events'])
        with mock.patch('phone_system.run_local_action') as writer:
            with self.assertRaisesRegex(ValueError, 'previous save needs review'):
                self.draft.save(self.root)
            writer.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'uncertain save'):
            self.draft.select({self.key: True})
        self.assertFalse(self.draft.values[self.key])

    def test_unchanged_draft_does_not_dispatch_and_unsupported_rows_cannot_change(self):
        with mock.patch('phone_system.run_local_action') as writer:
            self.draft.save(self.root)
            writer.assert_not_called()
        reservation = next(row['key'] for row in self.draft.rows.values() if row['is_reservation'])
        with self.assertRaisesRegex(ValueError, 'cannot be changed'):
            self.draft.select({reservation: False})
        self.assertFalse(self.draft.dirty)

    def test_explicit_bind_migrates_only_one_resolved_older_choice(self):
        # A unique tuple is required to resolve an older choice safely.
        unique = event(104, title='Example separate lesson')
        publish_agenda_snapshot([unique], [date(2030, 10, 15)], observed_at=datetime.now(timezone.utc),
                                path=self.root / 'data/agenda_snapshot.json')
        atomic_write_json(self.settings, {'ignored_events': [self.outside, event_identity_v2(unique)]})
        draft = EventChoiceDraft(read_view('events', self.root))
        key = next(iter(draft.rows))
        self.assertEqual(draft.request()['changes'], {})
        draft.bind_exact(key)
        self.assertEqual(draft.request()['changes'], {key: True})
        draft.save(self.root)
        self.assertEqual(set(load_settings(self.settings)['ignored_events']), {self.outside, event_identity(unique)})

    def test_explicit_respect_preserves_ambiguous_legacy_without_future_reactivation(self):
        atomic_write_json(self.settings, {'ignored_events': [event_identity_v2(self.events[0])]})
        draft = EventChoiceDraft(read_view('events', self.root))
        draft.keep_protected(self.key)
        self.assertEqual(draft.request()['changes'], {self.key: False})
        draft.save(self.root)
        self.assertIn(event_respect_key(self.events[0]), load_settings(self.settings)['ignored_events'])
        publish_agenda_snapshot([self.events[0]], [date(2030, 10, 15)], observed_at=datetime.now(timezone.utc),
                                path=self.root / 'data/agenda_snapshot.json')
        self.assertFalse(read_view('events', self.root)['events'][0]['ignored'])

    def test_assistant_context_does_not_describe_exact_respect_as_an_ignored_event(self):
        from assistant_context import _settings_context
        atomic_write_json(self.settings, {'ignored_events': [self.outside, event_respect_key(self.events[0])]})
        preferences = _settings_context(self.settings)
        self.assertEqual(preferences['ignored_event_count'], 1)
        self.assertEqual(preferences['respected_event_count'], 1)
        self.assertIn('not currently effective', preferences['event_choice_count_scope'])

    def fake_gui(self):
        instance = object.__new__(gui.AsimutBookerGUI)
        instance.event_draft = self.draft
        instance.events_progress_var = mock.Mock()
        instance.event_vars = {key: mock.Mock(get=lambda value=value: value) for key, value in self.draft.values.items()}
        instance.event_save_btn = mock.Mock()
        instance.log = mock.Mock()
        return instance

    def test_background_read_and_failed_explicit_read_keep_draft_revision(self):
        self.draft.select({self.key: False})
        instance = self.fake_gui()
        frame = mock.Mock()
        with mock.patch('phone_system.read_view', return_value={'revision': 'new', 'events': []}):
            instance._load_and_display_events(frame)
        self.assertIs(instance.event_draft, self.draft)
        frame.winfo_children.assert_not_called()
        with mock.patch('phone_system.read_view', side_effect=OSError('read failed')):
            instance._load_and_display_events(frame, discard=True)
        self.assertIs(instance.event_draft, self.draft)
        self.assertTrue(self.draft.dirty)

    def test_gui_unknown_value_error_does_not_claim_save_failed_or_close(self):
        self.draft.select({self.key: False})
        instance = self.fake_gui()
        dialog = mock.Mock()
        with mock.patch('phone_system.ROOT', self.root), mock.patch('phone_system.run_local_action', side_effect=ValueError('after-write')):
            with mock.patch('gui.messagebox.showerror') as show:
                instance._save_events_and_close(dialog)
        self.assertTrue(self.draft.uncertain)
        self.assertIn('could not be confirmed', show.call_args.args[1])
        dialog.destroy.assert_not_called()
        instance.event_save_btn.config.assert_called_with(state=gui.tk.DISABLED)


class ExactEventConsumersTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.object(tempo_coordination, 'SNAPSHOT_PATH', self.root / 'tempo.json'))
        self.stack.enter_context(mock.patch.object(tempo_coordination, 'RUNTIME_LOCK_PATH', self.root / 'runtime.lock'))
        self.stack.enter_context(mock.patch.object(b, 'AGENDA_SNAPSHOT_FILE', self.root / 'agenda.json'))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def scan(self, events, ignored):
        today = date(2030, 10, 15)
        raw = [dict(item, daysDiff=0, location='Example classroom') for item in events]
        for row in raw:
            row.pop('room', None)
        tracker = b.BookingTracker()
        b.scan_agenda(_AgendaPage(today, raw, [today]), tracker, today, ignored_events=ignored,
                      window_dates=[today], snapshot_path=self.root / 'agenda.json')
        return tracker

    def test_scan_exact_choice_does_not_free_identical_unselected_event(self):
        first, second = event(), event(102)
        selected = [event_identity(first)]
        self.assertFalse(self.scan([first], selected).overlaps_conflict(date(2030, 10, 15), 10, 11))
        self.assertTrue(self.scan([first, second], selected).overlaps_conflict(date(2030, 10, 15), 10, 11))

    def test_v2_collision_keeps_all_current_events_protected(self):
        first, second = event(), event(102)
        self.assertTrue(self.scan([first, second], [event_identity_v2(first)]).overlaps_conflict(date(2030, 10, 15), 10, 11))

    def test_explicit_respect_stays_blocking_when_legacy_collision_disappears(self):
        first = event()
        choices = [event_identity_v2(first), event_respect_key(first)]
        self.assertTrue(self.scan([first], choices).overlaps_conflict(date(2030, 10, 15), 10, 11))
        tracker = SimpleNamespace(agenda_events=[first], tempo_coordination=SimpleNamespace(protected_ids=lambda: set()))
        rows, ignored = room_upgrade_runtime.planning_events(b, tracker, choices)
        self.assertTrue(rows[0].get('blocksConflict', True))
        self.assertEqual(ignored, set())

    def test_upgrade_and_progressive_inputs_preserve_unselected_and_protected_ids(self):
        first, second = event(), event(102)
        tracker = SimpleNamespace(agenda_events=[first, second], tempo_coordination=SimpleNamespace(protected_ids=lambda: {500}))
        rows, protected = room_upgrade_runtime.planning_events(b, tracker, [event_identity(first)])
        self.assertEqual([row.get('blocksConflict', True) for row in rows], [False, True])
        self.assertEqual(protected, {101, 500})
        self.assertNotIn('blocksConflict', tracker.agenda_events[0])

    def test_gui_display_helper_uses_exact_identity_for_equal_tuples(self):
        first, second = event(), event(102)
        rows, _ = gui.build_event_preference_rows([first, second], [event_identity(first)])
        self.assertEqual([enabled for _, _, enabled in rows], [False, True])
        self.assertNotEqual(rows[0][1], rows[1][1])


if __name__ == '__main__':
    unittest.main()
