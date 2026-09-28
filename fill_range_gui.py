"""An inline My Week fill form, using the same durable worker as the phone."""
import tkinter as tk
from tkinter import ttk

from desktop_fill_range import DesktopFillRange
from fill_range import local_now, validate_choices
from open_canvas_ui import HelpTip
from quiet_focus import RoundedCard, label, SURFACE, MUTED


class FillRangePanel(RoundedCard):
    def __init__(self, parent, *, on_details, on_refresh, on_close, other_busy=lambda:False, controller=None):
        super().__init__(parent,padding=20,border='#e1e6ee')
        self.controller=controller or DesktopFillRange()
        self.on_details,self.on_refresh,self.on_close,self.other_busy=on_details,on_refresh,on_close,other_busy
        self.day=tk.StringVar(value=str(local_now().date()))
        self.start=tk.StringVar(value='11:00');self.end=tk.StringVar(value='13:00')
        self.error='';self.published=None;self.job=None;self._timer=None
        body=self.content
        heading=tk.Frame(body,bg=SURFACE);heading.pack(fill='x')
        label(heading,'Fill time range',size=24,bold=True).pack(side='left')
        HelpTip(heading,'Temporarily overrides targets, preferred times, booking-off dates, breaks, session length and cancelled-time protections. Eligible rooms, existing events and ASIMUT limits still apply.').pack(side='left',padx=10)
        label(body,'Book the gaps around existing practice.',size=14,color=MUTED).pack(anchor='w',pady=(8,18))
        fields=tk.Frame(body,bg=SURFACE);fields.pack(fill='x')
        self.entries=[]
        for i,(title,var) in enumerate((('Date (YYYY-MM-DD)',self.day),('From',self.start),('Until',self.end))):
            fields.columnconfigure(i,weight=2 if i==0 else 1,uniform='fill')
            col=tk.Frame(fields,bg=SURFACE);col.grid(row=0,column=i,sticky='ew',padx=(0,12 if i<2 else 0))
            label(col,title,size=13,color=MUTED).pack(anchor='w',pady=(0,8))
            entry=ttk.Entry(col,textvariable=var,font=('Segoe UI',-17),width=10)
            entry.pack(fill='x',ipady=9);self.entries.append(entry)
        self.note=label(body,'Temporarily overrides practice settings. Keeps existing bookings. ASIMUT limits apply.',size=13,color=MUTED,wraplength=600)
        self.note.pack(fill='x',pady=18)
        actions=tk.Frame(body,bg=SURFACE);actions.pack(fill='x')
        self.start_button=ttk.Button(actions,text='Fill this range',style='Primary.TButton',command=self.submit)
        self.start_button.pack(side='left',fill='x',expand=True)
        self.close_button=ttk.Button(actions,text='Cancel',command=on_close)
        self.close_button.pack(side='left',padx=(12,0))
        self.status=label(body,size=14,wraplength=650);self.status.pack(fill='x',pady=(18,8))
        self.progress=ttk.Progressbar(body,mode='indeterminate')
        self.results=tk.Frame(body,bg=SURFACE);self.results.pack(fill='x')
        row=tk.Frame(body,bg=SURFACE);row.pack(fill='x')
        self.stop=ttk.Button(row,text='Stop',command=self.controller.stop)
        self.review=ttk.Button(row,text='Check booking status',command=lambda:self.submit(review=True))
        self.retry=ttk.Button(row,text='Try remaining gaps',command=self.retry_range)
        self.bind('<Destroy>',self.destroyed,add='+')
        body.bind('<Configure>',lambda e:(self.status.configure(wraplength=max(230,e.width)),self.note.configure(wraplength=max(230,e.width))),add='+')
        self.poll()

    def open_date(self,day):
        if not self.controller.active:
            self.day.set(day)
        self.error=''
        self.entries[1].focus_set()

    def submit(self,review=False):
        if self.other_busy():
            self.error='Wait for the current operation to finish.';return
        try:
            choices={'date':self.day.get(),'start_time':self.start.get(),'end_time':self.end.get()}
            if not review:validate_choices(choices)
            self.controller.start(choices,review=review);self.error=''
        except Exception as exc:self.error=str(exc)

    def retry_range(self):
        choice=(self.job or {}).get('choices')
        if choice:
            self.day.set(choice['date']);self.start.set(choice['start_time']);self.end.set(choice['end_time'])
        self.submit()

    def open_booking(self,b):
        self.on_details({'eventId':b['event_id'],'room':b['room'],'date':b['date'],
                         'startTime':b['start'],'endTime':b['end'],'isReservation':True,'title':'Reservation'})

    def poll(self):
        try:
            job=self.controller.snapshot();active=self.controller.active;self.job=job
            state=(job or {}).get('state')
            held=state in {'running','checking','uncertain'} and not active
            self.start_button.configure(state='disabled' if active or held or self.other_busy() else 'normal')
            for entry in self.entries:entry.configure(state='disabled' if active or held else 'normal')
            self.close_button.configure(text='Back to My Week' if active else 'Cancel')
            result=(job or {}).get('result') or {}
            text=self.error or (job or {}).get('text','')
            if 'covered_minutes' in result:
                r=result['range'];text=f"{result['covered_minutes']} of {result['requested_minutes']} min covered · {r['date']} · {r['start_time']}–{r['end_time']}\n"+text
            self.status.configure(text=text)
            key=(job or {}).get('request_id'),state
            if key!=self.published:
                for child in self.results.winfo_children():child.destroy()
                for b in result.get('bookings',[]):
                    ttk.Button(self.results,text=f"{b['start']}–{b['end']} · {b['room']} · View booking",command=lambda b=b:self.open_booking(b)).pack(fill='x',pady=4)
                for gap in result.get('remaining',[]):
                    label(self.results,f"Unfilled: {gap['start']}–{gap['end']} · {gap['minutes']} min",size=14,color=MUTED).pack(anchor='w',pady=4)
                for reason in result.get('reasons',[]):
                    label(self.results,reason,size=13,color=MUTED,wraplength=600).pack(fill='x',pady=3)
                if job and not active:self.on_refresh()
                self.published=key
            for button,show in ((self.stop,active and state!='checking'),(self.review,held),(self.retry,not active and not held and bool(result.get('remaining')))):
                if show and not button.winfo_manager():button.pack(side='left',padx=(0,12),pady=(12,0))
                elif not show:button.pack_forget()
            if active and not self.progress.winfo_manager():self.progress.pack(fill='x',before=self.results,pady=8);self.progress.start(15)
            elif not active:self.progress.stop();self.progress.pack_forget()
        except Exception as exc:
            self.status.configure(text=str(exc));self.start_button.configure(state='disabled')
        self._timer=self.after(500,self.poll)

    def destroyed(self,event):
        if event.widget is self and self._timer:self.after_cancel(self._timer);self._timer=None
