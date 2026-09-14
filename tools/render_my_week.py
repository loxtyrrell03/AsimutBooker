"""Render an owned My Week fixture with invented data and no live app access."""
import ctypes
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_canvas_ui import install_theme
from quiet_focus import WeekPanel, WeekEventCard


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    out = Path(__file__).resolve().parents[1] / 'artifacts/my-week'
    out.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    root.withdraw()
    root.title('My Week · invented example data')
    errors, opened = [], []
    root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
    install_theme(root)
    panel = WeekPanel(root, on_calendar=lambda: None, on_refresh=lambda: None,
                      on_details=opened.append)
    panel.pack(fill='both', expand=True)
    day = datetime.now(ZoneInfo('Europe/London')).date()
    events = [
        dict(date=day.isoformat(), startTime='14:00', endTime='15:00',
             title='Reservation', room='B0.13', isReservation=True, eventId=123),
        dict(date=day.isoformat(), startTime='12:30', endTime='13:15',
             title='Piano lesson', room='Teaching room 2', isReservation=False),
        dict(date=day.isoformat(), startTime='15:00', endTime='16:00',
             title='Piano Department — meeting for returning students and chamber music groups',
             room='Recital Hall', isReservation=False),
    ]
    planned = [SimpleNamespace(date=day.isoformat(), start_time='13:15', end_time='13:45',
                               room='B0.28', confirmed_minutes=0)]
    try:
        for width in (1040, 760):
            root.geometry(f'{width}x900+0+0')
            panel.update_data(events, available=True, stale=False, checked='Example data · 11:42',
                              planned=planned)
            root.update_idletasks()
            handle = int(root.wm_frame(), 16)
            ctypes.windll.user32.ShowWindow(handle, 4)
            ctypes.windll.user32.SetWindowPos(handle, 1, 0, 0, 0, 0, 0x13)
            root.update()
            cards = [w for w in panel.rows.winfo_children() if isinstance(w, WeekEventCard)]
            assert len(cards) == 4
            starts = [next(w.cget('text') for w in descendants(c) if isinstance(w, tk.Label)) for c in cards]
            assert starts == ['12:30', '13:15', '14:00', '15:00'], starts
            assert cards[1].itemcget(cards[1].find_withtag('surface')[0], 'dash') == '1 3'
            assert cards[0].fill != cards[2].fill
            for card in cards:
                for widget in descendants(card):
                    if isinstance(widget, (tk.Label, ttk.Button)):
                        assert widget.winfo_width() >= widget.winfo_reqwidth() - 2, widget.cget('text')
                        assert widget.winfo_rootx() + widget.winfo_width() <= card.winfo_rootx() + card.winfo_width()
                for button in (w for w in descendants(card) if isinstance(w, ttk.Button)):
                    button.invoke()
            assert opened[-1] is events[0]
            ctypes.windll.user32.RedrawWindow(handle, None, None, 0x185)
            root.update()
            ImageGrab.grab(window=handle).save(out / f'my-week-{width}.png')
        panel.update_data(events, available=False, stale=True, planned=planned,
                          plan_stale=True, off_dates=[day.isoformat()],
                          closed_dates=[(day + timedelta(days=1)).isoformat()])
        root.update()
        labels = [w.cget('text') for w in descendants(panel) if isinstance(w, tk.Label)]
        assert 'Booking off' in labels and 'Practice rooms closed' in labels
        assert any('agenda is unavailable' in text for text in labels)
        assert any('plan needs a refresh' in text for text in labels)
        assert any(isinstance(w, ttk.Button) and w.cget('text') == 'View booking' for w in descendants(panel))
        panel.update_data([], available=True, stale=False)
        root.update()
        assert any(isinstance(w, tk.Label) and 'No upcoming sessions' in w.cget('text') for w in descendants(panel))
        assert not errors, errors
        print(f'Wide/narrow rendering, chronology, booking action and display states passed: {out}')
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
