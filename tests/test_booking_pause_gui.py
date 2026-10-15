from pathlib import Path
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from quiet_focus_gui import QuietFocusGUI
from app_settings import atomic_write_json
from booking_pause import read_pause, save_pause


class PauseGUITests(unittest.TestCase):
    def test_repeated_status_reads_keep_only_one_poll_and_destroy_cancels_it(self):
        root = tk.Tk(); root.withdraw()
        app = QuietFocusGUI()
        app.root, app.ui_font_family = root, 'Segoe UI'
        def pause_timers():
            return [key for key in root.tk.call('after', 'info')
                    if '_poll_booking_pause' in str(root.tk.call('after', 'info', key))]
        try:
            with patch('booking_pause.read_pause', return_value={'paused': False, 'revision': 'a' * 64}):
                app._create_quiet_shell()
                root.update()
                app._poll_booking_pause()
                app._poll_booking_pause()
                self.assertEqual(len(pause_timers()), 1)
        finally:
            root.destroy()
        self.assertEqual(pause_timers(), [])

    def test_destroy_removes_pending_pause_callback(self):
        root = tk.Tk(); root.withdraw()
        app = QuietFocusGUI()
        app.root, app.ui_font_family = root, 'Segoe UI'
        with patch('booking_pause.read_pause', return_value={'paused': False, 'revision': 'a' * 64}):
            app._create_quiet_shell()
            root.update()
            ids = list(root.tk.call('after', 'info'))
            root.destroy()
        remaining = root.tk.call('after', 'info')
        self.assertFalse(set(ids) & set(remaining))

    def test_closing_window_does_not_abandon_the_requested_pause_save(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            atomic_write_json(path, {'unrelated': {'keep': True}})
            script = '''
import sys, time, tkinter as tk
from pathlib import Path
from unittest.mock import patch
from booking_pause import read_pause, save_pause
from quiet_focus_gui import QuietFocusGUI
path=Path(sys.argv[1])
root=tk.Tk(); root.withdraw()
app=QuietFocusGUI(); app.root=root; app.ui_font_family='Segoe UI'
app.log=lambda *args:None
def delayed_save(args):
    time.sleep(.3)
    return save_pause(args,path)
with patch('booking_pause.read_pause',side_effect=lambda:read_pause(path)), patch('booking_pause.save_pause',side_effect=delayed_save):
    app._create_quiet_shell(); root.update()
    app._save_booking_pause()
    app._closing=True
    root.destroy()
'''
            subprocess.run([sys.executable, '-c', script, str(path)],
                           cwd=Path(__file__).resolve().parents[1], check=True,
                           capture_output=True, text=True, timeout=10)
            self.assertTrue(read_pause(path)['paused'])

    def test_save_and_lost_reply_are_applied_on_ui_thread_without_replaying(self):
        for lost_reply in (False, True):
            with self.subTest(lost_reply=lost_reply), TemporaryDirectory() as folder:
                path = Path(folder) / 'settings.json'
                atomic_write_json(path, {'unrelated': {'keep': True}})
                root = tk.Tk(); root.withdraw()
                app = QuietFocusGUI()
                app.root, app.ui_font_family, app.log = root, 'Segoe UI', Mock()
                main_thread = threading.get_ident()
                tk_call_threads = []
                def writer(args):
                    result = save_pause(args, path)
                    if lost_reply:
                        raise OSError('Synthetic lost reply after persistence')
                    return result
                try:
                    with patch('booking_pause.read_pause', side_effect=lambda: read_pause(path)), \
                            patch('booking_pause.save_pause', side_effect=writer) as save:
                        app._create_quiet_shell(); root.update()
                        actual_after = root.after
                        def checked_after(*args):
                            tk_call_threads.append(threading.get_ident())
                            return actual_after(*args)
                        with patch.object(root, 'after', side_effect=checked_after):
                            app.booking_pause_button.invoke()
                            app._save_booking_pause()  # Duplicate click while the save is pending.
                            def settled():
                                if app._booking_pause_working:
                                    root.after(20, settled)
                                else:
                                    root.quit()
                            root.after(20, settled)
                            timeout = root.after(3000, root.quit)
                            root.mainloop()
                            root.after_cancel(timeout)
                        self.assertFalse(app._booking_pause_working)
                        self.assertEqual(set(tk_call_threads), {main_thread})
                        if lost_reply:
                            self.assertIn('unconfirmed', app.booking_pause_label.cget('text'))
                            self.assertIn('disabled', app.booking_pause_button.state())
                            app._poll_booking_pause()  # Recover through a read, not another save.
                        self.assertTrue(app._booking_pause_state['paused'])
                        self.assertEqual(app.booking_pause_button.cget('text'), 'Unpause bookings')
                        save.assert_called_once()
                        self.assertTrue(read_pause(path)['paused'])
                finally:
                    root.destroy()

    def test_launch_failure_recovers_by_reading_without_a_save(self):
        root = tk.Tk(); root.withdraw()
        app = QuietFocusGUI()
        app.root, app.ui_font_family, app.log = root, 'Segoe UI', Mock()
        try:
            with patch('booking_pause.read_pause', return_value={'paused': False, 'revision': 'a' * 64}), \
                    patch('booking_pause.save_pause') as save, \
                    patch('quiet_focus_gui.threading.Thread.start', side_effect=RuntimeError('Synthetic launch failure')):
                app._create_quiet_shell(); root.update()
                app._save_booking_pause()
                app._poll_booking_pause()
                self.assertFalse(app._booking_pause_working)
                self.assertIn('unconfirmed', app.booking_pause_label.cget('text'))
                app._poll_booking_pause()
                self.assertEqual(app.booking_pause_button.cget('text'), 'Pause bookings')
                self.assertNotIn('disabled', app.booking_pause_button.state())
                save.assert_not_called()
        finally:
            root.destroy()

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
