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
PAGE = '#F8FAFC'


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
        binding = parent.bind('<Configure>', lambda event: widget.configure(wraplength=max(40, min(maximum, event.width - 8))), add='+')
        # Refreshing a page destroys its old labels while retaining the parent.
        # Remove their resize callbacks along with them.
        widget.bind('<Destroy>', lambda _event: parent.unbind('<Configure>', binding)
                    if parent.winfo_exists() else None, add='+')
    return widget


class RoundedCard(tk.Canvas):
    """Resizable rounded surface containing ordinary accessible Tk widgets."""
    def __init__(self, parent, fill=SURFACE, padding=24, border='', dotted=False, **kwargs):
        super().__init__(parent, bg=parent.cget('background') if isinstance(parent,tk.Frame) else PAGE, highlightthickness=0, bd=0, **kwargs)
        self.fill, self.padding = fill, padding
        self.border, self.dotted = border, dotted
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
        inset = 2 if self.border else 0
        right, bottom = w - inset, h - inset
        points = [r,inset,w-r,inset,right,inset,right,r,right,h-r,right,bottom,w-r,bottom,r,bottom,inset,bottom,inset,h-r,inset,r,inset,inset]
        self.create_polygon(points, smooth=True, splinesteps=24, fill=self.fill,
                            outline=self.border, width=2 if self.dotted else 1,
                            dash=(1, 3) if self.dotted else (), tags='surface')
        self.tag_lower('surface')
        self.itemconfigure(self.window, width=max(1, w - self.padding * 2))


class ScrollPage(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=PAGE)
        self.canvas = tk.Canvas(self, bg=PAGE, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scrollbar_visibility)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = tk.Frame(self.canvas, bg=PAGE)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.canvas.bind('<Configure>', self._resize_page)
        self.content.bind('<Configure>', self._resize_content)
        self.canvas.bind('<MouseWheel>', self._scroll_wheel)
        # Bind only to descendants of this page, never steal scrolling from other dialogs.
        self.bind('<Enter>', self._bind_wheel)

    def _resize_page(self,event):
        width=min(984,max(1,event.width-24))
        self.canvas.itemconfigure(self.window,width=width)
        self.canvas.coords(self.window,(event.width-width)//2,0)

    def _scrollbar_visibility(self,first,last):
        self.scrollbar.set(first,last)
        if float(first)>0 or float(last)<1:
            if not self.scrollbar.winfo_manager(): self.scrollbar.pack(side='right',fill='y',before=self.canvas)
        elif self.scrollbar.winfo_manager(): self.scrollbar.pack_forget()

    def _resize_content(self,_event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self._bind_wheel()

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
        self.body = tk.Frame(self.content, bg=PAGE, padx=24, pady=24)
        self.body.pack(fill='both', expand=True)
        heading = tk.Frame(self.body, bg=PAGE)
        heading.pack(fill='x', pady=(0,6))
        self.refresh_button=ttk.Button(heading,text='Refresh bookings',command=on_refresh)
        self.refresh_button.pack(side='right')
        label(heading, 'Today', size=32, bold=True).pack(side='left', pady=(8,0))
        self.date_label = label(self.body, size=14, color=MUTED)
        self.date_label.pack(anchor='w',pady=(0,12))
        self.notice = label(self.body, color='#8B5C11', size=13, wraplength=720)
        top = tk.Frame(self.body, bg=PAGE)
        top.pack(fill='x')
        self.hero = RoundedCard(top, fill=TINT)
        self.hero.pack(fill='x')
        c = self.hero.content
        self.eyebrow = label(c, 'UP NEXT', size=12, color=BLUE, bold=True)
        self.eyebrow.pack(anchor='w',pady=(0,12))
        self.room = label(c, 'Checking your bookings…', size=29, bold=True, wraplength=460)
        self.room.pack(anchor='w',fill='x')
        self.when = label(c, size=21, wraplength=460)
        self.when.pack(anchor='w',pady=(12,8))
        self.duration = label(c, size=14, color=MUTED)
        self.duration.pack(anchor='w')
        self.reconfirm = label(c, 'Reconfirm on college Wi-Fi when available.', size=12, color=MUTED, wraplength=360)
        self.reconfirm.pack(anchor='w',pady=(8,10))
        self.details = ttk.Button(c, text='View booking', style='Hero.Primary.TButton', command=lambda: self.on_details(self.next_event))
        self.details.pack(fill='x')
        section = tk.Frame(self.body,bg=PAGE)
        section.pack(fill='x',pady=(12,4))
        label(section,'Also today',size=21,bold=True).pack(side='left')

        self.events = tk.Frame(self.body,bg=PAGE)
        self.events.pack(fill='x')
        ttk.Separator(self.body).pack(fill='x',pady=12)
        week=tk.Frame(self.body,bg=PAGE);week.pack(fill='x')
        self.week_total=label(week,'Booked hours unavailable',size=18,bold=True)
        self.week_total.pack(side='left')
        ttk.Button(week,text='See my week →',command=on_week,style='QuietLink.TButton').pack(side='right')
        self.goal=label(self.body,size=13,color=MUTED);self.goal.pack(anchor='w',pady=(2,10))
        actions=tk.Frame(self.body,bg=PAGE);actions.pack(fill='x',pady=(8,14))
        ttk.Button(actions,text='Ask Assistant',command=lambda:on_ask('')).pack(side='left',padx=10)
        footer=tk.Frame(self.body,bg=PAGE);footer.pack(fill='x')
        self.freshness=label(footer,size=12,color=MUTED);self.freshness.pack(side='left')

    def update_data(self, events, *, available, stale, checked='', goal='', now=None):
        view=display_summary(events,now)
        self.next_event=view['next'] if available else None
        self.date_label.configure(text=datetime.fromisoformat(view['today']).strftime('%A, %d %B'))
        self.goal.configure(text=goal)
        self.week_total.configure(text=f"{view['week_minutes']/60:g} hours booked this week" if available else 'Booked hours unavailable')
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
            minutes=(datetime.strptime(event['endTime'],'%H:%M')-datetime.strptime(event['startTime'],'%H:%M')).total_seconds()/60
            self.duration.configure(text=f'{minutes/60:g} hours of practice')
            self.reconfirm.configure(text='Reconfirm on college Wi-Fi when available.')
            self.details.configure(text='View booking',command=lambda:self.on_details(self.next_event))
        else:
            self.eyebrow.configure(text='YOUR PRACTICE')
            self.room.configure(text='Make room for practice.' if available else 'Let’s check your bookings.')
            self.when.configure(text='No upcoming practice booking' if available else 'Your agenda is unavailable')
            self.duration.configure(text='in your last checked agenda' if available else 'Refresh to see your next session')
            self.reconfirm.configure(text='Use Find me a room now above to book the earliest available session.')
            self.details.configure(text='Find a room' if available else 'Refresh bookings',command=self.on_find if available else self.on_refresh)
        for child in self.events.winfo_children():child.destroy()
        rows=view['also_today'] if available else []
        for event in rows:
            row=tk.Frame(self.events,bg=PAGE,pady=8)
            row.pack(fill='x')
            label(row,f"{event['startTime']}\n{event['endTime']}",size=14,color=MUTED,width=8).pack(side='left')
            title=f"Room {event.get('room','')}" if event.get('isReservation') else event.get('title','College event')
            copy=tk.Frame(row,bg=PAGE);copy.pack(side='left',fill='x',expand=True)
            label(copy,title,size=16,bold=True,wraplength=450).pack(anchor='w')
            label(copy,'Booked practice' if event.get('isReservation') else event.get('room','College event'),size=13,color=MUTED).pack(anchor='w')
            ttk.Button(row,text='View',command=(lambda e=event:self.on_details(e)) if event.get('isReservation') else self.on_week,style='QuietLink.TButton').pack(side='right')
        if not rows: label(self.events,'Nothing else coming up in your checked agenda today.' if available else 'Refresh to check the rest of your day.',size=14,color=MUTED).pack(anchor='w',pady=10)


class WeekEventCard(RoundedCard):
    """Phone-style time column and labelled event colour; plans stay unbooked."""
    def __init__(self, parent, *, start, end, title, state, room='', planned=False,
                 reservation=False, on_details=None):
        accent = BLUE if planned or reservation else '#93620C'
        fill = '#F2F6FC' if planned else TINT if reservation else '#FFF8ED'
        super().__init__(parent, fill=fill, padding=16,
                         border='#94ACCD' if planned else '#B8D5F8' if reservation else '#E9D4AE',
                         dotted=planned)
        self.content.columnconfigure(1, weight=1)
        times=tk.Frame(self.content,bg=fill)
        times.grid(row=0,column=0,sticky='nw',padx=(0,20))
        label(times,start,size=17,bold=True,color=accent).pack(anchor='w')
        label(times,end,size=13,color=MUTED).pack(anchor='w',pady=(4,0))
        copy=tk.Frame(self.content,bg=fill)
        copy.grid(row=0,column=1,sticky='new')
        label(copy,state,size=12,color=accent).pack(anchor='w',fill='x')
        label(copy,title,size=18,bold=True,wraplength=740).pack(anchor='w',fill='x',pady=(5,0))
        if room:
            label(copy,room,size=13,color=MUTED,wraplength=740).pack(anchor='w',fill='x',pady=(5,0))
        if on_details:
            ttk.Button(copy,text='View booking',command=on_details,
                       style='QuietLink.TButton').pack(anchor='w',pady=(6,0))


class WeekPanel(ScrollPage):
    def __init__(self,parent,*,on_calendar,on_refresh,on_details):
        super().__init__(parent)
        self.on_details=on_details
        self.body=tk.Frame(self.content,bg=PAGE,padx=24,pady=24);self.body.pack(fill='both',expand=True)
        head=tk.Frame(self.body,bg=PAGE);head.pack(fill='x')
        label(head,'My Week',size=32,bold=True).pack(side='left')
        ttk.Button(head,text='Plan my practice',command=on_calendar,style='Primary.TButton').pack(side='right')
        self.status=label(self.body,size=13,color=MUTED);self.status.pack(anchor='w',pady=16)
        self.rows=tk.Frame(self.body,bg=PAGE);self.rows.pack(fill='x')
        ttk.Button(self.body,text='Refresh bookings',command=on_refresh,style='QuietLink.TButton').pack(anchor='w',pady=16)

    def update_data(self,events,*,available,stale,checked='',planned=(),closed_dates=(),off_dates=(),plan_days=(),plan_stale=False):
        self.status.configure(text=('Last checked agenda · ' if stale else 'Booked practice and college events · ')+ (checked or 'Not checked yet'))
        for child in self.rows.winfo_children():child.destroy()
        today=datetime.now(ZoneInfo('Europe/London')).date().isoformat()
        grouped={}
        for event in events:
            if event['date']>=today:grouped.setdefault(event['date'],[]).append(event)
        for candidate in planned:
            if candidate.date>=today:grouped.setdefault(candidate.date,[])
        for day in plan_days:
            if day.date>=today:grouped.setdefault(day.date,[])
        for day in closed_dates:
            if day>=today:grouped.setdefault(day,[])
        if plan_stale:
            label(self.rows,'Your practice plan needs a refresh. Bookings below are from the last checked agenda.',color=MUTED,wraplength=650).pack(fill='x',pady=8)
        if not available:label(self.rows,'Your agenda is unavailable. Refresh before relying on these plans.',color=MUTED,wraplength=600).pack(anchor='w',pady=20)
        if not grouped and available:label(self.rows,'No upcoming sessions in the last checked agenda.',color=MUTED).pack(anchor='w',pady=20)
        for day,day_events in sorted(grouped.items()):
            heading=label(self.rows,datetime.fromisoformat(day).strftime('%A, %d %B'),size=19,bold=True,color='#B73332' if day in closed_dates else INK)
            if day in closed_dates or day in off_dates: heading.configure(font=('Segoe UI',-19,'bold overstrike'))
            heading.pack(anchor='w',pady=(20,10))
            if day in closed_dates or day in off_dates:
                label(self.rows,'Practice rooms closed' if day in closed_dates else 'Booking off',color='#B73332' if day in closed_dates else MUTED,size=13).pack(anchor='w',pady=(0,6))
            sessions=[(event['startTime'],0,event) for event in day_events]
            sessions.extend((candidate.start_time,1,candidate) for candidate in planned if candidate.date==day)
            for _,is_plan,item in sorted(sessions,key=lambda entry:entry[:2]):
                if is_plan:
                    state = f'Planned extension · {item.confirmed_minutes} min already booked' if item.confirmed_minutes else 'Planned · not booked yet'
                    card=WeekEventCard(self.rows,start=item.start_time,end=item.end_time,
                                       title=item.room,state=state,planned=True)
                else:
                    reservation=bool(item.get('isReservation'))
                    title=f"Room {item.get('room') or 'not shown'}" if reservation else item.get('title') or 'College event'
                    card=WeekEventCard(self.rows,start=item['startTime'],end=item['endTime'],
                                       title=title,state='Booked' if reservation else 'College event',
                                       room='' if reservation else item.get('room',''),reservation=reservation,
                                       on_details=(lambda e=item:self.on_details(e)) if reservation else None)
                card.pack(fill='x',pady=5)
            plan_day=next((item for item in plan_days if item.date==day),None)
            if plan_day:
                label(self.rows,f'Daily target: {plan_day.target_minutes/60:g} hours',size=13,color=MUTED).pack(anchor='w',pady=8)
                if not plan_day.primary and not plan_day.additional and plan_day.reason:
                    label(self.rows,plan_day.reason,size=13,color=MUTED,wraplength=650).pack(fill='x')
