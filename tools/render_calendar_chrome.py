"""Owned native fixtures for centred navigation and full-day closure crosses."""
import ctypes
from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from booking_plan import BookingPlanReadResult
from tools.render_open_canvas import capture


def main():
    out=Path('artifacts/calendar-chrome');out.mkdir(parents=True,exist_ok=True)
    fixture=DesktopSettingsTests();fixture.setUp()
    app,root=fixture.app,fixture.root
    errors=[]
    root.report_callback_exception=lambda *error:errors.append(str(error[1]))
    closed=(date.today()+timedelta(days=1)).isoformat()
    off=(date.today()+timedelta(days=2)).isoformat()
    try:
        root.geometry('1920x1000+0+0');root.attributes('-alpha',1);root.update_idletasks()
        handle=int(root.wm_frame(),16)
        ctypes.windll.user32.ShowWindow(handle,4)
        ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
        with patch.object(app,'_refresh_quiet_views'),patch.object(app,'_load_cached_events'),patch.object(app,'_read_booking_plan_for_display',return_value=BookingPlanReadResult(None,False,'')),patch('room_catalog.closed_practice_dates',return_value=(closed,)):
            app._select_quiet_page('today')
            app.today_panel.update_data([],available=True,stale=False,checked='Example data',goal='Daily target: 3 hours')
            root.update();capture(root,out/'nav-wide.png')
            app.show_calendar_dialog('month')
            app.day_vars[off].set(False)
            app.calendar_scan_var.set('Example data · one confirmed closure and one booking-off date')
            for mode,width in (('month',1200),('month',760),('3days',1200),('plan',1200)):
                root.geometry(f'{width}x820')
                app.show_calendar_dialog(mode);root.update()
                capture(root,out/f'{mode}-{width}.png')
            if errors: raise AssertionError(errors)
            print(f'Owned fixture renders: {out}')
    finally:
        fixture.doCleanups()


if __name__=='__main__':main()
