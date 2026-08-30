"""Static safety checks for the Windows launchers and scheduler installer."""

from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from gui import AsimutBookerGUI, build_scheduler_remove_command


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_scheduled_launcher_requires_dot_venv_and_forces_headless(self):
        text = (ROOT / "run_booker.bat").read_text(encoding="utf-8")
        self.assertIn(r'.venv\Scripts\python.exe', text)
        self.assertIn("book_week.py --headless %*", text)
        self.assertNotIn(r"venv\Scripts\activate.bat", text)
        self.assertNotIn('set "PYTHON_EXE=python.exe"', text)
        self.assertIn("import playwright, yaml, book_week", text)
        self.assertLess(
            text.index("import playwright, yaml, book_week"),
            text.index("book_week.py --headless %*"),
        )

    def test_scheduled_launcher_preserves_exit_code_and_utf8(self):
        text = (ROOT / "run_booker.bat").read_text(encoding="utf-8")
        command_index = text.index("book_week.py --headless %*")
        capture_index = text.index('set "EXIT_CODE=%ERRORLEVEL%"')
        self.assertLess(command_index, capture_index)
        self.assertIn("exit /b %EXIT_CODE%", text)
        self.assertIn('set "PYTHONUTF8=1"', text)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', text)
        self.assertIn("chcp 65001", text)

    def test_gui_launcher_prefers_dot_venv_pythonw(self):
        text = (ROOT / "AsimutBooker.bat").read_text(encoding="utf-8")
        self.assertIn(r'.venv\Scripts\pythonw.exe', text)
        self.assertIn(r'.venv\Scripts\python.exe', text)
        self.assertIn(r'"%~dp0gui.py"', text)
        self.assertNotIn(r"venv\Scripts\activate.bat", text)


class SchedulerInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "setup_scheduled_tasks.ps1").read_text(encoding="utf-8")

    def test_installs_one_gui_detectable_recurring_task(self):
        self.assertIn('$TaskName = "AsimutBooker_Recurring"', self.text)
        self.assertEqual(len(re.findall(r"Register-ScheduledTask\s+`", self.text)), 1)
        self.assertIn('$Trigger.Repetition = $Repetition', self.text)
        self.assertIn('ClassName "MSFT_TaskRepetitionPattern"', self.text)
        self.assertIn('Interval = "PT${RepeatMinutes}M"', self.text)
        self.assertIn('$RepeatDurationIso = "PT14H46M"', self.text)

    def test_action_uses_headless_scheduled_launcher_mode(self):
        self.assertIn("--scheduled", self.text)
        self.assertIn("run_booker.bat", self.text)
        self.assertIn("-MultipleInstances IgnoreNew", self.text)

    def test_replaces_recurring_before_removing_only_known_legacy_tasks(self):
        self.assertIn('$_.TaskPath -eq "\\"', self.text)
        self.assertIn('$_.TaskName -eq "AsimutBooker"', self.text)
        self.assertNotIn('$_.TaskName -eq $TaskName', self.text)
        self.assertIn("'^AsimutBooker_[0-2][0-9][0-5][0-9]$'", self.text)
        self.assertIn("'^AsimutBooker-[0-2][0-9][0-5][0-9]$'", self.text)
        self.assertNotIn('$_.TaskName -like "AsimutBooker_*"', self.text)
        self.assertIn("-TaskName $legacyTask.TaskName", self.text)
        self.assertIn("-TaskPath $legacyTask.TaskPath", self.text)
        self.assertIn("-Force |", self.text)
        self.assertLess(
            self.text.index('throw "The task was registered without the exact scheduled launcher contract."'),
            self.text.index('$legacyTasks = @('),
        )

    def test_requires_the_isolated_runtime_before_registration(self):
        self.assertIn('.venv\\Scripts\\python.exe', self.text)
        self.assertIn("import playwright, yaml, book_week", self.text)
        self.assertLess(
            self.text.index("import playwright, yaml, book_week"),
            self.text.index("Register-ScheduledTask"),
        )

    def test_wake_recovery_and_noninteractive_setup_are_explicit(self):
        self.assertIn("-WakeToRun", self.text)
        self.assertIn("-StartWhenAvailable", self.text)
        self.assertIn("-RunOnlyIfNetworkAvailable", self.text)
        self.assertIn("/setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1", self.text)
        self.assertIn("/setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1", self.text)
        self.assertLess(
            self.text.index("/setdcvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1"),
            self.text.index("Register-ScheduledTask"),
        )
        self.assertNotIn("ReadKey", self.text)

    def test_registered_task_readback_verifies_exact_action_and_trigger(self):
        required_checks = (
            "$RegisteredTask.Settings.Enabled",
            '[string]$RegisteredTask.State -cne $ExpectedState',
            "@($RegisteredTask.Actions).Count -ne 1",
            "$RegisteredAction.Execute -ine $CmdPath",
            "$RegisteredAction.Arguments -cne $ActionArguments",
            "$RegisteredAction.WorkingDirectory -ine $WorkingDir",
            "@($RegisteredTask.Triggers).Count -ne 1",
            '$RegisteredTrigger.CimClass.CimClassName -ne "MSFT_TaskDailyTrigger"',
            "$RegisteredTrigger.Enabled",
            "$RegisteredTrigger.DaysInterval -ne 1",
            '$RegisteredTrigger.Repetition.Interval -ne "PT15M"',
            "$RegisteredTrigger.Repetition.Duration -ne $RepeatDurationIso",
            "$RegisteredTrigger.Repetition.StopAtDurationEnd",
            "$RegisteredTrigger.StartBoundary",
            "$RegisteredLocalTime -ne $FirstRunTime",
        )
        for check in required_checks:
            with self.subTest(check=check):
                self.assertIn(check, self.text)

        verification_start = self.text.index("function Assert-RegisteredTaskContract")
        legacy_removal = self.text.index("$legacyTasks = @(")
        for check in required_checks:
            self.assertLess(verification_start, self.text.index(check))
            self.assertLess(self.text.index(check), legacy_removal)

    def test_registered_task_readback_verifies_all_runtime_and_principal_settings(self):
        required_checks = (
            "$RegisteredTask.Settings.WakeToRun",
            "$RegisteredTask.Settings.StartWhenAvailable",
            "$RegisteredTask.Settings.RunOnlyIfNetworkAvailable",
            "$RegisteredTask.Settings.DisallowStartIfOnBatteries",
            "$RegisteredTask.Settings.StopIfGoingOnBatteries",
            '$RegisteredTask.Settings.MultipleInstances -ne "IgnoreNew"',
            '$RegisteredTask.Settings.ExecutionTimeLimit -ne "PT14M"',
            "$RegisteredTask.Settings.RestartCount -ne 1",
            '$RegisteredTask.Settings.RestartInterval -ne "PT1M"',
            "$CurrentUserSid = $CurrentIdentity.User.Value",
            "$RegisteredPrincipalName.Translate(",
            "$RegisteredUserSid -ne $CurrentUserSid",
            '[string]$RegisteredTask.Principal.LogonType -ne "Interactive"',
            '[string]$RegisteredTask.Principal.RunLevel -ne "Limited"',
        )
        for check in required_checks:
            with self.subTest(check=check):
                self.assertIn(check, self.text)

    def test_replacement_is_fully_verified_while_disabled_before_enable(self):
        settings = self.text.index("$Settings = New-ScheduledTaskSettingsSet")
        disabled_setting = self.text.index("-Disable `", settings)
        registration = self.text.index("Register-ScheduledTask `")
        disabled_readback = self.text.index("-ExpectedEnabled $false", registration)
        disabled_state = self.text.index('-ExpectedState "Disabled"', disabled_readback)
        enable = self.text.index("Enable-ScheduledTask `", disabled_state)
        enabled_readback = self.text.index("-ExpectedEnabled $true", enable)
        ready_state = self.text.index('-ExpectedState "Ready"', enabled_readback)

        self.assertLess(settings, disabled_setting)
        self.assertLess(disabled_setting, registration)
        self.assertLess(registration, disabled_readback)
        self.assertLess(disabled_readback, disabled_state)
        self.assertLess(disabled_state, enable)
        self.assertLess(enable, enabled_readback)
        self.assertLess(enabled_readback, ready_state)

    def test_failure_path_removes_unverified_task_and_restores_safely(self):
        registration = self.text.index("Register-ScheduledTask `")
        transaction_catch = self.text.index("} catch {", registration)
        cleanup_call = self.text.index(
            "Remove-ReplacementTaskFailClosed", transaction_catch
        )
        legacy_removal = self.text.index("$legacyTasks = @(", registration)

        self.assertLess(registration, legacy_removal)
        self.assertLess(legacy_removal, transaction_catch)
        self.assertLess(transaction_catch, cleanup_call)
        self.assertIn("function Remove-ReplacementTaskFailClosed", self.text)
        self.assertIn("Disable-ScheduledTask -TaskName $TaskName", self.text)
        self.assertIn("Unregister-ScheduledTask `", self.text)
        self.assertIn("$RemainingTask = Get-ReplacementTaskExact", self.text)
        self.assertIn(
            'throw "The unverified replacement task could not be disabled and removed."',
            self.text,
        )

        self.assertIn("$PreviousTaskXml = Export-ScheduledTask `", self.text)
        self.assertIn("ConvertTo-DisabledTaskXml", self.text)
        self.assertIn("-Xml $PreviousTaskDisabledXml", self.text)
        self.assertIn("$PreviousTaskCanBeReenabled", self.text)
        self.assertIn(
            '[string]$RestoredTask.State -cne "Disabled"', self.text
        )

    def test_rollback_xml_inserts_missing_enabled_element_in_task_namespace(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if powershell is None:
            self.skipTest("Windows PowerShell is required for this installer test")

        function_start = self.text.index("function ConvertTo-DisabledTaskXml")
        function_end = self.text.index(
            "function Get-ReplacementTaskExact", function_start
        )
        function_source = self.text[function_start:function_end]
        task_namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
        fixture = (
            f'<Task xmlns="{task_namespace}">'
            "<Settings><WakeToRun>true</WakeToRun><Hidden>false</Hidden></Settings>"
            "</Task>"
        )
        command = (
            '$ErrorActionPreference = "Stop"\n'
            f"{function_source}\n"
            "$TaskXml = @'\n"
            f"{fixture}\n"
            "'@\n"
            "ConvertTo-DisabledTaskXml -TaskXml $TaskXml"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        root = ET.fromstring(result.stdout.strip())
        settings = root.find(f"{{{task_namespace}}}Settings")
        self.assertIsNotNone(settings)
        enabled = settings.find(f"{{{task_namespace}}}Enabled")
        self.assertIsNotNone(enabled)
        self.assertEqual(enabled.text, "false")
        self.assertEqual(
            len(settings.findall(f"{{{task_namespace}}}Enabled")),
            1,
        )

    def test_task_enumeration_errors_are_not_treated_as_absence(self):
        exact_query_start = self.text.index("function Get-ReplacementTaskExact")
        exact_query_end = self.text.index(
            "function Remove-ReplacementTaskFailClosed", exact_query_start
        )
        exact_query = self.text[exact_query_start:exact_query_end]
        self.assertIn("Get-ScheduledTask -ErrorAction Stop", exact_query)
        self.assertNotIn("SilentlyContinue", exact_query)

        transaction_start = self.text.index("$ReplacementRegistrationStarted = $false")
        transaction_end = self.text.index('Write-Host "Created: $TaskName"')
        transaction = self.text[transaction_start:transaction_end]
        self.assertNotIn("Get-ScheduledTask -ErrorAction SilentlyContinue", transaction)


class GuiSchedulerSafetyTests(unittest.TestCase):
    def test_remove_command_is_elevated_idempotent_and_verifies_absence(self):
        command = build_scheduler_remove_command(
            local_app_data=Path(r"C:\Users\Test\AppData\Local")
        )
        self.assertEqual(
            command[:5],
            [
                r"C:\Users\Test\AppData\Local\AgentUAC\agent-uac.exe",
                "run",
                "--reason",
                "Remove and verify the AsimutBooker recurring scheduled task",
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
                "-Command",
            ],
        )
        script = command[-1]
        self.assertIn("if ($null -ne $task)", script)
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertIn("if ($null -ne $remaining)", script)
        self.assertNotIn("schtasks.exe", script)

    def test_status_refresh_does_not_query_on_the_tk_caller_thread(self):
        gui = object.__new__(AsimutBookerGUI)
        gui._schedule_status_generation = 0
        gui.tasks_status_var = Mock()
        started_threads = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        with (
            patch("gui.threading.Thread", DeferredThread),
            patch("gui.query_recurring_task_status") as query,
        ):
            gui.check_scheduled_tasks()

        query.assert_not_called()
        self.assertEqual(len(started_threads), 1)
        self.assertTrue(started_threads[0].daemon)
        gui.tasks_status_var.set.assert_called_once_with("⏳ Checking schedule...")

    def test_install_completion_only_starts_an_async_status_refresh(self):
        gui = object.__new__(AsimutBookerGUI)
        gui.schedule_setup_in_progress = True
        gui._start_scheduler_status_refresh = Mock()
        gui.log = Mock()
        result = Mock(returncode=0, stdout="installed", stderr="")

        with patch("gui.messagebox.showinfo"):
            gui._finish_schedule_setup(result, None)

        self.assertFalse(gui.schedule_setup_in_progress)
        gui._start_scheduler_status_refresh.assert_called_once_with()

    def test_remove_starts_a_daemon_worker_before_running_elevated_command(self):
        gui = object.__new__(AsimutBookerGUI)
        gui.schedule_remove_in_progress = False
        gui.schedule_setup_in_progress = False
        gui.log = Mock()
        started_threads = []

        class DeferredThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                started_threads.append(self)

        with (
            patch("gui.messagebox.askyesno", return_value=True),
            patch(
                "gui.build_scheduler_remove_command",
                return_value=[str(ROOT / "gui.py"), "unused"],
            ),
            patch("gui.threading.Thread", DeferredThread),
            patch("gui.subprocess.run") as run,
        ):
            gui._delete_scheduled_task(parent_dialog=Mock())

        run.assert_not_called()
        self.assertTrue(gui.schedule_remove_in_progress)
        self.assertEqual(len(started_threads), 1)
        self.assertTrue(started_threads[0].daemon)


if __name__ == "__main__":
    unittest.main()
