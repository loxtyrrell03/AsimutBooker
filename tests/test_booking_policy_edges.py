import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import book_week
from app_settings import SettingsError, load_settings
from book_week import (
    BookingTracker,
    calculate_target_hours_for_day,
    page_is_asimut_origin,
    interval_is_strictly_preferred,
    load_booking_strategy,
    load_disabled_dates,
    load_extendable_bookings,
    load_time_preferences,
    trim_to_strict_time_window,
)
from practice_plan import PracticePlan, remaining_target_hours
from runtime_guard import booking_times_match, hhmm_values_match, parse_confirmed_event_id


class SameRoomGapBoundaryTests(unittest.TestCase):
    def setUp(self):
        # 2026-08-30 is a Sunday, so these tests isolate same-room behavior
        # without a weekday peak-hours limit affecting the result.
        self.day = date(2026, 8, 30)
        self.day_at_noon = datetime(2026, 8, 30, 12, 0)

    def test_session_booking_accepts_date_datetime_mix_and_enforces_exact_gap(self):
        tracker = BookingTracker()
        tracker.add_booking("B0.29", self.day_at_noon, 9.0, 10.0)

        allowed, reason = tracker.can_book("B0.29", self.day, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("0min after", reason)

        allowed, reason = tracker.can_book("B0.29", self.day, 10.75, 30)
        self.assertFalse(allowed)
        self.assertIn("45min after", reason)

        allowed, reason = tracker.can_book("B0.29", self.day, 11.0, 30)
        self.assertTrue(allowed, reason)
        self.assertEqual(tracker.bookings_today(self.day), 1)
        self.assertEqual(tracker.bookings_today(self.day_at_noon), 1)

    def test_session_booking_enforces_gap_before_at_zero_45_and_60_minutes(self):
        tracker = BookingTracker()
        tracker.add_booking("B1.09", self.day, 12.0, 13.0)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 11.5, 30)
        self.assertFalse(allowed)
        self.assertIn("0min before", reason)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 10.75, 30)
        self.assertFalse(allowed)
        self.assertIn("45min before", reason)

        allowed, reason = tracker.can_book("B1.09", self.day_at_noon, 10.5, 30)
        self.assertTrue(allowed, reason)

    def test_existing_reservation_gap_is_room_specific_across_date_types(self):
        tracker = BookingTracker()
        tracker.add_existing_event(
            self.day,
            9.0,
            10.0,
            is_reservation=True,
            room="B0.27",
        )

        allowed, reason = tracker.can_book("B0.27", self.day_at_noon, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("existing reservation", reason)

        allowed, reason = tracker.can_book("B0.27", self.day_at_noon, 11.0, 30)
        self.assertTrue(allowed, reason)

        # A room gap must not block a different room at the same adjacent time.
        allowed, reason = tracker.can_book("B1.16", self.day_at_noon, 10.0, 30)
        self.assertTrue(allowed, reason)


class StrictTimeWindowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.strict = {
            "enabled": True,
            "start_hour": 14.0,
            "end_hour": 22.0,
            "strict_mode": True,
        }

    def test_full_interval_must_fit_including_exact_boundaries(self):
        self.assertTrue(interval_is_strictly_preferred(14.0, 22.0, self.strict))
        self.assertTrue(interval_is_strictly_preferred(14.0, 14.5, self.strict))
        self.assertFalse(interval_is_strictly_preferred(13.75, 14.5, self.strict))
        self.assertFalse(interval_is_strictly_preferred(21.75, 22.25, self.strict))

    def test_trim_keeps_exactly_30_minutes_and_rejects_15_minutes(self):
        self.assertEqual(
            trim_to_strict_time_window(13.5, 14.5, self.strict),
            (14.0, 14.5),
        )
        self.assertEqual(
            trim_to_strict_time_window(21.5, 22.5, self.strict),
            (21.5, 22.0),
        )
        self.assertEqual(
            trim_to_strict_time_window(13.5, 14.25, self.strict),
            (None, None),
        )
        self.assertEqual(
            trim_to_strict_time_window(21.75, 22.25, self.strict),
            (None, None),
        )

    def test_non_strict_preferences_do_not_trim_or_reject(self):
        preferred = dict(self.strict, strict_mode=False)
        self.assertEqual(
            trim_to_strict_time_window(8.0, 23.0, preferred),
            (8.0, 23.0),
        )
        self.assertTrue(interval_is_strictly_preferred(8.0, 23.0, preferred))

    def test_custom_preference_clock_values_are_parsed_exactly(self):
        prefs = load_time_preferences(
            {
                "time_preferences": {
                    "enabled": True,
                    "preset": "custom",
                    "custom_start_hour": 14,
                    "custom_start_min": 30,
                    "custom_end_hour": 21,
                    "custom_end_min": 45,
                    "strict_mode": True,
                }
            }
        )
        self.assertEqual(prefs["start_hour"], 14.5)
        self.assertEqual(prefs["end_hour"], 21.75)
        self.assertTrue(prefs["strict_mode"])


class FailClosedSettingsBoundaryTests(unittest.TestCase):
    def test_invalid_utf8_and_non_object_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_bytes(b"{\"value\": \xff}")
            with self.assertRaises(SettingsError):
                load_settings(path)

            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(SettingsError):
                load_settings(path)

    def test_booking_setting_sections_reject_wrong_shapes(self):
        invalid_calls = (
            lambda: load_disabled_dates({"disabled_dates": "2026-09-01"}),
            lambda: load_disabled_dates({"disabled_dates": ["2026-09-01", 2]}),
            lambda: load_time_preferences({"time_preferences": []}),
            lambda: load_booking_strategy({"booking_strategy": []}),
            lambda: load_extendable_bookings({"extendable_bookings": {}}),
            lambda: load_extendable_bookings({"extendable_bookings": [{}]}),
        )
        for invalid_call in invalid_calls:
            with self.subTest(call=invalid_call):
                with self.assertRaises(SettingsError):
                    invalid_call()

    def test_invalid_custom_time_window_is_rejected(self):
        invalid_preferences = (
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": 24,
                "custom_end_hour": 22,
            },
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": 18,
                "custom_end_hour": 14,
            },
            {
                "enabled": True,
                "preset": "custom",
                "custom_start_hour": "late",
                "custom_end_hour": 22,
            },
        )
        for prefs in invalid_preferences:
            with self.subTest(prefs=prefs):
                with self.assertRaises(SettingsError):
                    load_time_preferences({"time_preferences": prefs})

    def test_time_preferences_reject_malformed_dormant_and_coerced_values(self):
        invalid_preferences = (
            {"enabled": False, "preset": "surprise"},
            {"enabled": False, "custom_start_hour": "14"},
            {"enabled": False, "custom_start_min": True},
            {"enabled": "false"},
            {"strict_mode": "true"},
            {"preset": 1},
        )
        for prefs in invalid_preferences:
            with self.subTest(prefs=prefs):
                with self.assertRaises(SettingsError):
                    load_time_preferences({"time_preferences": prefs})


class ExtensionPracticeTargetBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.booking = {
            "room": "B0.29",
            "date": "2026-09-01",
            "startTime": "17:00",
            "endTime": "17:30",
            "target_end": "19:00",
            "created_at": "2026-08-27T17:30:00",
            "horizon_days": 5,
        }

    def test_met_daily_target_clears_extension_before_any_remote_work(self):
        with (
            mock.patch.object(book_week, "remove_extendable_booking") as remove,
            mock.patch.object(
                book_week,
                "calculate_max_extension",
                side_effect=AssertionError("must not calculate or edit"),
            ),
        ):
            result = book_week.try_extend_booking(
                object(), self.booking, remaining_daily_hours=0.0
            )

        self.assertEqual(
            result,
            (False, None, "Daily practice target is met; extension state cleared"),
        )
        remove.assert_called_once_with("B0.29", "2026-09-01", "17:00")

    def test_met_daily_target_reports_failure_to_clear_state(self):
        with mock.patch.object(
            book_week,
            "remove_extendable_booking",
            side_effect=SettingsError("locked"),
        ):
            success, new_end, message = book_week.try_extend_booking(
                object(), self.booking, remaining_daily_hours=0.0
            )

        self.assertFalse(success)
        self.assertIsNone(new_end)
        self.assertIn("could not be cleared", message)


class DailyTargetBoundaryTests(unittest.TestCase):
    class TrackerStub:
        def __init__(self, hours_by_day=None, weekly_remaining=28.0):
            self.hours_by_day = hours_by_day or {}
            self.weekly_remaining = weekly_remaining

        def get_hours_for_day(self, target_date):
            return self.hours_by_day.get(target_date.strftime("%Y-%m-%d"), 0.0)

        def get_remaining_quota_hours(self):
            return self.weekly_remaining

    def test_override_default_existing_hours_and_weekly_quota_all_cap_target(self):
        override_day = date(2026, 9, 1)
        default_day = date(2026, 9, 2)
        plan = PracticePlan(
            enabled=True,
            default_hours=2.5,
            date_overrides={override_day.isoformat(): 0.5},
        )

        tracker = self.TrackerStub()
        self.assertEqual(
            calculate_target_hours_for_day(
                override_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.5, 1),
        )

        tracker = self.TrackerStub({default_day.isoformat(): 0.75})
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (1.75, 4),
        )

        tracker = self.TrackerStub(weekly_remaining=0.5)
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.5, 1),
        )

        tracker = self.TrackerStub({default_day.isoformat(): 2.5})
        self.assertEqual(
            calculate_target_hours_for_day(
                default_day, [override_day, default_day], tracker, practice_plan=plan
            ),
            (0.0, 0),
        )

    def test_fractional_existing_time_rounds_remaining_down_to_quarter_hour(self):
        plan = PracticePlan(enabled=True, default_hours=2.5, date_overrides={})
        self.assertEqual(remaining_target_hours(plan, date(2026, 9, 2), 0.6), 1.75)


class ExactConfirmationBoundaryTests(unittest.TestCase):
    def test_encoded_or_decorated_event_ids_are_not_confirmation(self):
        rejected = (
            "/arrangement?eventId=%31",
            "/arrangement?event%49d=1",
            "/arrangement?eventId=1%20",
            "/arrangement?eventId=1;other=2",
            "/arrangement//?eventId=1",
            "/ARRANGEMENT?eventId=1",
        )
        for url in rejected:
            with self.subTest(url=url):
                self.assertIsNone(parse_confirmed_event_id(url))

    def test_time_values_with_seconds_or_whitespace_cannot_confirm(self):
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30:00", "11:00")
        )
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30", "11:00 ")
        )
        self.assertFalse(
            booking_times_match("09:30", "11:00", "09:30", "10:59")
        )

    def test_unicode_digits_are_not_canonical_confirmation_values(self):
        self.assertIsNone(parse_confirmed_event_id("/arrangement?eventId=1²"))
        self.assertIsNone(parse_confirmed_event_id("/arrangement?eventId=1２"))
        self.assertFalse(hhmm_values_match("0９:30", "0９:30"))


class LoginOriginBoundaryTests(unittest.TestCase):
    class Page:
        def __init__(self, url, closed=False):
            self.url = url
            self._closed = closed

        def is_closed(self):
            return self._closed

    def test_only_exact_secure_rwcmd_asimut_origin_is_accepted(self):
        self.assertTrue(
            page_is_asimut_origin(self.Page("https://rwcmd.asimut.net/overview"))
        )
        for url in (
            "http://rwcmd.asimut.net/overview",
            "https://rwcmd.asimut.net.evil.example/overview",
            "https://evil.example/?next=rwcmd.asimut.net",
            "https://asimut.net/overview",
        ):
            with self.subTest(url=url):
                self.assertFalse(page_is_asimut_origin(self.Page(url)))
        self.assertFalse(
            page_is_asimut_origin(
                self.Page("https://rwcmd.asimut.net/overview", closed=True)
            )
        )


class EntryPointSafetyBoundaryTests(unittest.TestCase):
    def test_missing_session_starts_with_no_storage_state_for_secure_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_state = Path(temp_dir) / "missing-state.json"
            with mock.patch.object(book_week, "state_file", missing_state):
                options = book_week.runtime_context_options()

            self.assertEqual(
                options,
                {"viewport": {"width": 1920, "height": 1080}},
            )
            self.assertNotIn("storage_state", options)


if __name__ == "__main__":
    unittest.main()
