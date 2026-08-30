import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from app_settings import SettingsError
from gui import (
    AsimutBookerGUI,
    PRACTICE_TARGET_CHOICES,
    ROOM_MINIMUM_BLOCK_CHOICES,
    apply_booking_day_updates,
    apply_practice_plan_update,
    append_history_entry,
    build_event_preference_rows,
    build_login_check_command,
    build_scheduler_setup_command,
    build_secure_login_update_command,
    catalog_booking_dates,
    changed_room_preference_fields,
    format_practice_hours,
    load_history_document,
    ignored_v2_keys_from_selection,
    is_exact_asimut_scan_url,
    parse_recurring_task_status,
    parse_room_preference_terms,
    parse_practice_target_choice,
    query_recurring_task_status,
    require_authenticated_scan_page,
    replace_history_document,
    room_catalog_ui_view,
    move_room_preference_item,
    validate_login_command,
    validate_booking_strategy_section,
    validate_time_preferences_section,
)
from event_identity import event_identity_v2, legacy_event_identity
from practice_plan import PracticePlan, PracticePlanError
from room_preferences import RoomPreferences, RoomPreferencesError


class GuiSchedulerHelpersTests(unittest.TestCase):
    def _healthy_task_payload(self):
        root = Path(__file__).resolve().parents[1]
        execute = str(
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "cmd.exe"
        )
        return {
            "TaskName": "AsimutBooker_Recurring",
            "TaskPath": "\\",
            "ActionCount": 1,
            "TriggerCount": 1,
            "State": "Ready",
            "Enabled": True,
            "NextRunTime": "2026-08-30 12:13",
            "Execute": execute,
            "Arguments": f'/d /c ""{root / "run_booker.bat"}" --scheduled"',
            "WorkingDirectory": str(root),
            "TriggerClass": "MSFT_TaskDailyTrigger",
            "TriggerEnabled": True,
            "DaysInterval": 1,
            "StartLocalTime": "07:13",
            "Interval": "PT15M",
            "Duration": "PT14H46M",
            "StopAtDurationEnd": False,
            "WakeToRun": True,
            "StartWhenAvailable": True,
            "RunOnlyIfNetworkAvailable": True,
            "DisallowStartIfOnBatteries": False,
            "StopIfGoingOnBatteries": False,
            "MultipleInstances": "IgnoreNew",
            "ExecutionTimeLimit": "PT14M",
            "RestartCount": 1,
            "RestartInterval": "PT1M",
            "PrincipalUserSid": "S-1-5-21-current-user",
            "CurrentUserSid": "S-1-5-21-current-user",
            "LogonType": "Interactive",
            "RunLevel": "Limited",
        }

    def test_setup_command_uses_headless_agent_uac_and_file_arguments(self):
        script = Path(r"C:\repo with spaces\setup_scheduled_tasks.ps1")
        command = build_scheduler_setup_command(
            script,
            local_app_data=Path(r"C:\Users\Test\AppData\Local"),
        )

        self.assertEqual(
            command[:5],
            [
                r"C:\Users\Test\AppData\Local\AgentUAC\agent-uac.exe",
                "run",
                "--reason",
                "Install or repair the AsimutBooker recurring scheduled task",
                "--",
            ],
        )
        self.assertEqual(
            command[5:10],
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ],
        )
        self.assertEqual(command[-1], str(script.resolve()))
        self.assertNotIn("Start-Process", command)
        self.assertNotIn("-Command", command)

    def test_setup_command_fails_when_agent_uac_location_is_unavailable(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "LOCALAPPDATA"):
                build_scheduler_setup_command(Path("setup_scheduled_tasks.ps1"))

    def test_status_parser_requires_exact_recurring_task(self):
        healthy_payload = self._healthy_task_payload()
        self.assertEqual(
            parse_recurring_task_status(json.dumps(healthy_payload)),
            {
                "task_name": "AsimutBooker_Recurring",
                "state": "Ready",
                "next_run": "2026-08-30 12:13",
                "healthy": True,
                "problem": "",
            },
        )
        for payload in (
            "not json",
            "[]",
            '{"TaskName":"AsimutBooker_1000","State":"Ready",'
            '"NextRunTime":"2026-08-30 12:13"}',
            '{"TaskName":"AsimutBooker_Recurring","State":1,"NextRunTime":"soon"}',
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    parse_recurring_task_status(payload)

        unhealthy = dict(healthy_payload, Interval="PT30M", WakeToRun=False)
        parsed = parse_recurring_task_status(json.dumps(unhealthy))
        self.assertFalse(parsed["healthy"])
        self.assertIn("wrong repetition window", parsed["problem"])
        self.assertIn("wake/recovery disabled", parsed["problem"])

        wrong_shape = dict(
            healthy_payload,
            TaskPath="\\Other\\",
            ActionCount=2,
            TriggerCount=0,
        )
        parsed = parse_recurring_task_status(json.dumps(wrong_shape))
        self.assertFalse(parsed["healthy"])
        self.assertIn("wrong task path", parsed["problem"])
        self.assertIn("wrong action count", parsed["problem"])
        self.assertIn("wrong trigger count", parsed["problem"])

    def test_status_parser_detects_drift_in_every_installer_contract_group(self):
        healthy_payload = self._healthy_task_payload()
        drift_cases = (
            ("disabled task", {"Enabled": False}, "task is disabled"),
            (
                "launcher path",
                {"Execute": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"},
                "wrong executable",
            ),
            (
                "launcher arguments",
                {"Arguments": healthy_payload["Arguments"] + " --check-only"},
                "wrong launcher arguments",
            ),
            (
                "working directory",
                {"WorkingDirectory": str(Path(healthy_payload["WorkingDirectory"]).parent)},
                "wrong working directory",
            ),
            (
                "trigger kind",
                {"TriggerClass": "MSFT_TaskLogonTrigger"},
                "wrong trigger type",
            ),
            ("trigger enabled", {"TriggerEnabled": False}, "trigger is disabled"),
            ("daily interval", {"DaysInterval": 2}, "wrong daily interval"),
            ("daily start", {"StartLocalTime": "07:14"}, "wrong daily start"),
            (
                "repetition end",
                {"StopAtDurationEnd": True},
                "repetition stops at duration end",
            ),
            (
                "network gating",
                {"RunOnlyIfNetworkAvailable": False},
                "network requirement disabled",
            ),
            (
                "battery start",
                {"DisallowStartIfOnBatteries": True},
                "battery policy drifted",
            ),
            (
                "battery stop",
                {"StopIfGoingOnBatteries": True},
                "battery policy drifted",
            ),
            (
                "execution limit",
                {"ExecutionTimeLimit": "PT15M"},
                "wrong execution time limit",
            ),
            ("restart count", {"RestartCount": 0}, "wrong restart policy"),
            (
                "restart interval",
                {"RestartInterval": "PT2M"},
                "wrong restart policy",
            ),
            (
                "principal user",
                {"PrincipalUserSid": "S-1-5-21-other-user"},
                "wrong task principal",
            ),
            ("logon type", {"LogonType": "Password"}, "wrong principal mode"),
            ("run level", {"RunLevel": "Highest"}, "wrong principal mode"),
        )
        for label, changes, reason in drift_cases:
            with self.subTest(label=label):
                parsed = parse_recurring_task_status(
                    json.dumps(dict(healthy_payload, **changes))
                )
                self.assertFalse(parsed["healthy"])
                self.assertIn(reason, parsed["problem"])

    @patch("gui.subprocess.run")
    def test_query_treats_only_the_exact_missing_exit_as_not_installed(self, run):
        run.return_value.returncode = 3
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        self.assertIsNone(query_recurring_task_status())

        run.return_value.returncode = 1
        run.return_value.stderr = "access denied"
        with self.assertRaisesRegex(RuntimeError, "access denied"):
            query_recurring_task_status()

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["powershell.exe", "-NoProfile", "-Command"])
        self.assertIn("AsimutBooker_Recurring", command[-1])
        for field in (
            "WorkingDirectory",
            "CimClassName",
            "StartBoundary",
            "StopAtDurationEnd",
            "RunOnlyIfNetworkAvailable",
            "DisallowStartIfOnBatteries",
            "ExecutionTimeLimit",
            "RestartInterval",
            "PrincipalUserSid",
            "LogonType",
            "RunLevel",
        ):
            with self.subTest(field=field):
                self.assertIn(field, command[-1])


class GuiLoginOperationTests(unittest.TestCase):
    def test_login_commands_use_isolated_runtime_and_never_manual_website_setup(self):
        root = Path(r"C:\repo with spaces\AsimutBooker")
        check = build_login_check_command(root)
        update = build_secure_login_update_command(root)

        expected_python = str(root / ".venv" / "Scripts" / "python.exe")
        expected_script = str(root / "book_week.py")
        self.assertEqual(
            check,
            [expected_python, expected_script, "--headless", "--login-only"],
        )
        self.assertEqual(
            update,
            [expected_python, expected_script, "--configure-autonomous-login"],
        )
        self.assertNotIn("--setup-login", check + update)

    def test_login_command_validation_requires_runtime_and_entry_point(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = build_login_check_command(root)
            with self.assertRaisesRegex(RuntimeError, "Python is missing"):
                validate_login_command(command)

            python_path = Path(command[0])
            python_path.parent.mkdir(parents=True)
            python_path.touch()
            with self.assertRaisesRegex(RuntimeError, "entry point is missing"):
                validate_login_command(command)

            Path(command[1]).touch()
            validate_login_command(command)

    def test_duplicate_login_operation_is_rejected(self):
        gui = object.__new__(AsimutBookerGUI)
        gui.login_operation_in_progress = True
        gui.login_check_btn = MagicMock()
        gui.login_update_btn = MagicMock()
        gui.progress_var = MagicMock()

        with patch("gui.messagebox.showwarning") as warning:
            self.assertFalse(gui._begin_login_operation("Checking"))

        warning.assert_called_once()
        gui.login_check_btn.configure.assert_not_called()
        gui.login_update_btn.configure.assert_not_called()
        gui.progress_var.set.assert_not_called()

    def test_headless_check_worker_reports_process_result_without_visible_browser(self):
        gui = object.__new__(AsimutBookerGUI)
        gui.root = MagicMock()
        gui._finish_login_operation = MagicMock()
        scheduled = []
        gui.root.after.side_effect = lambda delay, callback, *args: scheduled.append(
            (delay, callback, args)
        )
        command = [
            r"C:\repo\.venv\Scripts\python.exe",
            r"C:\repo\book_week.py",
            "--headless",
            "--login-only",
        ]
        result = MagicMock(returncode=0, stdout="Login ready\n", stderr="")

        with (
            patch("gui.build_login_check_command", return_value=command),
            patch("gui.validate_login_command") as validate,
            patch("gui.subprocess.run", return_value=result) as run,
        ):
            gui._run_login_check_thread()

        validate.assert_called_once_with(command)
        self.assertEqual(run.call_args.args[0], command)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            __import__("subprocess").CREATE_NO_WINDOW,
        )
        self.assertNotIn("--setup-login", run.call_args.args[0])
        _, callback, args = scheduled[0]
        callback(*args)
        gui._finish_login_operation.assert_called_once_with(
            "check",
            0,
            "Login ready\n",
            "",
            None,
        )

    def test_secure_update_worker_uses_new_interactive_console_without_secret_args(self):
        gui = object.__new__(AsimutBookerGUI)
        gui.root = MagicMock()
        gui._finish_login_operation = MagicMock()
        gui.root.after.side_effect = lambda _delay, callback, *args: callback(*args)
        command = [
            r"C:\repo\.venv\Scripts\python.exe",
            r"C:\repo\book_week.py",
            "--configure-autonomous-login",
        ]
        process = MagicMock()
        process.wait.return_value = 0

        with (
            patch("gui.build_secure_login_update_command", return_value=command),
            patch("gui.validate_login_command") as validate,
            patch("gui.subprocess.Popen", return_value=process) as popen,
        ):
            gui._run_secure_login_update_thread()

        validate.assert_called_once_with(command)
        self.assertEqual(popen.call_args.args[0], command)
        self.assertEqual(
            popen.call_args.kwargs["creationflags"],
            __import__("subprocess").CREATE_NEW_CONSOLE,
        )
        self.assertEqual(len(command), 3)
        gui._finish_login_operation.assert_called_once_with(
            "update",
            0,
            "",
            "",
            None,
        )


class GuiPracticePlanHelpersTests(unittest.TestCase):
    def test_target_choices_cover_half_hour_steps(self):
        self.assertEqual(PRACTICE_TARGET_CHOICES[0], "0.5h")
        self.assertEqual(PRACTICE_TARGET_CHOICES[-1], "12h")
        self.assertEqual(parse_practice_target_choice("1.5h"), 1.5)
        self.assertEqual(format_practice_hours(2.0), "2")

    def test_scoped_plan_save_preserves_unrelated_and_out_of_window_values(self):
        settings = {
            "cached_events": [{"id": 1}],
            "ignored_events": ["event"],
            "disabled_dates": ["2026-09-01", "2026-10-01"],
            "practice_plan": {
                "enabled": True,
                "default_hours": 2.0,
                "date_overrides": {
                    "2026-09-01": 1.0,
                    "2026-10-01": 3.0,
                },
            },
        }

        plan = apply_practice_plan_update(
            settings,
            plan_enabled=True,
            default_hours=2.5,
            day_states={"2026-09-01": True, "2026-09-02": False},
            date_overrides={"2026-09-01": None, "2026-09-02": 1.5},
        )

        self.assertEqual(settings["cached_events"], [{"id": 1}])
        self.assertEqual(settings["ignored_events"], ["event"])
        self.assertEqual(settings["disabled_dates"], ["2026-09-02", "2026-10-01"])
        self.assertEqual(
            settings["practice_plan"]["date_overrides"],
            {"2026-09-02": 1.5, "2026-10-01": 3.0},
        )
        self.assertEqual(plan.default_hours, 2.5)

    def test_plan_save_scopes_day_and_target_changes_independently(self):
        settings = {
            "disabled_dates": ["2026-09-01"],
            "practice_plan": {
                "enabled": True,
                "default_hours": 3.0,
                "date_overrides": {
                    "2026-09-01": 1.0,
                    "2026-09-02": 2.0,
                },
            },
        }

        plan = apply_practice_plan_update(
            settings,
            plan_enabled=True,
            # The per-date dialog did not edit the default or 2 September's
            # target, so concurrent values for both must survive.
            default_hours=None,
            day_states={"2026-09-02": False},
            date_overrides={"2026-09-01": 1.5},
        )

        self.assertEqual(
            settings["disabled_dates"],
            ["2026-09-01", "2026-09-02"],
        )
        self.assertEqual(
            settings["practice_plan"]["date_overrides"],
            {"2026-09-01": 1.5, "2026-09-02": 2.0},
        )
        self.assertEqual(plan.default_hours, 3.0)

    def test_invalid_existing_day_settings_fail_closed(self):
        settings = {"disabled_dates": "2026-09-01"}
        with self.assertRaises(PracticePlanError):
            apply_practice_plan_update(
                settings,
                plan_enabled=True,
                default_hours=2.0,
                day_states={"2026-09-01": True},
                date_overrides={"2026-09-01": None},
            )

        for day_states in ({"01/09/2026": True}, {"2026-09-01": "yes"}):
            with self.subTest(day_states=day_states):
                with self.assertRaises(PracticePlanError):
                    apply_practice_plan_update(
                        {},
                        plan_enabled=True,
                        default_hours=2.0,
                        day_states=day_states,
                        date_overrides={},
                    )

    def test_invalid_target_choice_is_rejected(self):
        for value in ("Default", "0h", "1.25h", "lots"):
            with self.subTest(value=value):
                with self.assertRaises(PracticePlanError):
                    parse_practice_target_choice(value)

    def test_scoped_booking_day_update_preserves_concurrent_out_of_scope_dates(self):
        settings = {
            "disabled_dates": ["2026-09-02", "2026-10-01"],
            "cached_events": [{"id": 1}],
        }

        apply_booking_day_updates(
            settings,
            {
                "2026-09-01": False,
                "2026-09-02": True,
            },
        )

        self.assertEqual(
            settings["disabled_dates"],
            ["2026-09-01", "2026-10-01"],
        )
        self.assertEqual(settings["cached_events"], [{"id": 1}])

    def test_invalid_hours_restore_persisted_plan_toggle_and_widget_state(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        gui = object.__new__(AsimutBookerGUI)
        gui.settings_available = True
        gui.practice_plan = PracticePlan(enabled=True, default_hours=2.0, date_overrides={})
        gui.practice_default_hours = Variable("1.25")
        gui.practice_plan_enabled = Variable(False)
        gui._update_practice_plan_ui_state = MagicMock()
        gui._update_practice_plan_summary = MagicMock()
        gui._update_settings = MagicMock()

        with patch("gui.messagebox.showerror") as showerror:
            gui.on_practice_plan_changed()

        self.assertEqual(gui.practice_default_hours.get(), "2")
        self.assertTrue(gui.practice_plan_enabled.get())
        gui._update_practice_plan_ui_state.assert_called_once_with()
        gui._update_practice_plan_summary.assert_called_once_with()
        gui._update_settings.assert_not_called()
        showerror.assert_called_once()


class GuiRoomPreferenceHelpersTests(unittest.TestCase):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class Catalog:
        complete = True
        source = "cache"
        fresh = False
        observed_at = datetime(2026, 8, 30, 11, 15, tzinfo=timezone.utc)

        def as_live_metadata(self):
            return {
                "B0.29": {
                    "instrument_tags": ["Piano"],
                    "room_type_tags": ["Practice room"],
                    "features": ["Adjustable piano stool"],
                },
                "B2.10": {
                    "instrument_tags": ["Percussion"],
                    "room_type_tags": ["Ensemble room"],
                    "features": ["Music stands"],
                },
            }

    def make_gui(self, preferences=None, catalog=None):
        gui = object.__new__(AsimutBookerGUI)
        gui.settings_available = True
        gui.settings_error = ""
        gui.room_preferences = preferences or RoomPreferences(
            ordered_rooms=("B0.29",),
        )
        gui.room_catalog = catalog
        gui.room_catalog_metadata = None
        gui.room_preferences_summary_var = self.Variable()
        gui.room_catalog_status_var = self.Variable()
        return gui

    def test_term_parser_and_move_helper_use_strict_room_contract(self):
        self.assertEqual(ROOM_MINIMUM_BLOCK_CHOICES, tuple(range(30, 121, 15)))
        self.assertEqual(
            parse_room_preference_terms(" Piano,  Percussion\nOrgan ", "instrument tags"),
            ("Piano", "Percussion", "Organ"),
        )
        with self.assertRaisesRegex(RoomPreferencesError, "duplicate"):
            parse_room_preference_terms("Piano, piano", "instrument tags")

        moved, selected = move_room_preference_item(
            ("B0.29", "B1.09", "B2.10"), 1, -1
        )
        self.assertEqual(moved, ("B1.09", "B0.29", "B2.10"))
        self.assertEqual(selected, 0)
        self.assertEqual(
            move_room_preference_item(moved, 0, -1),
            (moved, 0),
        )

    def test_changed_fields_are_scoped_to_actual_dialog_edits(self):
        opening = RoomPreferences(ordered_rooms=("B0.29",))
        candidate = replace(opening, minimum_block_minutes=45)

        self.assertEqual(
            changed_room_preference_fields(opening, candidate),
            {"minimum_block_minutes": 45},
        )

    def test_catalog_view_accepts_room_catalog_contract_and_labels_cache_stale(self):
        metadata, status = room_catalog_ui_view(self.Catalog())

        self.assertEqual(list(metadata), ["B0.29", "B2.10"])
        self.assertEqual(metadata["B0.29"]["instrument_tags"], ["Piano"])
        self.assertIn("2 rooms", status)
        self.assertIn("saved observation", status)
        self.assertIn("refresh required before booking", status)

        incomplete = self.Catalog()
        incomplete.complete = False
        metadata, status = room_catalog_ui_view(incomplete)
        self.assertIsNone(metadata)
        self.assertIn("incomplete", status)

    def test_catalog_view_accepts_real_room_catalog_dataclasses(self):
        from room_catalog import RoomCatalog, RoomCatalogRoom

        observed_at = datetime(2026, 8, 30, 11, 15, tzinfo=timezone.utc)
        room = RoomCatalogRoom(
            location_id=29,
            name="B0.29",
            secondary_name="Piano room",
            description="Practice room",
            inventory="Piano; adjustable stool",
            instrument_tags=("Piano",),
            room_type_tags=("Practice room",),
            features=("Adjustable piano stool",),
            horizon_minutes=5 * 24 * 60,
            booking_cutoff=observed_at + timedelta(days=5),
        )
        catalog = RoomCatalog(
            version=1,
            observed_at=observed_at,
            booking_horizon=observed_at + timedelta(days=7),
            global_horizon_minutes=7 * 24 * 60,
            minimum_booking_minutes=30,
            maximum_booking_minutes=120,
            minimum_booking_gap_minutes=60,
            booking_category_id=56,
            rooms=(room,),
            fresh=False,
        )

        metadata, status = room_catalog_ui_view(catalog)

        self.assertEqual(metadata["B0.29"]["features"], ["Adjustable piano stool"])
        self.assertIn("saved observation", status)

    def test_gui_loads_display_cache_and_refresh_reloads_it(self):
        gui = self.make_gui()
        cached = self.Catalog()
        with patch("room_catalog.load_cached_catalog", return_value=cached) as load:
            self.assertIs(gui.load_room_catalog_cache(), cached)
        load.assert_called_once_with(missing_ok=True)
        self.assertEqual(gui.room_catalog_load_error, "")

        gui.load_room_catalog_cache = MagicMock()
        gui.load_booking_days = MagicMock()
        gui._update_days_summary = MagicMock()
        gui._update_practice_plan_summary = MagicMock()
        gui.load_room_preferences_settings = MagicMock()
        gui._start_local_health_refresh = MagicMock()
        gui.check_scheduled_tasks = MagicMock()
        gui.refresh_status()
        gui.load_room_catalog_cache.assert_called_once_with()
        gui.load_booking_days.assert_called_once_with()
        gui._update_days_summary.assert_called_once_with()
        gui._update_practice_plan_summary.assert_called_once_with()
        gui.load_room_preferences_settings.assert_called_once_with()

    def test_booking_dates_follow_catalog_cutoff_instead_of_fixed_day_count(self):
        catalog = MagicMock()
        catalog.booking_horizon = datetime(
            2026, 9, 4, 14, 45, tzinfo=timezone.utc
        )

        self.assertEqual(
            catalog_booking_dates(catalog, today=datetime(2026, 8, 30).date()),
            tuple(
                datetime(2026, 8, 30).date() + timedelta(days=offset)
                for offset in range(6)
            ),
        )

    def test_loading_reconciles_new_catalog_rooms_without_dropping_saved_rank(self):
        gui = self.make_gui(catalog=self.Catalog())
        gui.load_settings = MagicMock(
            return_value={
                "unrelated": {"keep": True},
                "room_preferences": {"ordered_rooms": ["B0.29"]},
            }
        )
        gui._set_settings_error = MagicMock()

        gui.load_room_preferences_settings()

        self.assertEqual(gui.room_preferences.ordered_rooms, ("B0.29", "B2.10"))
        self.assertIn("2 catalog eligible rooms", gui.room_preferences_summary_var.get())
        self.assertIn("saved observation", gui.room_catalog_status_var.get())
        gui._set_settings_error.assert_not_called()

    def test_scoped_save_preserves_unrelated_and_concurrent_room_fields(self):
        opening = RoomPreferences(ordered_rooms=("B0.29",))
        candidate = replace(opening, minimum_block_minutes=45)
        gui = self.make_gui(opening)
        settings = {
            "unrelated": {"keep": True},
            "room_preferences": {
                "ordered_rooms": ["B0.29"],
                "acceptable_instrument_tags": ["Violin"],
                "minimum_block_minutes": 30,
            },
        }

        def update(mutator):
            mutator(settings)
            return True

        gui._update_settings = update

        self.assertTrue(
            gui.save_room_preferences_settings(
                candidate,
                opening_preferences=opening,
            )
        )

        self.assertEqual(settings["unrelated"], {"keep": True})
        self.assertEqual(
            settings["room_preferences"]["acceptable_instrument_tags"],
            ["Violin"],
        )
        self.assertEqual(settings["room_preferences"]["minimum_block_minutes"], 45)
        self.assertEqual(gui.room_preferences.acceptable_instrument_tags, ("Violin",))

    def test_excluding_new_catalog_room_appends_only_missing_order_dependency(self):
        opening = RoomPreferences(ordered_rooms=("B0.29", "B2.10"))
        candidate = replace(opening, excluded_rooms=("B2.10",))
        gui = self.make_gui(opening, self.Catalog())
        gui.room_catalog_metadata, _ = room_catalog_ui_view(gui.room_catalog)
        settings = {
            "room_preferences": {
                # B1.09 is a concurrent rank edit outside this dialog's
                # exclusion change and must remain exactly where it is.
                "ordered_rooms": ["B1.09", "B0.29"],
            }
        }

        def update(mutator):
            mutator(settings)
            return True

        gui._update_settings = update

        self.assertTrue(
            gui.save_room_preferences_settings(
                candidate,
                opening_preferences=opening,
            )
        )
        self.assertEqual(
            settings["room_preferences"]["ordered_rooms"],
            ["B1.09", "B0.29", "B2.10"],
        )
        self.assertEqual(settings["room_preferences"]["excluded_rooms"], ["B2.10"])

    def test_failed_write_restores_last_confirmed_room_model(self):
        opening = RoomPreferences(ordered_rooms=("B0.29",))
        candidate = replace(opening, allow_fragmented_sessions=False)
        gui = self.make_gui(opening)
        gui._update_settings = MagicMock(return_value=False)
        gui._install_confirmed_room_preferences = MagicMock()

        self.assertFalse(
            gui.save_room_preferences_settings(
                candidate,
                opening_preferences=opening,
            )
        )
        gui._install_confirmed_room_preferences.assert_called_once_with(opening)

    def test_invalid_candidate_restores_model_without_attempting_a_write(self):
        opening = RoomPreferences(ordered_rooms=("B0.29",))
        invalid = RoomPreferences(ordered_rooms=(" room with outer space ",))
        gui = self.make_gui(opening)
        gui._update_settings = MagicMock()
        gui._install_confirmed_room_preferences = MagicMock()

        with patch("gui.messagebox.showerror") as showerror:
            self.assertFalse(
                gui.save_room_preferences_settings(
                    invalid,
                    opening_preferences=opening,
                )
            )

        gui._update_settings.assert_not_called()
        gui._install_confirmed_room_preferences.assert_called_once_with(opening)
        showerror.assert_called_once()


class GuiPracticePlanPersistenceTests(unittest.TestCase):
    def test_failed_plan_write_restores_last_confirmed_controls(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        gui = object.__new__(AsimutBookerGUI)
        gui.settings_available = True
        gui.practice_plan = PracticePlan(
            enabled=True,
            default_hours=2.0,
            date_overrides={"2026-09-01": 1.0},
        )
        gui.practice_plan_overrides = {"2026-09-01": 1.0}
        gui.practice_default_hours = Variable("3")
        gui.practice_plan_enabled = Variable(False)
        gui._update_practice_plan_ui_state = MagicMock()
        gui._update_practice_plan_summary = MagicMock()
        gui._update_settings = MagicMock(return_value=False)

        gui.on_practice_plan_changed()

        self.assertEqual(gui.practice_default_hours.get(), "2")
        self.assertTrue(gui.practice_plan_enabled.get())
        self.assertEqual(gui.practice_plan_overrides, {"2026-09-01": 1.0})
        gui._update_practice_plan_ui_state.assert_called_once_with()
        gui._update_practice_plan_summary.assert_called_once_with()

    def test_compact_plan_save_merges_only_the_field_the_user_changed(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        cases = (
            {
                "name": "default changed locally while enabled changed elsewhere",
                "ui_enabled": True,
                "ui_default": "3",
                "latest": {
                    "enabled": False,
                    "default_hours": 2.5,
                    "date_overrides": {"2026-09-01": 1.0},
                },
                "expected_enabled": False,
                "expected_default": 3.0,
            },
            {
                "name": "enabled changed locally while default changed elsewhere",
                "ui_enabled": False,
                "ui_default": "2",
                "latest": {
                    "enabled": True,
                    "default_hours": 3.5,
                    "date_overrides": {"2026-09-01": 1.0},
                },
                "expected_enabled": False,
                "expected_default": 3.5,
            },
        )

        for case in cases:
            with self.subTest(case["name"]):
                gui = object.__new__(AsimutBookerGUI)
                gui.settings_available = True
                gui.practice_plan = PracticePlan(
                    enabled=True,
                    default_hours=2.0,
                    date_overrides={"2026-09-01": 1.0},
                )
                gui.practice_plan_overrides = {"2026-09-01": 1.0}
                gui.practice_plan_enabled = Variable(case["ui_enabled"])
                gui.practice_default_hours = Variable(case["ui_default"])
                gui._update_practice_plan_ui_state = MagicMock()
                gui._update_practice_plan_summary = MagicMock()
                settings = {"practice_plan": dict(case["latest"])}

                def update(mutator):
                    mutator(settings)
                    return True

                gui._update_settings = update

                gui.on_practice_plan_changed()

                saved = settings["practice_plan"]
                self.assertEqual(saved["enabled"], case["expected_enabled"])
                self.assertEqual(saved["default_hours"], case["expected_default"])
                self.assertEqual(
                    saved["date_overrides"],
                    {"2026-09-01": 1.0},
                )
                self.assertEqual(
                    gui.practice_plan_enabled.get(),
                    case["expected_enabled"],
                )
                self.assertEqual(
                    gui.practice_default_hours.get(),
                    format_practice_hours(case["expected_default"]),
                )

    def test_install_confirmed_plan_resyncs_every_compact_widget(self):
        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        gui = object.__new__(AsimutBookerGUI)
        gui.practice_plan_enabled = Variable(False)
        gui.practice_default_hours = Variable("2")
        gui.practice_plan_overrides = {}
        gui._update_practice_plan_ui_state = MagicMock()
        gui._update_practice_plan_summary = MagicMock()
        saved_plan = PracticePlan(
            enabled=True,
            default_hours=3.5,
            date_overrides={"2026-09-02": 1.5},
        )

        gui._install_confirmed_practice_plan(saved_plan)

        self.assertIs(gui.practice_plan, saved_plan)
        self.assertTrue(gui.practice_plan_enabled.get())
        self.assertEqual(gui.practice_default_hours.get(), "3.5")
        self.assertEqual(gui.practice_plan_overrides, {"2026-09-02": 1.5})
        gui._update_practice_plan_ui_state.assert_called_once_with()
        gui._update_practice_plan_summary.assert_called_once_with()


class GuiAuthenticatedScanTests(unittest.TestCase):
    def _room_scan_gui(self, generation=4):
        gui = object.__new__(AsimutBookerGUI)
        gui._room_scan_generation = generation
        gui.room_preferences = RoomPreferences(minimum_block_minutes=45)
        gui.root = MagicMock()
        gui.root.after.side_effect = (
            lambda _delay, callback, *args: callback(*args)
        )
        gui.scan_rooms_progress_var = MagicMock()
        gui.scan_rooms_btn = MagicMock()
        gui.log = MagicMock()
        gui._show_scan_results = MagicMock()
        setup_dialog = MagicMock()
        setup_dialog.winfo_exists.return_value = True
        gui.scan_rooms_dialog = setup_dialog
        return gui, setup_dialog

    def test_exact_scan_url_rejects_redirects_hosts_ports_and_decorations(self):
        self.assertTrue(
            is_exact_asimut_scan_url(
                "https://rwcmd.asimut.net/agenda",
                "/agenda",
            )
        )
        self.assertTrue(
            is_exact_asimut_scan_url(
                "https://rwcmd.asimut.net/overview?locationGroupId=10",
                "/overview",
                expected_query="locationGroupId=10",
            )
        )
        for url in (
            "http://rwcmd.asimut.net/agenda",
            "https://rwcmd.asimut.net:443/agenda",
            "https://rwcmd.asimut.net.evil.example/agenda",
            "https://rwcmd.asimut.net/login",
            "https://rwcmd.asimut.net/agenda?next=1",
            "https://rwcmd.asimut.net/agenda#event",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_exact_asimut_scan_url(url, "/agenda"))

    def test_scan_page_requires_both_authentication_and_exact_page(self):
        page = MagicMock()
        page.url = "https://rwcmd.asimut.net/agenda"
        require_authenticated_scan_page(page, lambda _page: True, "/agenda")

        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            require_authenticated_scan_page(page, lambda _page: False, "/agenda")
        page.url = "https://login.microsoftonline.com/"
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            require_authenticated_scan_page(page, lambda _page: True, "/agenda")

    def test_calendar_auth_failure_never_replaces_cache_and_error_is_eager(self):
        gui = object.__new__(AsimutBookerGUI)
        gui._calendar_scan_generation = 7
        gui._update_settings = MagicMock()
        gui._on_calendar_scan_error = MagicMock()

        scheduled = []
        gui.root = MagicMock()
        gui.root.after.side_effect = lambda delay, callback, *args: scheduled.append(
            (delay, callback, args)
        )

        manager = MagicMock()
        playwright = manager.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value

        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch("book_week.authenticated_runtime_context_options", return_value={}) as options,
            patch("book_week.restore_page_authentication") as restore,
            patch("room_catalog.refresh_from_site", side_effect=RuntimeError("session recovery failed")) as refresh_catalog,
            patch("book_week.safe_goto") as safe_goto,
            patch("book_week.page_is_authenticated", return_value=False),
            patch("book_week.persist_storage_state") as persist,
        ):
            gui._scan_calendar_events_thread(7)

        options.assert_called_once_with()
        restore.assert_called_once_with(page)
        refresh_catalog.assert_called_once_with(page)
        safe_goto.assert_not_called()
        persist.assert_not_called()
        gui._update_settings.assert_not_called()
        self.assertEqual(len(scheduled), 1)
        delay, callback, args = scheduled[0]
        self.assertEqual(delay, 0)
        callback(*args)
        gui._on_calendar_scan_error.assert_called_once_with(
            7,
            "session recovery failed",
        )

    def test_stale_calendar_result_cannot_replace_current_state_or_ui(self):
        gui = object.__new__(AsimutBookerGUI)
        gui._calendar_scan_generation = 3
        gui.calendar_events = {"existing": [{"id": 1}]}
        gui.calendar_dialog = MagicMock()
        gui.calendar_scan_var = MagicMock()
        gui._refresh_calendar = MagicMock()

        gui._on_calendar_scan_complete(2, {"stale": [{"id": 2}]})

        self.assertEqual(gui.calendar_events, {"existing": [{"id": 1}]})
        gui.calendar_dialog.winfo_exists.assert_not_called()
        gui.calendar_scan_var.set.assert_not_called()
        gui._refresh_calendar.assert_not_called()

    def test_room_scan_uses_live_policy_and_shared_verified_renderer_helpers(self):
        gui, setup_dialog = self._room_scan_gui()
        today = datetime.now().date()
        selected_dates = [
            (today + timedelta(days=1)).isoformat(),
            (today + timedelta(days=2)).isoformat(),
        ]

        manager = MagicMock()
        playwright = manager.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = "https://rwcmd.asimut.net/overview?locationGroupId=10"

        policy = MagicMock()
        policy.minimum_block_minutes = 45
        policy.booking_dates.return_value = tuple(
            today + timedelta(days=offset) for offset in range(3)
        )
        slot_snapshots = [
            [
                {
                    "room": "B0.29",
                    "slots": [
                        {"startHour": 9.0, "endHour": 9.5},
                        {"startHour": 10.0, "endHour": 10.75},
                    ],
                }
            ],
            [
                {
                    "room": "The Hopkins Studio",
                    "slots": [{"startHour": 12.0, "endHour": 13.0}],
                }
            ],
        ]

        def navigate(_page, target, _current, *, base_date=None):
            self.assertEqual(base_date, today)
            return target

        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch(
                "book_week.authenticated_runtime_context_options",
                return_value={},
            ) as options,
            patch("book_week.restore_page_authentication") as restore,
            patch(
                "book_week.refresh_live_room_policy",
                return_value=policy,
            ) as refresh_policy,
            patch("book_week.open_practice_room_overview") as open_overview,
            patch("book_week.navigate_to_day", side_effect=navigate) as navigate_day,
            patch("book_week.wait_for_practice_room_grid") as wait_grid,
            patch(
                "book_week.get_available_slots",
                side_effect=slot_snapshots,
            ) as get_slots,
            patch("book_week.page_is_authenticated", return_value=True),
            patch("book_week.persist_storage_state") as persist,
        ):
            gui._scan_rooms_thread(
                setup_dialog,
                selected_dates,
                gui._room_scan_generation,
            )

        options.assert_called_once_with()
        restore.assert_called_once_with(page)
        refresh_policy.assert_called_once_with(
            page,
            gui.room_preferences,
            today=today,
        )
        policy.booking_dates.assert_called_once_with(today)
        open_overview.assert_called_once_with(page, today)
        self.assertEqual(
            navigate_day.call_args_list,
            [
                call(page, 1, 0, base_date=today),
                call(page, 2, 1, base_date=today),
            ],
        )
        self.assertEqual(
            wait_grid.call_args_list,
            [
                call(page, today + timedelta(days=1)),
                call(page, today + timedelta(days=2)),
            ],
        )
        self.assertEqual(get_slots.call_count, 2)
        persist.assert_called_once_with(context)
        browser.close.assert_called_once_with()

        gui._show_scan_results.assert_called_once()
        shown_dialog, shown_slots, shown_dates = gui._show_scan_results.call_args.args
        self.assertIs(shown_dialog, setup_dialog)
        self.assertEqual(shown_dates, selected_dates)
        self.assertEqual(
            [
                (slot["date"], slot["room"], slot["start_time"], slot["duration_mins"])
                for slot in shown_slots
            ],
            [
                (selected_dates[0], "B0.29", "10:00", 45.0),
                (selected_dates[1], "The Hopkins Studio", "12:00", 60.0),
            ],
        )

    def test_room_scan_navigation_failure_is_reported_without_results_or_session_write(self):
        gui, setup_dialog = self._room_scan_gui()
        today = datetime.now().date()
        selected_dates = [(today + timedelta(days=1)).isoformat()]

        manager = MagicMock()
        playwright = manager.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = "https://rwcmd.asimut.net/overview?locationGroupId=10"
        policy = MagicMock()
        policy.minimum_block_minutes = 45
        policy.booking_dates.return_value = (
            today,
            today + timedelta(days=1),
        )

        with (
            patch("playwright.sync_api.sync_playwright", return_value=manager),
            patch("book_week.authenticated_runtime_context_options", return_value={}),
            patch("book_week.restore_page_authentication"),
            patch("book_week.refresh_live_room_policy", return_value=policy),
            patch("book_week.open_practice_room_overview"),
            patch(
                "book_week.navigate_to_day",
                side_effect=RuntimeError("verified navigation failed"),
            ),
            patch("book_week.wait_for_practice_room_grid") as wait_grid,
            patch("book_week.get_available_slots") as get_slots,
            patch("book_week.page_is_authenticated", return_value=True),
            patch("book_week.persist_storage_state") as persist,
            patch("gui.messagebox.showerror") as show_error,
        ):
            gui._scan_rooms_thread(
                setup_dialog,
                selected_dates,
                gui._room_scan_generation,
            )

        wait_grid.assert_not_called()
        get_slots.assert_not_called()
        persist.assert_not_called()
        gui._show_scan_results.assert_not_called()
        browser.close.assert_called_once_with()
        gui.scan_rooms_btn.config.assert_called_once_with(state="normal")
        self.assertIn(
            "verified navigation failed",
            gui.scan_rooms_progress_var.set.call_args.args[0],
        )
        show_error.assert_called_once()

    def test_stale_room_scan_callbacks_never_publish_or_report(self):
        gui, setup_dialog = self._room_scan_gui(generation=8)

        with patch("gui.messagebox.showerror") as show_error:
            gui._on_scan_rooms_complete(7, setup_dialog, [{"room": "stale"}], [])
            gui._on_scan_rooms_error(7, setup_dialog, "stale failure")

        gui._show_scan_results.assert_not_called()
        gui.scan_rooms_btn.config.assert_not_called()
        gui.scan_rooms_progress_var.set.assert_not_called()
        show_error.assert_not_called()


class GuiEventPreferenceHelpersTests(unittest.TestCase):
    def setUp(self):
        self.class_event = {
            "date": "2026-08-31",
            "startTime": "09:00",
            "endTime": "10:00",
            "title": "Technique class",
            "room": "B0.29",
            "isReservation": False,
        }
        self.reservation = {
            **self.class_event,
            "title": "Reservation",
            "room": "B1.09",
            "isReservation": True,
        }

    def test_ambiguous_legacy_selection_leaves_both_gui_rows_enabled(self):
        legacy_key = legacy_event_identity(self.class_event)
        rows, resolution = build_event_preference_rows(
            [self.class_event, self.reservation],
            [legacy_key],
        )

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(is_enabled for _event, _key, is_enabled in rows))
        self.assertEqual(resolution.ambiguous_legacy_keys, frozenset({legacy_key}))

    def test_unique_legacy_selection_migrates_to_v2_on_save(self):
        legacy_key = legacy_event_identity(self.class_event)
        rows, resolution = build_event_preference_rows(
            [self.class_event, dict(self.class_event)],
            [legacy_key],
        )

        self.assertEqual(len(rows), 1)
        event, event_key, is_enabled = rows[0]
        self.assertEqual(event, self.class_event)
        self.assertEqual(event_key, event_identity_v2(self.class_event))
        self.assertFalse(is_enabled)
        self.assertEqual(resolution.ambiguous_legacy_keys, frozenset())
        self.assertEqual(
            ignored_v2_keys_from_selection({event_key: is_enabled}),
            [event_identity_v2(self.class_event)],
        )

    def test_save_helper_rejects_legacy_identity_keys(self):
        with self.assertRaisesRegex(SettingsError, "non-v2"):
            ignored_v2_keys_from_selection(
                {legacy_event_identity(self.class_event): False}
            )


class GuiPreferenceValidationTests(unittest.TestCase):
    def test_time_preference_defaults_are_canonical(self):
        self.assertEqual(
            validate_time_preferences_section({}),
            {
                "enabled": False,
                "preset": "afternoon_evening",
                "custom_start_hour": 14,
                "custom_start_min": 0,
                "custom_end_hour": 22,
                "custom_end_min": 0,
                "strict_mode": False,
            },
        )

    def test_time_preference_section_and_boolean_types_fail_closed(self):
        malformed_values = (
            {"time_preferences": []},
            {"time_preferences": {"enabled": "false"}},
            {"time_preferences": {"strict_mode": "true"}},
            {"time_preferences": {"enabled": 1}},
            {"time_preferences": {"preset": "surprise"}},
        )
        for settings in malformed_values:
            with self.subTest(settings=settings):
                with self.assertRaises(SettingsError):
                    validate_time_preferences_section(settings)

    def test_custom_clock_rejects_24_hour_end_and_invalid_ranges(self):
        malformed_values = (
            {"custom_end_hour": 24},
            {"custom_end_hour": "23"},
            {"custom_start_min": 60},
            {"custom_start_hour": True},
            {"custom_start_hour": 18, "custom_end_hour": 18},
            {
                "custom_start_hour": 18,
                "custom_start_min": 30,
                "custom_end_hour": 18,
                "custom_end_min": 15,
            },
        )
        for values in malformed_values:
            prefs = {
                "enabled": True,
                "preset": "custom",
                "strict_mode": True,
                **values,
            }
            with self.subTest(values=values):
                with self.assertRaises(SettingsError):
                    validate_time_preferences_section({"time_preferences": prefs})

    def test_booking_strategy_requires_an_object_and_real_boolean(self):
        for settings in (
            {"booking_strategy": []},
            {"booking_strategy": {"reverse_date_order": "false"}},
            {"booking_strategy": {"reverse_date_order": 1}},
        ):
            with self.subTest(settings=settings):
                with self.assertRaises(SettingsError):
                    validate_booking_strategy_section(settings)

        validated = validate_booking_strategy_section(
            {"booking_strategy": {"reverse_date_order": True}}
        )
        self.assertTrue(validated["reverse_date_order"])
        self.assertTrue(validated["daily_planning"]["enabled"])
        self.assertEqual(
            validated["daily_planning"]["preferred_peak_start"], "12:00"
        )


class GuiHistoryPersistenceTests(unittest.TestCase):
    def test_append_and_clear_share_lock_and_use_atomic_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "booking_history.json"
            append_history_entry({"id": 1}, path)
            append_history_entry({"id": 2}, path)

            self.assertEqual(
                [entry["id"] for entry in load_history_document(path)["runs"]],
                [2, 1],
            )
            self.assertTrue(path.with_suffix(".json.lock").exists())
            self.assertTrue(path.with_suffix(".json.bak").exists())

            replace_history_document({"runs": []}, path)
            self.assertEqual(load_history_document(path), {"runs": []})

    def test_append_does_not_overwrite_malformed_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "booking_history.json"
            malformed = '{"not_runs": []}\n'
            path.write_text(malformed, encoding="utf-8")

            with self.assertRaises(SettingsError):
                append_history_entry({"id": 1}, path)

            self.assertEqual(path.read_text(encoding="utf-8"), malformed)

    def test_history_is_capped_at_one_hundred_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "booking_history.json"
            replace_history_document(
                {"runs": [{"id": number} for number in range(100)]}, path
            )
            append_history_entry({"id": "new"}, path)
            runs = load_history_document(path)["runs"]
            self.assertEqual(len(runs), 100)
            self.assertEqual(runs[0]["id"], "new")
            self.assertNotIn(99, [entry["id"] for entry in runs[1:]])


if __name__ == "__main__":
    unittest.main()
