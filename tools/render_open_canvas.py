"""Render an owned, non-activating Tk fixture; no live window/browser control.

Use the repository Python plus Pillow on PYTHONPATH for PNG output. Application
startup/background work and persistence are isolated by DesktopSettingsTests.
"""
import ctypes
from datetime import datetime, timedelta
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from PIL import Image, ImageDraw, ImageFont
from tkinter import ttk
from tests.test_desktop_settings import descendants
from booking_plan import BookingPlanReadResult
from desktop_cancellation import DesktopCancellation
from calendar_preferences_ui import open_calendar_preferences


def capture(root,path):
    root.update()
    ctypes.windll.user32.RedrawWindow(int(root.wm_frame(),16),None,None,0x185)
    root.update()
    from PIL import ImageGrab
    ImageGrab.grab(window=int(root.wm_frame(),16)).save(path)


def main():
    out=Path('artifacts/open-canvas');out.mkdir(parents=True,exist_ok=True)
    fixture=DesktopSettingsTests();fixture.setUp()
    app,root=fixture.app,fixture.root
    failures=[]
    root.report_callback_exception=lambda *exc: failures.append(str(exc[1]))
    root.geometry('1200x800+0+0');root.attributes('-alpha',1);root.update_idletasks()
    handle=int(root.wm_frame(),16)
    ctypes.windll.user32.ShowWindow(handle,4)
    ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
    root.update()
    day=datetime.now().date().isoformat()
    example=dict(eventId=123,date=day,startTime='12:00',endTime='14:00',room='A1.06',isReservation=True,title='Reservation')
    app._desktop_cancellation=DesktopCancellation(fixture.settings.parent)
    clipped=[]
    def check(name,host):
        for widget in descendants(host):
            if isinstance(widget,(ttk.Button,ttk.Entry,ttk.Combobox,ttk.Spinbox)) and widget.winfo_ismapped():
                if widget.winfo_width()<widget.winfo_reqwidth()-2 or widget.winfo_rootx()+widget.winfo_width()>root.winfo_rootx()+root.winfo_width()+1:
                    clipped.append((name,str(widget.cget('text')) if isinstance(widget,ttk.Button) else widget.winfo_class()))
    try:
        with patch.object(app,'_refresh_quiet_views'),patch.object(app,'_load_cached_events'),patch.object(app,'_start_scheduler_status_refresh'),patch.object(app,'load_history',return_value={'runs':[]}),patch.object(app,'_read_booking_plan_for_display',return_value=BookingPlanReadResult(None,False,'')):
            app._select_quiet_page('today')
            app.today_panel.update_data([example],available=True,stale=False,checked='today at 11:42',goal='Daily target: 3 hours',now=datetime.fromisoformat(day+'T10:00:00'))
            for page in ['today','settings','assistant','calendar']:
                app._select_quiet_page(page);root.update();capture(root,out/f'{page}.png')
            app._select_quiet_page('week')
            app.week_panel.update_data([example],available=True,stale=False,checked='Example data · 11:42',off_dates=[day],closed_dates=[(datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()])
            root.update();capture(root,out/'week.png')
            app._select_quiet_page('settings');root.geometry('1040x740');root.update();capture(root,out/'settings-1040.png')
            app._select_quiet_page('settings');root.geometry('760x740');root.update();capture(root,out/'settings-760.png')
            for key in ('today','week','assistant','settings'):
                app._select_quiet_page(key);root.update();capture(root,out/f'{key}-760.png')
                check(key,app.root.nametowidget(app.main_notebook.select()))
            for key,host in (('activity',app.activity_tab),('system',app.system_tab)):
                app.main_notebook.select(host);root.update();capture(root,out/f'{key}-760.png');check(key,host)
            root.geometry('1200x800')
            for key,action in [('rooms',app.show_room_preferences_dialog),('strategy',app.show_booking_strategy_dialog),
                ('targets',app.show_practice_plan_dialog),('dates',lambda:open_calendar_preferences(app,[datetime.fromisoformat(day).date()],fixture.settings)),
                ('scan',app.show_scan_rooms_dialog),('history',app.show_history_dialog),('schedule',app._show_scheduled_tasks_dialog),
                ('health',app.show_health_details),('tools',app._show_advanced_tools),
                ('booking',lambda:app._show_quiet_booking(example)),('cancellation',lambda:app._show_cancel_booking(example))]:
                action();root.geometry('1200x800');root.update();capture(root,out/f'{key}.png')
                root.geometry('760x740');root.update();capture(root,out/f'{key}-760.png');check(key,app.root.nametowidget(app.main_notebook.select()))
            for mode in ('month','fortnight','week','3days','plan'):
                app.show_calendar_dialog(mode);root.update();capture(root,out/f'calendar-{mode}-760.png');check(mode,app.calendar_tab)
            root.geometry('1200x800')
            app.main_notebook.select(app.system_tab);root.update();capture(root,out/'system.png')
        if failures: raise AssertionError(failures)
        if clipped: raise AssertionError(f'Clipped controls: {clipped}')
        names=['today','settings-1040','week','calendar','rooms','strategy','booking','cancellation','system']
        sheet=Image.new('RGB',(1500,1130),'#F8FAFC');draw=ImageDraw.Draw(sheet)
        font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',20)
        draw.text((20,12),'Implemented B · isolated desktop renders · example data',font=font,fill='#1D2430')
        for i,name in enumerate(names):
            preview=Image.open(out/f'{name}.png');preview.thumbnail((480,315))
            x=10+(i%3)*500;y=60+(i//3)*355
            draw.text((x,y),name.replace('-1040','').title(),font=font,fill='#1D2430');sheet.paste(preview,(x,y+30))
        sheet.save(out/'overview.png')
        print(f'Rendered owned fixture pages in {out}')
    finally: fixture.doCleanups()


if __name__=='__main__':main()
