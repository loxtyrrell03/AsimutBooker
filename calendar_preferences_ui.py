"""Deterministic desktop calendar day and multi-date preferences editor."""
from datetime import date
import tkinter as tk
from tkinter import ttk

from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict


def open_calendar_preferences(app, dates, settings_path):
    dates = sorted({value.isoformat() if isinstance(value, date) else value for value in dates})
    dates = [value for value in dates if value >= date.today().isoformat()]
    if not dates:
        return None
    window = tk.Toplevel(app.root)
    window.title('Calendar practice settings')
    window.geometry('740x620')
    window.minsize(680, 580)
    window.transient(app.root)
    body = ttk.Frame(window, padding=20)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='Practice dates', font=(app.ui_font_family, 20, 'bold')).pack(anchor='w')
    status = tk.StringVar()
    ttk.Label(body, textvariable=status, wraplength=650, foreground='#b73332').pack(fill='x', pady=8)
    content = ttk.Frame(body)
    content.pack(fill='both', expand=True)
    left = ttk.Frame(content)
    left.pack(side='left', fill='y', padx=(0, 20))
    ttk.Label(left, text='Select dates to change').pack(anchor='w')
    choices = tk.Listbox(left, selectmode='extended', exportselection=False, width=21, height=16,
                         font=(app.ui_font_family, 12), selectbackground='#0868d9')
    choices.pack(fill='both', expand=True, pady=8)
    for value in dates:
        choices.insert('end', date.fromisoformat(value).strftime('%a %d %b %Y'))
    choices.selection_set(0)
    ttk.Button(left, text='Select all dates', command=lambda: choices.selection_set(0, 'end')).pack(fill='x')
    right = ttk.Frame(content)
    right.pack(side='left', fill='both', expand=True)
    scope = tk.StringVar()
    ttk.Label(right, textvariable=scope, wraplength=410, font=(app.ui_font_family, 12, 'bold')).pack(anchor='w', pady=(0, 12))
    enabled = tk.BooleanVar()
    hours = tk.StringVar()
    mode = tk.StringVar(value='Use default time')
    start, end = tk.StringVar(), tk.StringVar()
    strict = tk.BooleanVar()
    default_label = tk.StringVar()
    ttk.Checkbutton(right, text='Book on these dates', variable=enabled).pack(anchor='w', pady=8)
    ttk.Label(right, text='Target hours (blank uses daily target)').pack(anchor='w')
    ttk.Spinbox(right, from_=.5, to=12, increment=.5, textvariable=hours, width=12).pack(anchor='w', pady=(4, 14))
    ttk.Label(right, text='Preferred time for these dates').pack(anchor='w')
    modes = ttk.Combobox(right, textvariable=mode, values=('Use default time', 'Custom time', 'Any time'), state='readonly')
    modes.pack(fill='x', pady=(4, 8))
    ttk.Label(right, textvariable=default_label, foreground='#667080', wraplength=410).pack(anchor='w')
    times = ttk.Frame(right)
    times.pack(fill='x', pady=12)
    clocks = [f'{minute // 60:02d}:{minute % 60:02d}' for minute in range(0, 1440, 15)]
    ttk.Label(times, text='Start').grid(row=0, column=0, sticky='w')
    ttk.Label(times, text='End').grid(row=0, column=1, sticky='w', padx=(16, 0))
    start_control = ttk.Combobox(times, textvariable=start, values=clocks, width=9, state='readonly')
    end_control = ttk.Combobox(times, textvariable=end, values=clocks, width=9, state='readonly')
    start_control.grid(row=1, column=0, sticky='w', pady=5)
    end_control.grid(row=1, column=1, sticky='w', padx=(16, 0), pady=5)
    strict_control = ttk.Checkbutton(right, text='Only book within these times', variable=strict)
    strict_control.pack(anchor='w', pady=8)
    ttk.Label(right, text='Turning dates off leaves existing reservations in place.', wraplength=400).pack(anchor='w', pady=12)
    draft_note = tk.StringVar()
    ttk.Label(right, textvariable=draft_note, foreground='#0868d9', wraplength=400).pack(anchor='w')
    document = {}
    drafts = {}
    loading = [False]
    loaded_selection = [()]

    def selected():
        return tuple(dates[int(index)] for index in choices.curselection())

    def update_controls(*_):
        state = 'readonly' if mode.get() == 'Custom time' else 'disabled'
        start_control.configure(state=state)
        end_control.configure(state=state)
        strict_control.configure(state='normal' if state == 'readonly' else 'disabled')

    def capture(field):
        if loading[0] or not document:
            return
        # Changes apply only after a real edit, never just from selecting dates.
        value = {'enabled': enabled.get(), 'hours': hours.get(), 'time': None if mode.get() == 'Use default time' else
                 {'enabled': mode.get() == 'Custom time', 'start_time': start.get(), 'end_time': end.get(), 'strict_mode': strict.get()}}
        for key in loaded_selection[0]:
            drafts[key] = {**drafts.get(key, {}), field: value[field]}
        draft_note.set(f'{len(drafts)} changed dates · Save changes to apply')
        update_controls()

    def show_selection(*_):
        keys = selected()
        if not keys or not document:
            return
        loaded_selection[0] = keys
        loading[0] = True
        first = keys[0]
        existing = document.get('date_time_preferences', {}).get(first)
        state = {'enabled': app.day_vars[first].get() if first in app.day_vars else first not in document['disabled_dates'],
                                   'hours': str(document['practice_plan']['date_overrides'].get(first, '')), 'time': existing, **drafts.get(first, {})}
        enabled.set(state['enabled']); hours.set(state['hours'])
        time = state['time'] or document['time_preferences']
        mode.set('Use default time' if state['time'] is None else 'Custom time' if time['enabled'] else 'Any time')
        start.set(time['start_time']); end.set(time['end_time']); strict.set(time['strict_mode'])
        scope.set(date.fromisoformat(first).strftime('%A %d %B') if len(keys) == 1 else f'{len(keys)} dates selected — edits apply to all')
        default = document['time_preferences']
        default_label.set(f"Default: {default['start_time']}–{default['end_time']}" if default['enabled'] else 'Default: any time')
        update_controls()
        loading[0] = False

    def reload():
        try:
            current = read_phone_preferences(settings_path)
        except Exception:
            status.set('Saved settings could not be loaded. Try again.'); return
        document.clear(); document.update(current)
        status.set('Draft retained. Review it before saving.' if drafts else '')
        save_button.configure(state='normal')
        show_selection()

    def save():
        if not drafts:
            status.set('No changes to save.'); return
        try:
            changes = {}
            day_changes = [{'date': key, 'enabled': value['enabled']} for key, value in drafts.items() if 'enabled' in value]
            hour_changes = [{'date': key, 'hours': None if value['hours'] == '' else float(value['hours'])} for key, value in drafts.items() if 'hours' in value]
            time_changes = {key: value['time'] for key, value in drafts.items() if 'time' in value}
            if day_changes: changes['booking_days'] = day_changes
            if hour_changes: changes['practice_plan'] = {'date_overrides': hour_changes}
            if time_changes: changes['date_time_preferences'] = time_changes
            saved = save_phone_preferences({'revision': document['revision'], 'changes': changes}, settings_path)
        except PreferenceConflict:
            status.set('Settings changed on another device. Reload saved values, review your draft, then save.')
            save_button.configure(state='disabled'); return
        except Exception as exc:
            status.set(f'Could not save: {exc}'); return
        for key, value in drafts.items():
            if 'enabled' not in value:
                continue
            if key in app.day_vars:
                app.day_vars[key].set(value['enabled'])
            if isinstance(getattr(app, 'calendar_day_snapshot', None), dict):
                app.calendar_day_snapshot[key] = value['enabled']
        document.clear(); document.update(saved); drafts.clear()
        app.load_practice_plan_settings()
        app.load_booking_days()
        app._refresh_calendar()
        app._update_days_summary()
        draft_note.set('Calendar settings saved')
        status.set('')

    for variable, field in ((enabled, 'enabled'), (hours, 'hours'), (mode, 'time'), (start, 'time'), (end, 'time'), (strict, 'time')):
        variable.trace_add('write', lambda *_, key=field: capture(key))
    choices.bind('<<ListboxSelect>>', show_selection)
    buttons = ttk.Frame(body)
    buttons.pack(fill='x', pady=(16, 0))
    save_button = ttk.Button(buttons, text='Save changes', command=save, style='Primary.TButton')
    save_button.pack(side='right', padx=(8, 0))
    ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='right')
    ttk.Button(buttons, text='Reload saved values', command=reload).pack(side='left')
    window.calendar_controls = {'dates': choices, 'enabled': enabled, 'hours': hours, 'mode': mode,
                                'start': start, 'end': end, 'strict': strict, 'save': save_button,
                                'status': status, 'drafts': drafts, 'reload': reload}
    reload()
    return window
