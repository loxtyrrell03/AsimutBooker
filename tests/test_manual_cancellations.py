import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app_settings import load_settings, save_settings, SettingsError
from booking_blackouts import subtract_rebooking_blackout
from manual_cancellations import remember_agenda, OBSERVED_KEY


class ManualCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"
        self.day = date(2026, 10, 1)
        self.now = datetime(2026, 10, 1, 10)
        self.event = dict(date=self.day.isoformat(), startTime="14:00", endTime="15:00",
                          room="Room A", eventId=123, isReservation=True)
        save_settings({"unrelated": "keep"}, self.path)

    def scan(self, events, dates=None):
        return remember_agenda(events, dates or [self.day], settings_path=self.path,
                               receipts_path=self.path.with_name("receipts.json"), now=self.now)

    def test_disappearance_persists_and_explicit_reopen_stays_open(self):
        self.assertEqual(self.scan([self.event]), ())
        windows = self.scan([])
        self.assertEqual([(w.start_time, w.end_time) for w in windows], [("14:00", "15:00")])
        self.assertEqual(load_settings(self.path)["unrelated"], "keep")
        subtract_rebooking_blackout(self.day, "14:00", "15:00", path=self.path)
        self.assertEqual(self.scan([]), ())

    def test_uncovered_date_retains_evidence_until_observed(self):
        self.scan([self.event])
        self.assertEqual(self.scan([], [date(2026, 10, 2)]), ())
        self.assertEqual(len(self.scan([])), 1)

    def test_expired_booking_and_classes_do_not_block(self):
        self.scan([self.event, dict(self.event, eventId=124, isReservation=False)])
        self.now = datetime(2026, 10, 1, 16)
        self.assertEqual(self.scan([]), ())

    def test_same_id_edit_updates_baseline(self):
        self.scan([self.event])
        self.assertEqual(self.scan([dict(self.event, room="Room B", endTime="16:00")]), ())
        self.assertEqual(self.scan([])[0].end_time, "16:00")

    def test_pending_internal_change_retains_evidence(self):
        self.scan([self.event])
        receipt = dict(kind="consolidation", status="pending", original={"event_id": 456},
                       originals=[{"event_id": 123}, {"event_id": 456}])
        with patch("manual_cancellations.load_journal", return_value={"receipts": {"r": receipt}}):
            self.assertEqual(self.scan([]), ())
            self.assertIn("123", load_settings(self.path)[OBSERVED_KEY])
            receipt["status"] = "verified"
            self.assertEqual(self.scan([]), ())
        self.assertEqual(self.scan([]), ())

    def test_corrupt_state_fails_without_consuming_evidence(self):
        save_settings({OBSERVED_KEY: []}, self.path)
        with self.assertRaises(SettingsError):
            self.scan([])
        self.assertEqual(load_settings(self.path)[OBSERVED_KEY], [])

    def test_new_baseline_never_invents_preexisting_cancellations(self):
        self.assertEqual(self.scan([]), ())

    def test_active_nonreservation_id_is_not_a_cancellation(self):
        self.scan([self.event])
        self.assertEqual(self.scan([dict(self.event, isReservation=False)]), ())

    def test_complete_scan_installs_conflict_and_incomplete_scan_preserves_baseline(self):
        import io
        from contextlib import redirect_stdout
        import book_week
        from tests.test_config_and_agenda_regressions import _AgendaPage

        day = date(2099, 10, 1)
        event = dict(self.event, date=day.isoformat(), daysDiff=0,
                     location="Room A", title="Reservation")
        snapshot = self.path.with_name("agenda.json")
        with patch.object(book_week, "LIVE_CATALOG_ROOM_NAMES", ["Room A"]), redirect_stdout(io.StringIO()):
            book_week.scan_agenda(_AgendaPage(day, [event], [day]), book_week.BookingTracker(),
                                  day, ignored_events=[], window_dates=[day], snapshot_path=snapshot)
            previous = load_settings(self.path)
            with patch.object(book_week, "_assert_complete_agenda_extraction", side_effect=RuntimeError("incomplete")):
                with self.assertRaises(RuntimeError):
                    book_week.scan_agenda(_AgendaPage(day, [], [day]), book_week.BookingTracker(),
                                          day, ignored_events=[], window_dates=[day], snapshot_path=snapshot)
            self.assertEqual(load_settings(self.path), previous)
            tracker = book_week.BookingTracker()
            book_week.scan_agenda(_AgendaPage(day, [], [day]), tracker, day,
                                  ignored_events=[], window_dates=[day], snapshot_path=snapshot)
            self.assertIn((14.0, 15.0), tracker.conflict_ranges[day.isoformat()])
