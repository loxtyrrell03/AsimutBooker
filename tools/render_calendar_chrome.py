"""Owned native fixtures for centred navigation and full-day closure crosses."""
import ctypes
import argparse
from contextlib import nullcontext
from datetime import date, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from booking_plan import BookingPlanReadResult
from tools.render_open_canvas import capture
from room_catalog import load_cached_catalog, closed_practice_dates


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,help='Render actual derived closure dates from this read-only catalog snapshot.')
    args=parser.parse_args()
    catalog=load_cached_catalog(args.catalog,missing_ok=False) if args.catalog else None
    out=Path('artifacts/calendar-closure-live' if catalog else 'artifacts/calendar-chrome');out.mkdir(parents=True,exist_ok=True)
    fixture=DesktopSettingsTests();fixture.setUp()
    app,root=fixture.app,fixture.root
    errors=[]
    root.report_callback_exception=lambda *error:errors.append(str(error[1]))
    closed=(date.today()+timedelta(days=1)).isoformat()
    off=(date.today()+timedelta(days=2)).isoformat()
    if catalog:
        app.room_catalog=catalog
        assert closed_practice_dates(catalog), 'Snapshot has no current confirmed closures'
    try:
        root.geometry('1920x1000+0+0');root.attributes('-alpha',1);root.update_idletasks()
        handle=int(root.wm_frame(),16)
        ctypes.windll.user32.ShowWindow(handle,4)
        ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
        closure_context=nullcontext() if catalog else patch('room_catalog.closed_practice_dates',return_value=(closed,))
        with patch.object(app,'_refresh_quiet_views'),patch.object(app,'_load_cached_events'),patch.object(app,'_read_booking_plan_for_display',return_value=BookingPlanReadResult(None,False,'')),closure_context:
            app._select_quiet_page('today')
            app.today_panel.update_data([],available=True,stale=False,checked='Example data',goal='Daily target: 3 hours')
            root.update();capture(root,out/'nav-wide.png')
            app.show_calendar_dialog('month')
            if not catalog:
                app.day_vars[off].set(False)
            app.calendar_scan_var.set('Live closure data · other calendar content omitted' if catalog else 'Example data · one confirmed closure and one booking-off date')
            for mode,width in (('month',1200),('month',760),('3days',1200),('plan',1200)):
                root.geometry(f'{width}x820')
                app.show_calendar_dialog(mode);root.update()
                if catalog:
                    assert app.calendar_closed_dates==set(closed_practice_dates(catalog))
                capture(root,out/f'{mode}-{width}.png')
            if errors: raise AssertionError(errors)
            print(f'Owned fixture renders: {out}')
    finally:
        fixture.doCleanups()


if __name__=='__main__':main()
