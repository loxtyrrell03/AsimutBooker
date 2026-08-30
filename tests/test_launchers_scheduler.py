"""Static safety checks for the Windows launchers and scheduler installer."""

from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

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
            self.text.index('throw "The task was registered without the expected scheduled launcher action."'),
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
