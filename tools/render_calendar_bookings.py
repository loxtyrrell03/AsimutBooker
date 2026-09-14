"""Render owned calendar fixtures with busy days; no live bookings or settings."""
import ctypes
from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from booking_plan import BookingPlanReadResult
from tests.test_desktop_settings import DesktopSettingsTests
from tools.render_open_canvas import capture


def main():
    out = Path('artifacts/calendar-bookings')
    out.mkdir(parents=True, exist_ok=True)
    fixture = DesktopSettingsTests()
    fixture.setUp()
    app, root = fixture.app, fixture.root
    errors = []
    root.report_callback_exception = lambda *error: errors.append(str(error[1]))
    today = date.today()
    events = {}
    for offset, count in ((0, 4), (1, 3), (2, 5), (3, 2), (4, 4), (5, 3)):
        day = (today + timedelta(days=offset)).isoformat()
        events[day] = [dict(date=day, eventId=offset*10+i, startTime=f'{8+i*2:02d}:00',
                           endTime=f'{9+i*2:02d}:00', room=('Weston Gallery', 'B0.29', 'Corus Room')[i % 3],
                           title='Ensemble rehearsal', isReservation=i != 1) for i in range(count)]
    try:
        root.geometry('1200x900+0+0')
        root.attributes('-alpha', 1)
        root.update_idletasks()
        handle = int(root.wm_frame(), 16)
        ctypes.windll.user32.ShowWindow(handle, 4)
        ctypes.windll.user32.SetWindowPos(handle, 1, 0, 0, 0, 0, 0x13)
        with patch.object(app, '_load_cached_events'), patch.object(
            app, '_read_booking_plan_for_display', return_value=BookingPlanReadResult(None, False, '')
        ), patch('room_catalog.closed_practice_dates', return_value=((today+timedelta(days=5)).isoformat(),)):
            for mode, width in (('month', 1200), ('fortnight', 1200), ('week', 760), ('3days', 1200)):
                root.geometry(f'{width}x900')
                app.show_calendar_dialog(mode)
                app.calendar_events = events
                app.calendar_scan_var.set('Example data · all bookings shown')
                app._refresh_calendar()
                root.update()
                if mode == 'month':
                    app.calendar_canvas.yview_moveto(.25)
                capture(root, out / f'{mode}-{width}.png')
            assert not errors, errors
        print(f'Owned fixture renders: {out.resolve()}')
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
