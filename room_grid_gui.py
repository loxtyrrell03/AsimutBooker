"""Native Rooms tab; shares the phone's read-only scan worker and grid cache."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from uuid import uuid4
import webbrowser

from open_canvas_ui import PAGE, BLUE, WHITE, MUTED, HelpTip
from room_catalog import SITE_TIMEZONE
from room_grid import ROOT, clock, read_grid
from runtime_guard import SingleInstanceLock


class RoomGridScan:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.lock = SingleInstanceLock(self.root / 'data/desktop-room-grid.lock')
        self.active = False
        self.text = ''
        self.folder = None

    def start(self, dates):
        if self.active or not self.lock.acquire():
            raise ValueError('A room refresh is already running.')
        self.active = True
        self.text = 'Checking room availability…'
        self.folder = self.root / 'data/desktop_operations' / str(uuid4())
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            threading.Thread(target=self._run, args=(dates,), daemon=False).start()
        except Exception:
            self.active = False
            self.lock.release()
            raise

    def stop(self):
        if self.active and self.folder:
            (self.folder / 'stop').touch()
            self.text = 'Stopping after the current read…'

    def _run(self, dates):
        try:
            process = subprocess.Popen([str(self.root / '.venv/Scripts/python.exe'), str(self.root / 'phone_operation_worker.py')],
                cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            process.stdin.write(json.dumps({'surface': 'desktop', 'request_id': self.folder.name,
                                          'action': 'scan', 'args': {'dates': dates}}))
            process.stdin.close()
            started = time.monotonic()
            while process.poll() is None:
                if not (self.folder / 'stop').exists():
                    try:
                        self.text = json.loads((self.folder / 'progress.json').read_text(encoding='utf-8'))['text']
                    except (OSError, ValueError, KeyError):
                        pass
                if time.monotonic() - started > 1200:
                    self.stop()
                time.sleep(.5)
            result = json.loads((self.folder / 'result.json').read_text(encoding='utf-8'))
            self.text = result['message']
        except Exception:
            self.text = 'Room refresh did not finish. Retry when the Booker is idle.'
        finally:
            self.active = False
            self.lock.release()


class RoomAvailabilityPanel(tk.Frame):
    def __init__(self, parent, *, other_busy=lambda: False, reader=read_grid, scanner=None):
        super().__init__(parent, bg=PAGE, padx=24, pady=20)
        self.reader, self.scanner, self.other_busy = reader, scanner or RoomGridScan(), other_busy
        self.selected = datetime.now(SITE_TIMEZONE).date()
        self.week = self.selected - timedelta(days=self.selected.weekday())
        self.document = {'days': {}}
        self.signature = None
        self.after_id = None
        self.booking_widgets = []
        head = tk.Frame(self, bg=PAGE); head.pack(fill='x')
        tk.Label(head, text='Room availability', font=('Segoe UI', 24, 'bold'), bg=PAGE, fg='#1d2430').pack(side='left')
        HelpTip(head, 'Rooms follow your saved filters and ASIMUT access checks. Free space still needs a live booking check for conflicts, quotas and booking windows.').pack(side='left', padx=8)
        self.refresh_button = ttk.Button(head, text='Refresh', command=self.refresh)
        self.refresh_button.pack(side='right')
        self.subtitle = tk.StringVar(value='Your bookable rooms')
        tk.Label(self, textvariable=self.subtitle, bg=PAGE, fg=MUTED, anchor='w').pack(fill='x', pady=(8, 12))
        bar = tk.Frame(self, bg=PAGE); bar.pack(fill='x')
        ttk.Button(bar, text='‹', width=3, command=lambda: self.change_week(-7)).pack(side='left')
        self.week_label = tk.Label(bar, bg=PAGE, font=('Segoe UI', 12, 'bold')); self.week_label.pack(side='left', padx=10)
        ttk.Button(bar, text='›', width=3, command=lambda: self.change_week(7)).pack(side='left')
        ttk.Button(bar, text='Today', command=self.today).pack(side='right')
        days = tk.Frame(self, bg=PAGE); days.pack(fill='x', pady=12)
        self.day_buttons = []
        for i in range(7):
            days.columnconfigure(i, weight=1, uniform='days')
            button = tk.Button(days, relief='flat', bd=0, pady=8, font=('Segoe UI', 11),
                               command=lambda i=i: self.choose(self.week + timedelta(days=i)))
            button.grid(row=0, column=i, sticky='ew', padx=2); self.day_buttons.append(button)
        tools = tk.Frame(self, bg=PAGE); tools.pack(fill='x', pady=(0, 8))
        tk.Label(tools, text='Filter rooms', bg=PAGE, fg=MUTED).pack(side='left', padx=(0, 8))
        self.query = tk.StringVar(); self.query.trace_add('write', lambda *_: self.render())
        ttk.Entry(tools, textvariable=self.query, width=22).pack(side='left')
        self.status = tk.StringVar()
        self.status_label = tk.Label(self, textvariable=self.status, bg=PAGE, fg=MUTED, anchor='w', justify='left')
        self.status_label.pack(fill='x', pady=(0, 8))
        self.status_label.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(100, e.width)))
        self.stop_button = ttk.Button(tools, text='Stop refresh', command=self.scanner.stop)
        self.stop_button.pack(side='right')
        self.legend = tk.Label(self, text='□ Free space     ■ Booked     ▨ Closed       Select a booking for details', bg=PAGE, fg=MUTED, anchor='w')
        self.legend.pack(side='bottom', fill='x', pady=10)
        holder = tk.Frame(self, bg=WHITE); holder.pack(fill='both', expand=True)
        self.holder = holder
        holder.columnconfigure(1, weight=1); holder.rowconfigure(1, weight=1)
        tk.Label(holder, text='Room / closes', bg=PAGE, fg=MUTED, anchor='w', padx=10).grid(row=0, column=0, sticky='nsew')
        self.axis = tk.Canvas(holder, height=40, bg=WHITE, highlightthickness=0)
        self.axis.grid(row=0, column=1, sticky='ew')
        self.names = tk.Canvas(holder, width=155, bg=PAGE, highlightthickness=0)
        self.names.grid(row=1, column=0, sticky='ns')
        self.track = tk.Canvas(holder, bg=WHITE, highlightthickness=0, takefocus=True)
        self.track.grid(row=1, column=1, sticky='nsew')
        self.xbar = ttk.Scrollbar(holder, orient='horizontal', command=self.xview)
        self.xbar.grid(row=2, column=1, sticky='ew')
        self.ybar = ttk.Scrollbar(holder, orient='vertical', command=self.yview)
        self.ybar.grid(row=1, column=2, sticky='ns')
        self.track.configure(xscrollcommand=self.xbar.set, yscrollcommand=self._yscroll)
        for canvas in (self.track, self.names):
            canvas.bind('<MouseWheel>', lambda e: self.yview('scroll', -int(e.delta / 120), 'units'))
        self.track.bind('<Shift-MouseWheel>', lambda e: self.xview('scroll', -int(e.delta / 120), 'units'))
        self.track.bind('<Left>', lambda e: self.xview('scroll', -1, 'units'))
        self.track.bind('<Right>', lambda e: self.xview('scroll', 1, 'units'))
        self.details = tk.Frame(self, bg=WHITE, padx=14, pady=12)
        self.details_label = tk.Label(self.details, bg=WHITE, anchor='w', justify='left', font=('Segoe UI', 11))
        self.details_label.pack(side='left', fill='both', expand=True)
        self.details_label.bind('<Configure>', lambda e: self.details_label.configure(wraplength=max(100, e.width)))
        self.link = ttk.Button(self.details, text='Open in ASIMUT'); self.link.pack(side='left', padx=10)
        ttk.Button(self.details, text='Close', command=self.details.pack_forget).pack(side='right')
        self.bind('<Destroy>', self._destroyed, add='+')
        self.render()

    def _destroyed(self, event):
        if event.widget is self and self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None

    def activate(self):
        self.document = self.reader(); self.render()
        if self.after_id is None:
            self.after_id = self.after(1500, self.poll)

    def poll(self):
        self.after_id = None
        if not self.winfo_exists(): return
        self.document = self.reader()
        signature = json.dumps(self.document, sort_keys=True)
        if signature != self.signature:
            self.signature = signature; self.render()
        self.status.set(self.scanner.text or self.status.get())
        self.stop_button.state(['!disabled'] if self.scanner.active else ['disabled'])
        self.refresh_button.state(['disabled'] if self.scanner.active or self.other_busy() else ['!disabled'])
        self.after_id = self.after(1500, self.poll)

    def today(self):
        self.selected = datetime.now(SITE_TIMEZONE).date()
        self.week = self.selected - timedelta(days=self.selected.weekday()); self.render()

    def change_week(self, n):
        self.week += timedelta(days=n); self.choose(self.week)

    def choose(self, day):
        self.selected = day; self.details.pack_forget(); self.render()

    def refresh(self):
        today = datetime.now(SITE_TIMEZONE).date()
        dates = [(self.week + timedelta(days=i)).isoformat() for i in range(7)
                 if today <= self.week + timedelta(days=i) <= today + timedelta(days=7)]
        if not dates:
            self.status.set('This week is outside the current live scan window.'); return
        if self.other_busy():
            self.status.set('Wait for the current Booker operation to finish.'); return
        try:
            self.scanner.start(dates); self.status.set(self.scanner.text)
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
        self.activate()

    def xview(self, *args):
        self.track.xview(*args); self.axis.xview_moveto(self.track.xview()[0])

    def _yscroll(self, first, last):
        self.ybar.set(first, last); self.names.yview_moveto(first)

    def yview(self, *args):
        self.track.yview(*args); self.names.yview_moveto(self.track.yview()[0])

    def show_booking(self, room, item):
        self.details_label.configure(text=f"{item['label']}\n{room} · {self.selected.strftime('%A %d %B')} · {clock(item['start'])}–{clock(item['end'])}")
        self.link.configure(command=lambda: webbrowser.open(f"https://rwcmd.asimut.net/arrangement?eventId={item['event_id']}"))
        self.link.state(['!disabled'] if item['event_id'] else ['disabled'])
        self.details.pack(fill='x', before=self.holder, pady=(0,12))

    def render(self):
        for widget in self.booking_widgets: widget.destroy()
        self.booking_widgets = []
        for canvas in (self.track, self.names, self.axis): canvas.delete('all')
        self.week_label.configure(text=f"{self.week.strftime('%d %b')} – {(self.week + timedelta(days=6)).strftime('%d %b %Y')}")
        for i, button in enumerate(self.day_buttons):
            day = self.week + timedelta(days=i)
            button.configure(text=day.strftime('%a\n%d'), bg=BLUE if day == self.selected else PAGE,
                             fg=WHITE if day == self.selected else '#1d2430')
        day = self.document.get('days', {}).get(self.selected.isoformat())
        if day:
            checked = datetime.fromisoformat(day['observed_at']).astimezone(SITE_TIMEZONE).strftime('%d %b, %H:%M')
            self.subtitle.set(f"{len(day['rooms'])} bookable rooms · {'Last checked' if day.get('stale') else 'Checked'} {checked}")
            self.status.set('Showing the last check. Availability may have changed.' if day.get('stale') else '')
        else:
            self.subtitle.set('Your bookable rooms')
            self.status.set(self.document.get('message') or 'No room grid for this day yet. Refresh this week to check.')
        width, y = 1280, 0
        for hour in range(7, 24):
            x = (hour - 7) * 80
            self.axis.create_text(x + (5 if hour < 23 else -5), 20, text=f'{hour:02d}:00', anchor='w' if hour < 23 else 'e', fill=MUTED, font=('Segoe UI', 9))
        for room in (day or {}).get('rooms', []):
            if self.query.get().casefold() not in room['name'].casefold(): continue
            ends, booked = [], []
            for item in room['intervals']:
                if item['kind'] != 'booked': continue
                lane = next((i for i, end in enumerate(ends) if end <= item['start']), len(ends))
                if lane == len(ends): ends.append(item['end'])
                else: ends[lane] = item['end']
                booked.append((lane, item))
            height = max(1, len(ends)) * 60 + 10
            self.names.create_text(10, y + 22, text=room['name'], anchor='w', width=138, font=('Segoe UI', 10, 'bold'), fill='#1d2430')
            close = 'Closed all day' if room['closed_all_day'] else f"Closes {room['closes']}" if room['closes'] else 'Close not shown'
            self.names.create_text(10, y + 47, text=close, anchor='w', fill=MUTED, font=('Segoe UI', 9))
            for h in range(17): self.track.create_line(h * 80, y, h * 80, y + height, fill='#e1e6ee')
            for item in room['intervals']:
                if item['kind'] == 'closed':
                    x1, x2 = (item['start'] - 420) * width / 960, (item['end'] - 420) * width / 960
                    self.track.create_rectangle(x1, y, x2, y + height, fill='#e1e6ee', outline='', stipple='gray50')
                    if x2 - x1 > 50: self.track.create_text((x1+x2)/2, y+height/2, text='Closed', fill=MUTED)
            for lane, item in booked:
                x = (item['start'] - 420) * width / 960
                w = (item['end'] - item['start']) * width / 960
                chars = max(1, int(w / 7) - 2)
                name = item['label'] if len(item['label']) <= chars else item['label'][:max(1, chars-1)] + '…'
                text = f"{name}\n{clock(item['start'])}–{clock(item['end'])}" if w > 82 else name
                button = tk.Button(self.track, text=text, relief='flat', borderwidth=0, bg='#eaf3ff', fg='#0756ad', activebackground='#d8eaff', anchor='w', padx=7, font=('Segoe UI', 9), command=lambda r=room['name'], b=item: self.show_booking(r, b))
                self.track.create_window(x+2, y+lane*60+5, window=button, anchor='nw', width=max(18,w-4), height=50)
                self.booking_widgets.append(button)
            y += height
            self.track.create_line(0, y, width, y, fill='#e1e6ee')
            self.names.create_line(0, y, 155, y, fill='#e1e6ee')
        self.track.configure(scrollregion=(0, 0, width, max(y, 150)))
        self.axis.configure(scrollregion=(0, 0, width, 40))
        self.names.configure(scrollregion=(0, 0, 155, max(y, 150)))
        self.stop_button.state(['!disabled'] if self.scanner.active else ['disabled'])
        self.axis.xview_moveto(self.track.xview()[0])
