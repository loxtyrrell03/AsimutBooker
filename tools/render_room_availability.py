"""Render the isolated native Rooms tab and verify real navigation/layout."""
import ctypes
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from tests.test_room_grid import fixture
from room_grid import normalize_day
from room_catalog import SITE_TIMEZONE
from tools.render_open_canvas import capture


def main():
    out=Path('artifacts/room-grid-20260927');out.mkdir(parents=True,exist_ok=True)
    test=DesktopSettingsTests();test.setUp()
    app,root=test.app,test.root
    errors=[];root.report_callback_exception=lambda *e:errors.append(str(e[1]))
    day=datetime.now(SITE_TIMEZONE).date().isoformat()
    doc=normalize_day(day,fixture(),['B0.13','B1.15']);doc['stale']=False
    doc['rooms'] += [{**doc['rooms'][0],'name':f'Example {i}'} for i in range(12)]
    app.room_availability.reader=lambda:{'days':{day:doc},'message':''}
    try:
        root.attributes('-alpha',1);root.update_idletasks()
        handle=int(root.wm_frame(),16)
        ctypes.windll.user32.ShowWindow(handle,4)
        ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
        app._select_quiet_page('rooms');root.update()
        assert app.main_notebook.select()==str(app.rooms_tab)
        panel=app.room_availability
        for width in (1040,760):
            root.geometry(f'{width}x800+0+0');root.update()
            panel.xview('moveto',.13);root.update()
            assert abs(panel.axis.xview()[0]-panel.track.xview()[0])<.003
            panel.yview('moveto',.2);root.update()
            assert abs(panel.names.yview()[0]-panel.track.yview()[0])<.003
            panel.yview('moveto',0)
            capture(root,out/f'rooms-desktop-{width}.png')
            panel.booking_widgets[0].invoke();root.update()
            assert 'Alex Morgan' in panel.details_label.cget('text')
            assert panel.details.winfo_ismapped()
            capture(root,out/f'rooms-desktop-detail-{width}.png')
            panel.details.pack_forget()
        panel.query.set('B1.15');root.update()
        assert not panel.booking_widgets
        panel.change_week(7);root.update()
        assert 'No room grid' in panel.status.get()
        assert not errors, errors
        print('Native Rooms: navigation, 760/1040 layout, linked scrolling, booking details and filtering passed.')
    finally:test.doCleanups()

if __name__=='__main__':main()
