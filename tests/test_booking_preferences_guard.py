import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest import mock

import book_week as b
from app_settings import atomic_write_json, load_settings, update_settings
from booking_preferences_guard import (
    BookingPreferencesChanged, booking_preference_run, booking_save_boundary,
)
import tests.test_horizon_snipe_flow as horizon_fixture


class BookingPreferenceGuardTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "settings.json"
        self.settings = {"time_preferences": {"enabled": True, "preset": "afternoon"}}
        atomic_write_json(self.path, self.settings)

    def test_changed_controls_stop_before_receipt_or_click(self):
        for key in ["time_preferences", "practice_plan", "disabled_dates", "room_preferences",
                    "booking_strategy", "ignored_events", "rebooking_blackouts"]:
            atomic_write_json(self.path, self.settings)
            with self.subTest(control=key), booking_preference_run(self.path, self.settings):
                update_settings(lambda settings: settings.update({key: {"changed": True}}), self.path)
                save = mock.Mock()
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    save()
                save.assert_not_called()

    def test_runtime_progress_does_not_invalidate_same_run(self):
        with booking_preference_run(self.path, self.settings):
            update_settings(lambda settings: settings.update({
                "extendable_bookings": [{"event_id": 42}], "agenda_events": [],
            }), self.path)
            with booking_save_boundary():
                pass

    def test_unreadable_or_removed_settings_stop_without_save(self):
        for corrupt in [True, False]:
            atomic_write_json(self.path, self.settings)
            with booking_preference_run(self.path, self.settings):
                if corrupt:
                    self.path.write_text("{", encoding="utf-8")
                else:
                    self.path.unlink()
                with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                    self.fail("Save must not be reached")

    def test_preference_write_waits_until_save_boundary_is_released(self):
        started, finished = Event(), Event()
        failures = []
        def edit():
            started.set()
            try:
                update_settings(lambda settings: settings.update({"disabled_dates": ["2026-09-15"]}), self.path)
            except Exception as exc:
                failures.append(exc)
            finally:
                finished.set()
        with booking_preference_run(self.path, self.settings):
            with booking_save_boundary():
                writer = Thread(target=edit)
                writer.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(.1))
                self.assertNotIn("disabled_dates", load_settings(self.path))
            writer.join(2)
            self.assertTrue(finished.is_set())
            self.assertEqual(failures, [])
            with self.assertRaises(BookingPreferencesChanged), booking_save_boundary():
                self.fail("A second Save must observe the new controls")

    def test_horizon_form_prepared_before_preference_change_never_saves(self):
        fixture = horizon_fixture.HorizonSnipeFlowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        page = fixture.build_page()
        def validate(*_args):
            update_settings(lambda settings: settings.update({"disabled_dates": ["2026-09-04"]}), self.path)
            return True, "fresh validation"
        with contextlib.ExitStack() as stack:
            for patch in fixture.patches(page):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(b, "refresh_new_booking_validation", side_effect=validate))
            receipt = stack.enter_context(mock.patch.object(b, "record_pending_create"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(booking_preference_run(self.path, self.settings))
            with self.assertRaises(BookingPreferencesChanged):
                b.try_horizon_snipe(page, fixture.slot(), fixture.TARGET_DATE, fixture.tracker(), 5)
        receipt.assert_not_called()
        self.assertNotIn(mock.call(140, 220), page.mouse.click.call_args_list)

    def test_changed_preferences_are_not_retried_in_another_room(self):
        with mock.patch.object(b, "try_book_slot", side_effect=BookingPreferencesChanged("changed")), \
                mock.patch.object(b, "get_available_slots") as scan:
            with self.assertRaises(BookingPreferencesChanged):
                b.attempt_booking_with_room_fallback(
                    object(), {"room": "A"}, None, None, 0,
                    time_prefs={}, daily_planning=b.DailyPlanningPreferences(),
                )
        scan.assert_not_called()

    def test_main_reports_changed_preferences_without_uncertain_mutation_warning(self):
        lock = mock.Mock()
        lock.acquire.return_value = True
        with mock.patch.object(b, "SingleInstanceLock", return_value=lock), \
                mock.patch.object(b, "settings_file", self.path), \
                mock.patch.object(b, "_load_and_validate_runtime_settings", return_value=(
                    self.settings, b.PracticePlan(), mock.Mock())), \
                mock.patch.object(b, "run_booking", side_effect=BookingPreferencesChanged("changed")), \
                mock.patch.object(b, "save_history") as history, \
                mock.patch.object(b, "send_notification") as notify, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main([]), 3)
        history.assert_not_called()
        notify.assert_not_called()
        lock.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
