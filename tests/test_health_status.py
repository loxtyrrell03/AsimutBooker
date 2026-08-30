import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from health_status import (
    HealthStatusError,
    collect_local_health,
    inspect_auth_cooldown,
    inspect_last_successful_run,
    inspect_pending_mutations,
    inspect_physical_wake,
    inspect_saved_session,
)
from mutation_receipts import MutationReceiptError


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
CLOCK = lambda: NOW


def write_json(path: Path, value) -> bytes:
    data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def valid_cookie():
    return {
        "name": "session",
        "value": "private-value",
        "domain": "rwcmd.asimut.net",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


def pending_receipt():
    receipt_id = "11111111-1111-4111-8111-111111111111"
    return {
        "schema_version": 1,
        "receipts": {
            receipt_id: {
                "id": receipt_id,
                "kind": "create",
                "status": "pending",
                "created_at": "2026-08-30T10:00:00Z",
                "updated_at": "2026-08-30T10:00:00Z",
                "room": "B0.29",
                "date": "2026-09-02",
                "start": "10:00",
                "end": "10:30",
            }
        },
    }


class HealthStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_sources_have_honest_non_error_states(self):
        self.assertEqual(
            inspect_last_successful_run(self.root / "history.json", clock=CLOCK).state,
            "unknown",
        )
        self.assertEqual(
            inspect_saved_session(self.root / "state.json", clock=CLOCK).state,
            "unknown",
        )
        self.assertEqual(
            inspect_auth_cooldown(self.root / "auth.json", clock=CLOCK).state,
            "ok",
        )
        self.assertEqual(
            inspect_pending_mutations(self.root / "receipts.json", clock=CLOCK).state,
            "ok",
        )
        wake = inspect_physical_wake(self.root / "wake.json", clock=CLOCK)
        self.assertEqual(wake.state, "unknown")
        self.assertIn("scheduler settings alone do not prove", wake.detail)

    def test_legacy_zero_booking_run_is_successful(self):
        path = self.root / "history.json"
        write_json(
            path,
            {
                "runs": [
                    {
                        "timestamp": "2026-08-30T11:30:00+00:00",
                        "bookings_made": 0,
                        "events_detected": 4,
                        "details": "No bookings made",
                    }
                ]
            },
        )

        item = inspect_last_successful_run(path, clock=CLOCK)

        self.assertEqual(item.state, "ok")
        self.assertIn("no bookings", item.detail)
        self.assertEqual(item.observed_at, datetime(2026, 8, 30, 11, 30, tzinfo=timezone.utc))

    def test_explicit_completed_outcome_takes_precedence_over_legacy_details(self):
        path = self.root / "history.json"
        write_json(
            path,
            {
                "runs": [
                    {
                        "timestamp": "2026-08-30T11:30:00Z",
                        "outcome": "completed",
                        "bookings_made": 0,
                        "details": "RECONCILIATION REQUIRED: stale legacy text",
                    }
                ]
            },
        )

        self.assertEqual(inspect_last_successful_run(path, clock=CLOCK).state, "ok")

    def test_noncompleted_outcome_and_legacy_reconciliation_are_skipped(self):
        path = self.root / "history.json"
        write_json(
            path,
            {
                "runs": [
                    {
                        "timestamp": "2026-08-30T11:50:00Z",
                        "outcome": "failed",
                        "bookings_made": 0,
                    },
                    {
                        "timestamp": "2026-08-30T11:40:00Z",
                        "bookings_made": 0,
                        "details": "  RECONCILIATION REQUIRED: uncertain save",
                    },
                    {
                        "timestamp": "2026-08-30T11:20:00Z",
                        "bookings_made": 1,
                        "details": "Booked one room",
                    },
                ]
            },
        )

        item = inspect_last_successful_run(path, clock=CLOCK)

        self.assertEqual(item.state, "ok")
        self.assertEqual(item.observed_at.minute, 20)

    def test_corrupt_history_fails_strictly(self):
        path = self.root / "history.json"
        write_json(path, {"runs": [{"timestamp": "not-a-time", "details": "ok"}]})
        with self.assertRaises(HealthStatusError):
            inspect_last_successful_run(path, clock=CLOCK)

    def test_saved_session_reports_structure_and_file_refresh_not_live_health(self):
        path = self.root / "state.json"
        original = write_json(path, {"cookies": [valid_cookie()], "origins": []})
        refreshed_epoch = datetime(2026, 8, 30, 9, 15, tzinfo=timezone.utc).timestamp()
        os.utime(path, (refreshed_epoch, refreshed_epoch))

        item = inspect_saved_session(path, clock=CLOCK)

        self.assertEqual(item.state, "ok")
        self.assertTrue(item.headline.startswith("Saved state last refreshed:"))
        self.assertIn("does not prove", item.detail)
        self.assertNotIn("private-value", item.headline + item.detail)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime, refreshed_epoch)

    def test_empty_but_structurally_valid_saved_session_warns(self):
        path = self.root / "state.json"
        write_json(path, {"cookies": [], "origins": []})
        item = inspect_saved_session(path, clock=CLOCK)
        self.assertEqual(item.state, "warn")
        self.assertIn("no cookies", item.detail)

    def test_corrupt_saved_session_fails_strictly(self):
        path = self.root / "state.json"
        write_json(path, {"cookies": {}, "origins": []})
        with self.assertRaises(HealthStatusError):
            inspect_saved_session(path, clock=CLOCK)

    def test_auth_ready_and_credential_block(self):
        path = self.root / "auth.json"
        write_json(path, {"version": 1, "status": "ready"})
        self.assertEqual(inspect_auth_cooldown(path, clock=CLOCK).state, "ok")

        write_json(
            path,
            {"version": 1, "status": "blocked", "category": "credential"},
        )
        item = inspect_auth_cooldown(path, clock=CLOCK)
        self.assertEqual(item.state, "error")
        self.assertIn("Secure login", item.headline)

    def test_active_auth_transient_cooldown_warns(self):
        path = self.root / "auth.json"
        write_json(
            path,
            {
                "version": 1,
                "status": "blocked",
                "category": "transient",
                "retry_after": NOW.timestamp() + 600,
            },
        )

        item = inspect_auth_cooldown(path, clock=CLOCK)

        self.assertEqual(item.state, "warn")
        self.assertIn("active", item.headline)

    def test_expired_auth_cooldown_is_read_only_byte_for_byte(self):
        path = self.root / "auth.json"
        original = write_json(
            path,
            {
                "version": 1,
                "status": "blocked",
                "category": "transient",
                "retry_after": NOW.timestamp() - 1,
            },
        )
        original_mtime = path.stat().st_mtime_ns

        item = inspect_auth_cooldown(path, clock=CLOCK)

        self.assertEqual(item.state, "ok")
        self.assertIn("expired", item.headline)
        self.assertIn("did not rewrite", item.detail)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, original_mtime)

    def test_invalid_auth_state_fails_strictly(self):
        path = self.root / "auth.json"
        write_json(path, {"version": 2, "status": "ready"})
        with self.assertRaises(HealthStatusError):
            inspect_auth_cooldown(path, clock=CLOCK)

    def test_pending_receipts_use_strict_journal_and_do_not_mutate(self):
        path = self.root / "receipts.json"
        original = write_json(path, pending_receipt())
        original_mtime = path.stat().st_mtime_ns

        item = inspect_pending_mutations(path, clock=CLOCK)

        self.assertEqual(item.state, "error")
        self.assertIn("1 pending mutation", item.headline)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mtime_ns, original_mtime)

    def test_corrupt_receipt_journal_raises_its_strict_parser_error(self):
        path = self.root / "receipts.json"
        write_json(path, {"schema_version": 1, "receipts": []})
        with self.assertRaises(MutationReceiptError):
            inspect_pending_mutations(path, clock=CLOCK)

    def test_physical_wake_unproven_and_overdue_are_distinct(self):
        path = self.root / "wake.json"
        base = {
            "version": 1,
            "status": "unproven",
            "method": "scheduled_wake_test",
        }
        write_json(path, {**base, "expected_wake_at": "2026-08-30T12:30:00Z"})
        future = inspect_physical_wake(path, clock=CLOCK)
        self.assertEqual(future.state, "unknown")
        self.assertIn("not yet proven", future.headline)

        write_json(path, {**base, "expected_wake_at": "2026-08-30T11:30:00Z"})
        overdue = inspect_physical_wake(path, clock=CLOCK)
        self.assertEqual(overdue.state, "warn")
        self.assertIn("overdue", overdue.headline)

    def test_physical_wake_pass_and_fail_require_explicit_observation(self):
        path = self.root / "wake.json"
        base = {
            "version": 1,
            "method": "scheduled_wake_test",
            "expected_wake_at": "2026-08-30T10:00:00Z",
            "observed_at": "2026-08-30T10:01:00Z",
        }
        write_json(path, {**base, "status": "passed"})
        self.assertEqual(inspect_physical_wake(path, clock=CLOCK).state, "ok")

        write_json(path, {**base, "status": "failed"})
        self.assertEqual(inspect_physical_wake(path, clock=CLOCK).state, "error")

    def test_invalid_wake_artifact_fails_strictly(self):
        path = self.root / "wake.json"
        write_json(
            path,
            {
                "version": 1,
                "status": "passed",
                "method": "scheduler_settings",
                "expected_wake_at": "2026-08-30T10:00:00Z",
                "observed_at": "2026-08-30T10:01:00Z",
            },
        )
        with self.assertRaises(HealthStatusError):
            inspect_physical_wake(path, clock=CLOCK)

    def test_local_collector_isolates_one_corrupt_card(self):
        history = self.root / "history.json"
        history.write_text("{broken", encoding="utf-8")
        auth = self.root / "auth.json"
        write_json(auth, {"version": 1, "status": "ready"})

        snapshot = collect_local_health(
            history_path=history,
            session_path=self.root / "missing-session.json",
            auth_path=auth,
            receipts_path=self.root / "missing-receipts.json",
            wake_path=self.root / "missing-wake.json",
            clock=CLOCK,
        )

        self.assertEqual(
            [item.key for item in snapshot.items],
            [
                "last_success",
                "saved_session",
                "auth_cooldown",
                "pending_mutations",
                "physical_wake",
            ],
        )
        self.assertEqual(snapshot.get("last_success").state, "error")
        self.assertEqual(snapshot.get("auth_cooldown").state, "ok")
        self.assertEqual(snapshot.get("pending_mutations").state, "ok")
        self.assertEqual(snapshot.collected_at, NOW)


if __name__ == "__main__":
    unittest.main()
