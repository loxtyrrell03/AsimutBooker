"""Native Rooms tab; shares the phone's read-only scan worker and grid cache."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
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
        self.state = None
        self.folder = None

    def start(self, dates):
        if self.active or not self.lock.acquire():
            raise ValueError('A room refresh is already running.')
        self.active = True
        self.state = 'running'
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
            self.state = result['state']
        except Exception:
            self.state = 'failed'
            self.text = 'Room refresh did not finish. Retry when the Booker is idle.'
        finally:
            self.active = False
            self.lock.release()


class RoomAvailabilityPanel(tk.Frame):
    def __init__(self, parent, *, other_busy=lambda: False, reader=read_grid, scanner=None):
        super().__init__(parent, bg=PAGE)
        self.body = tk.Frame(self, bg=PAGE, padx=24, pady=20)
        self.body.place(relx=.5, y=0, anchor='n', relheight=1, width=1000)
        self.bind('<Configure>', self._layout_body)
        style = ttk.Style(self)
        style.configure('RoomGrid.TButton', font=('Segoe UI', -16), padding=(16, 11))
        style.configure('RoomGrid.TEntry', font=('Segoe UI', -16), padding=(10, 9))
        self.booking_font = tkfont.Font(self, family='Segoe UI', size=-16)
        self.resize_after = None
        self.rendered_width = 0
        self.reader, self.scanner, self.other_busy = reader, scanner or RoomGridScan(), other_busy
        self.selected = datetime.now(SITE_TIMEZONE).date()
        self.week = self.selected - timedelta(days=self.selected.weekday())
        self.document = {'days': {}}
        self.signature = None
        self.active = False
        self.auto_attempts = set()
        self.notice = ''
        self.after_id = None
        self.booking_widgets = []
        head = tk.Frame(self.body, bg=PAGE); head.pack(fill='x')
        tk.Label(head, text='Room availability', font=('Segoe UI', -32, 'bold'), bg=PAGE, fg='#1d2430').pack(side='left')
        HelpTip(head, 'Rooms follow your saved filters and ASIMUT access checks. Free space still needs a live booking check for conflicts, quotas and booking windows.').pack(side='left', padx=8)
        self.refresh_button = ttk.Button(head, text='Refresh', style='RoomGrid.TButton', command=self.refresh)
        self.refresh_button.pack(side='right')
        self.subtitle = tk.StringVar(value='Your bookable rooms')
        tk.Label(self.body, textvariable=self.subtitle, bg=PAGE, fg=MUTED, anchor='w', font=('Segoe UI',-16)).pack(fill='x', pady=(8, 8))
        self.date_controls = tk.Frame(self.body, bg=PAGE, width=1000, height=132)
        self.date_controls.pack(anchor='center', pady=(6,8))
        self.date_controls.pack_propagate(False)
        bar = tk.Frame(self.date_controls, bg=PAGE); bar.pack(fill='x')
        ttk.Button(bar, text='‹', width=3, style='RoomGrid.TButton', command=lambda: self.change_week(-7)).pack(side='left')
        self.week_label = tk.Label(bar, bg=PAGE, font=('Segoe UI', -18, 'bold')); self.week_label.pack(side='left', padx=10)
        ttk.Button(bar, text='›', width=3, style='RoomGrid.TButton', command=lambda: self.change_week(7)).pack(side='left')
        ttk.Button(bar, text='Today', style='RoomGrid.TButton', command=self.today).pack(side='right')
        days = tk.Frame(self.date_controls, bg=PAGE); days.pack(fill='x', pady=(10,0))
        self.day_buttons = []
        for i in range(7):
            days.columnconfigure(i, weight=1, uniform='days')
            button = tk.Button(days, relief='flat', bd=0, pady=9, font=('Segoe UI', -17),
                               command=lambda i=i: self.choose(self.week + timedelta(days=i)))
            button.grid(row=0, column=i, sticky='ew', padx=2); self.day_buttons.append(button)
        tools = tk.Frame(self.body, bg=PAGE); tools.pack(fill='x', pady=(0, 8))
        tk.Label(tools, text='Filter rooms', bg=PAGE, fg=MUTED, font=('Segoe UI',-16)).pack(side='left', padx=(0, 8))
        self.query = tk.StringVar(); self.query.trace_add('write', lambda *_: self.render())
        ttk.Entry(tools, textvariable=self.query, width=22, font=('Segoe UI',-16), style='RoomGrid.TEntry').pack(side='left')
        self.status = tk.StringVar()
        self.status_label = tk.Label(self.body, textvariable=self.status, bg=PAGE, fg=MUTED, anchor='w', justify='left',font=('Segoe UI',-15))
        self.status_label.pack(fill='x', pady=(0, 8))
        self.status_label.bind('<Configure>', lambda e: self.status_label.configure(wraplength=max(100, e.width)))
        self.stop_button = ttk.Button(tools, text='Stop refresh', style='RoomGrid.TButton', command=self.scanner.stop)
        self.stop_button.pack(side='right')
        self.legend = tk.Label(self.body, text='□ Free space     ■ Booked     ▨ Closed       Select a booking for details', bg=PAGE, fg=MUTED, anchor='w',font=('Segoe UI',-15))
        self.legend.pack(side='bottom', fill='x', pady=10)
        holder = tk.Frame(self.body, bg=WHITE); holder.pack(fill='both', expand=True)
        self.holder = holder
        holder.columnconfigure(1, weight=1); holder.rowconfigure(1, weight=1)
        tk.Label(holder, text='Room / closes', bg=PAGE, fg=MUTED, anchor='w', padx=14,font=('Segoe UI',-15)).grid(row=0, column=0, sticky='nsew')
        self.axis = tk.Canvas(holder, width=1, height=46, bg=WHITE, highlightthickness=0)
        self.axis.grid(row=0, column=1, sticky='ew')
        self.names = tk.Canvas(holder, width=190, bg=PAGE, highlightthickness=0)
        self.names.grid(row=1, column=0, sticky='ns')
        self.track = tk.Canvas(holder, width=1, bg=WHITE, highlightthickness=0, takefocus=True)
        self.track.grid(row=1, column=1, sticky='nsew')
        self.track.bind('<Configure>', self._resize_grid)
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
        self.empty = tk.Frame(holder, bg=WHITE, padx=30, pady=30)
        self.empty_title = tk.Label(self.empty, bg=WHITE, fg='#1d2430', font=('Segoe UI', -22, 'bold'))
        self.empty_title.pack(pady=(0, 12))
        self.empty_message = tk.Label(self.empty, bg=WHITE, fg=MUTED, font=('Segoe UI', -16), justify='center', wraplength=500)
        self.empty_message.pack()
        self.empty.bind('<Configure>', lambda e: self.empty_message.configure(wraplength=max(100, e.width-60)))
        self.details = tk.Frame(self.body, bg=WHITE, padx=14, pady=12)
        self.details_label = tk.Label(self.details, bg=WHITE, anchor='w', justify='left', font=('Segoe UI', -17))
        self.details_label.pack(side='left', fill='both', expand=True)
        self.details_label.bind('<Configure>', lambda e: self.details_label.configure(wraplength=max(100, e.width)))
        self.link = ttk.Button(self.details, text='Open in ASIMUT',style='RoomGrid.TButton'); self.link.pack(side='left', padx=10)
        ttk.Button(self.details, text='Close', style='RoomGrid.TButton',command=self.details.pack_forget).pack(side='right')
        self.bind('<Destroy>', self._destroyed, add='+')
        self.render()

    def _destroyed(self, event):
        if event.widget is self:
            for callback in (self.after_id, self.resize_after):
                if callback: self.after_cancel(callback)
            self.after_id = self.resize_after = None

    def _layout_body(self, event):
        if event.widget is not self: return
        self.body.place_configure(width=min(event.width, 2440))
        if hasattr(self, 'date_controls'):
            self.date_controls.configure(width=min(max(1,event.width-48),1000))

    def _resize_grid(self, event):
        if max(1280,event.width) == self.rendered_width: return
        if self.resize_after: self.after_cancel(self.resize_after)
        self.resize_after = self.after_idle(self._draw_resized)

    def _draw_resized(self):
        self.resize_after = None
        self.render()

    def activate(self):
        self.active = True
        self.document = self.reader()
        self._maybe_refresh()
        self.render()
        if self.after_id is None:
            self.after_id = self.after(1500, self.poll)

    def deactivate(self):
        self.active = False
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None

    def _refresh_dates(self):
        today = datetime.now(SITE_TIMEZONE).date()
        return [(self.week + timedelta(days=i)).isoformat() for i in range(7)
                if today <= self.week + timedelta(days=i) <= today + timedelta(days=7)]

    def _maybe_refresh(self):
        revision = self.document.get('revision')
        key = (revision, self.week)
        dates = self._refresh_dates()
        if (not self.active or not revision or self.selected.isoformat() not in dates
                or key in self.auto_attempts or self.scanner.active or self.other_busy()):
            return
        if any(not self.document.get('days', {}).get(d) or self.document['days'][d].get('stale') for d in dates):
            self.refresh()

    def poll(self):
        self.after_id = None
        if not self.winfo_exists() or not self.active: return
        self.document = self.reader()
        self._maybe_refresh()
        signature = json.dumps([self.document, self.scanner.active, self.scanner.text, self.other_busy()], sort_keys=True)
        if signature != self.signature:
            self.signature = signature; self.render()
        self.after_id = self.after(1500, self.poll)

    def today(self):
        self.selected = datetime.now(SITE_TIMEZONE).date()
        self.week = self.selected - timedelta(days=self.selected.weekday()); self._maybe_refresh(); self.render()

    def change_week(self, n):
        self.week += timedelta(days=n); self.choose(self.week)

    def choose(self, day):
        self.selected = day; self.details.pack_forget(); self._maybe_refresh(); self.render()

    def refresh(self):
        dates = self._refresh_dates()
        if not dates:
            self.status.set('This week is outside the current live scan window.'); return
        if self.other_busy():
            self.status.set('Wait for the current Booker operation to finish.'); return
        # One automatic attempt per room-filter revision/week. Stop and failures
        # remain stopped; explicit Refresh is always available to retry.
        self.auto_attempts.add((self.document.get('revision'), self.week))
        try:
            self.scanner.start(dates); self.notice = ''; self.status.set(self.scanner.text)
        except (ValueError, OSError) as exc:
            self.notice = str(exc)
        self.render()

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
            self.details.pack_forget()
        if self.scanner.active or getattr(self.scanner, 'state', None) in {'failed', 'rejected', 'uncertain', 'stopped'}:
            self.status.set(self.scanner.text)
        elif self.notice and not day:
            self.status.set(self.notice)
        rooms = [r for r in (day or {}).get('rooms', []) if self.query.get().casefold() in r['name'].casefold()]
        if rooms:
            self.empty.place_forget()
        else:
            if day:
                title = 'No matching rooms' if self.query.get() else 'No eligible rooms'
                message = 'Try a different room name.' if self.query.get() else 'Your saved room filters determine which rooms appear here.'
            elif self.selected.isoformat() not in self._refresh_dates():
                title, message = 'No saved availability for this date', 'Choose a day in the current booking window to check live availability.'
            elif self.scanner.active:
                title, message = 'Loading room availability…', self.scanner.text
            elif self.other_busy():
                title, message = 'Waiting for the Booker…', 'Rooms will load when the current operation finishes.'
            else:
                title, message = 'Room availability is not loaded', self.notice or self.scanner.text or self.document.get('message') or 'Select Refresh to try again.'
            self.empty_title.configure(text=title)
            self.empty_message.configure(text=message)
            self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.empty.lift()
        width, y = max(1280,self.track.winfo_width()), 0
        self.rendered_width = width
        for hour in range(7, 24):
            x = (hour - 7) * width / 16
            self.axis.create_text(x + (7 if hour < 23 else -7), 23, text=f'{hour:02d}:00', anchor='w' if hour < 23 else 'e', fill=MUTED, font=('Segoe UI', -15))
        for room in rooms:
            ends, booked = [], []
            for item in room['intervals']:
                if item['kind'] != 'booked': continue
                lane = next((i for i, end in enumerate(ends) if end <= item['start']), len(ends))
                if lane == len(ends): ends.append(item['end'])
                else: ends[lane] = item['end']
                booked.append((lane, item))
            height = max(1, len(ends)) * 72 + 10
            self.names.create_text(14, y + 26, text=room['name'], anchor='w', width=164, font=('Segoe UI', -17, 'bold'), fill='#1d2430')
            close = 'Closed all day' if room['closed_all_day'] else f"Closes {room['closes']}" if room['closes'] else 'Close not shown'
            self.names.create_text(14, y + 55, text=close, anchor='w', fill=MUTED, font=('Segoe UI', -15))
            for h in range(17): self.track.create_line(h * width / 16, y, h * width / 16, y + height, fill='#e1e6ee')
            for item in room['intervals']:
                if item['kind'] == 'closed':
                    x1, x2 = (item['start'] - 420) * width / 960, (item['end'] - 420) * width / 960
                    self.track.create_rectangle(x1, y, x2, y + height, fill='#e1e6ee', outline='', stipple='gray50')
                    if x2 - x1 > 70: self.track.create_text((x1+x2)/2, y+height/2, text='Closed', fill=MUTED,font=('Segoe UI',-15))
            for lane, item in booked:
                x = (item['start'] - 420) * width / 960
                w = (item['end'] - item['start']) * width / 960
                name = item['label']
                if self.booking_font.measure(name) > w-20:
                    while name and self.booking_font.measure(name+'…') > w-20: name=name[:-1]
                    name = name+'…'
                time_label = f"{clock(item['start'])}–{clock(item['end'])}"
                if self.booking_font.measure(time_label)>w-20: time_label=clock(item['start'])
                text = f"{name}\n{time_label}"
                button = tk.Button(self.track, text=text, relief='flat', borderwidth=0, bg='#eaf3ff', fg='#0756ad', activebackground='#d8eaff', anchor='w', padx=8, font=self.booking_font, command=lambda r=room['name'], b=item: self.show_booking(r, b))
                self.track.create_window(x+3, y+lane*72+6, window=button, anchor='nw', width=max(18,w-6), height=60)
                self.booking_widgets.append(button)
            y += height
            self.track.create_line(0, y, width, y, fill='#e1e6ee')
            self.names.create_line(0, y, 190, y, fill='#e1e6ee')
        self.track.configure(scrollregion=(0, 0, width, max(y, 150)))
        self.axis.configure(scrollregion=(0, 0, width, 46))
        self.names.configure(scrollregion=(0, 0, 190, max(y, 150)))
        if self.track.winfo_width() >= width: self.xbar.grid_remove()
        else: self.xbar.grid()
        self.stop_button.state(['!disabled'] if self.scanner.active else ['disabled'])
        self.refresh_button.state(['disabled'] if self.scanner.active or self.other_busy() else ['!disabled'])
        self.axis.xview_moveto(self.track.xview()[0])
