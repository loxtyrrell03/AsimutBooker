"""Reservation protection controls use disposable local data only."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agenda_snapshot import publish_agenda_snapshot
from app_settings import SettingsError, atomic_write_json, load_settings
from booking_preferences_guard import BookingPreferencesChanged, booking_preference_run, booking_save_boundary
from manual_booking_overrides import assert_automatic_change_allowed
from mutation_receipts import record_pending_create
from phone_system import SystemConflict, read_view, run_local_action, validate_action
from reservation_pins import project_reservation_pins, record_for
from runtime_guard import SingleInstanceAlreadyRunning, SingleInstanceLock


class ReservationPinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='booker-pins-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'data/settings.json'
        self.event = dict(eventId=101, date='2030-10-15', room='B0.14', startTime='10:00',
                          endTime='11:00', title='Reservation', isReservation=True)
        self.other = self.event | dict(eventId=102, startTime='12:00', endTime='13:00')
        self.events = [self.event, self.other]
        self.settings = dict(unrelated={'keep': True}, ignored_events=['owner-choice'],
            rebooking_blackouts=[dict(date='2030-10-15', start_time='09:00', end_time='10:00')],
            manual_booking_overrides={'102': self.record(self.other)},
            observed_reservations={'101': self.record(self.event)},
            extendable_bookings=[self.goal(self.event), self.goal(self.other),
                self.goal(self.event | dict(eventId=103, room='B0.15'))])
        self.seed()

    def record(self, event):
        return {key: event[key] for key in ('date', 'room', 'startTime', 'endTime')}

    def goal(self, event, *, legacy=False):
        goal = self.record(event) | dict(created_at='2026-01-01T00:00:00+00:00',
            target_end=f"{int(event['endTime'][:2]) + 1:02}:00", horizon_minutes=240)
        if not legacy:
            goal.update(eventId=event['eventId'], event_url=f"https://rwcmd.asimut.net/arrangement?eventId={event['eventId']}")
        return goal

    def snapshot(self, *, observed=None):
        return publish_agenda_snapshot(self.events, [date(2030, 10, 15), date(2030, 10, 16)],
            observed_at=observed or datetime.now(timezone.utc), path=self.root / 'data/agenda_snapshot.json')

    def seed(self):
        atomic_write_json(self.path, self.settings)
        self.snapshot()

    def document(self):
        return read_view('pins', self.root)

    def selected(self, document=None, event_id=101):
        return next(row for row in (document or self.document())['reservations']
                    if row['target']['event_id'] == event_id)

    def save(self, *, document=None, target=None, protected=True):
        doc = document or self.document()
        return run_local_action('pins_save', dict(revision=doc['revision'],
            target=target or self.selected(doc)['target'], protected=protected), self.root)

    def test_pin_exact_current_and_only_its_goals_preserves_all_other_settings(self):
        self.settings['extendable_bookings'].append(self.goal(self.event, legacy=True))
        self.seed()
        before = deepcopy(self.settings)
        response = self.save()
        saved = load_settings(self.path)
        expected = before | dict(manual_booking_overrides=before['manual_booking_overrides'] |
            {'101': self.record(self.event)}, extendable_bookings=before['extendable_bookings'][1:3])
        self.assertEqual(saved, expected)
        self.assertTrue(response['ok'])
        self.assertTrue(self.selected(response['pins'])['pin_matches'])
        self.assertEqual(response['pins']['protected_ids'], [101, 102])
        self.assertNotIn('preference_run', saved)

    def test_unpin_never_restores_goals_reopens_time_or_repins_unchanged_scan(self):
        from manual_cancellations import remember_agenda
        self.save()
        before = load_settings(self.path)
        self.save(protected=False)
        after = load_settings(self.path)
        self.assertEqual(after, before | dict(manual_booking_overrides={'102': self.record(self.other)}))
        remember_agenda(self.events, [date(2030, 10, 15)], settings_path=self.path,
            receipts_path=self.root / 'data/mutation_receipts.json', now=datetime(2030, 10, 15, 8))
        self.assertNotIn('101', load_settings(self.path)['manual_booking_overrides'])
        self.assertEqual(after['rebooking_blackouts'], before['rebooking_blackouts'])

    def test_changed_live_tuple_shows_old_record_and_requires_current_exact_review(self):
        self.save()
        old = self.document()
        self.events[0] = self.event | dict(startTime='10:15')
        self.snapshot()
        current = self.document()
        row = self.selected(current)
        self.assertTrue(row['protected'] and row['eligible'])
        self.assertFalse(row['pin_matches'])
        self.assertEqual(row['saved_pin'], self.record(self.event))
        before = self.path.read_bytes()
        with self.assertRaises(SystemConflict):
            self.save(document=old)
        with self.assertRaises(SystemConflict):
            self.save(document=current, target=self.selected(old)['target'])
        self.assertEqual(self.path.read_bytes(), before)
        self.save(document=current)
        self.assertEqual(load_settings(self.path)['manual_booking_overrides']['101'], self.record(self.events[0]))

    def test_exact_current_review_can_remove_changed_pin_but_never_old_target(self):
        self.save()
        self.events[0] = self.event | dict(room='B0.15')
        self.snapshot()
        self.save(protected=False)
        self.assertFalse(self.selected()['protected'])

    def test_unpin_preserves_existing_latent_goal_and_makes_its_effect_reviewable(self):
        from book_week import load_extendable_bookings
        document = self.document()
        row = self.selected(document, 102)
        self.assertEqual(row['extension_goals'], [self.settings['extendable_bookings'][1]])
        self.assertNotIn(102, [goal.get('eventId') for goal in load_extendable_bookings(self.settings)])
        self.save(document=document, target=row['target'], protected=False)
        settings = load_settings(self.path)
        self.assertEqual(settings['extendable_bookings'], self.settings['extendable_bookings'])
        self.assertIn(102, [goal.get('eventId') for goal in load_extendable_bookings(settings)])

    def test_all_pins_goals_and_agenda_identity_are_revision_bound(self):
        variants = [lambda settings: settings['manual_booking_overrides'].update({'104': self.record(self.other)}),
                    lambda settings: settings['extendable_bookings'][0].update(target_end='11:45')]
        for change in variants:
            self.seed()
            doc = self.document()
            settings = load_settings(self.path)
            change(settings)
            atomic_write_json(self.path, settings)
            before = self.path.read_bytes()
            with self.assertRaises(SystemConflict):
                self.save(document=doc)
            self.assertEqual(self.path.read_bytes(), before)
        for change in (dict(eventId=110), dict(room='B0.15'), dict(endTime='11:15'),
                       dict(isReservation=False), dict(title='Reservation changed')):
            self.events = [self.event, self.other]
            self.seed()
            doc = self.document()
            self.events[0] = self.event | change
            self.snapshot()
            before = self.path.read_bytes()
            with self.assertRaises(SystemConflict):
                self.save(document=doc)
            self.assertEqual(self.path.read_bytes(), before)

    def test_observation_only_does_not_change_revision_but_scope_does(self):
        old = self.document()
        self.snapshot(observed=datetime.now(timezone.utc) - timedelta(seconds=2))
        self.assertEqual(self.document()['revision'], old['revision'])
        publish_agenda_snapshot(self.events, [date(2030, 10, 15)],
            path=self.root / 'data/agenda_snapshot.json')
        self.assertNotEqual(self.document()['revision'], old['revision'])

    def test_stale_pending_unknown_and_started_are_readonly_without_writes(self):
        before = self.path.read_bytes()
        self.snapshot(observed=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertFalse(self.selected()['eligible'])
        with self.assertRaises(SystemConflict):
            self.save()
        self.snapshot()
        record_pending_create(room='B0.14', booking_date='2030-10-15', start='14:00', end='15:00',
            path=self.root / 'data/mutation_receipts.json')
        self.assertEqual(self.document()['pending_count'], 1)
        with self.assertRaises(SystemConflict):
            self.save()
        self.assertEqual(self.path.read_bytes(), before)
        snapshot = self.snapshot(observed=datetime(2030, 10, 15, 10, tzinfo=timezone.utc))
        for pending in (None, 0):
            doc = project_reservation_pins(snapshot.event_dicts(), self.settings, stale=False,
                observed_at=snapshot.observed_at.isoformat(), covered_dates=['2030-10-15','2030-10-16'],
                pending_count=pending, now=datetime(2030, 10, 15, 10, tzinfo=timezone.utc))
            self.assertFalse(self.selected(doc)['eligible'])

    def test_missing_corrupt_agenda_or_receipts_never_changes_settings(self):
        doc = self.document()
        before = self.path.read_bytes()
        (self.root / 'data/agenda_snapshot.json').write_text('{}', encoding='utf-8')
        self.assertTrue(self.document()['stale'])
        with self.assertRaises(SystemConflict):
            self.save(document=doc)
        self.snapshot()
        (self.root / 'data/mutation_receipts.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(SettingsError):
            self.save(document=doc)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_expired_and_out_of_scope_saved_pins_are_visible_readonly(self):
        self.settings['manual_booking_overrides'].update({'201': self.record(self.event),
            '202': self.record(self.event) | dict(date='2020-01-01')})
        self.seed()
        doc = self.document()
        self.assertEqual(doc['protected_ids'], [102, 201, 202])
        before = self.path.read_bytes()
        for event_id in (201, 202):
            row = self.selected(doc, event_id)
            self.assertFalse(row['present'] or row['eligible'])
            self.assertTrue(row['protected'] and row['unsupported_reason'])
            with self.assertRaises(SystemConflict):
                self.save(document=doc, target=row['target'], protected=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_id_even_nonreservation_or_other_date_is_ineligible(self):
        for extra in (self.event | dict(room='B0.15'), self.event | dict(isReservation=False),
                      self.event | dict(date='2030-10-16')):
            self.events = [self.event, self.other, extra]
            self.snapshot()
            doc = self.document()
            self.assertTrue(all(not row['eligible'] for row in doc['reservations']
                                if row['target']['event_id'] == 101))
            with self.assertRaises(SystemConflict):
                self.save(document=doc)

    def test_missing_id_cancelled_or_uneditable_clock_is_ineligible(self):
        for change in (dict(eventId=None), dict(title='CANCELLED: Reservation'), dict(startTime='10:07')):
            self.events = [self.event | change]
            self.snapshot()
            row = next(row for row in self.document()['reservations'] if row['present'])
            self.assertFalse(row['eligible'])
            self.assertTrue(row['unsupported_reason'])

    def test_unknown_input_fields_invalid_id_and_nonboolean_are_rejected(self):
        doc = self.document()
        args = dict(revision=doc['revision'], target=self.selected(doc)['target'], protected=True)
        invalid = [args | dict(extra=True), args | dict(protected=1), args | dict(revision='invalid'),
                   args | dict(target=args['target'] | dict(extra='unknown')),
                   args | dict(target=args['target'] | dict(event_id=True))]
        for value in invalid:
            with self.assertRaises(ValueError):
                validate_action('pins_save', value)

    def test_unknown_pin_record_fields_reject_document_without_mutation(self):
        self.settings['manual_booking_overrides']['102']['allow_extension'] = True
        atomic_write_json(self.path, self.settings)
        before = self.path.read_bytes()
        with self.assertRaises(SettingsError):
            self.document()
        self.assertEqual(self.path.read_bytes(), before)

    def test_runtime_busy_and_precommit_rejection_keep_caches(self):
        doc = self.document()
        before = self.path.read_bytes()
        with SingleInstanceLock(self.root / 'data/booker-runtime.lock'), \
                patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SingleInstanceAlreadyRunning):
            self.save(document=doc)
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_new_pending_receipt_after_opening_blocks_same_revision_save(self):
        document = self.document()
        before = self.path.read_bytes()
        record_pending_create(room='B0.14', booking_date='2030-10-15', start='14:00', end='15:00',
            path=self.root / 'data/mutation_receipts.json')
        self.assertEqual(document['revision'], self.document()['revision'])
        with patch('booking_plan.clear_booking_plan') as clear, self.assertRaises(SystemConflict):
            self.save(document=document)
        clear.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_pin_invalidates_display_and_full_save_guard_without_queueing(self):
        from booking_plan import booking_plan_fingerprint
        before = load_settings(self.path)
        with patch('tempo_coordination.current_snapshot', return_value=SimpleNamespace(enabled=False)), \
                patch('booking_preferences_guard.coordination_run', side_effect=nullcontext), \
                patch('booking_preferences_guard.tempo_save_boundary', side_effect=lambda **_: nullcontext()):
            fingerprint = booking_plan_fingerprint(before, config_path=self.root / 'missing.yaml')
            with booking_preference_run(self.path, before), patch('booking_plan.clear_booking_plan') as clear:
                self.save()
                current = load_settings(self.path)
                self.assertNotEqual(fingerprint, booking_plan_fingerprint(current, config_path=self.root / 'missing.yaml'))
                self.assertNotIn('preference_run', current)
                self.assertEqual([call.args[0].name for call in clear.call_args_list], ['booking_plan.json', 'upgrade_plan.json'])
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail('Old run must stop before Save')
        with self.assertRaises(BookingPreferencesChanged):
            assert_automatic_change_allowed([101], path=self.path)

    def test_postcommit_cache_failure_is_recoverable_from_exact_saved_state(self):
        with patch('booking_plan.clear_booking_plan', side_effect=OSError('fixture invalidation failure')):
            with self.assertRaises(OSError):
                self.save()
        row = self.selected()
        self.assertTrue(row['protected'] and row['pin_matches'])

    def test_projection_does_not_read_disk_or_write_supplied_settings(self):
        snapshot = self.snapshot()
        before = deepcopy(self.settings)
        with patch('phone_system.load_settings', side_effect=AssertionError('No second settings read')), \
                patch('phone_system.read_agenda_snapshot', side_effect=AssertionError('No second agenda read')):
            doc = project_reservation_pins(snapshot.event_dicts(), self.settings, stale=False,
                observed_at=snapshot.observed_at.isoformat(), covered_dates=['2030-10-15','2030-10-16'], pending_count=0)
        self.assertTrue(self.selected(doc)['eligible'])
        self.assertEqual(self.settings, before)

    def test_london_future_boundary_and_dst_ambiguity_are_explicit(self):
        from reservation_pins import validate_target
        target = self.selected()['target']
        with self.assertRaises(ValueError):
            validate_target(target | dict(date='2030-10-27', start_time='01:00', end_time='01:30'))
        with self.assertRaises(ValueError):
            validate_target(target | dict(date='2030-03-31', start_time='01:00', end_time='01:30'))
        observed = datetime(2030, 10, 15, 8, 59, tzinfo=timezone.utc)
        snapshot = self.snapshot(observed=observed)
        for minute, expected in ((59, True), (60, False)):
            doc = project_reservation_pins(snapshot.event_dicts(), self.settings, stale=False,
                observed_at=observed.isoformat(), covered_dates=['2030-10-15','2030-10-16'], pending_count=0,
                now=datetime(2030, 10, 15, 8, tzinfo=timezone.utc) + timedelta(minutes=minute))
            self.assertEqual(self.selected(doc)['eligible'], expected)
