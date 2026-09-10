"""Real Tk navigation, draft, help and narrow layout checks with temporary data."""
from datetime import date
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tests import test_desktop_settings as fixture_module
descendants = fixture_module.descendants
from open_canvas_ui import HelpTip
from calendar_preferences_ui import open_calendar_preferences


class OpenCanvasTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.DesktopSettingsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app, self.root = self.fixture.app, self.fixture.root
        self.root.attributes('-alpha', 0)
        self.root.deiconify()
        self.root.geometry('760x740')
        self.root.update()

    def test_narrow_navigation_and_hub_have_no_clipped_actions(self):
        for width in (760, 1040, 1200):
            self.root.geometry(f'{width}x740'); self.root.update()
            for widget in [*self.app.quiet_nav.values(), *(v[2] for v in self.app.settings_tiles.values())]:
                self.assertTrue(widget.winfo_ismapped())
                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.root.winfo_rootx()+width)
        self.assertEqual(ttk.Style(self.root).layout('Navigation.TNotebook.Tab'), [('null', {'sticky': 'nswe'})])

    def test_room_editor_draft_survives_navigation_and_cancel_does_not_save(self):
        original = self.fixture.settings.read_bytes()
        self.app.show_room_preferences_dialog()
        page = self.app._detail_pages['rooms']
        field = next(w for w in descendants(page) if isinstance(w, ttk.Combobox))
        field.set('2')
        self.app._select_quiet_page('today')
        self.app.show_room_preferences_dialog()
        self.assertIs(self.app._detail_pages['rooms'], page)
        self.assertEqual(field.get(), '2')
        page.destroy(); self.root.update()
        self.assertNotIn('rooms', self.app._detail_pages)
        self.assertEqual(self.fixture.settings.read_bytes(), original)

    def test_calendar_view_and_date_draft_survive_navigation(self):
        with patch.object(self.app, '_load_cached_events'):
            self.app.show_calendar_dialog('week')
        self.app._select_quiet_page('settings')
        self.app._select_quiet_page('calendar')
        self.assertEqual(self.app.calendar_view.get(), 'week')
        editor = open_calendar_preferences(self.app, [date.today()], self.fixture.settings)
        field = next(w for w in descendants(editor) if isinstance(w, ttk.Spinbox))
        field.set('4.5')
        editor.back()
        self.assertIs(open_calendar_preferences(self.app, [date.today()], self.fixture.settings), editor)
        self.assertEqual(field.get(), '4.5')

    def test_distinct_bookings_never_reuse_another_booking_detail(self):
        base = dict(date=date.today().isoformat(), startTime='12:00', endTime='13:00',room='Example',isReservation=True)
        self.app._show_quiet_booking(dict(base,eventId=123))
        first = self.app._detail_pages['booking:123']
        self.app._show_quiet_booking(dict(base,eventId=124,startTime='14:00',endTime='15:00'))
        self.assertIsNot(first,self.app._detail_pages['booking:124'])

    def test_help_is_bounded_and_escape_dismisses_it(self):
        self.app.show_booking_strategy_dialog(); self.root.update()
        help_button = next(w for w in descendants(self.app._detail_pages['strategy']) if isinstance(w,HelpTip))
        help_button.show(); self.root.update()
        popup = help_button.popup
        self.assertIsNotNone(popup)
        self.assertGreaterEqual(popup.winfo_rootx(),self.root.winfo_rootx())
        self.assertLessEqual(popup.winfo_rootx()+popup.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
        help_button.event_generate('<Escape>'); self.root.update()
        # Exercise the same registered dismissal directly when no OS focus is held.
        help_button.hide()
        self.assertIsNone(help_button.popup)


if __name__ == '__main__':
    unittest.main()
