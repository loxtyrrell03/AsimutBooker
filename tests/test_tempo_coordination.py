"""Synthetic coordination contracts; these tests never contact ASIMUT."""
import contextlib
import copy
from datetime import date, datetime, timedelta, timezone
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest import mock

from app_settings import atomic_write_json
import book_week as booker
from booking_preferences_guard import booking_preference_run, booking_save_boundary, BookingPreferencesChanged
import tempo_coordination as tempo
from room_upgrade_runtime import tracker_for_events, planning_events
from booking_plan import booking_plan_fingerprint


NOW = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
DAY = date(2026, 9, 29)


def document(**changes):
    value = dict(version=1, instance_id="test-tempo", revision=1, enabled=True,
        generated_at=NOW.isoformat(), valid_until=(NOW + timedelta(hours=24)).isoformat(),
        dates=[DAY.isoformat()], busy=[dict(date=str(DAY), start="11:00", end="11:30")],
        windows=[dict(date=str(DAY), start="09:00", end="12:00", rooms=["Practice A"],
                      task_id="practice", block_id="practice-tuesday")], protected_event_ids=[42])
    value.update(changes)
    return value


class TempoCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(TemporaryDirectory())
        self.path = Path(folder) / "tempo" / "coordination.json"
        self.settings = Path(folder) / "settings.json"
        self.stack.enter_context(mock.patch.object(tempo, "SNAPSHOT_PATH", self.path))
        self.stack.enter_context(mock.patch.object(tempo, "RUNTIME_LOCK_PATH", Path(folder) / "runtime.lock"))
        self.stack.enter_context(mock.patch.object(tempo, "_now", side_effect=lambda value=None: value or NOW))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def publish(self, value=None, **kwargs):
        return tempo.publish_snapshot(value or document(), path=self.path, now=NOW, **kwargs)

    def test_absent_integration_is_fileless_and_standalone(self):
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.enabled)
        self.assertTrue(snapshot.permits(DAY, 10, 12, "Anywhere"))
        with booking_save_boundary(day=DAY, start="10:00", end="12:00", room="Anywhere"):
            pass
        self.assertFalse(self.path.parent.exists())

    def test_noop_retry_and_compare_and_swap(self):
        self.assertEqual(self.publish(expected_revision=0)["revision"], 1)
        self.assertTrue(self.publish(expected_revision=0)["unchanged"])
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=2), expected_revision=0)
        self.assertEqual(self.publish(document(revision=2), expected_revision=1)["revision"], 2)
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=4), expected_revision=2)
        with self.assertRaises(tempo.CoordinationError):
            self.publish(document(revision=3, instance_id="another-installation"), expected_revision=2)

    def test_expired_or_future_publication_rejected(self):
        for value in (document(valid_until=NOW.isoformat()),
                      document(generated_at=(NOW + timedelta(minutes=3)).isoformat())):
            with self.subTest(value=value), self.assertRaises(tempo.CoordinationError):
                self.publish(value)
        self.assertFalse(self.path.exists())

    def test_invalid_scope_or_unbounded_schema_rejected(self):
        variants = [document(dates=[]), document(dates=[str(DAY), str(DAY)]),
                    document(dates=["2028-01-01"]), document(revision=True), document(enabled="yes"),
                    document(valid_until=(NOW + timedelta(hours=25)).isoformat()),
                    document(busy=[dict(date="2026-09-30", start="09:00", end="10:00")]),
                    document(windows=[dict(date=str(DAY), start="12:00", end="09:00")]),
                    document(protected_event_ids=[True])]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(tempo.CoordinationError):
                self.publish(value)

    def test_missing_corrupt_or_stale_keeps_only_durable_scope_blocked(self):
        for fault in ("missing", "corrupt", "expired", "rollback", "same-revision-edited"):
            with self.subTest(fault=fault):
                self.publish()
                if fault == "missing":
                    self.path.unlink()
                elif fault == "corrupt":
                    self.path.write_text("{", encoding="utf-8")
                elif fault == "rollback":
                    self.publish(document(revision=2))
                    atomic_write_json(self.path, document())
                elif fault == "same-revision-edited":
                    atomic_write_json(self.path, document(windows=[]))
                snapshot = tempo.read_snapshot(now=NOW + timedelta(days=2) if fault == "expired" else NOW)
                self.assertTrue(snapshot.problem)
                self.assertFalse(snapshot.permits(DAY, 9, 10, "Practice A"))
                self.assertEqual(snapshot.blocked_ranges(DAY), [(0, 24)])
                self.assertTrue(snapshot.permits(DAY + timedelta(days=1), 9, 10, "Anywhere"))
                # Reset only this fixture's two exact files between cases.
                self.path.unlink(missing_ok=True)
                tempo._accepted_path(self.path).unlink(missing_ok=True)

    def test_interrupted_publish_keeps_new_scope_blocked(self):
        self.publish()
        next_doc = document(revision=2, dates=[str(DAY), "2026-09-30"])
        original = tempo.atomic_write_json
        def write(path, value):
            if path == self.path:
                raise OSError("synthetic interruption")
            original(path, value)
        with mock.patch.object(tempo, "atomic_write_json", side_effect=write), self.assertRaises(OSError):
            self.publish(next_doc)
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.permits("2026-09-30", 9, 10, "Anywhere"))
        self.assertTrue(snapshot.problem)
        self.assertTrue(self.publish(next_doc)["unchanged"])
        self.assertFalse(tempo.read_snapshot(now=NOW).problem)

    def test_explicit_disable_releases_scope_and_does_not_edit_settings(self):
        atomic_write_json(self.settings, {"practice_plan": {"target": "untouched"}})
        before = self.settings.read_bytes()
        self.publish()
        self.publish(document(revision=2, enabled=False, dates=[], busy=[], windows=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.enabled)
        self.assertTrue(snapshot.permits(DAY, 9, 10, "Anywhere"))
        self.assertEqual(self.settings.read_bytes(), before)

    def test_full_interval_and_exact_room_required(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertTrue(snapshot.permits(DAY, 9, 11, "Practice A"))
        self.assertTrue(snapshot.permits(DAY, 11.5, 12, "Practice A"))
        for start, end, room in ((8.5, 10, "Practice A"), (10.5, 11.5, "Practice A"),
                                 (11.5, 12.5, "Practice A"), (9, 10, "Practice B")):
            self.assertFalse(snapshot.permits(DAY, start, end, room))

    def test_adjacent_tasks_cannot_be_combined_into_one_booking(self):
        windows = [dict(date=str(DAY), start="09:00", end="10:00", rooms=[]),
                   dict(date=str(DAY), start="10:00", end="11:00", rooms=[])]
        self.publish(document(windows=windows, busy=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertFalse(snapshot.permits(DAY, 9, 11, "Practice A"))
        self.assertTrue(snapshot.permits(DAY, 9, 10, "Practice B"))

    def test_tracker_rebuilds_keep_constraints_without_quota_or_fake_events(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            first = booker.BookingTracker()
            rebuilt = tracker_for_events(booker, [], ())
        for tracker in (first, rebuilt):
            self.assertTrue(tracker.overlaps_conflict(DAY, 8, 9.25))
            self.assertTrue(tracker.overlaps_conflict(DAY, 10.5, 11.25))
            self.assertFalse(tracker.overlaps_conflict(DAY, 9, 11))
            self.assertEqual(tracker.get_total_booking_hours(), 0)
            self.assertEqual(tracker.reservation_ranges, {})
            self.assertEqual(tracker.agenda_events, [])
            self.assertFalse(tracker.can_book("Practice B", DAY, 9, 60)[0])
            self.assertTrue(tracker.can_book("Practice A", DAY, 9, 60)[0])

    def test_upgrade_room_availability_respects_windows_and_task_conflicts(self):
        self.publish()
        raw = [{"room": name, "slots": [{"startHour": 8, "endHour": 18}]}
               for name in ("Practice A", "Practice B")]
        old = copy.deepcopy(raw)
        values = tempo.read_snapshot(now=NOW).constrain_availability(raw, DAY)
        self.assertEqual(values[0]["slots"], [{"startHour": 9, "endHour": 11},
                                             {"startHour": 11.5, "endHour": 12}])
        self.assertEqual(values[1]["slots"], [])
        self.assertEqual(raw, old)

    def test_protected_bookings_stay_real_conflicts(self):
        self.publish()
        event = dict(eventId=42, date=str(DAY), startTime="10:00", endTime="11:00",
                     room="Practice A", isReservation=True, title="Reservation")
        with tempo.coordination_run(now=NOW):
            tracker = tracker_for_events(booker, [event], ())
            events, ignored_ids = planning_events(booker, tracker, ())
        self.assertIn(42, ignored_ids)
        self.assertNotEqual(events[0].get("blocksConflict"), False)
        self.assertEqual(tracker.get_hours_for_day(DAY), 1)

    def test_extension_is_capped_by_whole_window_and_task(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        self.assertEqual(snapshot.allowed_end(DAY, 9, 13, "Practice A"), 11)
        self.assertEqual(snapshot.allowed_end(DAY, 11.5, 13, "Practice A"), 12)
        self.assertEqual(snapshot.allowed_end(DAY, 8.5, 10, "Practice A"), 8.5)
        self.assertEqual(snapshot.allowed_end(DAY, 9, 11, "Practice B"), 9)

    def test_changed_revision_after_form_preparation_prevents_save(self):
        self.publish()
        saved = mock.Mock()
        with tempo.coordination_run(now=NOW):
            self.publish(document(revision=2, busy=[]))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                saved()
        saved.assert_not_called()

    def test_activation_mid_run_stops_old_plan_save(self):
        with tempo.coordination_run(now=NOW):
            self.publish()
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("An old standalone plan cannot cross activation")

    def test_stale_scope_blocks_save_but_other_date_remains_standalone(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            self.path.unlink()
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("Missing coordination must not silently release its dates")
            with booking_save_boundary(day=DAY + timedelta(days=1), start="09:00", end="10:00", room="Anywhere"):
                pass

    def test_protected_automatic_edit_rejected_manual_control_retained(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(event_ids=[42]):
                self.fail("Automatic cancellation is not allowed for a protected booking")
            with booking_save_boundary(event_ids=[42], automatic=False):
                pass

    def test_final_guard_checks_start_and_complete_extension_not_only_tail(self):
        self.publish()
        with tempo.coordination_run(now=NOW):
            for start, end in (("08:30", "10:00"), ("10:30", "11:45"), ("11:45", "12:15")):
                with self.subTest(start=start, end=end), self.assertRaises(BookingPreferencesChanged), \
                        booking_save_boundary(day=DAY, start=start, end=end, room="Practice A"):
                    self.fail("Whole interval must be accepted")

    def test_publisher_waits_for_save_to_finish(self):
        self.publish()
        began, done = Event(), Event()
        failures = []
        def publish():
            began.set()
            try:
                self.publish(document(revision=2, busy=[]))
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
        with tempo.coordination_run(now=NOW), booking_save_boundary(
                day=DAY, start="09:00", end="10:00", room="Practice A"):
            writer = Thread(target=publish)
            writer.start()
            self.assertTrue(began.wait(1))
            self.assertFalse(done.wait(.1))
        writer.join(3)
        self.assertTrue(done.is_set())
        self.assertEqual(failures, [])
        self.assertEqual(tempo.read_public_status(now=NOW)["revision"], 2)

    def test_preference_run_captures_coordination_revision_too(self):
        atomic_write_json(self.settings, {})
        self.publish()
        with booking_preference_run(self.settings, {}):
            self.publish(document(revision=2))
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary(
                    day=DAY, start="09:00", end="10:00", room="Practice A"):
                self.fail("Normal production runs pin both preference and Tempo revisions")

    def test_display_plan_fingerprint_tracks_coordination_and_retains_run_revision(self):
        standalone = booking_plan_fingerprint({}, config_path=self.settings)
        self.publish()
        with tempo.coordination_run(now=NOW):
            accepted = booking_plan_fingerprint({}, config_path=self.settings)
            self.publish(document(revision=2, windows=[]))
            self.assertEqual(booking_plan_fingerprint({}, config_path=self.settings), accepted)
        self.assertNotEqual(standalone, accepted)
        self.assertNotEqual(booking_plan_fingerprint({}, config_path=self.settings), accepted)
        self.publish(document(revision=3, enabled=False, dates=[], busy=[], windows=[]))
        self.assertEqual(booking_plan_fingerprint({}, config_path=self.settings), standalone)

    def test_first_enable_requires_idle_runtime_without_starting_or_stopping_it(self):
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH):
            with self.assertRaisesRegex(tempo.CoordinationError, "busy"):
                self.publish()
        self.assertFalse(self.path.exists())
        self.assertFalse(tempo._accepted_path(self.path).exists())
        self.publish()
        # Once the hook is active, changing a revision is serialized with Save,
        # not blocked by a long-running room scan that owns the normal runtime.
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH):
            self.publish(document(revision=2))
        self.assertEqual(tempo.read_public_status(now=NOW)["revision"], 2)

    def test_reenable_after_explicit_disable_requires_idle_runtime(self):
        self.publish(document(enabled=False, dates=[], busy=[], windows=[]))
        with tempo.SingleInstanceLock(tempo.RUNTIME_LOCK_PATH), self.assertRaisesRegex(tempo.CoordinationError, "busy"):
            self.publish(document(revision=2))
        self.assertFalse(tempo.read_public_status(now=NOW)["enabled"])

    def test_practice_demand_overlay_counts_confirmed_prefix_once_without_changing_saved_goal(self):
        self.publish(document(busy=[]))
        snapshot = tempo.read_snapshot(now=NOW)
        plan = booker.PracticePlan(enabled=True, default_hours=2, date_overrides={"2026-09-30": 4})
        reservations = {str(DAY): [(9, 9.5, "Practice A"), (13, 14, "Practice B")]}
        overlaid = snapshot.practice_plan_overlay(plan, reservations)
        self.assertEqual(overlaid.target_for(DAY), 4)  # 09–12 accepted + 13–14 booked.
        self.assertEqual(overlaid.target_for(date(2026, 9, 30)), 4)
        self.assertEqual(plan.target_for(DAY), 2)
        self.assertEqual(plan.date_overrides, {"2026-09-30": 4})
        # Filling the accepted window does not grow this run's fixed goal.
        reservations[str(DAY)].append((9.5, 12, "Practice A"))
        self.assertEqual(overlaid.target_for(DAY), 4)

    def test_practice_demand_preserves_disabled_mode_higher_saved_goal_and_stale_state(self):
        self.publish()
        snapshot = tempo.read_snapshot(now=NOW)
        disabled = booker.PracticePlan(enabled=False)
        self.assertIs(snapshot.practice_plan_overlay(disabled, {}), disabled)
        high_goal = booker.PracticePlan(enabled=True, default_hours=6)
        self.assertEqual(snapshot.practice_plan_overlay(high_goal, {}).target_for(DAY), 6)
        stale = tempo.read_snapshot(now=NOW + timedelta(days=2))
        self.assertIs(stale.practice_plan_overlay(high_goal, {}), high_goal)


if __name__ == "__main__":
    unittest.main()
