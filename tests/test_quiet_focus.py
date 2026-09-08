"""Quiet Focus display semantics and isolated desktop navigation."""
from datetime import datetime, timezone
import tkinter as tk
import unittest
from unittest.mock import patch

import gui
from quiet_focus import display_summary


def event(day, start, end, reservation=True):
    return dict(date=day, startTime=start, endTime=end, isReservation=reservation,
                room='B0.29', title='Reservation' if reservation else 'Class')


class QuietFocusTests(unittest.TestCase):
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
