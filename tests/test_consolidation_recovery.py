import copy
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import book_week as b
import mutation_receipts as journal
from agenda_snapshot import apply_verified_reservation, publish_agenda_snapshot, read_agenda_snapshot
from room_upgrades import (Reservation, RoomConsolidation, classify_consolidation_outcome,
                           consolidation_from_receipt)


class ConsolidationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / 'receipts.json'
        self.day = date(2099, 1, 1)
        self.originals = (Reservation(1, self.day, 'Fallback', 720, 750),
                          Reservation(2, self.day, 'Corus', 750, 810),
                          Reservation(3, self.day, 'Fallback', 810, 840))
        self.new = Reservation(2, self.day, 'Weston', 720, 840)
        self.upgrade = RoomConsolidation(self.originals, self.new)
        def old(r):
            return dict(event_id=r.event_id, room=r.room, date=r.day.isoformat(),
                        start=r.as_booking()['startTime'], end=r.as_booking()['endTime'])
        self.receipt = journal.record_pending_consolidation(room='Weston', booking_date=str(self.day),
            start='12:00', end='14:00', event_url=self.new.event_url,
            original=old(self.upgrade.original), originals=[old(r) for r in self.originals], path=self.path)
        self.events = [{**r.as_booking(), 'isReservation': True, 'title': 'Reservation'} for r in self.originals]
        self.page = mock.MagicMock()
        self.stack.enter_context(mock.patch.object(b, 'list_pending_mutation_receipts', side_effect=lambda: journal.list_pending(self.path)))
        self.stack.enter_context(mock.patch.object(b, 'verify_mutation_receipt', side_effect=lambda rid, **kw: journal.mark_verified(rid, path=self.path, **kw)))
        self.stack.enter_context(mock.patch.object(b, 'resolve_mutation_receipt', side_effect=lambda rid, **kw: journal.mark_resolved(rid, path=self.path, **kw)))
        self.stack.enter_context(mock.patch.object(b, 'safe_goto'))
        self.proof = self.stack.enter_context(mock.patch.object(b, 'verify_persisted_booking_page'))
        self.stack.enter_context(mock.patch.object(b, 'load_settings_document', return_value={}))
        self.stack.enter_context(mock.patch.object(b, 'load_ignored_events', return_value=[]))
        self.stack.enter_context(mock.patch.object(b, 'require_live_room_policy', return_value=SimpleNamespace(booking_dates=lambda today: (self.day,))))
        def scan(page, tracker, *args, **kwargs):
            tracker.agenda_events = copy.deepcopy(self.events)
            tracker.agenda_active_event_ids = [e['eventId'] for e in self.events]
        self.stack.enter_context(mock.patch.object(b, 'scan_agenda', side_effect=scan))
        self.cancel = self.stack.enter_context(mock.patch.object(b, 'cancel_reservation_exact'))

    def secure(self):
        self.events = [{**e, **self.new.as_booking()} if e['eventId'] == self.new.event_id else e for e in self.events]

    def retire(self, page, events, *, event_id, consolidation_receipt, **kwargs):
        self.assertEqual(classify_consolidation_outcome(self.events, self.receipt), 'secured')
        self.assertEqual(consolidation_receipt, self.receipt)
        self.assertIn(event_id, (1, 3))
        self.events = [e for e in self.events if e['eventId'] != event_id]
        return True, len(self.events)

    def test_journal_round_trip_and_duration_identity_validation(self):
        self.assertEqual(consolidation_from_receipt(journal.list_pending(self.path)[0]), self.upgrade)
        for change in ({'end': '13:30'}, {'originals': self.receipt['originals'] * 2},
                       {'original': {**self.receipt['original'], 'event_id': 99}}):
            changed = {**self.receipt, **change}
            with self.assertRaises(journal.MutationReceiptError):
                journal._validate_receipt(changed, changed['id'])

    def test_untouched_recovery_keeps_every_original_and_never_cancels(self):
        self.assertFalse(b.finish_consolidation(self.page, self.receipt))
        self.cancel.assert_not_called()
        self.assertEqual(len(self.events), 3)
        self.assertEqual(journal.load_journal(self.path)['receipts'][self.receipt['id']]['status'], 'resolved')

    def test_recovery_only_retires_donors_after_full_replacement_proof(self):
        self.secure()
        self.cancel.side_effect = self.retire
        self.assertTrue(b.finish_consolidation(self.page, self.receipt))
        self.assertEqual([call.kwargs['event_id'] for call in self.cancel.call_args_list], [1, 3])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(Reservation.from_event(self.events[0]), self.new)
        self.assertFalse(journal.list_pending(self.path))

    def test_crash_after_one_retirement_resumes_without_repeating_it(self):
        self.secure()
        self.events = [e for e in self.events if e['eventId'] != 1]
        self.cancel.side_effect = self.retire
        self.assertTrue(b.finish_consolidation(self.page, self.receipt))
        self.assertEqual([call.kwargs['event_id'] for call in self.cancel.call_args_list], [3])

    def test_lost_cancellation_response_stops_then_reconciles_without_repeating(self):
        self.secure()
        def lost(*args, **kwargs):
            self.retire(*args, **kwargs)
            raise b.BookingVerificationError('Lost response')
        self.cancel.side_effect = lost
        with self.assertRaises(b.BookingVerificationError):
            b.finish_consolidation(self.page, self.receipt)
        self.assertEqual(len(journal.list_pending(self.path)), 1)
        self.assertEqual(Reservation.from_event(next(e for e in self.events if e['eventId'] == 2)), self.new)
        self.cancel.reset_mock()
        self.cancel.side_effect = self.retire
        self.assertTrue(b.finish_consolidation(self.page, self.receipt))
        self.assertEqual([call.kwargs['event_id'] for call in self.cancel.call_args_list], [3])

    def test_read_only_reconciliation_does_not_retire_secured_donors(self):
        self.secure()
        b.reconcile_pending_mutation_receipts(self.page, self.events)
        self.cancel.assert_not_called()
        self.assertEqual(len(self.events), 3)
        self.assertEqual(len(journal.list_pending(self.path)), 1)

    def test_missing_changed_or_duplicate_anchor_and_changed_donor_block_cleanup(self):
        self.secure()
        before = copy.deepcopy(self.events)
        variants = ([e for e in before if e['eventId'] != 2], before + [before[1]],
                    [{**e, 'endTime': '13:30'} if e['eventId'] == 2 else e for e in before],
                    [{**e, 'room': 'Changed'} if e['eventId'] == 1 else e for e in before])
        for variant in variants:
            self.events = variant
            with self.assertRaises(b.BookingVerificationError):
                b.finish_consolidation(self.page, self.receipt)
        self.cancel.assert_not_called()

    def test_failed_independent_page_proof_never_retires_a_donor(self):
        self.secure()
        self.proof.side_effect = b.BookingVerificationError('No independent proof')
        with self.assertRaises(b.BookingVerificationError):
            b.finish_consolidation(self.page, self.receipt)
        self.cancel.assert_not_called()

    def test_display_replaces_all_exact_originals_but_keeps_unrelated_events_and_freshness(self):
        path = Path(self.temp) / 'agenda.json'
        unrelated = {**self.events[0], 'eventId': 8, 'startTime': '16:00', 'endTime': '17:00'}
        now = datetime.now(timezone.utc)
        publish_agenda_snapshot([*self.events, unrelated], [self.day], observed_at=now, path=path)
        self.assertTrue(apply_verified_reservation({**self.receipt, 'status': 'verified'}, path=path))
        snapshot = read_agenda_snapshot(path).snapshot
        self.assertEqual(snapshot.observed_at, now)
        self.assertEqual([e['eventId'] for e in snapshot.event_dicts()], [2, 8])
        self.assertEqual(Reservation.from_event(snapshot.event_dicts()[0]), self.new)
