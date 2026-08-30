import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from agenda_snapshot import (
    AgendaSnapshotError,
    publish_agenda_snapshot,
    read_agenda_snapshot,
    snapshot_from_dict,
)


class AgendaSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 30)
        self.dates = tuple(self.today + timedelta(days=index) for index in range(8))
        self.observed_at = datetime(2026, 8, 30, 17, 0, tzinfo=timezone.utc)
        self.events = [
            {
                "date": "2026-09-01",
                "startTime": "16:00",
                "endTime": "17:30",
                "title": "Reservation",
                "isReservation": True,
                "room": "B0.14",
                "eventId": 3580201,
                "daysDiff": 2,
            },
            {
                "date": "2026-08-31",
                "startTime": "10:20",
                "endTime": "11:15",
                "title": "Performance class",
                "isReservation": False,
                "room": None,
                "eventId": 3580200,
                "daysDiff": 1,
            },
        ]

    def test_round_trip_is_canonical_locked_and_preserves_bookings_and_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agenda.json"
            written = publish_agenda_snapshot(
                self.events,
                self.dates,
                observed_at=self.observed_at,
                path=path,
            )
            result = read_agenda_snapshot(
                path,
                now=self.observed_at + timedelta(minutes=5),
                expected_dates=self.dates,
            )

            self.assertFalse(result.stale, result.reason)
            self.assertEqual(result.snapshot, written)
            self.assertEqual(
                [event.title for event in written.events],
                ["Performance class", "Reservation"],
            )
            self.assertEqual(written.events[1].room, "B0.14")
            self.assertTrue(path.with_suffix(".json.lock").exists())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("daysDiff", raw["events"][0])

    def test_stale_or_changed_window_remains_displayable_but_is_labelled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agenda.json"
            publish_agenda_snapshot(
                self.events,
                self.dates,
                observed_at=self.observed_at,
                path=path,
            )
            shifted = tuple(value + timedelta(days=1) for value in self.dates)
            result = read_agenda_snapshot(
                path,
                now=self.observed_at + timedelta(hours=2),
                expected_dates=shifted,
            )

            self.assertIsNotNone(result.snapshot)
            self.assertTrue(result.stale)
            self.assertIn("older than", result.reason)
            self.assertIn("current booking window", result.reason)

    def test_reservation_without_exact_room_is_rejected(self):
        invalid = dict(self.events[0])
        invalid["room"] = None
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AgendaSnapshotError, "room is required"):
                publish_agenda_snapshot(
                    [invalid],
                    self.dates,
                    observed_at=self.observed_at,
                    path=Path(directory) / "agenda.json",
                )

    def test_reader_rejects_noncanonical_or_partial_documents(self):
        raw = {
            "version": 1,
            "observed_at": "2026-08-30T17:00:00Z",
            "dates": [value.isoformat() for value in self.dates],
            "events": [
                {
                    "date": "2026-09-01",
                    "startTime": "16:00",
                    "endTime": "17:30",
                    "title": "Reservation",
                    "isReservation": True,
                    "room": "B0.14",
                    "eventId": 3580201,
                    "unexpected": True,
                }
            ],
        }
        with self.assertRaisesRegex(AgendaSnapshotError, "unexpected or missing"):
            snapshot_from_dict(raw)

    def test_missing_snapshot_is_an_explicit_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            result = read_agenda_snapshot(Path(directory) / "missing.json")
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.stale)
        self.assertIn("No agenda snapshot", result.reason)


if __name__ == "__main__":
    unittest.main()
