import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import book_week as b


class ExtensionFailureReportingTests(unittest.TestCase):
    def test_failed_edit_reaches_history_and_exit_status_without_losing_successes(self):
        booking = dict(eventId=42, date="2026-09-22", room="B0.13", startTime="12:00",
                       endTime="12:30", target_end="14:00", created_at="2026-09-15T12:30:00")

        def run(*args):
            tracker = mock.Mock()
            tracker.get_hours_for_day.return_value = 1
            with (mock.patch.object(b, "load_settings_document", return_value={}),
                  mock.patch.object(b, "load_extendable_bookings", return_value=[booking]),
                  mock.patch("progressive_runtime.protected_event_ids", return_value=set()),
                  mock.patch.object(b, "booking_window_dates", return_value=[date(2026, 9, 22)]),
                  mock.patch.object(b, "extension_processing_sort_key", return_value=0),
                  mock.patch.object(b, "PRIORITY_ROOMS", ["B0.13"]),
                  mock.patch.object(b, "try_extend_booking", return_value=(False, None, "Edit operation failed"))):
                total, _ = b.process_pending_extensions(mock.Mock(), tracker, [],
                    b.PracticePlan(enabled=True, default_hours=4), [], None,
                    SimpleNamespace(), 1, ["EXTENDED: Other 2026-09-21 16:00-18:00"])
            b.save_history(total, 20, ["EXTENDED: Other 2026-09-21 16:00-18:00"])
            return 0

        with (tempfile.TemporaryDirectory() as directory,
              mock.patch.object(b, "history_file", Path(directory) / "history.json"),
              mock.patch.object(b, "_load_and_validate_runtime_settings", return_value=({}, None, None)),
              mock.patch.object(b, "SingleInstanceLock") as lock,
              mock.patch.object(b, "booking_preference_run", return_value=contextlib.nullcontext()),
              mock.patch.object(b, "run_booking", side_effect=run),
              mock.patch.object(b, "send_notification") as notify,
              contextlib.redirect_stdout(io.StringIO())):
            lock.return_value.acquire.return_value = True
            self.assertEqual(b.main(["--headless"]), 1)
            entry = json.loads(b.history_file.read_text())["runs"][0]
            self.assertEqual(entry["outcome"], "failed")
            self.assertEqual(entry["bookings_made"], 1)
            self.assertIn("EXTENSION FAILED:", entry["details"])
            self.assertIn("EXTENDED: Other", entry["details"])
            self.assertEqual(notify.call_args.args[0], "AsimutBooker needs attention")
        self.assertIsNone(b._run_extension_failures.get())

    def test_waiting_for_an_unlock_is_not_an_editor_failure(self):
        # Normal horizon waits never reach an editor attempt or failure state.
        with (tempfile.TemporaryDirectory() as directory,
              mock.patch.object(b, "history_file", Path(directory) / "history.json"),
              mock.patch.object(b, "send_notification")):
            token = b._run_extension_failures.set([])
            try:
                b.save_history(0, 20, [], notify=False)
            finally:
                b._run_extension_failures.reset(token)
            self.assertEqual(json.loads(b.history_file.read_text())["runs"][0]["outcome"], "completed")
