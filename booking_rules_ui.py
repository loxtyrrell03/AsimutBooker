"""Compact quota preset editor using the existing desktop controls."""
import tkinter as tk
from tkinter import ttk, messagebox
from open_canvas_ui import HelpTip
from booking_rules import load_booking_rules, apply_booking_rules, PRESETS, PRESET_LABELS

def show_booking_rules_dialog(app):
    current = load_booking_rules(app.load_settings())
    dialog = tk.Toplevel(app.root)
    dialog.title('Booking rules')
    dialog.transient(app.root)
    body = ttk.Frame(dialog, padding=20)
    body.pack(fill='both', expand=True)
    body.columnconfigure(1, weight=1)
    ttk.Label(body, text='Booking rules', font=(app.ui_font_family, 16, 'bold')).grid(row=0,column=0,columnspan=3,sticky='w')
    ttk.Label(body, text='ASIMUT must approve each booking. Its live limits may be lower.', wraplength=470).grid(row=1,column=0,columnspan=3,sticky='w',pady=10)
    preset = tk.StringVar(value=PRESET_LABELS[current.preset])
    selector = ttk.Combobox(body,textvariable=preset,values=list(PRESET_LABELS.values()),state='readonly',width=34)
    ttk.Label(body,text='Preset').grid(row=2,column=0,sticky='w',padx=(0,16),pady=8)
    selector.grid(row=2,column=1,columnspan=2,sticky='ew')
    fields = [('rolling_quota_hours','Advance quota (hours)'),('peak_quota_minutes','Weekday peak quota (minutes)'),
              ('free_horizon_minutes','Free horizon (minutes)'),('peak_start_minutes','Weekday peak start'),('peak_end_minutes','Weekday peak end')]
    variables, controls = {}, {}
    for row,(key,label) in enumerate(fields,3):
        variables[key] = tk.StringVar()
        ttk.Label(body,text=label).grid(row=row,column=0,sticky='w',padx=(0,16),pady=8)
        control = ttk.Entry(body,textvariable=variables[key],width=12)
        control.grid(row=row,column=1,sticky='ew')
        controls[key] = control
    HelpTip(body,'Both endpoints must fit inside this window. Available quota is used normally.').grid(row=5,column=2,padx=8)
    def populate(rule):
        for key,_ in fields:
            value=getattr(rule,key)
            variables[key].set(f'{value//60:02d}:{value%60:02d}' if key in ('peak_start_minutes','peak_end_minutes') else f'{value:g}')
        for control in controls.values():
            control.configure(state='normal' if preset.get()==PRESET_LABELS['custom'] else 'readonly')
    def selected(event=None):
        name=next(k for k,v in PRESET_LABELS.items() if v==preset.get())
        if name in PRESETS:
            populate(PRESETS[name])
        else:
            for control in controls.values(): control.configure(state='normal')
    selector.bind('<<ComboboxSelected>>',selected)
    populate(current)
    ttk.Label(body,text='Room horizons and durations are checked live. Existing reservations stay in place.',wraplength=470).grid(row=8,column=0,columnspan=3,sticky='w',pady=12)
    def save():
        try:
            name=next(k for k,v in PRESET_LABELS.items() if v==preset.get())
            patch={'preset':name}
            if name=='custom':
                for key,_ in fields:
                    value=variables[key].get()
                    if key in ('peak_start_minutes','peak_end_minutes'):
                        h,m=map(int,value.split(':'))
                        if not (0 <= h <= 24 and 0 <= m < 60 and (h < 24 or m == 0)):
                            raise ValueError('Peak times must use HH:MM between 00:00 and 24:00.')
                        patch[key]=h*60+m
                    else: patch[key]=float(value) if key=='rolling_quota_hours' else int(value)
            apply_booking_rules({},patch)
            if app._update_settings(lambda settings:apply_booking_rules(settings,patch)):
                app.log('Booking rules saved; future runs use the selected preset.','info')
                dialog.destroy()
        except (ValueError,RuntimeError) as exc:
            messagebox.showerror('Invalid booking rules',str(exc),parent=dialog)
    buttons=ttk.Frame(body); buttons.grid(row=9,column=0,columnspan=3,sticky='e')
    ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='left',padx=8)
    ttk.Button(buttons,text='Save rules',command=save).pack(side='left')
    return dialog
