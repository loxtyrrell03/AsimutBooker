import contextlib
import io
import unittest
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import book_week


def _install_test_live_policy(today, *, window_days=8):
    """Install one valid site-shaped policy for a mocked authenticated refresh."""

    observed_at = datetime.combine(today, time(12), tzinfo=timezone.utc)
    final_date = today + timedelta(days=window_days - 1)
    booking_horizon = datetime.combine(
        final_date,
        time(12),
        tzinfo=timezone.utc,
    )
    policy = book_week.LiveRoomPolicy(
        observed_at=observed_at,
        booking_horizon=booking_horizon,
        global_horizon_minutes=max(24 * 60, (window_days - 1) * 24 * 60),
        room_order=("B0.29", "B1.09"),
        room_horizon_minutes={"B0.29": 5 * 24 * 60, "B1.09": 3 * 24 * 60},
        room_metadata={
            "B0.29": {
                "instrument_tags": [],
                "room_type_tags": [],
                "features": [],
            },
            "B1.09": {
                "instrument_tags": [],
                "room_type_tags": [],
                "features": [],
            },
        },
        site_minimum_booking_minutes=30,
        site_maximum_booking_minutes=120,
        site_minimum_booking_gap_minutes=60,
        minimum_block_minutes=30,
        allow_fragmented_sessions=True,
    )
    return book_week.install_live_room_policy(policy, today=today)


def _refresh_test_live_policy(call_order=None, *, window_days=8):
    """Build a refresh mock that installs policy before run_booking continues."""

    def refresh(_page, _preferences, *, today=None):
        if call_order is not None:
            call_order.append("refresh")
        return _install_test_live_policy(today, window_days=window_days)

    return refresh


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

        named = self.parse_validated(
            [
                "--only-room",
                "The Hopkins Studio",
                "--max-action-minutes",
                "30",
            ]
        )
        self.assertEqual(named.only_room, "The Hopkins Studio")
        self.assert_rejected(
            ["--only-room", " room ", "--max-action-minutes", "30"]
        )

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

    def test_isolated_lifecycle_modes_are_scoped_and_mutually_exclusive(self):
        self.assert_rejected(["--extensions-only"])
        self.assert_rejected(
            ["--extensions-only", "--only-room", "B0.29"]
        )
        self.assert_rejected(
            [
                "--extensions-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
            ]
        )
        self.assert_rejected(
            [
                "--horizon-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
            ]
        )
        self.assert_rejected(
            [
                "--horizon-only",
                "--extensions-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
                "--target-time",
                "10:00",
            ]
        )

        extensions = self.parse_validated(
            [
                "--extensions-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
                "--max-action-minutes",
                "30",
            ]
        )
        self.assertTrue(extensions.extensions_only)
        self.assertEqual(extensions.max_action_minutes, 30)
        horizon = self.parse_validated(
            [
                "--horizon-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
                "--target-time",
                "10:00",
            ]
        )
        self.assertTrue(horizon.horizon_only)
        self.assert_rejected(
            [
                "--horizon-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "2",
                "--target-time",
                "10:00",
            ]
        )

    def test_delayed_scheduled_horizon_only_run_exits_without_fallthrough(self):
        with (
            mock.patch.object(book_week, "_scheduled_target_time", return_value=None),
            mock.patch.object(book_week, "_load_and_validate_runtime_settings") as load,
            mock.patch.object(book_week, "run_booking") as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = book_week.main(
                [
                    "--headless",
                    "--scheduled",
                    "--horizon-only",
                    "--only-room",
                    "B0.29",
                    "--max-actions",
                    "1",
                ]
            )

        self.assertEqual(result, 0)
        load.assert_not_called()
        run.assert_not_called()


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
            target_time="00:00",
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
        call_order = []

        def scan_agenda(*_args, **_kwargs):
            call_order.append("scan")
            return 0, []

        refresh_policy = mock.Mock(side_effect=_refresh_test_live_policy(call_order))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.multiple(
                book_week,
                refresh_live_room_policy=refresh_policy,
                scan_agenda=mock.Mock(side_effect=scan_agenda),
            ),
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
            mock.patch.object(
                book_week,
                "find_all_snipe_candidates_multi_day",
            ) as discover_snipes,
            mock.patch.object(book_week, "save_history") as save_history,
            mock.patch.object(book_week, "persist_storage_state"),
        ):
            result = book_week.run_booking(args, {}, book_week.PracticePlan())

        self.assertEqual(result, 0)
        refresh_policy.assert_called_once()
        self.assertEqual(call_order[:2], ["refresh", "scan"])
        self.assertEqual(extend.call_count, 1)
        discover_snipes.assert_not_called()
        self.assertEqual(save_history.call_args.args[0], 1)

    def test_extension_phase_runs_before_horizon_discovery(self):
        args = SimpleNamespace(
            headless=True,
            check_only=False,
            only_date=None,
            only_room=None,
            target_time="00:00",
            max_actions=2,
            max_action_minutes=None,
            horizon_only=False,
            extensions_only=False,
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
        order = []

        def process_extensions(*_args, **_kwargs):
            order.append("extensions")
            return 1, False

        def scan_agenda(*_args, **_kwargs):
            order.append("scan")
            return 0, []

        def discover_snipes(*_args, **_kwargs):
            order.append("snipes")
            return [], 0

        def open_overview(*_args, **_kwargs):
            order.append("overview")

        time_preferences = {
            "enabled": False,
            "start_hour": 7.0,
            "end_hour": 23.0,
            "strict_mode": False,
        }
        refresh_policy = mock.Mock(side_effect=_refresh_test_live_policy(order))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.multiple(
                book_week,
                refresh_live_room_policy=refresh_policy,
                scan_agenda=mock.Mock(side_effect=scan_agenda),
            ),
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(
                book_week,
                "open_practice_room_overview",
                side_effect=open_overview,
            ),
            mock.patch.object(book_week, "refresh_practice_room_overview"),
            mock.patch.object(book_week, "wait_until_target_time"),
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
            mock.patch.object(
                book_week,
                "process_pending_extensions",
                side_effect=process_extensions,
            ),
            mock.patch.object(
                book_week,
                "find_all_snipe_candidates_multi_day",
                side_effect=discover_snipes,
            ),
            mock.patch.object(book_week, "save_history"),
            mock.patch.object(book_week, "persist_storage_state"),
        ):
            result = book_week.run_booking(args, {}, book_week.PracticePlan())

        self.assertEqual(result, 0)
        refresh_policy.assert_called_once()
        self.assertEqual(
            order,
            ["refresh", "scan", "extensions", "overview", "snipes"],
        )

    def test_horizon_only_never_extends_refreshes_or_books_normally(self):
        args = SimpleNamespace(
            headless=True,
            check_only=False,
            only_date=None,
            only_room="B0.29",
            target_time="00:00",
            max_actions=1,
            max_action_minutes=120,
            horizon_only=True,
            extensions_only=False,
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
        target_date = datetime.now().date()
        candidate = {
            "room": "B0.29",
            "start_hour": 10.0,
            "days_ahead": 5,
            "target_date": target_date,
            "bookable_from": datetime.now(),
            "booking_minutes": 30,
        }
        call_order = []

        def scan_agenda(*_args, **_kwargs):
            call_order.append("scan")
            return 0, []

        refresh_policy = mock.Mock(side_effect=_refresh_test_live_policy(call_order))
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.multiple(
                book_week,
                refresh_live_room_policy=refresh_policy,
                scan_agenda=mock.Mock(side_effect=scan_agenda),
            ),
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(book_week, "open_practice_room_overview"),
            mock.patch.object(book_week, "refresh_practice_room_overview") as refresh,
            mock.patch.object(book_week, "wait_until_target_time") as target_wait,
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
            mock.patch.object(book_week, "process_pending_extensions") as extensions,
            mock.patch.object(
                book_week,
                "find_all_snipe_candidates_multi_day",
                return_value=([candidate], 0),
            ) as discover,
            mock.patch.object(
                book_week,
                "navigate_to_day",
                return_value=5,
            ) as navigate,
            mock.patch.object(
                book_week,
                "try_horizon_snipe",
                return_value=True,
            ) as horizon_snipe,
            mock.patch.object(book_week, "go_back") as go_back,
            mock.patch.object(book_week, "try_book_slot") as normal_booking,
            mock.patch.object(book_week, "save_history"),
            mock.patch.object(book_week, "persist_storage_state"),
        ):
            result = book_week.run_booking(args, {}, book_week.PracticePlan())

        self.assertEqual(result, 0)
        refresh_policy.assert_called_once()
        self.assertEqual(call_order[:2], ["refresh", "scan"])
        extensions.assert_not_called()
        discover.assert_called_once()
        horizon_snipe.assert_called_once()
        self.assertEqual(navigate.call_count, 1)
        go_back.assert_not_called()
        target_wait.assert_not_called()
        refresh.assert_not_called()
        normal_booking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
