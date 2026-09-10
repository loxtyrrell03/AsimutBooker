"""Render an owned, non-activating Tk fixture; no live window/browser control.

Use the repository Python plus Pillow on PYTHONPATH for PNG output. Application
startup/background work and persistence are isolated by DesktopSettingsTests.
"""
import ctypes
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from PIL import Image


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
    try:
        with patch.object(app,'_refresh_quiet_views'):
            app._select_quiet_page('today')
            app.today_panel.update_data([example],available=True,stale=False,checked='today at 11:42',goal='Daily target: 3 hours',now=datetime.fromisoformat(day+'T10:00:00'))
            for page in ['today','settings','assistant','calendar']:
                app._select_quiet_page(page);root.update();capture(root,out/f'{page}.png')
            app._select_quiet_page('settings');root.geometry('1040x740');root.update();capture(root,out/'settings-1040.png')
            app._select_quiet_page('settings');root.geometry('760x740');root.update();capture(root,out/'settings-760.png')
            root.geometry('1200x800')
            for key,action in [('rooms',app.show_room_preferences_dialog),('strategy',app.show_booking_strategy_dialog),
                               ('booking',lambda:app._show_quiet_booking(example))]:
                action();root.update();capture(root,out/f'{key}.png')
            app.main_notebook.select(app.system_tab);root.update();capture(root,out/'system.png')
        if failures: raise AssertionError(failures)
        print(f'Rendered owned fixture pages in {out}')
    finally: fixture.doCleanups()


if __name__=='__main__':main()
