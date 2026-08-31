import copy
import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import book_week
from event_identity import event_identity_v2


class BookingNotificationFormattingTests(unittest.TestCase):
    def test_multiword_site_room_name_is_preserved(self):
        self.assertEqual(
            book_week.format_booking_notification_detail(
                "2026-09-02 The Hopkins Studio 16:00 17:30 90"
            ),
            "Wed 2 Sep: The Hopkins Studio 16:00-17:30 "
            "(1 hour and 30 minutes)",
        )

    def test_malformed_detail_is_left_unchanged(self):
        detail = "unstructured booking detail"
        self.assertEqual(book_week.format_booking_notification_detail(detail), detail)

    def test_success_notification_reminds_user_to_reconfirm_on_college_wifi(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "booking_history.json"
            with (
                mock.patch.object(book_week, "history_file", history_path),
                mock.patch.object(book_week, "send_notification") as notify,
            ):
                book_week.save_history(
                    1,
                    0,
                    ["2026-09-02 Weston Gallery 12:00 14:00 120"],
                )

        notify.assert_called_once()
        title, message = notify.call_args.args[:2]
        self.assertEqual(title, "Booked 1 room")
        self.assertIn("Weston Gallery 12:00-14:00", message)
        self.assertIn("reconfirm provisional bookings on RWCMD Wi-Fi", message)


class _YamlStub:
    class YAMLError(Exception):
        pass

    def __init__(self, value):
        self.value = value

    def safe_load(self, _stream):
        if isinstance(self.value, BaseException):
            raise self.value
        return copy.deepcopy(self.value)


class StrictConfigTests(unittest.TestCase):
    def _load_existing_config(self, payload, *, yaml_available=True):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        app_dir = Path(temporary_directory.name)
        config_dir = app_dir / "config"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("fixture: true\n", encoding="utf-8")

        yaml_stub = _YamlStub(payload)
        with (
            mock.patch.object(book_week, "APP_DIR", app_dir),
            mock.patch.object(book_week, "YAML_AVAILABLE", yaml_available),
            mock.patch.object(book_week, "yaml", yaml_stub, create=True),
        ):
            return book_week.load_config()

    def test_missing_config_uses_an_independent_default_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(book_week, "APP_DIR", Path(temp_dir)):
                loaded = book_week.load_config()

        self.assertIsNot(loaded, book_week._DEFAULT_CONFIG)
        loaded["rolling_quota"] = 1
        loaded["test_only"] = True
        self.assertEqual(book_week._DEFAULT_CONFIG["rolling_quota"], 28)
        self.assertNotIn("test_only", book_week._DEFAULT_CONFIG)
        self.assertNotIn("priority_rooms", loaded)
        self.assertNotIn("room_horizons", loaded)

    def test_existing_config_requires_yaml_support(self):
        with self.assertRaisesRegex(
            book_week.ConfigError, "config.yaml exists but PyYAML is not installed"
        ):
            self._load_existing_config({}, yaml_available=False)

    def test_existing_unreadable_yaml_does_not_fall_back_to_defaults(self):
        parse_error = _YamlStub.YAMLError("broken YAML")
        with self.assertRaisesRegex(book_week.ConfigError, "Could not read config.yaml"):
            self._load_existing_config(parse_error)

    def test_supported_schema_accepts_exact_advanced_rule_values(self):
        loaded = self._load_existing_config(
            {
                "rules": {
                    "rolling_quota": 20.5,
                    "same_room_gap_minutes": 45,
                    "peak_hours": {
                        "start": "08:00",
                        "end": "17:00",
                        "max_hours": 3.5,
                    },
                }
            }
        )

        self.assertEqual(loaded["rolling_quota"], 20.5)
        self.assertEqual(loaded["same_room_gap_minutes"], 45)
        self.assertEqual((loaded["peak_start"], loaded["peak_end"]), (8, 17))
        self.assertEqual(loaded["max_peak_hours"], 3.5)
        self.assertEqual(set(loaded), set(book_week._DEFAULT_CONFIG))

    def test_yaml_room_order_and_horizons_are_rejected_as_site_owned(self):
        legacy_room_settings = {
            "room section": {
                "rooms": {
                    "priority": [{"room": "B0.29", "horizon": 5}],
                }
            },
            "top-level horizons": {"room_horizons": {"B0.29": 5}},
            "horizons under rules": {
                "rules": {"room_horizons": {"B0.29": 5}}
            },
        }
        for label, payload in legacy_room_settings.items():
            with self.subTest(label=label):
                with self.assertRaises(book_week.ConfigError):
                    self._load_existing_config(payload)

    def test_invalid_existing_config_cases_fail_closed(self):
        invalid_cases = {
            "non-mapping document": [],
            "unknown top-level section": {"schedule": {}},
            "unknown rules key": {"rules": {"quota": 28}},
            "boolean quota": {"rules": {"rolling_quota": True}},
            "non-quarter-hour gap": {"rules": {"same_room_gap_minutes": 10}},
            "non-padded peak time": {
                "rules": {"peak_hours": {"start": "9:00"}}
            },
            "reversed peak window": {
                "rules": {
                    "peak_hours": {"start": "17:00", "end": "08:00"}
                }
            },
        }

        for label, payload in invalid_cases.items():
            with self.subTest(label=label):
                with self.assertRaises(book_week.ConfigError):
                    self._load_existing_config(payload)

    def test_entrypoint_stops_on_config_error_before_session_or_playwright(self):
        output = io.StringIO()
        config_error = book_week.ConfigError("unsupported configuration fixture")
        with (
            mock.patch.object(book_week, "_CONFIG_ERROR", config_error),
            mock.patch.object(book_week, "validate_state_file") as validate_state,
            mock.patch.object(book_week, "sync_playwright") as playwright,
            redirect_stdout(output),
        ):
            result = book_week.main(["--headless"])

        self.assertEqual(result, 4)
        validate_state.assert_not_called()
        playwright.assert_not_called()
        self.assertIn("config.yaml cannot be followed safely", output.getvalue())


class _CountLocator:
    def count(self):
        return 1


class _AgendaPage:
    """Small, network-free page double for the Python side of agenda scanning."""

    def __init__(self, today, raw_events, window_dates):
        self.url = book_week.ASIMUT_AGENDA_URL
        self.today = today
        self.raw_events = raw_events
        self.window_dates = tuple(window_dates)
        self.configured_rooms = None
        self.extraction_script = None

    def is_closed(self):
        return False

    def goto(self, url, **_kwargs):
        self.url = url

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def wait_for_timeout(self, _milliseconds):
        return None

    def locator(self, _selector):
        return _CountLocator()

    @staticmethod
    def _configured_room(text, configured_rooms):
        matches = []
        for room in configured_rooms:
            pattern = rf"(?<![A-Za-z0-9.]){re.escape(room)}(?![A-Za-z0-9.])"
            if re.search(pattern, text or "", flags=re.IGNORECASE):
                matches.append(room)
        if not matches:
            return None
        longest = max(len(room) for room in matches)
        longest_matches = [room for room in matches if len(room) == longest]
        return longest_matches[0] if len(longest_matches) == 1 else None

    def evaluate(self, expression, *args):
        if expression == "() => document.body.innerText":
            return self.window_dates[-1].strftime("%d %B %Y").lstrip("0")
        if expression == "() => document.body.scrollHeight":
            return 1000
        if expression.startswith("window.scroll"):
            return None
        if (
            "agendaDayStructure" in expression
            and "function configuredRoomFromText" in expression
        ):
            scan_policy = args[0]
            configured_rooms = list(scan_policy["configuredRooms"])
            self.configured_rooms = configured_rooms
            self.extraction_script = expression
            events = []
            for raw_event in self.raw_events:
                event = dict(raw_event)
                if event.get("date") not in scan_policy["allowedDates"]:
                    continue
                location = event.pop("location", "")
                event["room"] = self._configured_room(location, configured_rooms)
                events.append(event)
            days = []
            for current in self.window_dates:
                event_ids = [
                    event["eventId"]
                    for event in self.raw_events
                    if event.get("date") == current.isoformat()
                    and isinstance(event.get("eventId"), int)
                    and event["eventId"] > 0
                ]
                days.append({
                    "header": current.strftime("%A %d %B %Y").replace(" 0", " "),
                    "parsedDate": current.isoformat(),
                    "activeEventIds": event_ids,
                    "eventIds": event_ids,
                    "invalidCards": [],
                })
            return {"events": events, "agendaDayStructure": days}
        raise AssertionError(f"Unexpected page evaluation: {expression[:80]}")


class _AgendaTracker:
    def __init__(self):
        self.existing_events = []
        self.peak_hours_by_day = {}

    def add_existing_event(self, target_date, start, end, **details):
        self.existing_events.append((target_date.date(), start, end, details))

    def get_total_booking_hours(self):
        return sum(
            end - start
            for _target_date, start, end, details in self.existing_events
            if details["is_reservation"]
        )

    def get_remaining_quota_hours(self):
        return book_week.MAX_ROLLING_QUOTA_HOURS - self.get_total_booking_hours()


class AgendaIdentityTests(unittest.TestCase):
    @staticmethod
    def _window_dates(today, count=6):
        return tuple(today + timedelta(days=offset) for offset in range(count))

    def test_configured_room_extraction_and_dedup_use_complete_event_identity(self):
        today = date(2026, 8, 30)
        window_dates = self._window_dates(today)
        common = {
            "date": today.isoformat(),
            "daysDiff": 0,
            "startTime": "09:00",
            "endTime": "10:00",
        }
        a_wing_reservation = {
            **common,
            "title": "Reservation",
            "isReservation": True,
            "location": "Music Practice Room A3.39 - AHC",
        }
        raw_events = [
            a_wing_reservation,
            dict(a_wing_reservation),  # exact DOM duplicate: remove it
            {
                **common,
                "title": "Reservation",
                "isReservation": True,
                "location": "Music Practice Room B0.29 - AHC",
            },
            {
                **common,
                "title": "Other",
                "isReservation": False,
                "location": "Class in A3.39",
            },
            {
                **common,
                "title": "Other",
                "isReservation": False,
                "location": "Unconfigured room A3.390",
            },
        ]
        page = _AgendaPage(today, raw_events, window_dates)
        tracker = _AgendaTracker()

        with (
            mock.patch.object(
                book_week,
                "LIVE_CATALOG_ROOM_NAMES",
                ["A3.39", "B0.29"],
            ),
            redirect_stdout(io.StringIO()),
        ):
            event_count, reservations = book_week.scan_agenda(
                page,
                tracker,
                today,
                ignored_events=[],
                window_dates=window_dates,
            )

        self.assertEqual(event_count, 4)
        self.assertEqual(len(tracker.existing_events), 4)
        self.assertEqual(len(tracker.agenda_events), 4)
        self.assertEqual(len(reservations), 2)
        self.assertEqual(
            [reservation["room"] for reservation in reservations],
            ["A3.39", "B0.29"],
        )
        self.assertIn("A3.39", page.configured_rooms)
        self.assertIn(
            "configuredRoomFromText(clean(locationLinks[0].textContent))",
            page.extraction_script,
        )
        self.assertNotIn("identityRoomFromText", page.extraction_script)

    def test_complete_agenda_scan_can_publish_display_snapshot(self):
        today = date(2026, 8, 30)
        window_dates = self._window_dates(today)
        page = _AgendaPage(
            today,
            [
                {
                    "date": today.isoformat(),
                    "daysDiff": 0,
                    "startTime": "09:00",
                    "endTime": "10:00",
                    "title": "Reservation",
                    "isReservation": True,
                    "location": "Music Practice Room B0.29 - AHC",
                    "eventId": 123,
                }
            ],
            window_dates,
        )
        tracker = _AgendaTracker()
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "agenda.json"
            with (
                mock.patch.object(book_week, "LIVE_CATALOG_ROOM_NAMES", ["B0.29"]),
                redirect_stdout(io.StringIO()),
            ):
                book_week.scan_agenda(
                    page,
                    tracker,
                    today,
                    ignored_events=[],
                    window_dates=window_dates,
                    snapshot_path=snapshot_path,
                )

            from agenda_snapshot import read_agenda_snapshot

            result = read_agenda_snapshot(
                snapshot_path,
                now=datetime.now().astimezone(),
                expected_dates=window_dates,
            )
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.events[0].room, "B0.29")

    def test_reservation_without_one_exact_live_room_fails_closed(self):
        today = date(2026, 8, 30)
        window_dates = self._window_dates(today)
        page = _AgendaPage(
            today,
            [
                {
                    "date": today.isoformat(),
                    "daysDiff": 0,
                    "startTime": "09:00",
                    "endTime": "10:00",
                    "title": "Reservation",
                    "isReservation": True,
                    "location": "A retired room not in the live catalog",
                }
            ],
            window_dates,
        )

        with (
            mock.patch.object(book_week, "LIVE_CATALOG_ROOM_NAMES", ["Studio"]),
            redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "one exact live catalog name"),
        ):
            book_week.scan_agenda(
                page,
                _AgendaTracker(),
                today,
                ignored_events=[],
                window_dates=window_dates,
            )

    def test_overlapping_live_room_names_choose_longest_exact_candidate(self):
        self.assertEqual(
            _AgendaPage._configured_room(
                "Studio 1 (Practice)", ["Studio", "Studio 1"]
            ),
            "Studio 1",
        )

    def test_ambiguous_legacy_ignore_key_conservatively_ignores_neither_event(self):
        today = date(2026, 8, 31)  # Monday: peak accounting applies.
        window_dates = self._window_dates(today)
        ignored_key = f"{today.isoformat()}_09:00_10:00"
        raw_events = [
            {
                "date": today.isoformat(),
                "daysDiff": 0,
                "startTime": "09:00",
                "endTime": "10:00",
                "title": "Reservation",
                "isReservation": True,
                "location": "Music Practice Room B0.29 - AHC",
            },
            {
                "date": today.isoformat(),
                "daysDiff": 0,
                "startTime": "09:00",
                "endTime": "10:00",
                "title": "Other",
                "isReservation": False,
                "location": "Class",
            },
        ]
        page = _AgendaPage(today, raw_events, window_dates)
        tracker = book_week.BookingTracker()

        with redirect_stdout(io.StringIO()):
            event_count, reservations = book_week.scan_agenda(
                page,
                tracker,
                today,
                ignored_events=[ignored_key],
                window_dates=window_dates,
            )

        # One old key cannot choose between the class and reservation, so both
        # remain conflicts and the reservation still consumes every budget.
        self.assertEqual(event_count, 2)
        self.assertEqual(len(reservations), 1)
        self.assertEqual(tracker.get_total_booking_hours(), 1.0)
        self.assertEqual(tracker.get_hours_for_day(today), 1.0)
        self.assertEqual(tracker.get_peak_used_for_day(today), 60.0)
        self.assertTrue(tracker.overlaps_conflict(today, 9.0, 10.0))

        allowed, reason = tracker.can_book("B1.16", today, 9.0, 30)
        self.assertFalse(allowed)
        self.assertIn("existing reservation", reason)
        allowed, reason = tracker.can_book("B0.29", today, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("gap from existing reservation", reason)

    def test_v2_ignored_reservation_still_counts_quota_peak_and_room_gap(self):
        today = date(2026, 8, 31)
        window_dates = self._window_dates(today)
        scanned_event = {
            "date": today.isoformat(),
            "daysDiff": 0,
            "startTime": "09:00",
            "endTime": "10:00",
            "title": "Reservation",
            "isReservation": True,
            "room": "B0.29",
        }
        raw_event = {
            **scanned_event,
            "location": "Music Practice Room B0.29 - AHC",
        }
        raw_event.pop("room")
        page = _AgendaPage(today, [raw_event], window_dates)
        tracker = book_week.BookingTracker()

        with redirect_stdout(io.StringIO()):
            event_count, reservations = book_week.scan_agenda(
                page,
                tracker,
                today,
                ignored_events=[event_identity_v2(scanned_event)],
                window_dates=window_dates,
            )

        self.assertEqual(event_count, 0)
        self.assertEqual(len(reservations), 1)
        self.assertEqual(tracker.get_total_booking_hours(), 1.0)
        self.assertEqual(tracker.get_hours_for_day(today), 1.0)
        self.assertEqual(tracker.get_peak_used_for_day(today), 60.0)
        self.assertFalse(tracker.overlaps_conflict(today, 9.0, 10.0))

        allowed, reason = tracker.can_book("B1.16", today, 9.0, 30)
        self.assertTrue(allowed, reason)
        allowed, reason = tracker.can_book("B0.29", today, 10.0, 30)
        self.assertFalse(allowed)
        self.assertIn("gap from existing reservation", reason)

    def test_unique_legacy_ignore_key_remains_compatible(self):
        today = date(2026, 8, 31)
        window_dates = self._window_dates(today)
        raw_event = {
            "date": today.isoformat(),
            "daysDiff": 0,
            "startTime": "09:00",
            "endTime": "10:00",
            "title": "Other",
            "isReservation": False,
            "location": "Class",
        }
        page = _AgendaPage(today, [raw_event], window_dates)
        tracker = book_week.BookingTracker()

        with redirect_stdout(io.StringIO()):
            event_count, reservations = book_week.scan_agenda(
                page,
                tracker,
                today,
                ignored_events=[f"{today.isoformat()}_09:00_10:00"],
                window_dates=window_dates,
            )

        self.assertEqual(event_count, 0)
        self.assertEqual(reservations, [])
        self.assertNotIn(today.isoformat(), tracker.conflict_ranges)


if __name__ == "__main__":
    unittest.main()
