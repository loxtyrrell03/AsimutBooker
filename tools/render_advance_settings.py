"""Render isolated owned desktop settings fixtures, without changing live state."""
import ctypes
from pathlib import Path
import sys
from tkinter import ttk

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests, descendants
from tools.render_open_canvas import capture


def main():
    out=Path('artifacts/advance-quota'); out.mkdir(parents=True,exist_ok=True)
    fixture=DesktopSettingsTests(); fixture.setUp()
    root,app=fixture.root,fixture.app
    errors=[]; root.report_callback_exception=lambda *exc:errors.append(str(exc[1]))
    try:
        root.geometry('1040x800+0+0'); root.attributes('-alpha',1); root.update_idletasks()
        handle=int(root.wm_frame(),16)
        ctypes.windll.user32.ShowWindow(handle,4)
        ctypes.windll.user32.SetWindowPos(handle,1,0,0,0,0,0x13)
        capture(root,out/'desktop-hub.png')
        app.show_advance_quota_dialog()
        dialog=app._detail_pages['advance_quota']
        capture(root,out/'desktop-compact.png')
        buttons={w.cget('text'):w for w in descendants(dialog) if isinstance(w,ttk.Button)}
        buttons['▸ Rooms and time periods'].invoke()
        buttons['Add time period'].invoke()
        for width in (1040,760):
            root.geometry(f'{width}x900'); root.update()
            for widget in descendants(dialog):
                if isinstance(widget,(ttk.Button,ttk.Entry,ttk.Combobox,ttk.Spinbox)) and widget.winfo_ismapped():
                    assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width(), widget
            capture(root,out/f'desktop-periods-{width}.png')
        assert not errors, errors
        print('PASS owned desktop 760/1040px compact and expanded controls; isolated state')
    finally: fixture.doCleanups()


if __name__=='__main__': main()
