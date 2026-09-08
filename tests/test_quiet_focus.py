"""Quiet Focus display semantics and isolated desktop navigation."""
from datetime import datetime, timedelta, timezone
import tkinter as tk
import unittest
from unittest.mock import patch

import gui
from quiet_focus import display_summary


def event(day, start, end, reservation=True):
    return dict(date=day, startTime=start, endTime=end, isReservation=reservation,
                room='B0.29', title='Reservation' if reservation else 'Class')


class QuietFocusTests(unittest.TestCase):
    def test_status_reload_preserves_calendar_edits_and_merges_unedited_dates(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = object.__new__(gui.AsimutBookerGUI)
            today = datetime.now().date()
            live = today.isoformat()
            other = (today + timedelta(days=1)).isoformat()
            future = (today + timedelta(days=45)).isoformat()
            app.room_catalog = None
            app.day_vars = {key: tk.BooleanVar(value=True) for key in (live, other, future)}
            original_variables = dict(app.day_vars)
            app.calendar_day_snapshot = {key: True for key in app.day_vars}
            app.day_vars[live].set(False)
            app.day_vars[future].set(False)
            with patch.object(app, 'load_settings', return_value={'disabled_dates': [other]}), \
                 patch('gui.catalog_booking_dates', return_value=(today, today + timedelta(days=1))):
                app.load_booking_days()
                self.assertFalse(app.day_vars[live].get(), 'Refresh discarded the edited live date')
                self.assertFalse(app.day_vars[future].get(), 'Refresh discarded the future date')
                self.assertFalse(app.day_vars[other].get(), 'Unedited date did not follow the saved settings')
                self.assertIs(app.day_vars[live], original_variables[live])
                self.assertFalse(app.calendar_day_snapshot[other])
                self.assertTrue(app.calendar_day_snapshot[live])
                self.assertEqual(app.booking_dates, (today, today + timedelta(days=1)))
                app.load_booking_days(preserve_calendar_edits=False)
                self.assertTrue(app.day_vars[live].get())
                self.assertNotIn(future, app.day_vars)
        finally:
            root.destroy()

    def test_failed_process_launch_reports_error_after_exception_scope_ends(self):
        app = object.__new__(gui.AsimutBookerGUI)
        callbacks, errors = [], []
        from unittest.mock import Mock
        app.root = Mock()
        app.root.after.side_effect = lambda delay, callback: callbacks.append(callback)
        app.log = lambda message, tag: errors.append((message, tag))
        app._on_booker_finished = Mock()
        with patch('gui.subprocess.Popen', side_effect=OSError('Launch unavailable')):
            app._run_booker_thread(True)
        for callback in callbacks:
            callback()
        self.assertIn('Launch unavailable', errors[0][0])
        app._on_booker_finished.assert_called_once()

    def test_active_reservation_and_week_total_exclude_classes_and_other_weeks(self):
        events = [event('2026-09-08', '08:00', '09:00'),
                  event('2026-09-08', '10:00', '12:00'),
                  event('2026-09-08', '12:00', '14:00', False),
                  event('2026-09-14', '14:00', '16:00')]
        view = display_summary(events, datetime(2026, 9, 8, 10, tzinfo=timezone.utc))
        self.assertIs(view['next'], events[1])
        self.assertTrue(view['in_progress'])
        self.assertEqual(view['week_minutes'], 180)
        self.assertEqual(view['also_today'], [events[2]])

    def test_london_midnight_and_empty_agenda(self):
        view = display_summary([], datetime(2026, 9, 7, 23, 30, tzinfo=timezone.utc))
        self.assertEqual(view['today'], '2026-09-08')
        self.assertIsNone(view['next'])
        self.assertEqual(view['week_minutes'], 0)

    def test_desktop_navigation_and_drafts_do_not_send_or_replace_user_text(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(gui.AsimutBookerGUI, '_initialize_assistant'), \
                 patch.object(gui.AsimutBookerGUI, 'refresh_status'), \
                 patch.object(gui.AsimutBookerGUI, 'load_settings', return_value={}):
                app = gui.AsimutBookerGUI(root)
                root.update_idletasks()
                self.assertEqual(app.main_notebook.select(), str(app.today_tab))
                with patch.object(app, '_refresh_quiet_views'), \
                     patch.object(app, '_send_assistant_message') as send:
                    with patch.object(app, '_scan_calendar_events') as scan:
                        # Native tab clicks and keyboard navigation select the
                        # notebook directly; they bypass the sidebar command.
                        app.main_notebook.select(app.calendar_tab)
                        root.update()
                        self.assertGreater(len(app.calendar_frame.winfo_children()), 0)
                        self.assertEqual(app.calendar_canvas.winfo_manager(), 'pack')
                        scan.assert_called_once()
                        app.main_notebook.select(app.today_tab)
                        root.update()
                        app.main_notebook.select(app.calendar_tab)
                        root.update()
                        scan.assert_called_once()
                    self.assertEqual(app.main_notebook.select(), str(app.calendar_tab))
                    self.assertIs(app.calendar_dialog, app.calendar_tab)
                    self.assertIsNone(root.grab_current())
                    day = next(iter(app.day_vars))
                    original = app.day_vars[day].get()
                    app.day_vars[day].set(not original)
                    app._select_quiet_page('today')
                    app._select_quiet_page('calendar')
                    self.assertEqual(app.day_vars[day].get(), not original)
                    with patch.object(app, 'save_booking_days', return_value=True) as save:
                        app._save_calendar_and_close(app.calendar_tab, app.calendar_day_snapshot)
                    save.assert_called_once_with({day: not original})
                    self.assertTrue(app.calendar_tab.winfo_exists())
                    self.assertEqual(app.calendar_day_snapshot[day], not original)
                    for name in ('week', 'settings', 'today'):
                        app._select_quiet_page(name)
                    app._quiet_ask('Find a room tomorrow')
                    self.assertEqual(app.main_notebook.select(), str(app.assistant_tab))
                    self.assertEqual(app.assistant_panel.composer.get('1.0', 'end-1c'), 'Find a room tomorrow')
                    app._quiet_ask('Cancel another booking')
                    self.assertEqual(app.assistant_panel.composer.get('1.0', 'end-1c'), 'Find a room tomorrow')
                    send.assert_not_called()
                app.today_panel.update_data([], available=False, stale=True)
                self.assertEqual(app.today_panel.details.cget('text'), 'Refresh bookings')
                app.assistant_panel.close()
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
