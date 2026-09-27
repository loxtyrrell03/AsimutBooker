"""Render the isolated native Rooms tab and verify real navigation/layout."""
import ctypes
import argparse
from datetime import datetime
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from tests.test_room_grid import fixture
from room_grid import normalize_day, read_grid
from room_catalog import SITE_TIMEZONE
from tools.render_open_canvas import capture


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    out=Path('artifacts/room-grid-20260927');out.mkdir(parents=True,exist_ok=True)
    test=DesktopSettingsTests();test.setUp()
    app,root=test.app,test.root
    root.maxsize(5000,3000)
    errors=[];root.report_callback_exception=lambda *e:errors.append(str(e[1]))
    day=datetime.now(SITE_TIMEZONE).date().isoformat()
    doc=normalize_day(day,fixture(),['B0.13','B1.15']);doc['stale']=False
    doc['rooms'] += [{**doc['rooms'][0],'name':f'Example {i}'} for i in range(12)]
    app.room_availability.reader=read_grid if args.live else lambda:{'days':{day:doc},'message':''}
    prefix='live-' if args.live else ''
    try:
        root.attributes('-alpha',1);root.update_idletasks()
        handle=int(root.wm_frame(),16)
        ctypes.windll.user32.ShowWindow(handle,4)
        ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
        app._select_quiet_page('rooms');root.update()
        assert app.main_notebook.select()==str(app.rooms_tab)
        panel=app.room_availability
        for width in (1040,760,1920,3440):
            root.geometry(f'{width}x{1320 if width == 3440 else 900 if width == 1920 else 800}+0+0');root.update()
            assert root.winfo_width()==width, (width,root.winfo_width())
            assert abs(panel.body.winfo_rootx()+panel.body.winfo_width()/2-(panel.winfo_rootx()+panel.winfo_width()/2))<2
            assert panel.rendered_width==max(1280,panel.track.winfo_width())
            assert panel.booking_font.cget('size')==-16
            panel.xview('moveto',.13);root.update()
            assert abs(panel.axis.xview()[0]-panel.track.xview()[0])<.003
            panel.yview('moveto',.2);root.update()
            assert abs(panel.names.yview()[0]-panel.track.yview()[0])<.003
            panel.yview('moveto',0)
            capture(root,out/f'{prefix}rooms-desktop-{width}.png')
            panel.booking_widgets[0].invoke();root.update()
            assert ('Alex Morgan' in panel.details_label.cget('text')) if not args.live else len(panel.details_label.cget('text'))>10
            assert panel.details.winfo_ismapped()
            capture(root,out/f'{prefix}rooms-desktop-detail-{width}.png')
            panel.details.pack_forget()
        if not args.live:
            panel.query.set('B1.15');root.update()
            assert not panel.booking_widgets
            panel.change_week(7);root.update()
            assert 'No room grid' in panel.status.get()
        assert not errors, errors
        print('Native Rooms: 760/1040/1920/3440 layout, centered content, full-width timeline, readable fonts, linked scrolling and booking details passed.')
    finally:test.doCleanups()

if __name__=='__main__':main()
