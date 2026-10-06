import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from quiet_focus_gui import QuietFocusGUI


class PauseGUITests(unittest.TestCase):
    def test_shared_state_updates_button_at_narrow_and_desktop_widths(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        app = QuietFocusGUI()
        app.root, app.ui_font_family = root, 'Segoe UI'
        state = {'paused': False, 'revision': 'a' * 64}
        with patch('booking_pause.read_pause', side_effect=lambda: state):
            app._create_quiet_shell()
            for width in (760, 1040):
                root.geometry(f'{width}x740')
                root.update()
                self.assertEqual(app.booking_pause_button.cget('text'), 'Pause bookings')
                self.assertLess(app.booking_pause_button.winfo_reqwidth(), width // 2)
            state = {'paused': True, 'revision': 'b' * 64}
            app._poll_booking_pause()
            self.assertEqual(app.booking_pause_button.cget('text'), 'Unpause bookings')
            self.assertIn('paused', app.booking_pause_label.cget('text'))
            with patch('booking_pause.read_pause', side_effect=ValueError('unavailable')):
                app._poll_booking_pause()
                self.assertIn('disabled', app.booking_pause_button.state())
