"""Today-panel controls for one explicit room request."""
import json
import tkinter as tk
from tkinter import ttk

from app_settings import atomic_write_json
from desktop_room_now import DesktopRoomNow
from open_canvas_ui import HelpTip
from quiet_focus import RoundedCard, label, BLUE, TINT, SURFACE as WHITE, INK, MUTED
from room_now import DURATIONS


class RoomNowPanel(RoundedCard):
    def __init__(self, parent, *, on_details, on_refresh, other_busy=lambda: False, controller=None):
        super().__init__(parent, padding=20, border='#e1e6ee')
        self.controller = controller or DesktopRoomNow()
        self.on_details, self.on_refresh, self.other_busy = on_details, on_refresh, other_busy
        self.mode = tk.StringVar(value='preferred')
        self.minutes = tk.IntVar(value=60)
        self.choice_path = self.controller.root / 'data/room_now_preferences.json'
        try:
            values = json.loads(self.choice_path.read_text(encoding='utf-8'))
            if values['mode'] in {'preferred','longest'} and values['minutes'] in DURATIONS:
                self.mode.set(values['mode']); self.minutes.set(values['minutes'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.published = None
        self.error = ''
        self.job = None
        c = self.content
        self.left = tk.Frame(c,bg=WHITE);self.left.grid(row=0,column=0,sticky='new',padx=(0,24))
        self.right = tk.Frame(c,bg=WHITE);self.right.grid(row=0,column=1,sticky='new')
        c.columnconfigure(0,weight=1,uniform='room-now');c.columnconfigure(1,weight=1,uniform='room-now')
        label(self.left,'Find me a room now',size=23,bold=True,wraplength=480).pack(fill='x')
        label(self.left,'Start soonest. Make the most of your time.',size=13,color=MUTED,wraplength=430).pack(fill='x',pady=(6,16))
        modes=tk.Frame(self.left,bg=TINT);modes.pack(fill='x')
        self.choices=[];self.mode_buttons=[]
        for value,title in [('preferred','Preferred duration'),('longest','Longest possible')]:
            button=tk.Button(modes,text=title,font=('Segoe UI',-13),relief='flat',bd=1,pady=10,
                             command=lambda v=value:self.choose(v,120 if v=='longest' else 60))
            button.pack(side='left',fill='x',expand=True,padx=3,pady=3)
            self.choices.append(button);self.mode_buttons.append((value,button))
        row=tk.Frame(self.right,bg=WHITE);row.pack(fill='x')
        self.duration_label=label(row,size=13,color=MUTED);self.duration_label.pack(side='left')
        self.help=HelpTip(row,'Starts as soon as possible today. Books as close to the chosen duration as it can, without going over.');self.help.pack(side='left',padx=6)
        lengths=tk.Frame(self.right,bg=WHITE);lengths.pack(fill='x',pady=(7,0))
        self.length_buttons=[]
        for n,title in [(30,'30 min'),(60,'1 hour'),(90,'1½ hours'),(120,'2 hours')]:
            button=tk.Button(lengths,text=title,font=('Segoe UI',-13),pady=10,relief='solid',bd=1,
                             command=lambda n=n:self.choose(self.mode.get(),n))
            button.pack(side='left',fill='x',expand=True,padx=(0,4))
            self.choices.append(button);self.length_buttons.append((n,button))
        custom=tk.Frame(self.right,bg=WHITE);custom.pack(fill='x',pady=10)
        label(custom,'Custom duration',size=12,color=MUTED).pack(side='left')
        self.custom=ttk.Combobox(custom,values=[f'{n} min' for n in DURATIONS],state='readonly',width=9)
        self.custom.pack(side='right');self.custom.bind('<<ComboboxSelected>>',lambda _:self.choose(self.mode.get(),int(self.custom.get().split()[0])))
        self.start_button=ttk.Button(self.right,text='Find me a room now',style='Primary.TButton',command=self.start)
        self.start_button.pack(fill='x')
        label(self.right,'Books a room as soon as possible today.',size=12,color=MUTED,wraplength=440).pack(fill='x',pady=(8,0))
        self.result=tk.Frame(c,bg=WHITE);self.result.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(12,0))
        self.status=label(self.result,size=14,wraplength=900);self.status.pack(fill='x')
        self.progress=ttk.Progressbar(self.result,mode='indeterminate')
        actions=tk.Frame(self.result,bg=WHITE);actions.pack(fill='x')
        self.stop=ttk.Button(actions,text='Stop',command=self.controller.stop)
        self.review=ttk.Button(actions,text='Check booking status',command=lambda:self.start(review=True))
        self.details=ttk.Button(actions,text='View booking',command=self.open_booking)
        c.bind('<Configure>',self.fit,add='+')
        self._timer=None
        self.bind('<Destroy>',self.destroyed,add='+')
        self.update_controls();self.poll()

    def fit(self,event):
        if event.widget is not self.content:return
        narrow=event.width<700
        self.status.configure(wraplength=max(240,event.width))
        self.left.grid_configure(row=0,column=0,columnspan=2 if narrow else 1,padx=(0,0 if narrow else 24),pady=(0,14 if narrow else 0))
        self.right.grid_configure(row=1 if narrow else 0,column=0 if narrow else 1,columnspan=2 if narrow else 1)
        self.result.grid_configure(row=2 if narrow else 1)

    def choose(self,mode,minutes):
        self.mode.set(mode);self.minutes.set(minutes)
        try:atomic_write_json(self.choice_path,{'mode':mode,'minutes':minutes})
        except OSError:pass
        self.update_controls()

    def update_controls(self):
        for value,button in self.mode_buttons:
            button.configure(bg=WHITE if value==self.mode.get() else TINT,fg=BLUE if value==self.mode.get() else MUTED)
        for value,button in self.length_buttons:
            button.configure(bg=TINT if value==self.minutes.get() else WHITE,fg=BLUE if value==self.minutes.get() else INK)
        self.duration_label.configure(text='Maximum duration' if self.mode.get()=='longest' else 'Preferred duration')
        self.help.help_text=('Starts as soon as possible today. Takes the longest available session, up to your maximum.' if self.mode.get()=='longest' else 'Starts as soon as possible today. Books as close to this duration as it can, without going over.')
        self.custom.set(f'{self.minutes.get()} min')

    def start(self,review=False):
        if self.other_busy():
            self.error='Wait for the current operation to finish.';return
        try:self.controller.start({'mode':self.mode.get(),'minutes':self.minutes.get()},review=review);self.error=''
        except Exception as exc:self.error=str(exc)

    def open_booking(self):
        b=(self.job or {}).get('result',{}).get('booking')
        if b:self.on_details({'eventId':b['event_id'],'room':b['room'],'date':b['date'],'startTime':b['start'],'endTime':b['end'],'isReservation':True,'title':'Reservation'})

    def poll(self):
        try:
            self.job=self.controller.snapshot();active=self.controller.active
            state=(self.job or {}).get('state');uncertain=state in {'running','checking','uncertain'} and not active
            locked=active or uncertain or self.other_busy()
            self.start_button.configure(state='disabled' if locked else 'normal')
            for button in self.choices:button.configure(state='disabled' if active or uncertain else 'normal')
            self.custom.configure(state='disabled' if active or uncertain else 'readonly')
            text=self.error or (self.job or {}).get('text','')
            b=(self.job or {}).get('result',{}).get('booking')
            if b:
                requested=self.job['result'].get('requested_minutes',b['duration_minutes'])
                text+=f"\n{b['date']} · {b['duration_minutes']} min booked"+(f' · requested {requested} min' if b['duration_minutes']<requested else '')
            if uncertain:text+=' Check booking status before trying again.'
            self.status.configure(text=text)
            for button,show in [(self.stop,active and state!='checking'),(self.review,uncertain),(self.details,bool(b))]:
                if show and not button.winfo_manager():button.pack(side='left',padx=(0,8),pady=(10,0))
                elif not show:button.pack_forget()
            if active and not self.progress.winfo_manager():self.progress.pack(fill='x',before=self.status,pady=(0,8));self.progress.start(15)
            elif not active:self.progress.stop();self.progress.pack_forget()
            key=(self.job or {}).get('request_id'),state
            if self.job and not active and key!=self.published:self.published=key;self.on_refresh()
        except Exception as exc:
            self.status.configure(text=str(exc));self.start_button.configure(state='disabled')
        self._timer=self.after(500,self.poll)

    def destroyed(self,event):
        if event.widget is self and self._timer:
            self.after_cancel(self._timer);self._timer=None
