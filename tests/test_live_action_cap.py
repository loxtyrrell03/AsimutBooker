import contextlib
import io
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import book_week


class LiveActionCliBoundaryTests(unittest.TestCase):
    def parse_validated(self, argv):
        parser = book_week.build_argument_parser()
        args = parser.parse_args(argv)
        book_week._validate_cli_args(parser, args)
        return args

    def assert_rejected(self, argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parse_validated(argv)

    def test_accepts_only_supported_steps_with_a_mutation_scope(self):
        for minutes in book_week.LIVE_ACTION_MINUTES:
            with self.subTest(minutes=minutes):
                args = self.parse_validated(
                    [
                        "--only-room",
                        "B0.29",
                        "--max-action-minutes",
                        str(minutes),
                    ]
                )
                self.assertEqual(args.max_action_minutes, minutes)

        for minutes in (0, 29, 31, 44, 121, 150):
            with self.subTest(invalid_minutes=minutes):
                self.assert_rejected(
                    [
                        "--only-room",
                        "B0.29",
                        "--max-action-minutes",
                        str(minutes),
                    ]
                )

    def test_cap_requires_room_or_date_and_cannot_run_in_check_only_mode(self):
        self.assert_rejected(["--max-action-minutes", "30"])
        self.assert_rejected(
            [
                "--check-only",
                "--only-room",
                "B0.29",
                "--max-action-minutes",
                "30",
            ]
        )

        today = datetime.now().date().isoformat()
        args = self.parse_validated(
            ["--only-date", today, "--max-action-minutes", "30"]
        )
        self.assertEqual(args.only_date, today)


class LiveActionDurationBoundaryTests(unittest.TestCase):
    def test_create_cap_stays_subordinate_to_daily_and_weekly_budgets(self):
        self.assertEqual(
            book_week._bounded_create_duration_minutes(120, 2.0, 28.0, 30),
            30,
        )
        self.assertEqual(
            book_week._bounded_create_duration_minutes(120, 0.5, 28.0, 120),
            30,
        )
        self.assertEqual(
            book_week._bounded_create_duration_minutes(120, 2.0, 0.75, 120),
            45,
        )

    def test_thirty_minute_create_cap_has_no_later_extension_target(self):
        maximum = book_week._bounded_create_duration_minutes(
            120,
            remaining_daily_hours=2.0,
            remaining_weekly_hours=28.0,
            max_action_minutes=30,
        )
        target = book_week._extension_target_end_hour(
            start_hour=9.0,
            current_end_hour=9.5,
            max_possible_duration=maximum,
        )
        self.assertIsNone(target)

    def test_extension_cap_applies_to_increment_not_total_booking_length(self):
        booking = {
            "room": "B0.29",
            "date": "2026-09-01",
            "startTime": "17:00",
            "endTime": "17:30",
            "target_end": "19:00",
            "created_at": "2026-08-27T17:30:00",
            "horizon_days": 5,
            "eventId": 4242,
            "event_url": "https://rwcmd.asimut.net/arrangement?eventId=4242",
        }
        page = object()

        with (
            mock.patch.object(
                book_week,
                "calculate_max_extension",
                return_value=(True, 19.0, "ready"),
            ),
            mock.patch.object(
                book_week,
                "edit_reservation_end_time",
                return_value=True,
            ) as edit,
            mock.patch.object(book_week, "update_extendable_booking_end_time") as update,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            success, new_end, message = book_week.try_extend_booking(
                page,
                booking,
                max_action_minutes=30,
            )

        self.assertTrue(success, message)
        self.assertEqual(new_end, "18:00")
        edit.assert_called_once_with(page, booking, "18:00")
        update.assert_called_once_with("B0.29", "2026-09-01", "17:00", "18:00")

    def test_max_actions_one_stops_after_first_successful_extension(self):
        today = datetime.now().date().isoformat()
        bookings = [
            {
                "room": "B0.29",
                "date": today,
                "startTime": start,
                "endTime": end,
                "target_end": target,
                "created_at": datetime.now().isoformat(),
                "horizon_days": 5,
            }
            for start, end, target in (
                ("09:00", "09:30", "10:00"),
                ("12:00", "12:30", "13:00"),
            )
        ]
        args = SimpleNamespace(
            headless=True,
            check_only=False,
            only_date=None,
            only_room="B0.29",
            target_time=None,
            max_actions=1,
            max_action_minutes=30,
        )

        page = mock.MagicMock()
        page.locator.return_value.first.count.return_value = 0
        context = mock.MagicMock()
        context.new_page.return_value = page
        browser = mock.MagicMock()
        browser.new_context.return_value = context
        playwright = mock.MagicMock()
        playwright.chromium.launch.return_value = browser
        playwright_context = mock.MagicMock()
        playwright_context.__enter__.return_value = playwright

        time_preferences = {
            "enabled": False,
            "start_hour": 7.0,
            "end_hour": 23.0,
            "strict_mode": False,
        }
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.object(book_week, "scan_agenda", return_value=(0, [])),
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(book_week, "safe_goto"),
            mock.patch.object(book_week, "open_practice_room_overview"),
            mock.patch.object(book_week, "assert_calendar_date"),
            mock.patch.object(book_week, "load_disabled_dates", return_value=set()),
            mock.patch.object(book_week, "load_time_preferences", return_value=time_preferences),
            mock.patch.object(
                book_week,
                "load_booking_strategy",
                return_value={"reverse_date_order": False},
            ),
            mock.patch.object(
                book_week,
                "is_any_room_bookable_on_day",
                return_value=(False, "outside horizon"),
            ),
            mock.patch.object(book_week, "load_extendable_bookings", return_value=bookings),
            mock.patch.object(
                book_week,
                "try_extend_booking",
                return_value=(True, "10:00", "Extended successfully"),
            ) as extend,
            mock.patch.object(book_week, "save_history") as save_history,
            mock.patch.object(book_week, "persist_storage_state"),
        ):
            result = book_week.run_booking(args, {}, book_week.PracticePlan())

        self.assertEqual(result, 0)
        self.assertEqual(extend.call_count, 1)
        self.assertEqual(save_history.call_args.args[0], 1)


if __name__ == "__main__":
    unittest.main()
