"""Process-level single-instance coverage for the desktop control panel."""

import unittest
from unittest.mock import MagicMock, patch

import gui


class GuiSingleInstanceTests(unittest.TestCase):
    @patch("gui.tk.Tk")
    @patch("gui.SingleInstanceLock")
    def test_duplicate_launch_exits_before_creating_a_window(
        self, lock_type, tk_root
    ):
        lock = lock_type.return_value
        lock.acquire.return_value = False

        self.assertFalse(gui.main())

        lock_type.assert_called_once_with(gui.GUI_INSTANCE_LOCK_FILE)
        tk_root.assert_not_called()
        lock.release.assert_not_called()

    @patch("gui.AsimutBookerGUI")
    @patch("gui.tk.Tk")
    @patch("gui.SingleInstanceLock")
    def test_owned_lock_covers_the_complete_gui_lifetime(
        self, lock_type, tk_root, gui_type
    ):
        lock = lock_type.return_value
        lock.acquire.return_value = True
        root = tk_root.return_value
        events = []
        gui_type.side_effect = lambda _root: events.append("constructed") or MagicMock()
        root.mainloop.side_effect = lambda: events.append("mainloop")
        lock.release.side_effect = lambda: events.append("released")

        self.assertTrue(gui.main())

        gui_type.assert_called_once_with(root)
        root.mainloop.assert_called_once_with()
        self.assertEqual(events, ["constructed", "mainloop", "released"])

    @patch("gui.AsimutBookerGUI", side_effect=RuntimeError("startup failed"))
    @patch("gui.tk.Tk")
    @patch("gui.SingleInstanceLock")
    def test_startup_failure_still_releases_the_lock(
        self, lock_type, _tk_root, _gui_type
    ):
        lock = lock_type.return_value
        lock.acquire.return_value = True

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            gui.main()

        lock.release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
