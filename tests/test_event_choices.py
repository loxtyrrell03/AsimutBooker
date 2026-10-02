"""Dated exact conflict choices use only disposable agenda/settings fixtures."""
from datetime import date, datetime, timedelta, timezone
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agenda_snapshot import publish_agenda_snapshot
from app_settings import atomic_write_json, load_settings
from event_identity import EventIdentityError, event_identity_v2, event_identity_v3, event_respect_key, legacy_event_identity
from phone_system import project_event_choices, read_view, run_local_action, SystemConflict
from runtime_guard import SingleInstanceAlreadyRunning, SingleInstanceLock


class EventChoiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='booker-event-choices-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'data/settings.json'
        self.event = dict(eventId=100, date='2030-10-15', startTime='10:00', endTime='11:00',
                          title='Example ordinary class', room='Example A', isReservation=False)
        self.events = [self.event]
        self.ignored = ['out-of-view-owner-choice']
        self.seed()

    def seed(self, *, observed=None):
        atomic_write_json(self.path, dict(unrelated='keep', ignored_events=self.ignored,
                                          manual_booking_overrides={'example': 'keep'}))
        self.snapshot(observed=observed)

    def snapshot(self, *, observed=None):
        publish_agenda_snapshot(self.events, [date(2030, 10, 15), date(2030, 10, 16)],
                                observed_at=observed or datetime.now(timezone.utc),
                                path=self.root / 'data/agenda_snapshot.json')

    def document(self):
        return read_view('events', self.root)

    def save(self, document, changes):
        return run_local_action('events_save', dict(revision=document['revision'], changes=changes), self.root)

    def test_identical_tuple_rows_remain_distinct_and_only_selected_remote_event_changes(self):
        self.events.append(self.event | {'eventId': 101})
        self.seed()
        document = self.document()
        self.assertEqual(len({row['key'] for row in document['events']}), 2)
        selected = next(row for row in document['events'] if row['event_id'] == 100)
        self.save(document, {selected['key']: True})
        saved = self.document()
        self.assertEqual({row['event_id']: row['ignored'] for row in saved['events']}, {100: True, 101: False})
        self.assertEqual(load_settings(self.path)['ignored_events'], sorted(self.ignored + [event_identity_v3(self.event)]))
        self.assertTrue(all(row['eligible'] for row in saved['events']))
        self.assertEqual(load_settings(self.path)['unrelated'], 'keep')
        self.assertEqual(load_settings(self.path)['manual_booking_overrides'], {'example': 'keep'})

    def test_projection_uses_supplied_snapshot_without_loading_or_writing(self):
        with patch('phone_system.load_settings', side_effect=AssertionError('No second settings read')), \
                patch('phone_system.read_agenda_snapshot', side_effect=AssertionError('No second agenda read')):
            document = project_event_choices(self.events, [], stale=False,
                                             observed_at='2030-10-15T08:00:00+00:00', covered_dates=['2030-10-15'])
        row = document['events'][0]
        self.assertEqual(row['identity'], event_identity_v3(self.event))
        self.assertEqual((row['event_id'], row['is_reservation'], row['eligible']), (100, False, True))
        self.assertEqual(document['covered_dates'], ['2030-10-15'])
        self.assertEqual(document['observed_at'], '2030-10-15T08:00:00+00:00')

    def test_malformed_choice_storage_fails_closed(self):
        for invalid in ('saved-key', ['key', False]):
            with self.subTest(invalid=invalid), self.assertRaises(EventIdentityError):
                project_event_choices(self.events, invalid, stale=False,
                                      observed_at='2030-10-15T08:00:00+00:00', covered_dates=['2030-10-15'])

    def test_rows_expose_reservations_and_missing_ids_without_write_authority(self):
        self.events += [self.event | {'eventId': 101, 'isReservation': True},
                        self.event | {'eventId': None, 'title': 'Unknown identity'}]
        self.seed()
        document = self.document()
        ineligible = [row for row in document['events'] if not row['eligible']]
        self.assertEqual(len(ineligible), 2)
        before = self.path.read_bytes()
        for row in ineligible:
            self.assertTrue(row['unsupported_reason'])
            with self.assertRaises(SystemConflict):
                self.save(document, {row['key']: True})
            self.assertEqual(self.path.read_bytes(), before)

    def test_stale_changed_identity_or_changed_reviewed_field_rejects_whole_patch(self):
        for changed in ({'eventId': 102}, {'date': '2030-10-16'}, {'title': 'New title'},
                        {'startTime': '10:15'}, {'endTime': '11:15'}, {'room': 'Example B'},
                        {'isReservation': True}):
            self.events = [self.event]
            self.seed()
            document = self.document()
            self.events = [self.event | changed]
            self.snapshot()
            before = self.path.read_bytes()
            with self.subTest(changed=changed), self.assertRaises(SystemConflict):
                self.save(document, {document['events'][0]['key']: True})
            self.assertEqual(self.path.read_bytes(), before)
        self.events = [self.event]
        self.seed(observed=datetime.now(timezone.utc) - timedelta(days=2))
        document = self.document()
        self.assertTrue(document['stale'])
        with self.assertRaises(SystemConflict):
            self.save(document, {document['events'][0]['key']: True})

    def test_unrelated_and_out_of_view_old_choices_are_never_migrated(self):
        second = self.event | {'eventId': 101, 'title': 'Another event', 'startTime': '12:00', 'endTime': '13:00'}
        self.events.append(second)
        legacy = legacy_event_identity(second)
        self.ignored += [legacy, event_identity_v2(self.event | {'date': '2030-10-16'})]
        self.seed()
        before = self.path.read_bytes()
        document = self.document()
        self.assertEqual(self.path.read_bytes(), before)
        selected = next(row for row in document['events'] if row['event_id'] == 100)
        old = next(row for row in document['events'] if row['event_id'] == 101)
        self.assertEqual((old['ignored'], old['choice_basis']), (True, 'legacy'))
        self.save(document, {selected['key']: True})
        self.assertEqual(load_settings(self.path)['ignored_events'], sorted(self.ignored + [event_identity_v3(self.event)]))

    def test_explicit_unchanged_value_can_bind_unique_older_choice_to_exact_current_event(self):
        for old in (legacy_event_identity(self.event), event_identity_v2(self.event)):
            self.ignored = [old, 'unrelated']
            self.seed()
            document = self.document()
            row = document['events'][0]
            self.assertTrue(row['ignored'])
            self.assertIn(row['choice_basis'], ('legacy', 'v2'))
            self.save(document, {row['key']: True})
            self.assertEqual(load_settings(self.path)['ignored_events'], sorted(['unrelated', event_identity_v3(self.event)]))
            self.assertEqual(self.document()['events'][0]['choice_basis'], 'exact')

    def test_explicit_respect_removes_only_unique_aliases_for_that_exact_event(self):
        self.ignored += [legacy_event_identity(self.event), event_identity_v2(self.event), event_identity_v3(self.event)]
        self.seed()
        document = self.document()
        self.save(document, {document['events'][0]['key']: False})
        self.assertEqual(load_settings(self.path)['ignored_events'], sorted(['out-of-view-owner-choice', event_respect_key(self.event)]))
        self.assertEqual(self.document()['events'][0]['choice_basis'], 'exact')

    def test_explicit_respect_remains_authoritative_when_ambiguous_old_choice_becomes_unique(self):
        neighbor = self.event | {'eventId': 101}
        self.events.append(neighbor)
        self.ignored += [legacy_event_identity(self.event), event_identity_v2(self.event)]
        self.seed()
        original = self.document()
        row = next(row for row in original['events'] if row['event_id'] == 100)
        self.save(original, {row['key']: False})
        saved = self.document()
        self.assertNotEqual(saved['revision'], original['revision'])
        self.assertTrue(set(self.ignored) <= set(load_settings(self.path)['ignored_events']))
        self.events = [self.event]
        self.snapshot()
        row = self.document()['events'][0]
        self.assertEqual((row['ignored'], row['choice_basis']), (False, 'exact'))
        self.assertFalse(row['unresolved_choice'])
        # A later explicit Allow replaces only its exact negative decision.
        self.save(self.document(), {row['key']: True})
        self.assertNotIn(event_respect_key(self.event), load_settings(self.path)['ignored_events'])
        self.assertEqual((self.document()['events'][0]['ignored'], self.document()['events'][0]['choice_basis']), (True, 'exact'))

    def test_default_respect_can_be_explicitly_bound_without_clearing_out_of_view_choices(self):
        document = self.document()
        self.save(document, {document['events'][0]['key']: False})
        self.assertEqual(load_settings(self.path)['ignored_events'], sorted(self.ignored + [event_respect_key(self.event)]))
        self.assertNotEqual(document['revision'], self.document()['revision'])

    def test_exact_respect_invalidates_display_and_final_save_and_queues_normal_preference_check(self):
        from booking_plan import booking_plan_fingerprint
        from booking_preferences_guard import BookingPreferencesChanged, booking_preference_run, booking_save_boundary
        before = load_settings(self.path)
        document = self.document()
        with patch('tempo_coordination.current_snapshot', return_value=SimpleNamespace(enabled=False)), \
                patch('booking_preferences_guard.coordination_run', side_effect=nullcontext), \
                patch('booking_preferences_guard.tempo_save_boundary', side_effect=lambda **_: nullcontext()):
            old_fingerprint = booking_plan_fingerprint(before, config_path=self.root / 'unused.yaml')
            with booking_preference_run(self.path, before):
                self.save(document, {document['events'][0]['key']: False})
                current = load_settings(self.path)
                self.assertNotEqual(old_fingerprint, booking_plan_fingerprint(current, config_path=self.root / 'unused.yaml'))
                self.assertEqual(current['preference_run']['state'], 'pending')
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail('An in-flight run must not cross Save after Respect changes')

    def test_ambiguous_older_choice_is_ineffective_visible_and_preserved_on_exact_edit(self):
        self.events.append(self.event | {'eventId': 101})
        self.ignored += [legacy_event_identity(self.event), event_identity_v2(self.event)]
        self.seed()
        document = self.document()
        self.assertEqual(document['unresolved_choice_count'], 2)
        self.assertTrue(all(row['unresolved_choice'] and not row['ignored'] for row in document['events']))
        self.save(document, {document['events'][0]['key']: True})
        self.assertTrue(set(self.ignored) <= set(load_settings(self.path)['ignored_events']))
        self.assertEqual(sum(row['ignored'] for row in self.document()['events']), 1)

    def test_newly_changed_concurrent_choice_rejects_without_losing_any_saved_choice(self):
        document = self.document()
        current = load_settings(self.path)
        current['ignored_events'].append('concurrent-out-of-view')
        atomic_write_json(self.path, current)
        before = self.path.read_bytes()
        with self.assertRaises(SystemConflict):
            self.save(document, {document['events'][0]['key']: True})
        self.assertEqual(self.path.read_bytes(), before)

    def test_mixed_ineligible_patch_is_rejected_without_partial_save_or_plan_clear(self):
        self.events.append(self.event | {'eventId': 101, 'isReservation': True})
        self.seed()
        document = self.document()
        before = self.path.read_bytes()
        with patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SystemConflict):
            self.save(document, {row['key']: True for row in document['events']})
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_active_runtime_keeps_choice_and_plan_unchanged(self):
        document = self.document()
        before = self.path.read_bytes()
        with SingleInstanceLock(self.root / 'data/booker-runtime.lock'), \
                patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SingleInstanceAlreadyRunning):
            self.save(document, {document['events'][0]['key']: True})
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_unchanged_new_observation_keeps_revision_but_scope_change_does_not(self):
        first = self.document()
        self.snapshot(observed=datetime.now(timezone.utc) + timedelta(seconds=1))
        second = self.document()
        self.assertEqual(first['revision'], second['revision'])
        scoped = project_event_choices(self.events, self.ignored, stale=False,
            observed_at=second['observed_at'], covered_dates=['2030-10-15'])
        self.assertNotEqual(first['revision'], scoped['revision'])

    def test_full_reviewed_title_and_room_are_not_truncated(self):
        self.events = [self.event | {'title': 'T' * 250, 'room': 'R' * 110}]
        self.seed()
        row = self.document()['events'][0]
        self.assertEqual(row['title'], 'T' * 250)
        self.assertEqual(row['room'], 'R' * 110)

    def test_same_remote_date_conflict_is_ineligible_and_rejects_save(self):
        self.events.append(self.event | {'title': 'Conflicting duplicate'})
        self.seed()
        document = self.document()
        self.assertTrue(all(not row['eligible'] and row['unresolved_choice'] for row in document['events']))
        with self.assertRaises(SystemConflict):
            self.save(document, {document['events'][0]['key']: True})
