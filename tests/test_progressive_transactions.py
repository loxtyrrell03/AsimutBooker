"""Exact persisted scope for partial upgrades and their compensating edits."""
import unittest
from datetime import date, datetime, timezone
from uuid import uuid4

from progressive_transactions import (TransferEdit, record, reservation, validate_transfer,
                                       transfer_allows_step, transfer_allows_cancel)
from room_upgrades import Reservation


def transfer_fixture(phase="initial", *, original_id=42, seed_id=99):
    day = date(2026, 9, 21)
    start = 720 if phase == "initial" else 810 if phase == "complete" else 750
    original = Reservation(original_id, day, "Fallback", start, 840)
    seed = None if phase == "initial" else Reservation(seed_id, day, "Best", 720, start)
    end = 750 if phase == "initial" else 840 if phase == "complete" else 765
    prefix = Reservation(seed_id if seed else original_id, day, "Best", 720, end)
    target = Reservation(prefix.event_id, day, "Best", 720, 840)
    remaining = [] if phase == "complete" else [record(Reservation(original_id, day, "Fallback", end, 840))]
    return {"plan_id": str(uuid4()), "originals": [record(original)], "remaining": remaining,
            "seed_before": record(seed) if seed else None, "replacement": record(prefix),
            "target": record(target), "opens_at": datetime(2026, 9, 16, 11, 30, tzinfo=timezone.utc).isoformat(),
            "baseline_ids": [original_id, seed_id] if seed else [original_id], "minimum_minutes": 30,
            "adjustment_order": [original_id], "started_steps": []}


class ProgressiveTransactionTests(unittest.TestCase):
    def test_initial_partial_final_payloads_round_trip_exact_records(self):
        for phase in ("initial", "extending", "complete"):
            with self.subTest(phase=phase):
                payload = transfer_fixture(phase)
                self.assertIs(validate_transfer(payload), payload)
                self.assertEqual(record(reservation(payload["replacement"])), payload["replacement"])

    def test_duration_edits_require_same_id_date_and_room(self):
        original = reservation(transfer_fixture()["originals"][0])
        for replacement in (
            Reservation(43, original.day, original.room, 750, 840),
            Reservation(42, date(2026, 9, 22), original.room, 750, 840),
            Reservation(42, original.day, "Best", 750, 840), original,
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                TransferEdit(original, replacement)

    def test_parent_allows_only_recorded_forward_and_restore_steps(self):
        t = transfer_fixture("extending")
        parent = {"kind": "transfer", "transfer": t}
        old, kept = reservation(t["originals"][0]), reservation(t["remaining"][0])
        seed, prefix = reservation(t["seed_before"]), reservation(t["replacement"])
        for a, z in ((old, kept), (kept, old), (seed, prefix), (prefix, seed)):
            self.assertTrue(transfer_allows_step(parent, TransferEdit(a, z)))
        unrecorded = Reservation(old.event_id, old.day, old.room, 780, 840)
        self.assertFalse(transfer_allows_step(parent, TransferEdit(old, unrecorded)))
        self.assertFalse(transfer_allows_step({**parent, "kind": "create"}, TransferEdit(old, kept)))

    def test_only_fully_retired_original_may_be_cancelled(self):
        t = transfer_fixture("complete")
        parent = {"kind": "transfer", "transfer": t}
        old = reservation(t["originals"][0])
        self.assertTrue(transfer_allows_cancel(parent, old))
        self.assertFalse(transfer_allows_cancel(parent, reservation(t["seed_before"])))
        self.assertFalse(transfer_allows_cancel({**parent, "kind": "consolidation"}, old))
        partial = transfer_fixture()
        self.assertFalse(transfer_allows_cancel({"kind": "transfer", "transfer": partial},
                                              reservation(partial["originals"][0])))

    def test_shifted_fallback_retains_its_duration_bound(self):
        t = transfer_fixture()
        t["remaining"][0].update(start="13:00", end="14:30")
        self.assertIs(validate_transfer(t), t)

    def test_one_fallback_cannot_grow_using_another_originals_minutes(self):
        t = transfer_fixture()
        t["originals"][0]["end"] = "13:00"
        t["originals"].append({**t["originals"][0], "event_id": 43, "start": "13:00", "end": "14:00"})
        t["baseline_ids"].append(43)
        t["adjustment_order"].append(43)
        # Ninety retained minutes under ID 42 would exceed its original sixty,
        # even though the total still adds up to two hours.
        with self.assertRaisesRegex(ValueError, "bounded duration"):
            validate_transfer(t)

    def test_invalid_partial_state_is_never_an_authorized_transaction(self):
        invalids = []
        t = transfer_fixture(); t["remaining"][0]["start"] = "12:15"; invalids.append(t)
        t = transfer_fixture(); t["remaining"][0]["start"] = "13:00"; invalids.append(t)
        t = transfer_fixture(); t["remaining"][0]["event_id"] = 43; invalids.append(t)
        t = transfer_fixture(); t["replacement"]["event_id"] = 999; invalids.append(t)
        t = transfer_fixture(); t["replacement"]["start"] = "12:01"; invalids.append(t)
        t = transfer_fixture(); t["baseline_ids"] = []; invalids.append(t)
        t = transfer_fixture(); t["opens_at"] = "2026-09-16T12:30:00"; invalids.append(t)
        t = transfer_fixture("extending"); t["seed_before"]["event_id"] = 42; invalids.append(t)
        t = transfer_fixture(); t["minimum_minutes"] = True; invalids.append(t)
        t = transfer_fixture(); t["unexpected"] = True; invalids.append(t)
        t = transfer_fixture(); t["adjustment_order"] = []; invalids.append(t)
        t = transfer_fixture(); t["adjustment_order"] = [42, 42]; invalids.append(t)
        t = transfer_fixture(); t["adjustment_order"] = [99]; invalids.append(t)
        t = transfer_fixture(); t["adjustment_order"] = [True]; invalids.append(t)
        t = transfer_fixture(); t["started_steps"] = ["source:999"]; invalids.append(t)
        t = transfer_fixture(); t["started_steps"] = ["destination", "destination"]; invalids.append(t)
        for i, payload in enumerate(invalids):
            with self.subTest(case=i), self.assertRaises((ValueError, TypeError)):
                validate_transfer(payload)
