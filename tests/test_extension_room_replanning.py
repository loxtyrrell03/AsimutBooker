import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import book_week as b


class ExtensionRoomReplanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"
        self.day = date.today() + timedelta(days=1)
        self.booking = dict(eventId=42, event_url="https://rwcmd.asimut.net/arrangement?eventId=42",
            date=self.day.isoformat(), room="B0.13", startTime="12:00", endTime="12:30",
            target_end="14:00", horizon_minutes=10080, created_at=datetime.now().isoformat())
        self.event = {**self.booking, "isReservation": True}
        self.settings = {"practice_plan": {"default_hours": 4}, "extendable_bookings": [self.booking]}
        self.path.write_text(json.dumps(self.settings))
        self.grid = [{"room": "B0.13", "slots": [{"startHour": 12.5, "endHour": 13.0}]}]

    def reconcile(self, grid=None, events=None, *, horizon=14, pending=False):
        with (mock.patch.object(b, "settings_file", self.path),
              mock.patch.object(b, "list_pending_mutation_receipts", return_value=[{}] if pending else []),
              mock.patch.object(b, "plan_horizon_extension", return_value=mock.Mock(can_extend=True, max_end_hour=horizon))):
            return b.reconcile_extension_targets_with_grid(self.day,
                self.grid if grid is None else grid, [self.event] if events is None else events)

    def test_caps_only_runtime_target_to_contiguous_free_time(self):
        self.assertEqual(self.reconcile(), 1)
        expected = copy.deepcopy(self.settings)
        expected["extendable_bookings"][0]["target_end"] = "13:00"
        self.assertEqual(json.loads(self.path.read_text()), expected)

    def test_immediately_blocked_tail_releases_hold_without_touching_booking(self):
        self.assertEqual(self.reconcile(grid=[{"room": "B0.13", "slots": []}]), 1)
        self.assertEqual(json.loads(self.path.read_text())["extendable_bookings"], [])
        self.assertEqual(self.event["endTime"], "12:30")

    def test_accepts_scan_agenda_reservation_projection(self):
        event = dict(self.event)
        event.pop("isReservation")
        self.assertEqual(self.reconcile(events=[event]), 1)

    def test_unknown_grid_or_identity_retains_intent(self):
        for grid in ([], self.grid * 2, [{"room": "B0.13"}],
                     [{"room": "B0.13", "slots": [{"startHour": 12, "endHour": 15}]}],
                     [{"room": "B0.13", "slots": [{"startHour": "12:30", "endHour": 13}]}]):
            with self.subTest(grid=grid):
                self.assertEqual(self.reconcile(grid=grid), 0)
        for events in ([], [self.event] * 2, [{**self.event, "endTime": "13:00"}],
                       [{**self.event, "isReservation": False}]):
            with self.subTest(events=events):
                self.assertEqual(self.reconcile(events=events), 0)
        self.assertEqual(json.loads(self.path.read_text()), self.settings)

    def test_unopened_target_and_pending_save_retain_capacity(self):
        self.assertEqual(self.reconcile(horizon=12.75), 0)
        self.assertEqual(self.reconcile(pending=True), 0)
        self.assertEqual(json.loads(self.path.read_text()), self.settings)

    def test_fully_available_tail_keeps_target(self):
        self.assertEqual(self.reconcile(grid=[{"room": "B0.13", "slots": [{"startHour": 12.5, "endHour": 15}]}]), 0)

    def test_concurrent_tracking_change_aborts_instead_of_overwriting(self):
        original_update = b.update_settings
        def drift(mutator, path):
            updated = copy.deepcopy(self.settings)
            updated["extendable_bookings"][0]["endTime"] = "12:45"
            path.write_text(json.dumps(updated))
            return original_update(mutator, path)
        with mock.patch.object(b, "update_settings", side_effect=drift):
            with self.assertRaises(b.SettingsError):
                self.reconcile()
        self.assertEqual(json.loads(self.path.read_text())["extendable_bookings"][0]["endTime"], "12:45")
