"""Quiet Focus desktop presentation. Reads display snapshots; never books rooms."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import tkinter as tk
from tkinter import ttk

BLUE = '#0868D9'
TINT = '#EAF3FF'
INK = '#1D2430'
MUTED = '#667080'
SURFACE = '#FFFFFF'
LINE = '#E1E6EE'


def display_summary(events, now=None):
    """Choose the next practice reservation, including a session in progress."""
    now = now or datetime.now(ZoneInfo('Europe/London'))
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo('Europe/London'))
    today, clock = now.date().isoformat(), now.strftime('%H:%M')
    ordered = sorted(events, key=lambda e: (e['date'], e['startTime']))
    next_event = next((e for e in ordered if e.get('isReservation') and
                       (e['date'] > today or e['date'] == today and e['endTime'] > clock)), None)
    monday = now.date() - timedelta(days=now.weekday())
    end = (monday + timedelta(days=7)).isoformat()
    minutes = 0
    for event in ordered:
        if event.get('isReservation') and monday.isoformat() <= event['date'] < end:
            start = datetime.strptime(event['startTime'], '%H:%M')
            finish = datetime.strptime(event['endTime'], '%H:%M')
            minutes += max(0, int((finish - start).total_seconds() / 60))
    return {
        'today': today, 'next': next_event, 'week_minutes': minutes,
        'also_today': [e for e in ordered if e['date'] == today and e['endTime'] > clock and e is not next_event],
        'in_progress': bool(next_event and next_event['date'] == today and next_event['startTime'] <= clock),
    }


def label(parent, text='', size=15, color=INK, bold=False, **kwargs):
    widget = tk.Label(parent, text=text, font=('Segoe UI', -size, 'bold' if bold else 'normal'),
                      bg=parent.cget('background'), fg=color, anchor='w', justify='left', **kwargs)
    if 'wraplength' in kwargs:
        maximum = kwargs['wraplength']
        parent.bind('<Configure>', lambda event: widget.configure(wraplength=max(40, min(maximum, event.width - 8))), add='+')
    return widget


class RoundedCard(tk.Canvas):
    """Resizable rounded surface containing ordinary accessible Tk widgets."""
    def __init__(self, parent, fill=SURFACE, padding=24, **kwargs):
        super().__init__(parent, bg=SURFACE, highlightthickness=0, bd=0, **kwargs)
        self.fill, self.padding = fill, padding
        self.content = tk.Frame(self, bg=fill)
        self.window = self.create_window(padding, padding, window=self.content, anchor='nw')
        self.bind('<Configure>', self._layout)
        self.content.bind('<Configure>', self._fit)

    def _fit(self, _event=None):
        height = self.content.winfo_reqheight() + self.padding * 2
        if self.winfo_reqheight() != height:
            self.configure(height=height)

    def _layout(self, event):
        w, h, r = event.width, event.height, 22
        self.delete('surface')
        points = [r,0,w-r,0,w,0,w,r,w,h-r,w,h,w-r,h,r,h,0,h,0,h-r,0,r,0,0]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=self.fill,
                            outline='', tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window, width=max(1, w - self.padding * 2))


class ScrollPage(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=SURFACE)
        self.canvas = tk.Canvas(self, bg=SURFACE, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = tk.Frame(self.canvas, bg=SURFACE)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.content.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<MouseWheel>', self._scroll_wheel)
        # Bind only to descendants of this page, never steal scrolling from other dialogs.
        self.bind('<Enter>', self._bind_wheel)

    def _bind_wheel(self, _event=None):
        def visit(widget):
            widget.bind('<MouseWheel>', self._scroll_wheel)
            for child in widget.winfo_children(): visit(child)
        visit(self.content)

    def _scroll_wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        # A wheel gesture scrolls the page without also changing a spinbox or
        # combobox value through its class binding.
        return 'break'


class TodayPanel(ScrollPage):
    def __init__(self, parent, *, on_find, on_week, on_ask, on_refresh, on_details):
        super().__init__(parent)
        self.on_find, self.on_week, self.on_ask = on_find, on_week, on_ask
        self.on_refresh, self.on_details = on_refresh, on_details
        self.next_event = None
        self.body = tk.Frame(self.content, bg=SURFACE, padx=34, pady=24)
        self.body.pack(fill='both', expand=True)
        heading = tk.Frame(self.body, bg=SURFACE)
        heading.pack(fill='x', pady=(0,20))
        self.date_label = label(heading, size=14, color=MUTED)
        self.date_label.pack(anchor='w')
        ttk.Button(heading, text='Find a room', command=on_find, style='Primary.TButton').pack(side='right')
        label(heading, 'Today', size=32, bold=True).pack(side='left', pady=(8,0))
        self.notice = label(self.body, color='#8B5C11', size=13, wraplength=720)
        top = tk.Frame(self.body, bg=SURFACE)
        top.pack(fill='x')
        top.columnconfigure(0, weight=5, uniform='cards')
        top.columnconfigure(1, weight=3, uniform='cards')
        self.hero = RoundedCard(top, fill=TINT)
        self.hero.grid(row=0,column=0,sticky='nsew',padx=(0,18))
        c = self.hero.content
        self.eyebrow = label(c, 'UP NEXT', size=12, color=BLUE, bold=True)
        self.eyebrow.pack(anchor='w',pady=(0,18))
        self.room = label(c, 'Checking your bookings…', size=29, bold=True, wraplength=460)
        self.room.pack(anchor='w',fill='x')
        self.when = label(c, size=21, wraplength=460)
        self.when.pack(anchor='w',pady=(12,8))
        self.duration = label(c, size=14, color=MUTED)
        self.duration.pack(anchor='w')
        self.reconfirm = label(c, 'Reconfirm on college Wi-Fi when available.', size=12, color=MUTED, wraplength=360)
        self.reconfirm.pack(anchor='w',pady=(16,12))
        self.details = ttk.Button(c, text='View booking', style='Primary.TButton', command=lambda: self.on_details(self.next_event))
        self.details.pack(anchor='w')
        week = RoundedCard(top, fill='#F7F9FC')
        week.grid(row=0,column=1,sticky='nsew')
        c = week.content
        label(c,'This week',size=20,bold=True).pack(anchor='w')
        self.week_total = label(c,'—',size=37,bold=True)
        self.week_total.pack(anchor='w',pady=(24,0))
        label(c,'booked in your checked agenda',size=13,color=MUTED,wraplength=230).pack(anchor='w')
        self.goal = label(c,size=14,color=MUTED,wraplength=230)
        self.goal.pack(anchor='w',pady=(20,16))
        ttk.Button(c,text='See my week  →',command=on_week,style='QuietLink.TButton').pack(anchor='w')
        section = tk.Frame(self.body,bg=SURFACE)
        section.pack(fill='x',pady=(20,8))
        label(section,'Also today',size=21,bold=True).pack(side='left')
        ttk.Button(section,text='My Week  →',command=on_week,style='QuietLink.TButton').pack(side='right')
        self.events = tk.Frame(self.body,bg=SURFACE)
        self.events.pack(fill='x')
        ask = RoundedCard(self.body,fill='#F7F9FC',padding=18)
        ask.pack(fill='x',pady=(18,12))
        ttk.Button(ask.content,text='Ask Assistant',command=lambda:on_ask(''),style='Toolbar.TButton').pack(side='right')
        label(ask.content,'Need to change your plans?',size=16,bold=True).pack(anchor='w')
        label(ask.content,'Find a room, change a session, or take a day off.',size=13,color=MUTED).pack(anchor='w',pady=(5,0))
        footer=tk.Frame(self.body,bg=SURFACE);footer.pack(fill='x')
        self.freshness=label(footer,size=12,color=MUTED);self.freshness.pack(side='left')
        self.refresh_button=ttk.Button(footer,text='Refresh bookings',command=on_refresh,style='QuietLink.TButton')
        self.refresh_button.pack(side='right')

    def update_data(self, events, *, available, stale, checked='', goal='', now=None):
        view=display_summary(events,now)
        self.next_event=view['next'] if available else None
        self.date_label.configure(text=datetime.fromisoformat(view['today']).strftime('%A, %d %B'))
        self.goal.configure(text=goal)
        self.week_total.configure(text=f"{view['week_minutes']/60:g} hours" if available else '—')
        self.freshness.configure(text=f'Last checked {checked}' if checked else 'Not checked yet')
        if stale:
            self.notice.configure(text='Showing your last checked bookings. Refresh to check for changes.')
            self.notice.pack(fill='x',before=self.hero.master,pady=(0,14))
        else: self.notice.pack_forget()
        if self.next_event:
            event=self.next_event
            self.eyebrow.configure(text=('HAPPENING NOW' if view['in_progress'] else 'UP NEXT') + (' · Last checked' if stale else ' · Booked'))
            self.room.configure(text=f"Room {event.get('room') or 'not shown'}")
            day='Today' if event['date']==view['today'] else datetime.fromisoformat(event['date']).strftime('%a, %d %b')
            self.when.configure(text=f"{day} · {event['startTime']}–{event['endTime']}")
            self.duration.configure(text='Time reserved for your practice')
            self.reconfirm.configure(text='Reconfirm on college Wi-Fi when available.')
            self.details.configure(text='View booking',command=lambda:self.on_details(self.next_event))
        else:
            self.eyebrow.configure(text='YOUR PRACTICE')
            self.room.configure(text='Make room for practice.' if available else 'Let’s check your bookings.')
            self.when.configure(text='No upcoming practice booking' if available else 'Your agenda is unavailable')
            self.duration.configure(text='in your last checked agenda' if available else 'Refresh to see your next session')
            self.reconfirm.configure(text='Choose a date and time with the assistant.')
            self.details.configure(text='Find a room' if available else 'Refresh bookings',command=self.on_find if available else self.on_refresh)
        for child in self.events.winfo_children():child.destroy()
        rows=view['also_today'] if available else []
        for event in rows:
            row=tk.Frame(self.events,bg=SURFACE,pady=10)
            row.pack(fill='x')
            label(row,f"{event['startTime']}\n{event['endTime']}",size=14,color=MUTED,width=8).pack(side='left')
            title=f"Room {event.get('room','')}" if event.get('isReservation') else event.get('title','College event')
            copy=tk.Frame(row,bg=SURFACE);copy.pack(side='left',fill='x',expand=True)
            label(copy,title,size=16,bold=True,wraplength=450).pack(anchor='w')
            label(copy,'Booked practice' if event.get('isReservation') else event.get('room','College event'),size=13,color=MUTED).pack(anchor='w')
            ttk.Button(row,text='View',command=(lambda e=event:self.on_details(e)) if event.get('isReservation') else self.on_week,style='QuietLink.TButton').pack(side='right')
        if not rows: label(self.events,'Nothing else coming up in your checked agenda today.' if available else 'Refresh to check the rest of your day.',size=14,color=MUTED).pack(anchor='w',pady=10)


class WeekPanel(ScrollPage):
    def __init__(self,parent,*,on_calendar,on_refresh,on_details):
        super().__init__(parent)
        self.on_details=on_details
        self.body=tk.Frame(self.content,bg=SURFACE,padx=34,pady=30);self.body.pack(fill='both',expand=True)
        head=tk.Frame(self.body,bg=SURFACE);head.pack(fill='x')
        label(head,'My Week',size=32,bold=True).pack(side='left')
        ttk.Button(head,text='Plan my practice',command=on_calendar,style='Primary.TButton').pack(side='right')
        self.status=label(self.body,size=13,color=MUTED);self.status.pack(anchor='w',pady=16)
        self.rows=tk.Frame(self.body,bg=SURFACE);self.rows.pack(fill='x')
        ttk.Button(self.body,text='Refresh bookings',command=on_refresh,style='QuietLink.TButton').pack(anchor='w',pady=16)

    def update_data(self,events,*,available,stale,checked='',planned=()):
        self.status.configure(text=('Last checked agenda · ' if stale else 'Booked practice and college events · ')+ (checked or 'Not checked yet'))
        for child in self.rows.winfo_children():child.destroy()
        today=datetime.now(ZoneInfo('Europe/London')).date().isoformat()
        grouped={}
        for event in events:
            if event['date']>=today:grouped.setdefault(event['date'],[]).append(event)
        for candidate in planned:
            if candidate.date>=today:grouped.setdefault(candidate.date,[])
        if not available:label(self.rows,'Your agenda is unavailable. Refresh before relying on these plans.',color=MUTED,wraplength=600).pack(anchor='w',pady=20)
        if not grouped and available:label(self.rows,'No upcoming sessions in the last checked agenda.',color=MUTED).pack(anchor='w',pady=20)
        for day,day_events in sorted(grouped.items()):
            label(self.rows,datetime.fromisoformat(day).strftime('%A, %d %B'),size=19,bold=True).pack(anchor='w',pady=(20,10))
            for event in sorted(day_events,key=lambda e:e['startTime']):
                card=RoundedCard(self.rows,fill=TINT if event.get('isReservation') else '#F3EFF8',padding=17);card.pack(fill='x',pady=5)
                title=f"Room {event.get('room','')}" if event.get('isReservation') else event.get('title','College event')
                if event.get('isReservation'):ttk.Button(card.content,text='View booking',command=lambda e=event:self.on_details(e)).pack(side='right')
                label(card.content,f"{event['startTime']}–{event['endTime']}   {title}",size=17,bold=True,wraplength=580).pack(anchor='w')
                label(card.content,'Booked' if event.get('isReservation') else event.get('room','College event'),size=13,color=MUTED).pack(anchor='w',pady=(6,0))
            for candidate in planned:
                if candidate.date!=day:continue
                card=RoundedCard(self.rows,fill='#F6F8FB',padding=17);card.pack(fill='x',pady=5)
                label(card.content,f'{candidate.start_time}–{candidate.end_time}   {candidate.room}',size=17,bold=True).pack(anchor='w')
                state = f'Planned extension · {candidate.confirmed_minutes} min already booked' if candidate.confirmed_minutes else 'Planned · not booked yet'
                label(card.content,state,size=13,color=MUTED).pack(anchor='w',pady=(6,0))
