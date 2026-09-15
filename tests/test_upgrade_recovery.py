import copy
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import book_week as b
from agenda_snapshot import apply_verified_reservation, publish_agenda_snapshot, read_agenda_snapshot


class UpgradeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(id="upgrade-test", kind="upgrade", status="pending", room="Best",
                            date="2099-01-01", start="14:00", end="16:00",
                            event_url="https://rwcmd.asimut.net/arrangement?eventId=42",
                            original=dict(event_id=42, room="Fallback", date="2099-01-01",
                                          start="12:00", end="14:00"))
        self.old = dict(eventId=42, isReservation=True, room="Fallback", date="2099-01-01",
                        startTime="12:00", endTime="14:00", title="Reservation")
        self.new = {**self.old, "room": "Best", "startTime": "14:00", "endTime": "16:00"}
        self.page = mock.Mock()
        for name in ("list_pending_mutation_receipts", "safe_goto", "verify_persisted_booking_page",
                     "remove_extendable_booking_by_event_id", "verify_mutation_receipt", "resolve_mutation_receipt"):
            patch = mock.patch.object(b, name)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)
        self.list_pending_mutation_receipts.return_value = [self.receipt]

    def test_applied_edit_reconciles_exact_event_and_cleans_old_extension(self):
        b.reconcile_pending_mutation_receipts(self.page, [self.new])
        self.verify_persisted_booking_page.assert_called_once_with(self.page, "Best", "2099-01-01", "14:00", "16:00")
        self.remove_extendable_booking_by_event_id.assert_called_once_with(42)
        self.verify_mutation_receipt.assert_called_once()
        self.resolve_mutation_receipt.assert_not_called()

    def test_unchanged_original_resolves_without_cancelling_or_altering_extensions(self):
        b.reconcile_pending_mutation_receipts(self.page, [self.old])
        self.verify_persisted_booking_page.assert_called_once_with(self.page, "Fallback", "2099-01-01", "12:00", "14:00")
        self.resolve_mutation_receipt.assert_called_once()
        self.remove_extendable_booking_by_event_id.assert_not_called()
        self.verify_mutation_receipt.assert_not_called()

    def test_missing_wrong_or_duplicate_event_blocks_all_further_mutation(self):
        for events in ([], [self.new, self.new], [{**self.new, "endTime": "15:00"}],
                       [{**self.new, "eventId": 43}], [{"eventId": 42}]):
            with self.subTest(events=events), self.assertRaises(b.BookingVerificationError):
                b.reconcile_pending_mutation_receipts(self.page, events)
        self.verify_mutation_receipt.assert_not_called()
        self.resolve_mutation_receipt.assert_not_called()

    def test_event_page_must_independently_confirm_agenda_state(self):
        self.verify_persisted_booking_page.side_effect = b.BookingVerificationError("changed remotely")
        with self.assertRaises(b.BookingVerificationError):
            b.reconcile_pending_mutation_receipts(self.page, [self.new])
        self.verify_mutation_receipt.assert_not_called()
        self.resolve_mutation_receipt.assert_not_called()

    def test_expiry_waits_until_both_original_and_new_intervals_have_ended(self):
        self.receipt.update(start="10:00", end="12:00")
        with mock.patch.object(b, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2099, 1, 1, 13)
            b.reconcile_pending_mutation_receipts(self.page, [self.old])
        self.verify_persisted_booking_page.assert_called_once()
        self.assertIn("original", self.resolve_mutation_receipt.call_args.kwargs["resolution"])

    def test_display_replaces_only_the_exact_original_and_preserves_other_bookings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agenda.json"
            other = {**self.old, "eventId": 43, "startTime": "17:00", "endTime": "18:00"}
            observed = datetime.now(timezone.utc)
            publish_agenda_snapshot([self.old, other], [date(2099, 1, 1)], observed_at=observed, path=path)
            receipt = {**self.receipt, "status": "verified"}
            self.assertTrue(apply_verified_reservation(receipt, path=path))
            snapshot = read_agenda_snapshot(path).snapshot
            self.assertEqual(snapshot.observed_at, observed)
            self.assertEqual(snapshot.event_dicts(), [self.new, other])
            self.assertTrue(apply_verified_reservation(receipt, path=path))
            changed = copy.deepcopy(receipt)
            changed["original"]["event_id"] = 99
            self.assertFalse(apply_verified_reservation(changed, path=path))
