import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from booking_plan import (
    BookingPlanError,
    BookingPlanSnapshot,
    DayPlan,
    PlanCandidate,
    booking_plan_fingerprint,
    clear_booking_plan,
    read_booking_plan,
    snapshot_from_dict,
    snapshot_to_dict,
    write_booking_plan,
)


NOW = datetime(2026, 8, 30, 11, 0, tzinfo=timezone.utc)


def candidate(
    *,
    room="B0.29",
    booking_date="2026-09-04",
    start="12:00",
    end="14:00",
    unlock_at=None,
    initial=30,
    potential=120,
    confirmed=0,
    state="waiting",
    reason="Best afternoon peak-hours fit.",
):
    return PlanCandidate(
        room=room,
        date=booking_date,
        start_time=start,
        end_time=end,
        unlock_at=unlock_at or datetime(2026, 8, 30, 12, 30, tzinfo=timezone.utc),
        initial_minutes=initial,
        potential_minutes=potential,
        confirmed_minutes=confirmed,
        state=state,
        reason=reason,
    )


def day_plan(*, primary=None, backups=(), **changes):
    values = {
        "date": "2026-09-04",
        "target_minutes": 180,
        "existing_minutes": 30,
        "peak_used_minutes": 30,
        "peak_limit_minutes": 120,
        "status": "waiting",
        "primary": candidate() if primary is None else primary,
        "additional": (),
        "backups": tuple(backups),
        "held_peak_minutes": 90,
        "reason": "Holding the remaining peak allowance for the afternoon.",
    }
    values.update(changes)
    return DayPlan(**values)


def snapshot(*, days=None, **changes):
    values = {
        "version": 1,
        "generated_at": NOW,
        "valid_until": NOW + timedelta(minutes=3),
        "policy_observed_at": NOW - timedelta(seconds=20),
        "strategy_fingerprint": "a" * 64,
        "status": "active",
        "summary": "One preferred booking is waiting; one fallback is available.",
        "days": (day_plan(),) if days is None else tuple(days),
    }
    values.update(changes)
    return BookingPlanSnapshot(**values)


class BookingPlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "booking_plan.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_round_trip_uses_exact_v1_shape_and_canonical_timestamps(self):
        backup = candidate(
            room="B1.09",
            start="14:00",
            end="15:30",
            potential=90,
            reason="Fallback if the first room is taken.",
        )
        source = snapshot(days=[day_plan(backups=(backup,))])

        raw = snapshot_to_dict(source)
        restored = snapshot_from_dict(raw)

        self.assertEqual(restored, source)
        self.assertEqual(raw["generated_at"], "2026-08-30T11:00:00Z")
        self.assertEqual(
            set(raw),
            {
                "version",
                "generated_at",
                "valid_until",
                "policy_observed_at",
                "strategy_fingerprint",
                "status",
                "summary",
                "days",
            },
        )
        self.assertEqual(raw["days"][0]["primary"]["end_time"], "14:00")

    def test_round_trip_preserves_multiple_selected_day_sessions(self):
        later = candidate(
            room="B1.09",
            start="16:00",
            end="18:00",
            reason="Selected off-peak session.",
        )
        day = day_plan(
            additional=(later,),
            target_minutes=270,
            existing_minutes=30,
            peak_used_minutes=30,
            held_peak_minutes=0,
        )

        restored = snapshot_from_dict(snapshot_to_dict(snapshot(days=[day])))

        self.assertEqual(restored.days[0].additional, (later,))

    def test_write_is_atomic_and_read_returns_current_snapshot(self):
        written = write_booking_plan(snapshot(), self.path)
        result = read_booking_plan(self.path, now=NOW + timedelta(minutes=1))

        self.assertFalse(result.stale)
        self.assertEqual(result.reason, "Booking plan is current.")
        self.assertEqual(result.snapshot, written)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.path.with_suffix(".json.lock").exists())

    def test_missing_plan_is_stale_and_read_only(self):
        result = read_booking_plan(self.path, now=NOW)

        self.assertTrue(result.stale)
        self.assertIsNone(result.snapshot)
        self.assertIn("No booking plan", result.reason)
        self.assertFalse(self.path.exists())

    def test_expired_plan_is_explicitly_stale_but_available_for_stale_display(self):
        write_booking_plan(snapshot(), self.path)

        result = read_booking_plan(self.path, now=NOW + timedelta(minutes=3))

        self.assertTrue(result.stale)
        self.assertIsNotNone(result.snapshot)
        self.assertIn("expired", result.reason)

    def test_strategy_change_marks_snapshot_stale(self):
        write_booking_plan(snapshot(), self.path)

        result = read_booking_plan(
            self.path,
            now=NOW + timedelta(minutes=1),
            expected_strategy_fingerprint="b" * 64,
        )

        self.assertTrue(result.stale)
        self.assertIsNotNone(result.snapshot)
        self.assertIn("preferences changed", result.reason)

    def test_matching_strategy_fingerprint_is_current(self):
        write_booking_plan(snapshot(), self.path)
        result = read_booking_plan(
            self.path,
            now=NOW,
            expected_strategy_fingerprint="a" * 64,
        )
        self.assertFalse(result.stale)

    def test_settings_fingerprint_is_stable_and_scoped_to_plan_inputs(self):
        first = {
            "booking_strategy": {"reverse_date_order": False},
            "time_preferences": {"enabled": True},
            "unrelated_ui_state": {"expanded": True},
        }
        reordered = {
            "unrelated_ui_state": {"expanded": False},
            "time_preferences": {"enabled": True},
            "booking_strategy": {"reverse_date_order": False},
        }
        self.assertEqual(
            booking_plan_fingerprint(first), booking_plan_fingerprint(reordered)
        )

        changed = dict(first)
        changed["booking_strategy"] = {"reverse_date_order": True}
        self.assertNotEqual(
            booking_plan_fingerprint(first), booking_plan_fingerprint(changed)
        )

        ignored_changed = dict(first)
        ignored_changed["ignored_events"] = ["event:v2:test"]
        self.assertNotEqual(
            booking_plan_fingerprint(first),
            booking_plan_fingerprint(ignored_changed),
        )

        blackout_changed = dict(first)
        blackout_changed["rebooking_blackouts"] = [
            {
                "date": "2026-09-01",
                "start_time": "12:00",
                "end_time": "18:00",
            }
        ]
        self.assertNotEqual(
            booking_plan_fingerprint(first),
            booking_plan_fingerprint(blackout_changed),
        )

    def test_settings_fingerprint_includes_advanced_rule_file(self):
        config_path = Path(self.temporary.name) / "config.yaml"
        config_path.write_text("rules:\n  peak_end: 16\n", encoding="utf-8")
        before = booking_plan_fingerprint({}, config_path=config_path)
        config_path.write_text("rules:\n  peak_end: 17\n", encoding="utf-8")
        after = booking_plan_fingerprint({}, config_path=config_path)

        self.assertNotEqual(before, after)

    def test_settings_fingerprint_rejects_non_json_values(self):
        with self.assertRaisesRegex(BookingPlanError, "fingerprint"):
            booking_plan_fingerprint({"booking_strategy": {"bad": object()}})

    def test_malformed_or_invalid_files_return_stale_without_rewriting(self):
        self.path.write_text("{broken", encoding="utf-8")
        original = self.path.read_bytes()
        malformed = read_booking_plan(self.path, now=NOW)
        self.assertTrue(malformed.stale)
        self.assertIsNone(malformed.snapshot)
        self.assertEqual(self.path.read_bytes(), original)

        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        original = self.path.read_bytes()
        invalid = read_booking_plan(self.path, now=NOW)
        self.assertTrue(invalid.stale)
        self.assertIsNone(invalid.snapshot)
        self.assertIn("missing fields", invalid.reason)
        self.assertEqual(self.path.read_bytes(), original)

        wrong_types = snapshot_to_dict(snapshot())
        wrong_types["days"][0]["primary"]["state"] = []
        self.path.write_text(json.dumps(wrong_types), encoding="utf-8")
        invalid_types = read_booking_plan(self.path, now=NOW)
        self.assertTrue(invalid_types.stale)
        self.assertIsNone(invalid_types.snapshot)
        self.assertIn("state is invalid", invalid_types.reason)

    def test_unknown_keys_are_rejected_at_every_level(self):
        raw = snapshot_to_dict(snapshot())
        for target in (raw, raw["days"][0], raw["days"][0]["primary"]):
            mutated = json.loads(json.dumps(raw))
            if target is raw:
                mutated["surprise"] = True
            elif target is raw["days"][0]:
                mutated["days"][0]["surprise"] = True
            else:
                mutated["days"][0]["primary"]["surprise"] = True
            with self.assertRaisesRegex(BookingPlanError, "unknown fields"):
                snapshot_from_dict(mutated)

    def test_noncanonical_or_naive_timestamps_are_rejected(self):
        raw = snapshot_to_dict(snapshot())
        invalid_values = (
            "2026-08-30T11:00:00",
            "2026-08-30T11:00:00+00:00",
            "2026-08-30T12:00:00+01:00",
            "2026-08-30T11:00:00.000Z",
        )
        for value in invalid_values:
            mutated = json.loads(json.dumps(raw))
            mutated["generated_at"] = value
            with self.subTest(value=value), self.assertRaises(BookingPlanError):
                snapshot_from_dict(mutated)

        with self.assertRaisesRegex(BookingPlanError, "timezone-aware"):
            snapshot_to_dict(snapshot(generated_at=datetime(2026, 8, 30, 11, 0)))

    def test_snapshot_timestamp_relationships_are_strict(self):
        with self.assertRaisesRegex(BookingPlanError, "valid_until"):
            snapshot_to_dict(snapshot(valid_until=NOW))
        with self.assertRaisesRegex(BookingPlanError, "policy_observed_at"):
            snapshot_to_dict(snapshot(policy_observed_at=NOW + timedelta(seconds=1)))

    def test_candidate_times_must_be_quarter_hour_and_match_potential(self):
        with self.assertRaisesRegex(BookingPlanError, "15-minute boundary"):
            snapshot_to_dict(snapshot(days=[day_plan(primary=candidate(start="12:05"))]))
        with self.assertRaisesRegex(BookingPlanError, "duration must equal"):
            snapshot_to_dict(
                snapshot(days=[day_plan(primary=candidate(end="13:30", potential=120))])
            )
        with self.assertRaisesRegex(BookingPlanError, "cannot exceed"):
            snapshot_to_dict(
                snapshot(days=[day_plan(primary=candidate(initial=120, potential=90, end="13:30"))])
            )

    def test_potential_plan_cannot_claim_a_booking_is_confirmed(self):
        with self.assertRaisesRegex(BookingPlanError, "state is invalid"):
            snapshot_to_dict(
                snapshot(days=[day_plan(primary=candidate(state="booked"))])
            )

    def test_partial_confirmation_counts_only_the_remaining_plan_capacity(self):
        progressing = day_plan(
            primary=candidate(confirmed=30),
            target_minutes=120,
            existing_minutes=30,
            peak_used_minutes=30,
            held_peak_minutes=90,
            status="in_progress",
        )

        restored = snapshot_from_dict(snapshot_to_dict(snapshot(days=[progressing])))

        self.assertEqual(restored.days[0].primary.confirmed_minutes, 30)

    def test_fully_confirmed_candidate_must_be_removed_in_favour_of_agenda(self):
        with self.assertRaisesRegex(BookingPlanError, "fully confirmed"):
            snapshot_to_dict(
                snapshot(days=[day_plan(primary=candidate(confirmed=120))])
            )

    def test_minute_fields_reject_bool_negative_overflow_and_non_quarter_values(self):
        invalid = (True, -15, 1441, 31, 1.5, "30")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BookingPlanError):
                snapshot_to_dict(snapshot(days=[day_plan(target_minutes=value)]))

    def test_peak_budget_relationships_are_enforced(self):
        invalid_days = (
            day_plan(existing_minutes=15, peak_used_minutes=30),
            day_plan(peak_used_minutes=135, peak_limit_minutes=120),
            day_plan(peak_used_minutes=60, peak_limit_minutes=120, held_peak_minutes=75),
            day_plan(held_peak_minutes=135),
        )
        for invalid_day in invalid_days:
            with self.subTest(day=invalid_day), self.assertRaises(BookingPlanError):
                snapshot_to_dict(snapshot(days=[invalid_day]))

    def test_candidate_dates_must_match_day_and_duplicates_are_rejected(self):
        wrong_date = candidate(booking_date="2026-09-05")
        with self.assertRaisesRegex(BookingPlanError, "day date"):
            snapshot_to_dict(snapshot(days=[day_plan(primary=wrong_date)]))

        duplicate = candidate()
        with self.assertRaisesRegex(BookingPlanError, "duplicate"):
            snapshot_to_dict(snapshot(days=[day_plan(backups=(duplicate,))]))

    def test_backups_and_held_peak_require_primary(self):
        no_primary = replace(day_plan(), primary=None, backups=(candidate(),))
        with self.assertRaisesRegex(BookingPlanError, "backups without"):
            snapshot_to_dict(snapshot(days=[no_primary]))
        held_without_primary = replace(day_plan(), primary=None, backups=())
        with self.assertRaisesRegex(BookingPlanError, "requires a primary"):
            snapshot_to_dict(snapshot(days=[held_without_primary]))

    def test_days_must_be_unique_and_ascending(self):
        first = day_plan()
        second_candidate = candidate(booking_date="2026-09-05")
        second = replace(first, date="2026-09-05", primary=second_candidate)
        with self.assertRaisesRegex(BookingPlanError, "ascending"):
            snapshot_to_dict(snapshot(days=[second, first]))
        with self.assertRaisesRegex(BookingPlanError, "unique"):
            snapshot_to_dict(snapshot(days=[first, first]))

    def test_status_enums_and_required_trimmed_text_are_strict(self):
        with self.assertRaisesRegex(BookingPlanError, "status is invalid"):
            snapshot_to_dict(snapshot(status="fresh"))
        with self.assertRaisesRegex(BookingPlanError, "status is invalid"):
            snapshot_to_dict(snapshot(days=[day_plan(status="almost")]))
        with self.assertRaisesRegex(BookingPlanError, "state is invalid"):
            snapshot_to_dict(
                snapshot(days=[day_plan(primary=candidate(state="reserved"))])
            )
        with self.assertRaisesRegex(BookingPlanError, "trimmed"):
            snapshot_to_dict(snapshot(summary=" trailing "))
        raw = snapshot_to_dict(snapshot())
        raw["version"] = 1.0
        with self.assertRaisesRegex(BookingPlanError, "unsupported version"):
            snapshot_from_dict(raw)

    def test_invalid_write_never_replaces_an_existing_valid_plan(self):
        write_booking_plan(snapshot(), self.path)
        original = self.path.read_bytes()

        with self.assertRaises(BookingPlanError):
            write_booking_plan(snapshot(status="wrong"), self.path)

        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(read_booking_plan(self.path, now=NOW).stale)

    def test_clear_is_idempotent_and_removes_only_the_artifact(self):
        write_booking_plan(snapshot(), self.path)
        neighbor = self.path.with_name("settings.json")
        neighbor.write_text("{}", encoding="utf-8")

        clear_booking_plan(self.path)
        clear_booking_plan(self.path)

        self.assertFalse(self.path.exists())
        self.assertTrue(neighbor.exists())

    def test_read_requires_an_aware_clock(self):
        with self.assertRaisesRegex(BookingPlanError, "timezone-aware"):
            read_booking_plan(self.path, now=datetime(2026, 8, 30, 11, 0))


if __name__ == "__main__":
    unittest.main()
