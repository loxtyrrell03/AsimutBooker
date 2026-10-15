"""Render only an owned synthetic Settings/Pause fixture; never control live apps."""
import ctypes
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_desktop_settings import DesktopSettingsTests
from tools.render_open_canvas import capture


def main():
    output = Path('artifacts/desktop-pause-lifecycle')
    output.mkdir(parents=True, exist_ok=True)
    fixture = DesktopSettingsTests()
    fixture.setUp()
    app, root = fixture.app, fixture.root
    errors, rows = [], []
    root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
    original = fixture.settings.read_bytes()
    try:
        root.geometry('1040x740+0+0')
        root.attributes('-alpha', 1)
        root.update_idletasks()
        handle = int(root.wm_frame(), 16)
        # Map this fixture without activating it or displacing the user's app.
        ctypes.windll.user32.ShowWindow(handle, 4)
        ctypes.windll.user32.SetWindowPos(handle, 1, 0, 0, 0, 0, 0x13)
        for name, width, height, paused in (
                ('settings-1040', 1040, 740, False),
                ('paused-760', 760, 600, True),
                ('unavailable-1200', 1200, 740, None)):
            root.geometry(f'{width}x{height}')
            reader = (patch('booking_pause.read_pause', side_effect=ValueError('Synthetic unavailable status'))
                      if paused is None else patch('booking_pause.read_pause',
                          return_value={'paused': paused, 'revision': 'a' * 64}))
            with reader:
                app._poll_booking_pause()
                app._show_settings_hub()
                root.update()
                canvas = app.settings_scroll.canvas
                if width >= 1040:
                    assert canvas.yview() == (0.0, 1.0), (name, canvas.yview())
                button = app.booking_pause_button
                assert button.winfo_width() >= button.winfo_reqwidth() - 1
                assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + width
                assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + height
                assert ('disabled' in button.state()) == (paused is None)
                capture(root, output / f'{name}.png')
                rows.append(dict(view=name, size=[width, height], scroll=list(canvas.yview()),
                                 pause=app.booking_pause_label.cget('text'), button=button.cget('text')))
                if width == 760:
                    canvas.yview_moveto(1)
                    root.update()
                    assert canvas.yview()[1] == 1.0
                    capture(root, output / 'paused-760-bottom.png')
        assert fixture.settings.read_bytes() == original
        assert not errors, errors
        (output / 'receipt.json').write_text(json.dumps(dict(synthetic=True, views=rows,
            callback_errors=errors, settings_unchanged=True), indent=2) + '\n', encoding='utf-8')
        print(f'Owned render and geometry checks passed: {output}')
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
