import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app_settings import SettingsError
from gui import (
    AsimutBookerGUI,
    PRACTICE_TARGET_CHOICES,
    apply_booking_day_updates,
    apply_practice_plan_update,
    append_history_entry,
    build_event_preference_rows,
    build_login_check_command,
    build_scheduler_setup_command,
    build_secure_login_update_command,
    format_practice_hours,
    load_history_document,
    ignored_v2_keys_from_selection,
    is_exact_asimut_scan_url,
    parse_recurring_task_status,
    parse_practice_target_choice,
    query_recurring_task_status,
    require_authenticated_scan_page,
    replace_history_document,
    validate_login_command,
    validate_booking_strategy_section,
    validate_time_preferences_section,
)
from event_identity import event_identity_v2, legacy_event_identity
from practice_plan import PracticePlan, PracticePlanError


class GuiSchedulerHelpersTests(unittest.TestCase):
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
        healthy_payload = {
            "TaskName": "AsimutBooker_Recurring",
            "State": "Ready",
            "NextRunTime": "2026-08-30 12:13",
            "Execute": r"C:\Windows\System32\cmd.exe",
            "Arguments": r'/d /c ""C:\repo\run_booker.bat" --scheduled"',
            "Interval": "PT15M",
            "Duration": "PT14H46M",
            "WakeToRun": True,
            "StartWhenAvailable": True,
            "MultipleInstances": "IgnoreNew",
        }
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
            patch("book_week.safe_goto", side_effect=RuntimeError("session recovery failed")) as safe_goto,
            patch("book_week.page_is_authenticated", return_value=False),
            patch("book_week.persist_storage_state") as persist,
        ):
            gui._scan_calendar_events_thread(7)

        options.assert_called_once_with()
        restore.assert_called_once_with(page)
        safe_goto.assert_called_once_with(page, "https://rwcmd.asimut.net/agenda")
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

        self.assertEqual(
            validate_booking_strategy_section(
                {"booking_strategy": {"reverse_date_order": True}}
            ),
            {"reverse_date_order": True},
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
