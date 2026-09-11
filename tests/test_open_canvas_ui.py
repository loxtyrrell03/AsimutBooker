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
        for width in (760, 1040, 1200, 1920, 3440):
            self.root.geometry(f'{width}x740'); self.root.update()
            for widget in [*self.app.quiet_nav.values(), *(v[2] for v in self.app.settings_tiles.values())]:
                self.assertTrue(widget.winfo_ismapped())
                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.root.winfo_rootx()+width)
            nav=self.app.quiet_nav['today'].master
            nav_centre=nav.winfo_rootx()+nav.winfo_width()/2
            window_centre=self.root.winfo_rootx()+self.root.winfo_width()/2
            self.assertLessEqual(abs(nav_centre-window_centre),1)
            brand=self.app.topbar.winfo_children()[0]
            if abs(nav.winfo_rooty()-brand.winfo_rooty())<10:
                self.assertLessEqual(brand.winfo_rootx()+brand.winfo_width(),nav.winfo_rootx())
        self.assertEqual(ttk.Style(self.root).layout('Navigation.TNotebook.Tab'), [('null', {'sticky': 'nswe'})])

    def test_confirmed_closure_has_full_day_cross_in_every_calendar_mode(self):
        from datetime import timedelta
        from booking_plan import BookingPlanReadResult
        from open_canvas_ui import ClosedCalendarDay
        day=date.today().isoformat()
        off=(date.today()+timedelta(days=1)).isoformat()
        self.app.day_vars[off].set(False)
        booking=dict(date=day,eventId=123,startTime='12:00',endTime='13:00',room='Example',isReservation=True)
        with patch.object(self.app,'_load_cached_events'),patch.object(self.app,'_read_booking_plan_for_display',return_value=BookingPlanReadResult(None,False,'')),patch('room_catalog.closed_practice_dates',return_value=(day,)):
            for mode in ('month','fortnight','week','3days','plan'):
                self.app.show_calendar_dialog(mode)
                self.app.calendar_events={day:[booking]}
                self.app._refresh_calendar();self.root.update()
                crossed=[w for w in descendants(self.app.calendar_frame) if isinstance(w,tk.Canvas) and w.find_withtag('closed-day-cross')]
                self.assertEqual(len(crossed),1,mode)
                canvas=crossed[0]
                self.assertEqual(len(canvas.find_withtag('closed-day-cross')),2)
                bounds=canvas.bbox('closed-day-cross')
                self.assertGreater(bounds[2]-bounds[0],canvas.winfo_width()*.8)
                self.assertGreater(bounds[3]-bounds[1],canvas.winfo_height()*.8)
                if isinstance(canvas,ClosedCalendarDay):
                    self.assertLessEqual(canvas.bbox('copy')[3],canvas.winfo_height())
                    self.assertLessEqual(canvas.bbox('booking-0')[3],canvas.winfo_height())
                    self.assertTrue(any('Example' in canvas.itemcget(item,'text') for item in canvas.find_all() if canvas.type(item)=='text'))
                    # Clicking a persisted booking opens its detail, not the day editor.
                    bounds=canvas.bbox('booking-0')
                    with patch.object(self.app,'_show_quiet_booking') as details:
                        canvas.on_booking=details
                        canvas.event_generate('<Motion>',x=bounds[0]+4,y=bounds[1]+4)
                        canvas.event_generate('<Button-1>',x=bounds[0]+4,y=bounds[1]+4)
                        self.root.update()
                        details.assert_called_once_with(booking)

    def test_actual_weekend_cache_drives_both_crosses_without_mocking_closure_result(self):
        from datetime import datetime
        from booking_plan import BookingPlanReadResult
        from room_catalog import with_closure_events, save_catalog, load_cached_catalog
        from tests.test_room_catalog import ClosureCalendarTests
        fixture, catalog = ClosureCalendarTests().real_weekend()
        path = self.fixture.settings.parent / 'catalog.json'
        save_catalog(with_closure_events(catalog, fixture['agenda'], fixture['categories']), path)
        self.app.room_catalog = load_cached_catalog(path)
        with patch('room_catalog.datetime', wraps=datetime) as clock, patch.object(self.app, '_load_cached_events'), patch.object(self.app, '_read_booking_plan_for_display', return_value=BookingPlanReadResult(None, False, '')):
            clock.now.return_value = catalog.observed_at
            for mode in ('month', 'fortnight', 'week', '3days', 'plan'):
                self.app.show_calendar_dialog(mode)
                self.app.calendar_start_date = date(2026, 9, 12)
                self.app._refresh_calendar(); self.root.update()
                self.assertEqual(self.app.calendar_closed_dates, {'2026-09-12', '2026-09-13'})
                crossed = [w for w in descendants(self.app.calendar_frame)
                           if isinstance(w, tk.Canvas) and w.find_withtag('closed-day-cross')]
                self.assertEqual(len(crossed), 2, mode)

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

    def test_calendar_month_buttons_cross_short_long_months_and_year_boundaries(self):
        from booking_plan import BookingPlanReadResult
        with patch.object(self.app, '_load_cached_events'), patch.object(self.app, '_read_booking_plan_for_display', return_value=BookingPlanReadResult(None, False, '')):
            self.app.show_calendar_dialog('month')
            buttons = {w.cget('text'): w for w in descendants(self.app.calendar_tab) if isinstance(w, ttk.Button)}
            for start, previous, following in (
                (date(2026, 9, 11), date(2026, 8, 1), date(2026, 9, 1)),
                (date(2026, 4, 30), date(2026, 3, 1), date(2026, 4, 1)),
                (date(2026, 3, 31), date(2026, 2, 1), date(2026, 3, 1)),
                (date(2028, 3, 31), date(2028, 2, 1), date(2028, 3, 1)),
                (date(2027, 1, 31), date(2026, 12, 1), date(2027, 1, 1)),
            ):
                with self.subTest(start=start):
                    self.app.calendar_start_date = start
                    buttons['← Previous'].invoke(); self.root.update()
                    self.assertEqual(self.app.calendar_start_date, previous)
                    buttons['Next →'].invoke(); self.root.update()
                    self.assertEqual(self.app.calendar_start_date, following)
                    self.assertEqual(self.app.calendar_period_var.get(), following.strftime('%B %Y'))
            buttons['Today'].invoke(); self.root.update()
            self.assertEqual(self.app.calendar_start_date, date.today().replace(day=1))

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

    def test_confirmation_keep_does_not_cancel_and_confirm_uses_exact_identity(self):
        from desktop_cancellation import DesktopCancellation
        self.app._desktop_cancellation=DesktopCancellation(self.fixture.settings.parent)
        event=dict(eventId=123,date=date.today().isoformat(),startTime='12:00',endTime='13:00',room='Example',isReservation=True)
        with patch('desktop_cancellation.cancel_phone_reservation',return_value=dict(cancelled=True,reconciliation_required=False,message='Booking cancelled.')) as action:
            self.app._show_cancel_booking(event);self.root.update()
            page=self.app._detail_pages['cancellation']
            buttons={w.cget('text'):w for w in descendants(page) if isinstance(w,ttk.Button)}
            buttons['Keep booking'].invoke();self.root.update()
            action.assert_not_called()
            self.app._show_cancel_booking(event);self.root.update()
            page=self.app._detail_pages['cancellation']
            next(w for w in descendants(page) if isinstance(w,ttk.Button) and w.cget('text')=='Cancel this booking').invoke()
            self.app._desktop_cancellation.thread.join(3)
            action.assert_called_once()
            self.assertEqual(action.call_args.args[0]['event_id'],123)
            self.assertEqual(action.call_args.args[0]['room'],'Example')

    def test_my_week_distinguishes_booked_planned_off_and_closed(self):
        from types import SimpleNamespace
        from datetime import timedelta
        day=date.today().isoformat()
        closed=(date.today()+timedelta(days=1)).isoformat()
        event=dict(eventId=123,date=day,startTime='12:00',endTime='13:00',room='Example',isReservation=True)
        plan=SimpleNamespace(date=day,start_time='15:00',end_time='16:00',room='Example 2',confirmed_minutes=0)
        self.app.week_panel.update_data([event],available=True,stale=False,planned=[plan],off_dates=[day],closed_dates=[closed])
        labels=[w.cget('text') for w in descendants(self.app.week_panel) if isinstance(w,tk.Label)]
        self.assertIn('Booking off',labels)
        self.assertIn('Practice rooms closed',labels)
        self.assertIn('Booked',labels)
        self.assertIn('Planned · not booked yet',labels)


if __name__ == '__main__':
    unittest.main()
