"""Isolated real-widget checks for the System details page."""
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from tests import test_desktop_settings as settings_fixture


class SystemDetailsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = settings_fixture.DesktopSettingsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app, self.root = self.fixture.app, self.fixture.root
        self.app.main_notebook.select(self.app.system_tab)
        self.root.attributes('-alpha', 0)
        self.root.deiconify()
        self.root.update()

    def test_layout_with_long_status_and_expanded_tools(self):
        self.app.health_headline_vars['last_success'].set(
            'Last successful run: 8 September 2026, 18:30 British Summer Time')
        self.app.automation_detail_var.set('Automatic booking is scheduled and its safety checks are clear. '
                                           'Wake from sleep has not yet been physically proven.')
        self.app.system_troubleshooting_toggle.invoke()
        for width, height in ((1040, 740), (1200, 820)):
            self.root.geometry(f'{width}x{height}')
            self.root.update()
            for widget in settings_fixture.descendants(self.app.system_details_body):
                if isinstance(widget, ttk.Button):
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth() - 1)
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                     self.root.winfo_rootx() + width)
            canvas = self.app.system_details_body.master
            canvas.yview_moveto(1)
            self.root.update()
            self.assertAlmostEqual(canvas.yview()[1], 1)

    def test_actions_and_disclosure(self):
        buttons = {w.cget('text') for w in settings_fixture.descendants(self.app.system_details_body)
                   if isinstance(w, ttk.Button)}
        self.assertFalse({'Open Calendar', 'Open Activity', 'Automatic Schedule'} & buttons)
        self.assertFalse(self.app.login_check_btn.winfo_ismapped())
        self.app.system_troubleshooting_toggle.invoke()
        self.root.update()
        self.assertTrue(self.app.login_check_btn.winfo_ismapped())
        with patch.object(self.app, 'run_booker') as run:
            self.app.run_headless_btn.invoke()
            run.assert_called_once_with(headless=True)
            run.reset_mock()
            self.app.run_visible_btn.invoke()
            run.assert_called_once_with(headless=False)
        self.assertIn('disabled', self.app.stop_btn.state())
        self.app.progress_var.set('Checking availability…')
        self.root.update()
        self.assertTrue(self.app.progress_label.winfo_ismapped())
        self.app.progress_var.set('')
        self.root.update()
        self.assertFalse(self.app.progress_label.winfo_ismapped())


if __name__ == '__main__':
    unittest.main()
