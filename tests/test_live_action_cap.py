import contextlib
import io
import unittest
from datetime import date, datetime, time, timedelta, timezone
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
    room_names = ("B0.29", "B1.09")
    room_location_ids = {"B0.29": 96, "B1.09": 87}
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
        all_room_names=room_names,
        all_room_location_ids=room_location_ids,
        overview_url=book_week.canonical_overview_url(
            room_names,
            room_location_ids,
        ),
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

    def test_plan_only_accepts_read_scopes_and_rejects_mutation_or_timing_modes(self):
        plain = self.parse_validated(["--plan-only"])
        self.assertTrue(plain.plan_only)

        scoped = self.parse_validated(
            ["--plan-only", "--only-date", "2026-09-04", "--only-room", "B0.29"]
        )
        self.assertEqual(scoped.only_date, "2026-09-04")
        self.assertEqual(scoped.only_room, "B0.29")

        invalid_combinations = (
            ["--plan-only", "--check-only"],
            ["--plan-only", "--target-time", "10:00"],
            ["--plan-only", "--scheduled"],
            ["--plan-only", "--max-actions", "1"],
            [
                "--plan-only",
                "--only-room",
                "B0.29",
                "--max-action-minutes",
                "30",
            ],
            [
                "--plan-only",
                "--horizon-only",
                "--only-room",
                "B0.29",
                "--max-actions",
                "1",
                "--target-time",
                "10:00",
            ],
        )
        for argv in invalid_combinations:
            with self.subTest(argv=argv):
                self.assert_rejected(argv)


class PlanOnlyRuntimeIsolationTests(unittest.TestCase):
    def test_plan_only_publishes_after_fresh_reads_without_entering_mutation_paths(self):
        args = SimpleNamespace(
            headless=True,
            check_only=False,
            plan_only=True,
            only_date=None,
            only_room=None,
            target_time=None,
            scheduled=False,
            max_actions=None,
            max_action_minutes=None,
            horizon_only=False,
            extensions_only=False,
        )
        page = mock.MagicMock()
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
        daily_planning = book_week.DailyPlanningPreferences()

        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(book_week, "sync_playwright", return_value=playwright_context),
            mock.patch.object(
                book_week,
                "authenticated_runtime_context_options",
                return_value={},
            ),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.object(
                book_week,
                "refresh_live_room_policy",
                side_effect=_refresh_test_live_policy(),
            ) as refresh_policy,
            mock.patch.object(book_week, "scan_agenda", return_value=(0, [])) as scan,
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(book_week, "load_ignored_events", return_value=set()),
            mock.patch.object(book_week, "load_disabled_dates", return_value=set()),
            mock.patch.object(
                book_week,
                "load_time_preferences",
                return_value=time_preferences,
            ),
            mock.patch.object(
                book_week,
                "load_booking_strategy",
                return_value={
                    "reverse_date_order": False,
                    "daily_planning": daily_planning,
                },
            ),
            mock.patch.object(book_week, "generate_read_only_booking_plan") as generate,
            mock.patch.object(book_week, "process_pending_extensions") as extensions,
            mock.patch.object(book_week, "find_all_snipe_candidates_multi_day") as snipes,
            mock.patch.object(book_week, "try_horizon_snipe") as horizon_save,
            mock.patch.object(book_week, "try_book_slot") as ordinary_save,
            mock.patch.object(book_week, "save_history") as history,
            mock.patch.object(book_week, "persist_storage_state") as persist,
        ):
            result = book_week.run_booking(
                args,
                {},
                book_week.PracticePlan(),
                room_preferences=mock.Mock(),
            )

        self.assertEqual(result, 0)
        refresh_policy.assert_called_once()
        scan.assert_called_once()
        generate.assert_called_once()
        persist.assert_called_once_with(context)
        context.close.assert_called_once()
        browser.close.assert_called_once()
        extensions.assert_not_called()
        snipes.assert_not_called()
        horizon_save.assert_not_called()
        ordinary_save.assert_not_called()
        history.assert_not_called()

    def test_generate_read_only_plan_publishes_the_fresh_day_decision(self):
        today = date(2026, 8, 31)
        frozen_now = datetime(2026, 8, 31, 10, 0)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        opportunity = book_week.BookingOpportunity(
            room="B0.29",
            target_date=today,
            start_minutes=12 * 60,
            end_minutes=14 * 60,
            unlock_at=datetime(2026, 8, 31, 9, 30),
            room_priority=0,
            initial_minutes=30,
            potential_minutes=120,
            preferred_minutes=120,
            soft_preferred_minutes=120,
            peak_minutes=120,
            peak_window_start_minutes=9 * 60,
            peak_window_end_minutes=16 * 60,
            source_gap_start_minutes=12 * 60,
            source_gap_end_minutes=14 * 60,
        )
        tracker = mock.MagicMock()
        tracker.reservation_ranges = {}
        tracker.bookings = []
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_peak_used_for_day.return_value = 0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        policy = mock.Mock(
            observed_at=datetime(2026, 8, 31, 9, 59, tzinfo=timezone.utc)
        )
        args = SimpleNamespace(only_date=None, only_room=None)
        published_snapshot = object()

        with (
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(book_week, "booking_window_dates", return_value=(today,)),
            mock.patch.object(book_week, "ALLOW_FRAGMENTED_SESSIONS", True),
            mock.patch.object(book_week, "open_practice_room_overview") as open_grid,
            mock.patch.object(book_week, "wait_for_practice_room_grid") as wait_grid,
            mock.patch.object(book_week, "get_available_slots", return_value=[]) as slots,
            mock.patch.object(
                book_week,
                "build_day_booking_opportunities",
                return_value=[opportunity],
            ),
            mock.patch.object(
                book_week,
                "publish_booking_plan",
                return_value=published_snapshot,
            ) as publish,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = book_week.generate_read_only_booking_plan(
                mock.MagicMock(),
                policy,
                {"booking_strategy": {}},
                book_week.PracticePlan(enabled=True, default_hours=2.0),
                tracker,
                {"enabled": False, "strict_mode": False},
                book_week.DailyPlanningPreferences(),
                set(),
                args,
                today=today,
            )

        self.assertIs(result, published_snapshot)
        open_grid.assert_called_once()
        wait_grid.assert_called_once()
        slots.assert_called_once()
        publish.assert_called_once()
        day_plans, published_policy, published_settings = publish.call_args.args
        self.assertIs(published_policy, policy)
        self.assertEqual(published_settings, {"booking_strategy": {}})
        self.assertEqual(len(day_plans), 1)
        self.assertEqual(day_plans[0].primary.room, "B0.29")
        self.assertEqual(day_plans[0].primary.start_time, "12:00")
        self.assertEqual(publish.call_args.kwargs["status"], "active")
        self.assertIn("Next: 2026-08-31 12:00-14:00", publish.call_args.kwargs["summary"])

    def test_disabled_daily_planning_preview_uses_legacy_gap_start_order(self):
        today = date(2026, 8, 31)
        frozen_now = datetime(2026, 8, 31, 13, 0)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        def opportunity(start, end, preferred):
            return book_week.BookingOpportunity(
                room="B0.29",
                target_date=today,
                start_minutes=start,
                end_minutes=end,
                unlock_at=datetime(2026, 8, 31, 9, 30),
                room_priority=0,
                initial_minutes=30,
                potential_minutes=end - start,
                preferred_minutes=preferred,
                soft_preferred_minutes=preferred,
                peak_minutes=end - start,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=9 * 60,
                source_gap_end_minutes=14 * 60,
            )

        early = opportunity(9 * 60, 11 * 60, 0)
        interior_afternoon = opportunity(12 * 60, 14 * 60, 120)
        tracker = mock.MagicMock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_peak_used_for_day.return_value = 0
        tracker.get_remaining_quota_hours.return_value = 28.0
        tracker.get_remaining_peak_minutes.return_value = 120
        policy = mock.Mock(
            observed_at=datetime(2026, 8, 31, 12, 59, tzinfo=timezone.utc)
        )
        args = SimpleNamespace(only_date=None, only_room=None)
        disabled_strategy = book_week.DailyPlanningPreferences(
            enabled=False,
            desired_peak_block_minutes=60,
        )

        with (
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(book_week, "booking_window_dates", return_value=(today,)),
            mock.patch.object(book_week, "ALLOW_FRAGMENTED_SESSIONS", True),
            mock.patch.object(book_week, "open_practice_room_overview"),
            mock.patch.object(book_week, "wait_for_practice_room_grid"),
            mock.patch.object(book_week, "get_available_slots", return_value=[]),
            mock.patch.object(
                book_week,
                "build_day_booking_opportunities",
                return_value=[early, interior_afternoon],
            ) as build,
            mock.patch.object(book_week, "publish_booking_plan") as publish,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            book_week.generate_read_only_booking_plan(
                mock.MagicMock(),
                policy,
                {},
                book_week.PracticePlan(enabled=True, default_hours=2.0),
                tracker,
                {
                    "enabled": True,
                    "strict_mode": False,
                    "start_hour": 12.0,
                    "end_hour": 16.0,
                },
                disabled_strategy,
                set(),
                args,
                today=today,
            )

        day = publish.call_args.args[0][0]
        self.assertEqual(day.primary.start_time, "09:00")
        self.assertIn("foresight is disabled", day.reason)
        self.assertEqual(
            build.call_args.args[4].desired_peak_block_minutes,
            int(book_week.MAX_BOOKING_HOURS * 60),
        )

    def test_no_fragment_plan_still_shows_pending_extension_progress(self):
        today = date(2026, 8, 30)
        target_date = date(2026, 9, 4)
        frozen_now = datetime(2026, 8, 30, 12, 31)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

        tracker = mock.MagicMock()
        tracker.get_hours_for_day.return_value = 0.5
        tracker.get_peak_used_for_day.return_value = 30
        tracker.get_remaining_quota_hours.return_value = 27.5
        tracker.get_remaining_peak_minutes.return_value = 90
        booking = {
            "date": target_date.isoformat(),
            "room": "B0.29",
            "startTime": "12:00",
            "endTime": "12:30",
            "target_end": "14:00",
        }
        planning_context = {
            "extension_target_by_date": {target_date.isoformat(): 90},
            "extension_peak_by_date": {target_date.isoformat(): 90},
            "extension_bookings": (booking,),
        }
        policy = mock.Mock(
            observed_at=datetime(2026, 8, 30, 11, 30, tzinfo=timezone.utc)
        )
        args = SimpleNamespace(only_date=None, only_room=None)

        with (
            mock.patch.object(book_week, "datetime", FrozenDateTime),
            mock.patch.object(
                book_week,
                "booking_window_dates",
                return_value=(target_date,),
            ),
            mock.patch.multiple(
                book_week,
                PRIORITY_ROOMS=["B0.29"],
                ROOM_HORIZON_MINUTES={"B0.29": 5 * 24 * 60},
                MINIMUM_BLOCK_MINUTES=30,
            ),
            mock.patch.object(book_week, "open_practice_room_overview"),
            mock.patch.object(
                book_week,
                "fragmentation_allows_new_booking",
                return_value=(False, "Fragments are disabled"),
            ),
            mock.patch.object(book_week, "wait_for_practice_room_grid") as wait_grid,
            mock.patch.object(book_week, "get_available_slots") as slots,
            mock.patch.object(book_week, "publish_booking_plan") as publish,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            book_week.generate_read_only_booking_plan(
                mock.MagicMock(),
                policy,
                {},
                book_week.PracticePlan(enabled=True, default_hours=2.0),
                tracker,
                {"enabled": False, "strict_mode": False},
                book_week.DailyPlanningPreferences(),
                set(),
                args,
                today=today,
                planning_context=planning_context,
            )

        day = publish.call_args.args[0][0]
        self.assertEqual(day.status, "in_progress")
        self.assertEqual(day.primary.confirmed_minutes, 30)
        self.assertEqual(day.primary.potential_minutes, 120)
        wait_grid.assert_not_called()
        slots.assert_not_called()

    def test_horizon_display_reserves_one_aggregate_weekly_allowance(self):
        now = datetime(2026, 8, 30, 10, 0)
        dates = (date(2026, 9, 4), date(2026, 9, 5))

        def opportunity(target_date, room, priority):
            weekday_peak = 120 if target_date.weekday() < 5 else 0
            return book_week.BookingOpportunity(
                room=room,
                target_date=target_date,
                start_minutes=12 * 60,
                end_minutes=14 * 60,
                unlock_at=now,
                room_priority=priority,
                initial_minutes=30,
                potential_minutes=120,
                preferred_minutes=weekday_peak,
                soft_preferred_minutes=weekday_peak,
                peak_minutes=weekday_peak,
                peak_window_start_minutes=9 * 60,
                peak_window_end_minutes=16 * 60,
                source_gap_start_minutes=12 * 60,
                source_gap_end_minutes=14 * 60,
            )

        opportunities_by_date = {
            dates[0].isoformat(): (opportunity(dates[0], "First", 0),),
            dates[1].isoformat(): (opportunity(dates[1], "Second", 1),),
        }
        planning_context = {
            "extension_target_by_date": {},
            "extension_peak_by_date": {},
            "extension_bookings": (),
            "display_days": {},
        }
        tracker = mock.MagicMock()
        tracker.get_hours_for_day.return_value = 0.0
        tracker.get_remaining_quota_hours.return_value = 2.0
        tracker.get_remaining_peak_minutes.return_value = 120
        tracker.get_peak_used_for_day.return_value = 0

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["First", "Second"],
            ALLOW_FRAGMENTED_SESSIONS=True,
        ):
            days = book_week.build_horizon_display_days(
                opportunities_by_date,
                planning_context,
                tracker,
                book_week.PracticePlan(),
                book_week.DailyPlanningPreferences(),
                now=now,
            )

        selected = [
            candidate
            for day in days
            for candidate in (
                (() if day.primary is None else (day.primary,)) + day.additional
            )
        ]
        self.assertEqual(sum(item.potential_minutes for item in selected), 120)
        self.assertIsNotNone(days[0].primary)
        self.assertIsNone(days[1].primary)

    def test_verified_horizon_create_becomes_explicit_extension_progress(self):
        target_date = date(2026, 9, 4)
        primary = book_week.PlanCandidate(
            room="B0.29",
            date=target_date.isoformat(),
            start_time="12:00",
            end_time="14:00",
            unlock_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
            initial_minutes=30,
            potential_minutes=120,
            confirmed_minutes=0,
            state="ready",
            reason="Preferred afternoon block.",
        )
        day = book_week.DayPlan(
            date=target_date.isoformat(),
            target_minutes=120,
            existing_minutes=0,
            peak_used_minutes=0,
            peak_limit_minutes=120,
            status="planned",
            primary=primary,
            additional=(),
            backups=(),
            held_peak_minutes=0,
            reason="Ready.",
        )
        context = {
            "display_days": {day.date: day},
            "held_peak_by_date": {},
            "held_target_by_date": {},
        }
        tracker = mock.Mock()
        tracker.get_hours_for_day.return_value = 0.5
        tracker.get_peak_used_for_day.return_value = 30
        candidate_data = {
            "target_date": target_date,
            "room": "B0.29",
            "start_hour": 12.0,
        }

        with mock.patch.object(book_week, "publish_planning_context") as publish:
            book_week.record_plan_create_progress(
                context,
                mock.Mock(),
                {},
                tracker,
                candidate_data,
                30,
                now=datetime(2026, 8, 30, 12, 1),
            )

        updated = context["display_days"][target_date.isoformat()]
        self.assertEqual(updated.status, "in_progress")
        self.assertEqual(updated.primary.confirmed_minutes, 30)
        self.assertEqual(updated.existing_minutes, 30)
        self.assertEqual(updated.held_peak_minutes, 90)
        self.assertEqual(context["extension_target_by_date"][updated.date], 90)
        self.assertEqual(context["extension_peak_by_date"][updated.date], 90)
        publish.assert_called_once()

    def test_plan_publication_fingerprints_fresh_extension_state_only(self):
        old_settings = {
            "booking_strategy": {"source": "used-for-this-plan"},
            "extendable_bookings": [],
        }
        latest_settings = {
            "booking_strategy": {"source": "concurrent-gui-change"},
            "extendable_bookings": [{"eventId": 42}],
        }
        policy = mock.Mock(
            observed_at=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
        )

        with (
            mock.patch.object(
                book_week,
                "load_settings_document",
                return_value=latest_settings,
            ),
            mock.patch.object(
                book_week,
                "booking_plan_fingerprint",
                return_value="sha256:test",
            ) as fingerprint,
            mock.patch.object(book_week, "write_booking_plan") as write,
        ):
            snapshot = book_week.publish_booking_plan(
                (),
                policy,
                old_settings,
                summary="No current opportunity.",
                status="idle",
                now=datetime(2026, 8, 30, 10, 1),
            )

        fingerprint_settings = fingerprint.call_args.args[0]
        self.assertEqual(
            fingerprint_settings["booking_strategy"],
            old_settings["booking_strategy"],
        )
        self.assertEqual(
            fingerprint_settings["extendable_bookings"],
            latest_settings["extendable_bookings"],
        )
        self.assertEqual(snapshot.strategy_fingerprint, "sha256:test")
        write.assert_called_once()

    def test_pending_extension_reappears_as_partial_plan_progress(self):
        target_date = date(2026, 9, 4)
        day = book_week.DayPlan(
            date=target_date.isoformat(),
            target_minutes=120,
            existing_minutes=30,
            peak_used_minutes=30,
            peak_limit_minutes=120,
            status="unplanned",
            primary=None,
            additional=(),
            backups=(),
            held_peak_minutes=0,
            reason="No new opportunity.",
        )
        booking = {
            "date": target_date.isoformat(),
            "room": "B0.29",
            "startTime": "12:00",
            "endTime": "12:30",
            "target_end": "14:00",
        }
        context = {
            "extension_bookings": (booking,),
            "extension_peak_by_date": {target_date.isoformat(): 90},
        }

        with mock.patch.multiple(
            book_week,
            PRIORITY_ROOMS=["B0.29"],
            ROOM_HORIZON_MINUTES={"B0.29": 5 * 24 * 60},
            MINIMUM_BLOCK_MINUTES=30,
        ):
            updated = book_week.attach_extension_progress(
                day,
                context,
                now=datetime(2026, 8, 30, 12, 31),
            )

        self.assertEqual(updated.status, "in_progress")
        self.assertEqual(updated.primary.confirmed_minutes, 30)
        self.assertEqual(updated.primary.potential_minutes, 120)
        self.assertEqual(updated.held_peak_minutes, 90)


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
