import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mutation_receipts import (
    MutationReceiptError,
    attach_event_url,
    list_pending,
    load_journal,
    mark_resolved,
    mark_verified,
    record_pending,
    record_pending_create,
    record_pending_extension,
    record_uncertain,
)


class MutationReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "mutation_receipts.json"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _details(self, index=0):
        return {
            "room": f"B0.{20 + index}",
            "booking_date": "2026-09-02",
            "start": f"{10 + index:02d}:00",
            "end": f"{11 + index:02d}:00",
            "path": self.path,
        }

    def test_missing_journal_is_empty_and_read_does_not_create_it(self):
        self.assertEqual(
            load_journal(self.path),
            {"schema_version": 1, "receipts": {}},
        )
        self.assertEqual(list_pending(self.path), [])
        self.assertFalse(self.path.exists())

    def test_records_all_three_pending_kinds_with_unique_ids(self):
        created = record_pending_create(**self._details(0))
        extended = record_pending_extension(**self._details(1))
        uncertain = record_uncertain(
            **self._details(2), event_url="https://rwcmd.asimut.net/event?eventId=123"
        )

        self.assertEqual(created["kind"], "create")
        self.assertEqual(extended["kind"], "extension")
        self.assertEqual(uncertain["kind"], "uncertain")
        self.assertEqual({receipt["status"] for receipt in list_pending(self.path)}, {"pending"})
        self.assertEqual(len({created["id"], extended["id"], uncertain["id"]}), 3)
        self.assertEqual(
            uncertain["event_url"], "https://rwcmd.asimut.net/event?eventId=123"
        )

    def test_verified_receipt_leaves_pending_list_and_keeps_other_receipts(self):
        first = record_pending_create(**self._details(0))
        second = record_pending_extension(**self._details(1))

        verified = mark_verified(
            first["id"],
            path=self.path,
            event_url="https://rwcmd.asimut.net/arrangement?eventId=456",
        )

        self.assertEqual(verified["status"], "verified")
        self.assertIn("verified_at", verified)
        self.assertEqual([receipt["id"] for receipt in list_pending(self.path)], [second["id"]])
        journal = load_journal(self.path)
        self.assertEqual(journal["receipts"][first["id"]]["event_url"], verified["event_url"])
        self.assertIn(second["id"], journal["receipts"])

    def test_event_url_can_be_durably_attached_before_full_verification(self):
        receipt = record_pending_create(**self._details())
        observed = attach_event_url(
            receipt["id"],
            "https://rwcmd.asimut.net/arrangement?eventId=456",
            path=self.path,
        )
        self.assertEqual(observed["status"], "pending")
        self.assertEqual(list_pending(self.path)[0]["event_url"], observed["event_url"])

    def test_resolve_pending_and_verified_receipts(self):
        pending = record_pending_create(**self._details(0))
        verified = record_pending_extension(**self._details(1))
        mark_verified(verified["id"], path=self.path)

        direct = mark_resolved(pending["id"], path=self.path, resolution="not applied")
        after_verification = mark_resolved(verified["id"], path=self.path)

        self.assertEqual(direct["status"], "resolved")
        self.assertEqual(direct["resolution"], "not applied")
        self.assertEqual(after_verification["status"], "resolved")
        self.assertIn("verified_at", after_verification)
        self.assertEqual(list_pending(self.path), [])

    def test_terminal_transitions_are_safe_and_idempotent(self):
        receipt = record_pending_create(**self._details())
        verified = mark_verified(receipt["id"], path=self.path)
        verified_again = mark_verified(receipt["id"], path=self.path)
        self.assertEqual(verified_again, verified)

        resolved = mark_resolved(receipt["id"], path=self.path)
        resolved_again = mark_resolved(receipt["id"], path=self.path)
        self.assertEqual(resolved_again, resolved)
        with self.assertRaises(MutationReceiptError):
            mark_verified(receipt["id"], path=self.path)

    def test_invalid_input_does_not_create_or_change_journal(self):
        with self.assertRaises(MutationReceiptError):
            record_pending("delete", **self._details())
        self.assertFalse(self.path.exists())

        valid = record_pending_create(**self._details())
        original = self.path.read_bytes()
        with self.assertRaises(MutationReceiptError):
            record_pending_create(
                room="B0.27",
                booking_date="02/09/2026",
                start="11:00",
                end="10:00",
                path=self.path,
            )
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list_pending(self.path)[0]["id"], valid["id"])

    def test_malformed_existing_data_fails_closed_without_overwrite(self):
        self.path.write_text('{"schema_version": 1, "receipts": [}', encoding="utf-8")
        original = self.path.read_bytes()

        with self.assertRaisesRegex(MutationReceiptError, "autonomous mutation must stop"):
            list_pending(self.path)
        with self.assertRaises(MutationReceiptError):
            record_pending_create(**self._details())

        self.assertEqual(self.path.read_bytes(), original)

    def test_structurally_invalid_existing_data_fails_closed(self):
        self.path.write_text(
            json.dumps({"schema_version": 1, "receipts": {"not-a-uuid": {}}}),
            encoding="utf-8",
        )
        original = self.path.read_bytes()

        with self.assertRaises(MutationReceiptError):
            record_pending_create(**self._details())

        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_writers_preserve_every_receipt(self):
        def write(index):
            return record_pending_create(**self._details(index))

        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(write, range(8)))

        journal = load_journal(self.path)
        self.assertEqual(len(journal["receipts"]), 8)
        self.assertEqual(set(journal["receipts"]), {receipt["id"] for receipt in receipts})

    def test_returned_values_cannot_mutate_the_journal(self):
        receipt = record_pending_create(**self._details())
        receipt["room"] = "CORRUPTED"
        pending = list_pending(self.path)
        pending[0]["room"] = "ALSO CORRUPTED"

        self.assertEqual(list_pending(self.path)[0]["room"], "B0.20")

    def test_unknown_receipt_fails_without_changing_other_records(self):
        receipt = record_pending_create(**self._details())
        original = self.path.read_bytes()
        with self.assertRaises(MutationReceiptError):
            mark_resolved("00000000-0000-0000-0000-000000000000", path=self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list_pending(self.path)[0]["id"], receipt["id"])


if __name__ == "__main__":
    unittest.main()
