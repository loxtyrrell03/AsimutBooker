"""Exercise the real Settings controls with isolated persistence and no model."""
from contextlib import ExitStack
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import gui


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class DesktopSettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.settings = Path(folder) / 'settings.json'
        self.settings.write_text(json.dumps({'unrelated': {'keep': True}}))
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.stack.enter_context(patch('gui.SETTINGS_FILE', self.settings))
        for name in ('_initialize_assistant', 'refresh_status', '_scan_calendar_events',
                     '_refresh_booking_plan_display', 'load_room_catalog_cache'):
            self.stack.enter_context(patch.object(gui.AsimutBookerGUI, name))
        today = date.today()
        self.stack.enter_context(patch('gui.catalog_booking_dates', return_value=(today, today + timedelta(days=1))))
        self.ask = self.stack.enter_context(patch.object(gui.AsimutBookerGUI, '_quiet_ask'))
        self.send = self.stack.enter_context(patch.object(gui.AsimutBookerGUI, '_send_assistant_message'))
        self.schedule = self.stack.enter_context(patch.object(gui.AsimutBookerGUI, 'view_scheduled_tasks'))
        self.app = gui.AsimutBookerGUI(self.root)
        self.addCleanup(self.app.assistant_panel.close)
        self.app._select_quiet_page('settings')
        self.root.update()

    def test_controls_persist_directly_and_survive_navigation(self):
        app = self.app
        self.assertNotIn('Advanced preferences', [app.main_notebook.tab(tab, 'text') for tab in app.main_notebook.tabs()])
        app.assistant_panel.composer.insert('1.0', 'An existing draft')
        app.practice_plan_enable_cb.invoke()
        app.practice_default_hours.set('4')
        app.practice_default_spin.tk.call(app.practice_default_spin.cget('command'))
        app.time_prefs_enable_cb.invoke()
        app.time_prefs_dropdown.set('Afternoon (12:00-18:00)')
        app.time_prefs_dropdown.event_generate('<<ComboboxSelected>>')
        app.time_prefs_strict_cb.invoke()
        app.reverse_date_order_cb.invoke()
        self.root.update()
        saved = json.loads(self.settings.read_text())
        self.assertEqual(saved['practice_plan']['default_hours'], 4)
        self.assertTrue(saved['practice_plan']['enabled'])
        self.assertEqual(saved['time_preferences']['preset'], 'afternoon')
        self.assertTrue(saved['time_preferences']['strict_mode'])
        self.assertTrue(saved['booking_strategy']['reverse_date_order'])
        self.assertEqual(saved['unrelated'], {'keep': True})
        app._select_quiet_page('today')
        app.quiet_nav['settings'].invoke()
        self.assertEqual(app.main_notebook.select(), str(app.preferences_page))
        self.assertEqual(app.practice_default_hours.get(), '4')
        self.assertEqual(app.assistant_panel.composer.get('1.0', 'end-1c'), 'An existing draft')
        self.ask.assert_not_called()
        self.send.assert_not_called()

    def test_support_actions_stay_deterministic(self):
        buttons = {w.cget('text'): w for w in descendants(self.app.preferences_page)
                   if isinstance(w, ttk.Button)}
        buttons['Manage schedule'].invoke()
        self.schedule.assert_called_once()
        for name, target in (('System details', self.app.system_tab),
                             ('Activity and history', self.app.activity_tab)):
            buttons[name].invoke()
            self.root.update()
            self.assertEqual(self.app.main_notebook.select(), str(target))
            self.assertIn('selected', self.app.quiet_nav['settings'].state())
            self.app.quiet_nav['settings'].invoke()
        with patch('quiet_focus_gui.threading.Thread') as refresh:
            buttons['Refresh bookings'].invoke()
            refresh.assert_called_once_with(
                target=self.app._run_booker_thread,
                args=(True, ('--agenda-only', '--wait-for-runtime-seconds', '180'), 'Agenda refresh'),
                daemon=False,
            )
            refresh.return_value.start.assert_called_once()
        self.ask.assert_not_called()
        self.send.assert_not_called()

    def test_editors_open_real_forms_without_chat_or_writing_on_cancel(self):
        app = self.app
        original = self.settings.read_bytes()
        for control, title in (
            (app.practice_plan_customize_btn, 'Customize Practice Plan'),
            (app.room_preferences_btn, 'Room Preferences'),
            (app.booking_strategy_btn, 'Daily Booking Strategy'),
        ):
            with self.subTest(editor=title):
                control.invoke()
                self.root.update_idletasks()
                dialog = next(w for w in app._detail_pages.values() if w.title() == title)
                self.assertEqual(dialog.title(), title)
                buttons = [w for w in descendants(dialog) if isinstance(w, ttk.Button)]
                self.assertTrue(any(str(w.cget('text')).startswith('Save') for w in buttons))
                next(w for w in buttons if w.cget('text') == 'Cancel').invoke()
                self.assertEqual(app.main_notebook.select(), str(app.preferences_page))
        app.calendar_btn.invoke()
        self.root.update()
        self.assertEqual(app.main_notebook.select(), str(app.calendar_tab))
        self.assertGreater(len(app.calendar_frame.winfo_children()), 0)
        self.assertEqual(self.settings.read_bytes(), original)
        self.ask.assert_not_called()
        self.send.assert_not_called()

    def test_minimum_window_fits_controls_and_wheel_never_edits_values(self):
        # Map a fully transparent test window to exercise actual Tk geometry.
        self.root.attributes('-alpha', 0)
        self.root.deiconify()
        for width, height in ((1040, 740), (1200, 820)):
            with self.subTest(size=(width, height)):
                self.root.geometry(f'{width}x{height}')
                self.root.update()
                for widget in descendants(self.app.preferences_page):
                    if not isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Combobox, ttk.Spinbox)):
                        continue
                    if not widget.winfo_ismapped():
                        continue
                    self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth() - 1, str(widget))
                    right = widget.winfo_rootx() + widget.winfo_width()
                    self.assertLessEqual(right, self.root.winfo_rootx() + width)
                canvas = self.app.settings_scroll.canvas
                canvas.yview_moveto(0)
                # All six settings groups fit at both supported window sizes.
                self.assertEqual(canvas.yview(), (0.0, 1.0))
                for card, _, _ in self.app.settings_tiles.values():
                    self.assertLessEqual(card.winfo_rooty() + card.winfo_height(),
                                         canvas.winfo_rooty() + canvas.winfo_height())
                self.app.time_prefs_enabled.set(True)
                self.app.time_prefs_dropdown.set('Custom...')
                self.app._open_settings_group('Preferred time')
                self.app._update_time_prefs_ui_state()
                self.root.update()
                self.assertEqual(canvas.yview(), (0.0, 1.0))
                for entry in (self.app.custom_start_hour_entry, self.app.custom_end_min_entry):
                    self.assertGreaterEqual(entry.winfo_width(), entry.winfo_reqwidth() - 1)
                # Extra error/detail content may still need the scroll fallback.
                overflow = tk.Frame(self.app.settings_scroll.content, height=800)
                overflow.pack(fill=tk.X)
                self.root.update()
                before = self.settings.read_bytes()
                target = self.app.practice_default_hours.get()
                self.app.practice_default_spin.event_generate('<MouseWheel>', delta=-120)
                self.root.update()
                self.assertGreater(canvas.yview()[0], 0)
                self.assertEqual(self.app.practice_default_hours.get(), target)
                self.assertEqual(self.settings.read_bytes(), before)
                canvas.yview_moveto(1)
                self.root.update()
                self.assertAlmostEqual(canvas.yview()[1], 1.0)
                overflow.destroy()
                self.app.time_prefs_enabled.set(False)
                self.app._update_time_prefs_ui_state()
                self.app._show_settings_hub()
                canvas.yview_moveto(0)
                self.root.update()


if __name__ == '__main__':
    unittest.main()
