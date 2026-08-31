import tempfile
import unittest
from pathlib import Path
from unittest import mock

import assistant_tools
from app_settings import InterProcessFileLock
from assistant_tools import AssistantToolError, BookerToolSurface


class AssistantMutationLockTests(unittest.TestCase):
    def test_mutating_tools_are_serialized_across_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "assistant-mutation.lock"
            blocker = InterProcessFileLock(lock_path)
            blocker.acquire()
            surface = BookerToolSurface()
            with mock.patch.object(
                assistant_tools, "ASSISTANT_MUTATION_LOCK_FILE", lock_path
            ), mock.patch.object(
                surface, "_update_preferences", return_value={"updated": True}
            ) as handler:
                with self.assertRaisesRegex(
                    AssistantToolError, "Another assistant or Booker change has already started"
                ):
                    surface.dispatch("update_booker_preferences", {})
                handler.assert_not_called()
            blocker.release()

    def test_read_only_tools_do_not_wait_for_mutation_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "assistant-mutation.lock"
            blocker = InterProcessFileLock(lock_path)
            blocker.acquire()
            surface = BookerToolSurface()
            with mock.patch.object(
                assistant_tools, "ASSISTANT_MUTATION_LOCK_FILE", lock_path
            ), mock.patch.object(surface, "_get_context", return_value={"ok": True}):
                self.assertEqual(surface.dispatch("get_booker_context", {}), {"ok": True})
            blocker.release()


if __name__ == "__main__":
    unittest.main()
