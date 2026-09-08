import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_chat import CodexChatController, CodexConfigurationError, resolve_codex_executable


class CodexDiscoveryTests(unittest.TestCase):
    def test_path_takes_precedence(self):
        with patch("codex_chat.shutil.which", return_value="installed-codex"):
            self.assertEqual(resolve_codex_executable(), "installed-codex")

    @unittest.skipUnless(os.name == "nt", "Windows desktop installation")
    def test_missing_path_finds_newest_release_and_ignores_incomplete_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "OpenAI" / "Codex" / "bin"
            for name, timestamp in (("zzz-old", 100), ("aaa-new", 200)):
                executable = root / name / "codex.exe"
                executable.parent.mkdir(parents=True)
                executable.touch()
                os.utime(executable, (timestamp, timestamp))
            (root / "incomplete" / "codex.exe").mkdir(parents=True)
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch(
                "codex_chat.shutil.which", return_value=None
            ):
                controller = CodexChatController(None)
                self.assertEqual(controller._process_command,
                                 (str(root / "aaa-new" / "codex.exe"), "app-server", "--stdio"))

    def test_missing_installation_explains_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch(
                "codex_chat.shutil.which", return_value=None
            ):
                with self.assertRaisesRegex(CodexConfigurationError, "Install Codex"):
                    resolve_codex_executable()

    def test_explicit_command_does_not_require_discovery(self):
        with patch("codex_chat.resolve_codex_executable", side_effect=AssertionError):
            self.assertEqual(CodexChatController(None, codex_executable="custom")._process_command[0], "custom")
            self.assertEqual(CodexChatController(None, process_command=("test-server",))._process_command, ("test-server",))
