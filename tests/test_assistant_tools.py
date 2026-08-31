import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agenda_snapshot import publish_agenda_snapshot
from app_settings import SettingsError, atomic_write_json, load_settings
from assistant_context import ContextPaths
from assistant_tools import (
    AssistantToolError,
    BookerToolSurface,
    authorize_tool_mutation,
    dynamic_tool_specs,
)
from booking_blackouts import load_rebooking_blackouts
from booking_plan import BookingPlanError
from mutation_receipts import SCHEMA_VERSION as RECEIPT_SCHEMA_VERSION


class AssistantToolSurfaceTests(unittest.TestCase):
    """Host-contract tests; natural-language interpretation belongs to Terra evals."""

    CANCELLATION_FIELDS = (
        "event_id",
        "date",
        "start_time",
        "end_time",
        "room",
        "match_token",
    )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths = ContextPaths(
            settings=self.root / "settings.json",
            agenda=self.root / "agenda.json",
            plan=self.root / "plan.json",
            catalog=self.root / "catalog.json",
            receipts=self.root / "receipts.json",
            history=self.root / "history.json",
            session=self.root / "state.json",
            auth=self.root / "auth.json",
            wake=self.root / "wake.json",
        )
        atomic_write_json(
            self.paths.settings,
            {
                "unrelated_user_value": {"preserve": True},
                "disabled_dates": ["2026-09-10"],
                "practice_plan": {
                    "enabled": True,
                    "default_hours": 2.0,
                    "date_overrides": {"2026-09-02": 1.0},
                },
            },
        )
        atomic_write_json(
            self.paths.receipts,
            {"schema_version": RECEIPT_SCHEMA_VERSION, "receipts": {}},
        )
        atomic_write_json(self.paths.history, {"runs": []})
        self.commands = []
        self.last_observed = datetime.now(timezone.utc)

        def runner(flags, **kwargs):
            self.commands.append(list(flags))
            kwargs["progress"]("Fixture", "Completed without live Asimut access")
            return {
                "exit_code": 0,
                "completed": True,
                "output": ["History saved: 1 bookings, 2 events"],
            }

        self.surface = BookerToolSurface(paths=self.paths, command_runner=runner)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _reservation(
        event_id,
        booking_date,
        start="16:00",
        end="17:00",
        room=None,
        *,
        is_reservation=True,
        title=None,
    ):
        return {
            "date": str(booking_date),
            "startTime": start,
            "endTime": end,
            "title": title or ("Reservation" if is_reservation else "Class"),
            "isReservation": is_reservation,
            "room": room or f"B0.{event_id % 100:02d}",
            "eventId": event_id,
        }

    def _publish(self, events, *, observed_at=None):
        observed_at = observed_at or datetime.now(timezone.utc)
        event_dates = sorted({date.fromisoformat(item["date"]) for item in events})
        if event_dates:
            dates = [
                event_dates[0] + timedelta(days=offset)
                for offset in range((event_dates[-1] - event_dates[0]).days + 1)
            ]
        else:
            dates = [datetime.now(timezone.utc).date()]
        publish_agenda_snapshot(
            events,
            dates,
            observed_at=observed_at,
            path=self.paths.agenda,
        )
        self.last_observed = observed_at
        return observed_at

    def _find_by_ids(self, *event_ids):
        return self.surface.dispatch(
            "find_reservations",
            {"event_ids": list(event_ids)},
        )

    def _target(self, match):
        return {key: match[key] for key in self.CANCELLATION_FIELDS}

    def _bulk_runner(self, targets, exit_codes):
        commands = []
        remaining = {item["event_id"]: dict(item) for item in targets}
        observed = self.last_observed
        codes = iter(exit_codes)

        def runner(flags, **kwargs):
            nonlocal observed
            commands.append(list(flags))
            kwargs["progress"]("Fixture", "Synthetic cancellation")
            exit_code = next(codes)
            if exit_code == 0:
                event_id = int(flags[flags.index("--cancel-event-id") + 1])
                remaining.pop(event_id, None)
                observed += timedelta(seconds=1)
                self._publish(
                    [
                        self._reservation(
                            item["event_id"],
                            item["date"],
                            item["start_time"],
                            item["end_time"],
                            item["room"],
                        )
                        for item in remaining.values()
                    ],
                    observed_at=observed,
                )
            return {
                "exit_code": exit_code,
                "completed": exit_code == 0,
                "output": [f"Synthetic exit {exit_code}"],
            }

        return runner, commands

    def test_specs_are_typed_and_expose_only_current_model_tools(self):
        specs = {item["name"]: item for item in dynamic_tool_specs()}
        self.assertEqual(
            set(specs),
            {
                "get_booker_context",
                "refresh_booker_data",
                "find_reservations",
                "set_future_practice_plan",
                "update_booker_preferences",
                "run_booker",
                "cancel_reservations",
                "reopen_booking_window",
            },
        )
        self.assertTrue(all(item["type"] == "function" for item in specs.values()))

        find_schema = specs["find_reservations"]["inputSchema"]
        self.assertEqual(find_schema["properties"]["event_ids"]["maxItems"], 64)
        self.assertEqual(find_schema["properties"]["scope"]["enum"], ["upcoming"])
        self.assertEqual(find_schema["properties"]["days"]["maximum"], 31)
        self.assertTrue(any("oneOf" in item for item in find_schema["allOf"]))

        for name in {
            "set_future_practice_plan",
            "update_booker_preferences",
            "run_booker",
            "cancel_reservations",
            "reopen_booking_window",
        }:
            self.assertNotIn("request_quote", specs[name]["inputSchema"]["properties"])

        cancel_schema = specs["cancel_reservations"]["inputSchema"]
        self.assertEqual(cancel_schema["required"], ["selection_id"])
        self.assertEqual(set(cancel_schema["properties"]), {"selection_id"})
        self.assertEqual(
            cancel_schema["properties"]["selection_id"]["pattern"],
            r"^selection:[0-9a-f]{64}$",
        )

        reopen_schema = specs["reopen_booking_window"]["inputSchema"]
        self.assertEqual(
            set(reopen_schema["required"]),
            {"date", "start_time", "end_time"},
        )

    def test_current_mutation_context_is_fresh_and_file_backed(self):
        context = self.surface.current_mutation_context()
        self.assertEqual(set(context["sections"]), {"mutations"})
        self.assertEqual(context["sections"]["mutations"]["pending"], [])
        atomic_write_json(
            self.paths.receipts,
            {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "receipts": {
                    "58c047cf-eff8-44ab-9e4d-1b1d115cfba0": {
                        "id": "58c047cf-eff8-44ab-9e4d-1b1d115cfba0",
                        "kind": "cancel",
                        "status": "pending",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "room": "B0.29",
                        "date": "2026-09-01",
                        "start": "16:00",
                        "end": "17:00",
                        "event_url": (
                            "https://rwcmd.asimut.net/arrangement?eventId=4242"
                        ),
                    }
                },
            },
        )
        self.assertEqual(
            len(
                self.surface.current_mutation_context()["sections"]["mutations"][
                    "pending"
                ]
            ),
            1,
        )

    def test_mutation_authorization_requires_the_complete_normalized_message(self):
        request = "Please cancel all my bookings tomorrow afternoon."
        self.assertEqual(
            authorize_tool_mutation(
                "cancel_reservations",
                {"request_quote": "  PLEASE   cancel all my bookings tomorrow afternoon. "},
                request,
            ),
            "PLEASE   cancel all my bookings tomorrow afternoon.",
        )
        for quote in ("", "cancel all my bookings", "tomorrow afternoon"):
            with self.subTest(quote=quote), self.assertRaises(AssistantToolError):
                authorize_tool_mutation(
                    "cancel_reservations",
                    {"request_quote": quote},
                    request,
                )
        with self.assertRaises(AssistantToolError):
            authorize_tool_mutation(
                "cancel_reservations",
                {"request_quote": request},
                "",
            )

    def test_host_does_not_reinterpret_a_complete_message_semantically(self):
        # Quoted/meta/negated safety is a model-level eval. The host's job is
        # provenance plus typed domain/fresh-identity validation.
        request = 'Summarize this example: "cancel my booking tomorrow".'
        self.assertEqual(
            authorize_tool_mutation(
                "cancel_reservations",
                {"request_quote": request},
                request,
            ),
            request,
        )

    def test_high_level_future_plan_saves_complete_dated_targets(self):
        start = date.today() + timedelta(days=1)
        end = start + timedelta(days=6)
        request = (
            "This week is busy; give me two hours on weekdays and three hours "
            "over the weekend."
        )
        targets = []
        cursor = start
        while cursor <= end:
            targets.append(
                {
                    "date": cursor.isoformat(),
                    "hours": 3.0 if cursor.weekday() >= 5 else 2.0,
                }
            )
            cursor += timedelta(days=1)
        result = self.surface.dispatch(
            "set_future_practice_plan",
            {
                "request_quote": request,
                "title": "Busy week",
                "intent_summary": "Two weekday hours and three weekend hours.",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily_targets": targets,
                "replace_overlapping": True,
            },
            user_request=request,
        )
        settings = load_settings(self.paths.settings)
        self.assertEqual(result["target_semantics"], "Each value is total desired practice on that date.")
        self.assertTrue(result["session_planning"]["split_larger_targets"])
        self.assertEqual(
            settings["unrelated_user_value"],
            {"preserve": True},
        )
        self.assertEqual(len(settings["assistant_practice_intentions"]), 1)
        for target in targets:
            self.assertEqual(
                settings["practice_plan"]["date_overrides"][target["date"]],
                target["hours"],
            )

    def test_future_plan_rejects_incomplete_or_out_of_range_targets(self):
        start = date.today() + timedelta(days=1)
        end = start + timedelta(days=1)
        request = "Plan twelve and a half hours tomorrow."
        base = {
            "request_quote": request,
            "title": "Invalid",
            "intent_summary": "Invalid target",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily_targets": [{"date": start.isoformat(), "hours": 12.5}],
            "replace_overlapping": True,
        }
        with self.assertRaises(AssistantToolError):
            self.surface.dispatch(
                "set_future_practice_plan",
                base,
                user_request=request,
            )
        self.assertNotIn(
            "assistant_practice_intentions",
            load_settings(self.paths.settings),
        )

    def test_preference_patch_is_atomic_scoped_and_preserves_unrelated_settings(self):
        request = "Keep Tuesday free and make Wednesday three hours."
        result = self.surface.dispatch(
            "update_booker_preferences",
            {
                "request_quote": request,
                "booking_days": [{"date": "2026-09-01", "enabled": False}],
                "practice_plan": {
                    "date_overrides": [
                        {"date": "2026-09-02", "hours": 3.0},
                    ]
                },
            },
            user_request=request,
        )
        settings = load_settings(self.paths.settings)
        self.assertEqual(settings["unrelated_user_value"], {"preserve": True})
        self.assertIn("2026-09-01", settings["disabled_dates"])
        self.assertEqual(settings["practice_plan"]["date_overrides"]["2026-09-02"], 3.0)
        self.assertEqual(result["changed"]["booking_days"]["2026-09-01"], False)

        before = self.paths.settings.read_bytes()
        with self.assertRaises(AssistantToolError):
            self.surface.dispatch(
                "update_booker_preferences",
                {
                    "request_quote": request,
                    "practice_plan": {
                        "date_overrides": [
                            {"date": "2026-09-03", "hours": 2.0},
                            {"date": "2026-09-03", "hours": 3.0},
                        ]
                    },
                },
                user_request=request,
            )
        self.assertEqual(self.paths.settings.read_bytes(), before)

    def test_relative_practice_adjustment_uses_saved_numeric_baseline(self):
        request = "Practice one hour more on September second."
        result = self.surface.dispatch(
            "update_booker_preferences",
            {
                "request_quote": request,
                "practice_plan": {
                    "date_adjustments": [
                        {"date": "2026-09-02", "delta_hours": 1.0},
                    ]
                },
            },
            user_request=request,
        )
        self.assertEqual(
            result["changed"]["practice_plan"]["date_overrides"]["2026-09-02"],
            2.0,
        )

    def test_run_booker_enforces_one_action_and_exact_duration_scope(self):
        request = "Try the next best planned booking now."
        result = self.surface.dispatch(
            "run_booker",
            {"request_quote": request, "max_actions": 1},
            user_request=request,
        )
        self.assertEqual(result["verified_actions"], 1)
        self.assertEqual(self.commands[-1], ["--headless", "--max-actions", "1"])

        with self.assertRaisesRegex(AssistantToolError, "exactly one"):
            self.surface.dispatch(
                "run_booker",
                {"request_quote": request, "max_actions": 2},
                user_request=request,
            )
        with self.assertRaisesRegex(AssistantToolError, "requires both"):
            self.surface.dispatch(
                "run_booker",
                {
                    "request_quote": request,
                    "max_actions": 1,
                    "max_action_minutes": 60,
                },
                user_request=request,
            )
        with self.assertRaisesRegex(AssistantToolError, "30-120"):
            self.surface.dispatch(
                "run_booker",
                {
                    "request_quote": request,
                    "max_actions": 1,
                    "only_date": "2026-09-01",
                    "only_room": "B0.29",
                    "max_action_minutes": 135,
                },
                user_request=request,
            )

    def test_find_by_date_uses_exact_start_and_keeps_overlaps_separate(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(101, booking_date, "16:00", "17:00", "B0.29"),
                self._reservation(102, booking_date, "15:30", "16:30", "B0.30"),
                self._reservation(
                    103,
                    booking_date,
                    "16:00",
                    "17:00",
                    "B1.09",
                    is_reservation=False,
                ),
            ]
        )
        result = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "start_time": "16:00"},
        )
        self.assertEqual([item["event_id"] for item in result["matches"]], [101])
        self.assertEqual([item["event_id"] for item in result["overlaps"]], [102])
        self.assertRegex(result["selection_id"], r"^selection:[0-9a-f]{64}$")

    def test_named_period_is_half_open_and_carries_dated_window(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(201, booking_date, "11:30", "12:15"),
                self._reservation(202, booking_date, "17:45", "18:15"),
                self._reservation(203, booking_date, "11:00", "12:00"),
                self._reservation(204, booking_date, "18:00", "19:00"),
                self._reservation(
                    205,
                    booking_date,
                    "14:00",
                    "15:00",
                    is_reservation=False,
                ),
            ]
        )
        result = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        self.assertEqual([item["event_id"] for item in result["matches"]], [201, 202])
        self.assertEqual(
            result["interpreted_window"],
            {
                "name": "afternoon",
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "18:00",
                "selection": "reservation_interval_overlap",
            },
        )

    def test_find_event_ids_preserves_terra_order_and_rejects_nonreservations(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(301, booking_date, "14:00", "15:00"),
                self._reservation(302, booking_date, "15:00", "16:00"),
                self._reservation(
                    303,
                    booking_date,
                    "16:00",
                    "17:00",
                    is_reservation=False,
                ),
            ]
        )
        result = self._find_by_ids(302, 301)
        self.assertEqual([item["event_id"] for item in result["matches"]], [302, 301])
        self.assertEqual(
            result["interpreted_scope"]["event_ids"],
            [302, 301],
        )
        self.assertRegex(result["selection_id"], r"^selection:[0-9a-f]{64}$")
        for invalid in ([303], [999], [301, 301], [0], [True]):
            with self.subTest(invalid=invalid), self.assertRaises(AssistantToolError):
                self.surface.dispatch("find_reservations", {"event_ids": invalid})

    def test_find_event_ids_accepts_64_and_rejects_65(self):
        booking_date = date.today() + timedelta(days=1)
        event_ids = list(range(1000, 1065))
        self._publish(
            [self._reservation(event_id, booking_date) for event_id in event_ids]
        )
        result = self.surface.dispatch(
            "find_reservations",
            {"event_ids": event_ids[:64]},
        )
        self.assertEqual(len(result["matches"]), 64)
        with self.assertRaisesRegex(AssistantToolError, "1-64"):
            self.surface.dispatch(
                "find_reservations",
                {"event_ids": event_ids},
            )

    def test_upcoming_scope_returns_all_future_or_a_rolling_day_horizon(self):
        today = date.today()
        self._publish(
            [
                self._reservation(401, today - timedelta(days=1), "14:00", "15:00"),
                self._reservation(402, today + timedelta(days=1), "14:00", "15:00"),
                self._reservation(403, today + timedelta(days=6), "14:00", "15:00"),
                self._reservation(404, today + timedelta(days=8), "14:00", "15:00"),
                self._reservation(
                    405,
                    today + timedelta(days=2),
                    "14:00",
                    "15:00",
                    is_reservation=False,
                ),
            ]
        )
        all_upcoming = self.surface.dispatch(
            "find_reservations",
            {"scope": "upcoming"},
        )
        self.assertEqual(
            [item["event_id"] for item in all_upcoming["matches"]],
            [402, 403, 404],
        )
        self.assertIsNone(all_upcoming["interpreted_scope"]["days"])

        next_week = self.surface.dispatch(
            "find_reservations",
            {"scope": "upcoming", "days": 7},
        )
        self.assertEqual(
            [item["event_id"] for item in next_week["matches"]],
            [402, 403],
        )
        self.assertEqual(next_week["interpreted_scope"]["days"], 7)
        self.assertRegex(next_week["selection_id"], r"^selection:[0-9a-f]{64}$")

    def test_find_modes_and_upcoming_filters_fail_closed(self):
        booking_date = (date.today() + timedelta(days=1)).isoformat()
        invalid_arguments = (
            {},
            {"date": booking_date, "scope": "upcoming"},
            {"date": booking_date, "days": 7},
            {"scope": "future"},
            {"scope": "upcoming", "days": 0},
            {"scope": "upcoming", "days": 32},
            {"scope": "upcoming", "days": True},
            {"scope": "upcoming", "room": "B0.29"},
            {"event_ids": [1], "date": booking_date},
            {"event_ids": [1], "days": 7},
            {
                "date": booking_date,
                "time_period": "afternoon",
                "start_time": "14:00",
            },
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(AssistantToolError):
                self.surface.dispatch("find_reservations", arguments)

    def test_stale_agenda_returns_no_cancellation_selection(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(501, booking_date)],
            observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        result = self._find_by_ids(501)
        self.assertFalse(result["fresh"])
        self.assertTrue(result["refresh_required"])
        self.assertEqual(result["matches"], [])
        self.assertIsNone(result["selection_id"])

    def test_exact_date_outside_snapshot_coverage_requires_refresh(self):
        covered_date = date.today() + timedelta(days=1)
        missing_date = covered_date + timedelta(days=1)
        self._publish([self._reservation(511, covered_date)])
        result = self.surface.dispatch(
            "find_reservations",
            {"date": missing_date.isoformat(), "time_period": "afternoon"},
        )
        self.assertFalse(result["fresh"])
        self.assertTrue(result["refresh_required"])
        self.assertTrue(result["coverage_required"])
        self.assertIn(missing_date.isoformat(), result["reason"])
        self.assertEqual(result["matches"], [])
        self.assertIsNone(result["selection_id"])
        self.assertEqual(result["interpreted_window"]["date"], missing_date.isoformat())

    def test_rolling_scope_requires_complete_calendar_date_coverage(self):
        today = date.today()
        # Two marker classes make the published coverage contiguous only through
        # day six; a rolling seven-day interval also touches calendar day seven.
        self._publish(
            [
                self._reservation(
                    521,
                    today,
                    "14:00",
                    "15:00",
                    is_reservation=False,
                ),
                self._reservation(
                    522,
                    today + timedelta(days=6),
                    "14:00",
                    "15:00",
                    is_reservation=False,
                ),
            ]
        )
        result = self.surface.dispatch(
            "find_reservations",
            {"scope": "upcoming", "days": 7},
        )
        self.assertFalse(result["fresh"])
        self.assertTrue(result["refresh_required"])
        self.assertTrue(result["coverage_required"])
        self.assertIn((today + timedelta(days=7)).isoformat(), result["reason"])
        self.assertEqual(result["matches"], [])
        self.assertIsNone(result["selection_id"])

    def test_rolling_scope_excludes_an_event_starting_at_midnight_boundary(self):
        london = ZoneInfo("Europe/London")
        frozen_now = datetime(2026, 9, 1, 0, 0, tzinfo=london)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return frozen_now.replace(tzinfo=None)
                return frozen_now.astimezone(tz)

        self._publish(
            [
                self._reservation(531, "2026-09-01", "00:00", "00:30"),
                self._reservation(532, "2026-09-02", "00:00", "00:30"),
            ]
        )
        with patch("assistant_tools.datetime", FrozenDateTime):
            result = self.surface.dispatch(
                "find_reservations",
                {"scope": "upcoming", "days": 1},
            )
        self.assertTrue(result["fresh"])
        self.assertEqual([item["event_id"] for item in result["matches"]], [531])
        self.assertEqual(result["interpreted_scope"]["start"], frozen_now.isoformat())
        self.assertEqual(
            result["interpreted_scope"]["end"],
            (frozen_now + timedelta(days=1)).isoformat(),
        )

    def test_date_and_upcoming_selection_reject_duplicate_event_ids_and_tokens(self):
        booking_date = date.today() + timedelta(days=1)
        duplicate_id_events = [
            self._reservation(541, booking_date, "14:00", "15:00", "B0.29"),
            self._reservation(541, booking_date, "15:00", "16:00", "B0.30"),
        ]
        for arguments in (
            {"date": booking_date.isoformat()},
            {"scope": "upcoming"},
        ):
            with self.subTest(kind="event_id", arguments=arguments):
                self._publish(duplicate_id_events)
                with self.assertRaisesRegex(AssistantToolError, "unique positive event IDs"):
                    self.surface.dispatch("find_reservations", arguments)

        unique_events = [
            self._reservation(551, booking_date, "14:00", "15:00", "B0.29"),
            self._reservation(552, booking_date, "15:00", "16:00", "B0.30"),
        ]
        duplicate_token = "sha256:" + "a" * 64
        for arguments in (
            {"date": booking_date.isoformat()},
            {"scope": "upcoming"},
        ):
            with self.subTest(kind="match_token", arguments=arguments):
                self._publish(unique_events)
                with patch("assistant_tools._event_token", return_value=duplicate_token):
                    with self.assertRaisesRegex(AssistantToolError, "fresh tokens"):
                        self.surface.dispatch("find_reservations", arguments)

    def test_exact_selection_cancellation_uses_fresh_id_and_persists_exact_blackout(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(601, booking_date, "15:00", "16:00", "B0.29")]
        )
        selection = self._find_by_ids(601)
        request = "Cancel my 3pm booking tomorrow."
        result = self.surface.dispatch(
            "cancel_reservations",
            {
                "request_quote": request,
                "selection_id": selection["selection_id"],
            },
            user_request=request,
        )
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["outcomes"][0]["verified_absent"])
        self.assertEqual(
            result["protected_windows"],
            [
                {
                    "date": booking_date.isoformat(),
                    "start_time": "15:00",
                    "end_time": "16:00",
                }
            ],
        )
        self.assertEqual(
            [item.to_dict() for item in load_rebooking_blackouts(path=self.paths.settings)],
            result["protected_windows"],
        )
        self.assertEqual(
            self.commands,
            [
                [
                    "--headless",
                    "--cancel-event-id",
                    "601",
                    "--cancel-date",
                    booking_date.isoformat(),
                    "--cancel-start",
                    "15:00",
                    "--cancel-end",
                    "16:00",
                    "--cancel-room",
                    "B0.29",
                ]
            ],
        )

    def test_named_period_success_protects_the_whole_window_and_clears_plan(self):
        booking_date = date.today() + timedelta(days=1)
        events = [
            self._reservation(701, booking_date, "13:00", "14:00", "B0.29"),
            self._reservation(702, booking_date, "16:00", "17:00", "B0.30"),
        ]
        self._publish(events)
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        targets = [self._target(match) for match in found["matches"]]
        runner, commands = self._bulk_runner(targets, [0, 0])
        self.surface._command_runner = runner
        self.paths.plan.write_text("stale plan", encoding="utf-8")
        request = "Cancel my bookings tomorrow afternoon."
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": found["selection_id"]},
            user_request=request,
        )
        expected = [
            {
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "18:00",
            }
        ]
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(result["protected_windows"], expected)
        self.assertEqual(
            [item.to_dict() for item in load_rebooking_blackouts(path=self.paths.settings)],
            expected,
        )
        self.assertEqual(len(commands), 2)
        self.assertFalse(self.paths.plan.exists())

    def test_named_period_complete_failure_creates_no_blackout(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(711, booking_date, "14:00", "15:00", "B0.29")]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        runner, commands = self._bulk_runner(
            [self._target(found["matches"][0])],
            [7],
        )
        self.surface._command_runner = runner
        request = "Cancel my bookings tomorrow afternoon."
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": found["selection_id"]},
            user_request=request,
        )
        self.assertEqual(result["cancelled_count"], 0)
        self.assertEqual(result["protected_windows"], [])
        self.assertEqual(load_rebooking_blackouts(path=self.paths.settings), ())
        self.assertEqual(len(commands), 1)

    def test_named_period_partial_success_still_protects_the_whole_window(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(721, booking_date, "13:00", "14:00", "B0.29"),
                self._reservation(722, booking_date, "16:00", "17:00", "B0.30"),
            ]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        runner, commands = self._bulk_runner(
            [self._target(match) for match in found["matches"]],
            [0, 7],
        )
        self.surface._command_runner = runner
        request = "Cancel my bookings tomorrow afternoon."
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": found["selection_id"]},
            user_request=request,
        )
        expected = [
            {
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "18:00",
            }
        ]
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["stopped_early"])
        self.assertEqual(result["protected_windows"], expected)
        self.assertEqual(
            [item.to_dict() for item in load_rebooking_blackouts(path=self.paths.settings)],
            expected,
        )
        self.assertEqual(len(commands), 2)

    def test_blackout_write_failure_stops_after_first_verified_success(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(761, booking_date, "13:00", "14:00", "B0.29"),
                self._reservation(762, booking_date, "16:00", "17:00", "B0.30"),
            ]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        runner, commands = self._bulk_runner(
            [self._target(match) for match in found["matches"]],
            [0, 0],
        )
        self.surface._command_runner = runner
        request = "Cancel my bookings tomorrow afternoon."
        with (
            patch(
                "assistant_tools.add_rebooking_blackouts",
                side_effect=SettingsError("synthetic settings write failure"),
            ) as write_blackout,
            patch("assistant_tools.clear_booking_plan") as clear_plan,
        ):
            result = self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": found["selection_id"]},
                user_request=request,
            )
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["stopped_early"])
        self.assertEqual([item["event_id"] for item in result["remaining_targets"]], [762])
        self.assertEqual(
            [item["status"] for item in result["outcomes"]],
            ["verified_cancelled", "not_attempted"],
        )
        self.assertEqual(
            result["requested_protection_windows"],
            [
                {
                    "date": booking_date.isoformat(),
                    "start_time": "12:00",
                    "end_time": "18:00",
                }
            ],
        )
        self.assertEqual(result["protected_windows"], [])
        self.assertFalse(result["protection_persisted"])
        self.assertIn("synthetic settings write failure", result["blackout_update_error"])
        self.assertIsNone(result["plan_effect"])
        self.assertEqual(load_rebooking_blackouts(path=self.paths.settings), ())
        self.assertEqual(len(commands), 1)
        write_blackout.assert_called_once()
        clear_plan.assert_not_called()

    def test_cancel_plan_clear_failure_reports_persisted_protection_warning(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(771, booking_date, "15:00", "16:00", "B0.29")]
        )
        found = self._find_by_ids(771)
        self.paths.plan.write_text("stale plan", encoding="utf-8")
        request = "Cancel my 3pm booking tomorrow."
        with patch(
            "assistant_tools.clear_booking_plan",
            side_effect=BookingPlanError("synthetic plan clear failure"),
        ):
            result = self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": found["selection_id"]},
                user_request=request,
            )
        expected = [
            {
                "date": booking_date.isoformat(),
                "start_time": "15:00",
                "end_time": "16:00",
            }
        ]
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["protection_persisted"])
        self.assertEqual(result["protected_windows"], expected)
        self.assertIn("synthetic plan clear failure", result["plan_invalidation_warning"])
        self.assertIsNotNone(result["plan_effect"])
        self.assertEqual(
            [item.to_dict() for item in load_rebooking_blackouts(path=self.paths.settings)],
            expected,
        )
        self.assertTrue(self.paths.plan.exists())

    def test_broad_protection_is_persisted_before_remaining_target_refresh(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(781, booking_date, "13:00", "14:00", "B0.29"),
                self._reservation(782, booking_date, "16:00", "17:00", "B0.30"),
            ]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        targets = [self._target(match) for match in found["matches"]]
        runner, commands = self._bulk_runner(targets, [0, 0])
        self.surface._command_runner = runner
        original_refresh = self.surface._refresh_cancellation_targets_after_success
        protection_seen_during_refresh = []
        expected = [
            {
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "18:00",
            }
        ]

        def checking_refresh(remaining, *, after):
            persisted = [
                item.to_dict()
                for item in load_rebooking_blackouts(path=self.paths.settings)
            ]
            protection_seen_during_refresh.append(persisted)
            return original_refresh(remaining, after=after)

        request = "Cancel my bookings tomorrow afternoon."
        with patch.object(
            self.surface,
            "_refresh_cancellation_targets_after_success",
            side_effect=checking_refresh,
        ):
            result = self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": found["selection_id"]},
                user_request=request,
            )
        self.assertEqual(protection_seen_during_refresh, [expected])
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(result["protected_windows"], expected)
        self.assertEqual(len(commands), 2)

    def test_selection_is_current_turn_scoped_and_cannot_be_reused(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish([self._reservation(801, booking_date)])
        selection = self._find_by_ids(801)["selection_id"]
        request = "Cancel that booking."
        self.surface.begin_turn()
        with self.assertRaisesRegex(AssistantToolError, "stale|earlier user turn"):
            self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": selection},
                user_request=request,
            )
        self.assertEqual(self.commands, [])

        # Resolve again in the current turn, consume it once, and reject retry.
        selection = self._find_by_ids(801)["selection_id"]
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": selection},
            user_request=request,
        )
        self.assertEqual(result["cancelled_count"], 1)
        with self.assertRaises(AssistantToolError):
            self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": selection},
                user_request=request,
            )
        self.assertEqual(len(self.commands), 1)

    def test_fake_or_changed_selection_is_rejected_before_command(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(811, booking_date, "14:00", "15:00", "B0.29")]
        )
        request = "Cancel that booking."
        for selection in ("selection:not-a-token", "selection:" + "f" * 64):
            with self.subTest(selection=selection), self.assertRaises(AssistantToolError):
                self.surface.dispatch(
                    "cancel_reservations",
                    {"request_quote": request, "selection_id": selection},
                    user_request=request,
                )

        selected = self._find_by_ids(811)["selection_id"]
        self._publish(
            [self._reservation(811, booking_date, "14:00", "15:00", "B0.30")],
            observed_at=self.last_observed + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(AssistantToolError, "changed|exact|fresh"):
            self.surface.dispatch(
                "cancel_reservations",
                {"request_quote": request, "selection_id": selected},
                user_request=request,
            )
        self.assertEqual(self.commands, [])

    def test_bulk_cancellation_revalidates_after_success_and_stops_on_uncertain(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(821, booking_date, "13:00", "14:00", "B0.21"),
                self._reservation(822, booking_date, "14:00", "15:00", "B0.22"),
                self._reservation(823, booking_date, "15:00", "16:00", "B0.23"),
            ]
        )
        found = self._find_by_ids(821, 822, 823)
        runner, commands = self._bulk_runner(
            [self._target(match) for match in found["matches"]],
            [0, 5, 0],
        )
        self.surface._command_runner = runner
        request = "Cancel those three bookings."
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": found["selection_id"]},
            user_request=request,
        )
        self.assertEqual(result["requested_count"], 3)
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["stopped_early"])
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual(
            [item["status"] for item in result["outcomes"]],
            ["verified_cancelled", "uncertain_pending", "not_attempted"],
        )
        self.assertEqual([item["event_id"] for item in result["remaining_targets"]], [823])
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            [item.to_dict() for item in load_rebooking_blackouts(path=self.paths.settings)],
            [
                {
                    "date": booking_date.isoformat(),
                    "start_time": "13:00",
                    "end_time": "14:00",
                }
            ],
        )

    def test_bulk_stops_when_success_has_no_newer_agenda_for_remaining_ids(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [
                self._reservation(831, booking_date, "13:00", "14:00"),
                self._reservation(832, booking_date, "14:00", "15:00"),
            ]
        )
        found = self._find_by_ids(831, 832)
        request = "Cancel those bookings."
        result = self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": request, "selection_id": found["selection_id"]},
            user_request=request,
        )
        self.assertEqual(result["cancelled_count"], 1)
        self.assertTrue(result["stopped_early"])
        self.assertIn("newer fresh agenda", result["stop_reason"])
        self.assertEqual([item["event_id"] for item in result["remaining_targets"]], [832])
        self.assertEqual(len(self.commands), 1)

    def test_reopen_splits_then_removes_persistent_blackouts(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(901, booking_date, "14:00", "15:00", "B0.29")]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        cancel_request = "Cancel my booking tomorrow afternoon."
        self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": cancel_request, "selection_id": found["selection_id"]},
            user_request=cancel_request,
        )
        self.paths.plan.write_text("stale plan", encoding="utf-8")

        reopen_request = "You can book between 2pm and 4pm tomorrow again."
        split = self.surface.dispatch(
            "reopen_booking_window",
            {
                "request_quote": reopen_request,
                "date": booking_date.isoformat(),
                "start_time": "14:00",
                "end_time": "16:00",
            },
            user_request=reopen_request,
        )
        expected_split = [
            {
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "14:00",
            },
            {
                "date": booking_date.isoformat(),
                "start_time": "16:00",
                "end_time": "18:00",
            },
        ]
        self.assertEqual(split["remaining_rebooking_blackouts"], expected_split)
        self.assertFalse(self.paths.plan.exists())

        reopen_all_request = "Reopen the whole afternoon tomorrow."
        removed = self.surface.dispatch(
            "reopen_booking_window",
            {
                "request_quote": reopen_all_request,
                "date": booking_date.isoformat(),
                "start_time": "12:00",
                "end_time": "18:00",
            },
            user_request=reopen_all_request,
        )
        self.assertEqual(removed["remaining_rebooking_blackouts"], [])
        self.assertEqual(load_rebooking_blackouts(path=self.paths.settings), ())

    def test_reopen_plan_clear_failure_reports_persisted_change_and_warning(self):
        booking_date = date.today() + timedelta(days=1)
        self._publish(
            [self._reservation(941, booking_date, "14:00", "15:00", "B0.29")]
        )
        found = self.surface.dispatch(
            "find_reservations",
            {"date": booking_date.isoformat(), "time_period": "afternoon"},
        )
        cancel_request = "Cancel my booking tomorrow afternoon."
        self.surface.dispatch(
            "cancel_reservations",
            {"request_quote": cancel_request, "selection_id": found["selection_id"]},
            user_request=cancel_request,
        )
        self.paths.plan.write_text("stale plan", encoding="utf-8")

        reopen_request = "Reopen tomorrow afternoon."
        with patch(
            "assistant_tools.clear_booking_plan",
            side_effect=BookingPlanError("synthetic reopen plan clear failure"),
        ):
            result = self.surface.dispatch(
                "reopen_booking_window",
                {
                    "request_quote": reopen_request,
                    "date": booking_date.isoformat(),
                    "start_time": "12:00",
                    "end_time": "18:00",
                },
                user_request=reopen_request,
            )
        self.assertTrue(result["reopened_persisted"])
        self.assertEqual(result["remaining_rebooking_blackouts"], [])
        self.assertIn(
            "synthetic reopen plan clear failure",
            result["plan_invalidation_warning"],
        )
        self.assertIsNotNone(result["plan_effect"])
        self.assertEqual(load_rebooking_blackouts(path=self.paths.settings), ())
        self.assertTrue(self.paths.plan.exists())

    def test_reopen_requires_overlap_valid_interval_and_full_provenance(self):
        booking_date = (date.today() + timedelta(days=1)).isoformat()
        request = "Reopen tomorrow afternoon."
        with self.assertRaisesRegex(AssistantToolError, "does not overlap"):
            self.surface.dispatch(
                "reopen_booking_window",
                {
                    "request_quote": request,
                    "date": booking_date,
                    "start_time": "12:00",
                    "end_time": "18:00",
                },
                user_request=request,
            )
        with self.assertRaisesRegex(AssistantToolError, "after"):
            self.surface.dispatch(
                "reopen_booking_window",
                {
                    "request_quote": request,
                    "date": booking_date,
                    "start_time": "18:00",
                    "end_time": "12:00",
                },
                user_request=request,
            )
        with self.assertRaisesRegex(AssistantToolError, "complete active"):
            self.surface.dispatch(
                "reopen_booking_window",
                {
                    "request_quote": "Reopen afternoon",
                    "date": booking_date,
                    "start_time": "12:00",
                    "end_time": "18:00",
                },
                user_request=request,
            )

    def test_refresh_modes_are_read_only_and_allow_listed(self):
        for scope, expected in {
            "agenda": ["--headless", "--check-only"],
            "plan": ["--headless", "--plan-only"],
            "login": ["--headless", "--login-only"],
        }.items():
            with self.subTest(scope=scope):
                result = self.surface.dispatch(
                    "refresh_booker_data",
                    {"scope": scope},
                )
                self.assertEqual(result["scope"], scope)
                self.assertEqual(self.commands[-1], expected)
        with self.assertRaises(AssistantToolError):
            self.surface.dispatch("refresh_booker_data", {"scope": "cancel"})


if __name__ == "__main__":
    unittest.main()
