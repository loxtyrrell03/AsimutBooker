import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from codex_chat import (
    CodexChatController, CodexConfigurationError, CodexConnectionError,
    resolve_codex_executable,
)
from phone_server import PhoneServerError, load_phone_server_config


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


@unittest.skipUnless(os.name == "nt", "Windows desktop installation")
class DesktopUpdateRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "OpenAI" / "Codex" / "bin"
        self.old = self.release("old", 100)
        self.new = self.release("new", 200)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path_lookup = patch("codex_chat.shutil.which", side_effect=AssertionError("No PATH fallback for an explicit executable"))
        self.path_lookup.start()
        self.addCleanup(self.path_lookup.stop)

    def release(self, name, timestamp):
        path = self.bin / name / "codex.exe"
        path.parent.mkdir(parents=True)
        path.touch()
        os.utime(path, (timestamp, timestamp))
        return path

    def config(self, executable):
        path = self.root / "phone.json"
        path.write_text(json.dumps({
            "version": 2, "allowed_login": "owner@example.test",
            "public_origin": "https://lox-pc.tail.test:10443",
            "host": "127.0.0.1", "port": 8794,
            "codex_executable": str(executable),
        }), encoding="utf-8")
        return path

    def test_expired_phone_pin_uses_installed_release_without_rewriting_config(self):
        path = self.config(self.old)
        before = path.read_bytes()
        self.old.unlink()
        (self.bin / "incomplete" / "codex.exe").mkdir(parents=True)
        self.assertEqual(load_phone_server_config(path).codex_executable, str(self.new))
        self.assertEqual(path.read_bytes(), before)

    def test_existing_phone_pin_is_preserved_even_with_newer_release(self):
        self.assertEqual(load_phone_server_config(self.config(self.old)).codex_executable, str(self.old))

    def test_missing_custom_or_nested_path_is_not_replaced(self):
        for path in (self.root / "custom" / "codex.exe", self.bin / "old" / "nested" / "codex.exe"):
            with self.subTest(path=path), self.assertRaises(PhoneServerError):
                load_phone_server_config(self.config(path))

    def test_missing_installation_is_rejected(self):
        self.old.unlink()
        self.new.unlink()
        with self.assertRaises(PhoneServerError):
            load_phone_server_config(self.config(self.old))

    def test_directory_at_explicit_executable_is_rejected(self):
        self.old.unlink()
        self.old.mkdir()
        with self.assertRaises(PhoneServerError):
            load_phone_server_config(self.config(self.old))

    async def test_update_between_controller_creation_and_start_uses_current_release_once(self):
        controller = CodexChatController(None, codex_executable=self.old)
        self.old.unlink()
        create = AsyncMock(side_effect=FileNotFoundError("synthetic launch failure"))
        with patch("codex_chat.asyncio.create_subprocess_exec", create):
            with self.assertRaises(CodexConnectionError):
                await controller.start()
        self.assertEqual(create.call_args.args, (str(self.new), "app-server", "--stdio"))
        create.assert_awaited_once()

    async def test_explicit_process_command_is_never_rewritten(self):
        controller = CodexChatController(None, process_command=(str(self.old), "test-server"))
        self.old.unlink()
        create = AsyncMock(side_effect=FileNotFoundError("synthetic launch failure"))
        with patch("codex_chat.asyncio.create_subprocess_exec", create):
            with self.assertRaises(CodexConnectionError):
                await controller.start()
        self.assertEqual(create.call_args.args, (str(self.old), "test-server"))
        create.assert_awaited_once()
